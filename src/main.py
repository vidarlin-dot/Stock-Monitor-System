# -*- coding: utf-8 -*-
"""US stock daily report with FocusScore analysis.

FocusScore pipeline (same as Taiwan):
  1. Fetch market data for watchlist via yfinance
  2. Compute FocusScore = 0.20*Heat + 0.30*Inst + 0.30*Catalyst
                          + 0.10*Trend - Risk
  3. Filter: score >= 60 OR tracking=焦點股 in sheet
  4. Build report with operation suggestions
  5. Update Google Sheet with focus scores
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Any, Dict, List

import pytz

from config import GoogleSheetsManager
from line_notifier import LineNotifier
from us_market_context import (
    fetch_market_context,
    build_market_context_lines,
    get_mood_label,
    scenario_lines,
    disclaimer_lines,
    weekend_note,
)
from us_market_data import (
    StockMarketData,
    compute_focus_score,
    fetch_all_us_stock_data,
)
try:
    from analyst_target_scraper import (
        fetch_targets_for_watchlist,
        load_prev_snapshot,
        save_snapshot,
        diff_targets,
        load_analyst_snapshot,
        save_analyst_snapshot,
        diff_analyst_counts,
    )
    EXA_AVAILABLE = True
except ImportError:
    EXA_AVAILABLE = False

logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone("Asia/Taipei")

FOCUS_THRESHOLD = 60
MAX_FOCUS_STOCKS = 10


def _fmt_price(val):
    if val <= 0: return "N/A"
    return f"${val:,.2f}"


def _build_stock_block(ticker, data, h, score_info):
    lines = []
    price = data.current_price
    target = data.mean_target
    rec = data.rec_label
    notes = str(h.get("notes", "")).strip()
    analyst_comment = str(h.get("analyst_comment", "")).strip()
    company = data.company_name or ticker

    lines.append(f"\n📊 {ticker} {company}")
    lines.append(f"📈 現價：{_fmt_price(price)}  ({data.day_change_pct:+.2f}%)")

    ma20 = data.close_20d
    ma5 = data.close_5d
    buy_zone = None
    sell_zone = None
    if ma20 > 0 and price > ma20 * 1.02:
        buy_zone = f"{_fmt_price(ma20 * 0.98)}～{_fmt_price(price * 0.97)}"
    elif ma5 > 0:
        buy_zone = f"{_fmt_price(ma5 * 0.97)}～{_fmt_price(price * 0.98)}"
    else:
        buy_zone = f"現價附近或回測 MA20 ({_fmt_price(ma20)})"
    if target > 0:
        sell_zone = f"{_fmt_price(target * 0.95)}～{_fmt_price(target * 1.05)}"
    elif data.high_20d > 0:
        sell_zone = f"{_fmt_price(data.high_20d)}～{_fmt_price(data.high_20d * 1.05)}"
    if buy_zone: lines.append(f"⬆️ 買進區間：{buy_zone}")
    if sell_zone: lines.append(f"⬇️ 賣出區間：{sell_zone}")

    ups = 0.0
    if target > 0 and price > 0:
        ups = (target - price) / price * 100
    if rec: lines.append(f"📝 分析師建議：{rec} ({data.analysts}家追蹤)")
    if target > 0: lines.append(f"🎯 目標價：{_fmt_price(target)} (距現價 {ups:+.1f}%)")
    if notes: lines.append(f"📌 備註：{notes[:40]}")
    if analyst_comment: lines.append(f"📌 分析師評論：{analyst_comment[:40]}")

    cat = score_info["category"]
    score = score_info["focus_score"]
    lines.append(f"🔥 FocusScore：{score:.0f} 分 | {cat}")

    ops = []
    if ups > 15: ops.append(f"距目標價 {ups:.0f}% 空間，可考慮分批布局")
    elif ups > 5: ops.append(f"距目標價 {ups:.0f}%，觀察回調買點")
    elif ups < -5: ops.append("股價已超過目標價，注意獲利了結時機")
    if data.day_change_pct > 5: ops.append("漲幅過大，建議等待回調再進場")
    elif data.day_change_pct < -5: ops.append("跌幅較大，確認支撐站穩後再考慮接單")
    if cat == "偏多焦點" and score >= 70: ops.append("整體評價偏多，可逢低留意")
    elif cat == "風險焦點": ops.append("風險扣分較高，建議保守操作")
    if not ops: ops.append("觀察量價配合，等待明確訊號")
    lines.append(f"📝 操作建議：{'; '.join(ops[:2])}")
    return chr(10).join(lines)


def _classify_status(price: float, low: float, high: float) -> str:
    """Classify a stock's status relative to a support/resistance band.

    low/high form a *band*; if price is below the band it is 'below support',
    inside it is 'in band', above is 'above band'.
    For support bands (low < high) we additionally mark 'near support'
    when price is within 1% of the band's top edge.
    """
    if not low or not high or not price:
        return "觀望"
    lo = min(low, high)
    hi = max(low, high)
    if price < lo * 0.99:
        return "轉弱"
    if price <= hi:
        if price >= hi * 0.99:
            return "接近壓力"
        return "支撐區內"
    if price <= hi * 1.03:
        return "壓力區"
    return "過熱"


def _status_for_stock(data) -> str:
    """Use 20-day range for support/resistance classification."""
    price = data.current_price
    # support band = last 20 days low, resistance band = last 20 days high
    sup_lo = data.low_20d
    sup_hi = data.close_20d * 0.97 if data.close_20d else 0
    res_lo = data.high_20d * 0.98
    res_hi = data.high_20d
    if price <= 0:
        return "觀望"
    if not sup_lo or not res_lo:
        return "觀望"
    if price < sup_lo:
        return "轉弱"
    if price > res_hi:
        return "過熱"
    if price >= res_lo:
        return "壓力區"
    if price <= sup_hi:
        return "回踩"
    return "觀望"


def _scenario_for_stock(data, target: float = 0) -> str:
    """Return a specific scenario-based action note for one US stock."""
    price = data.current_price
    if price <= 0:
        return "觀望"
    sup_lo = data.low_20d
    sup_hi = round(data.close_20d * 0.97, 2) if data.close_20d else 0
    res_lo = round(data.high_20d * 0.98, 2)
    res_hi = data.high_20d

    if target > 0 and price > target:
        gap = (price / target - 1) * 100
        return f"已高於目標價 {gap:.0f}%，注意回調"
    if price > res_hi:
        return "已過壓力區，不追高，等回踩"
    if price >= res_lo:
        return f"壓力區（{res_lo:,.2f}～{res_hi:,.2f}），突破前不追高"
    if price <= sup_hi:
        return f"回踩支撐（{sup_lo:,.2f}～{sup_hi:,.2f}），量縮守穩可觀察"
    return "現價在支撐與壓力之間，觀望"


def build_daily_report(holdings_data, exa_changes: dict = None, analyst_added: dict = None):
    now_tw = datetime.now(TW_TZ)
    date_str = now_tw.strftime("%Y-%m-%d (%a)")
    wk_note = weekend_note().strip()

    auto_focus_tickers: List[str] = []
    scored_tickers: List[str] = []
    for h in holdings_data:
        ticker = str(h.get("ticker", h.get("代碼", ""))).strip().upper()
        if not ticker:
            continue
        tracking = str(h.get("tracking", h.get("追蹤", ""))).strip()
        if tracking == "焦點股":
            auto_focus_tickers.append(ticker)
        else:
            scored_tickers.append(ticker)

    all_tickers = auto_focus_tickers + scored_tickers
    stocks_data = fetch_all_us_stock_data(all_tickers)
    if not stocks_data:
        return (
            f"# 美股 AI 焦點股廣播 | {date_str}\n{wk_note}\n"
            "⚠️ 無法取得股票資料，請稍後再試。",
            [], {}, {},
        )

    all_scores: Dict[str, Dict[str, Any]] = {}
    stock_info: Dict[str, Dict[str, Any]] = {}
    for h in holdings_data:
        ticker = str(h.get("ticker", h.get("代碼", ""))).strip().upper()
        if not ticker:
            continue
        h["短名"] = h.get("company_name", h.get("名稱", ""))
        h["tracking"] = str(h.get("tracking", h.get("追蹤", ""))).strip()
        data = stocks_data.get(ticker)
        if data is None or data.current_price <= 0:
            continue
        score_info = compute_focus_score(data, h)
        all_scores[ticker] = score_info
        stock_info[ticker] = {"data": data, "h": h, "score": score_info}

    qualified = [(t, s) for t, s in all_scores.items()
                 if s["focus_score"] >= FOCUS_THRESHOLD]
    qualified.sort(key=lambda x: x[1]["focus_score"], reverse=True)

    auto_added = []
    for t in auto_focus_tickers:
        if t in all_scores and t not in [x[0] for x in qualified]:
            auto_added.append((t, all_scores[t]))
    if auto_added:
        qualified = auto_added + qualified

    # Exa-scraped target changes
    exa_changes = exa_changes or {}
    analyst_added = analyst_added or {}
    exa_added = []
    for ticker, chg in exa_changes.items():
        if ticker not in [x[0] for x in qualified]:
            s_info = all_scores.get(ticker)
            if s_info:
                exa_added.append((ticker, s_info))

    analyst_added_tickers = [
        (t, all_scores[t]) for t in analyst_added
        if t in all_scores and t not in [x[0] for x in qualified]
    ]
    all_promoted = exa_added + analyst_added_tickers

    # ------------------------------------------------------------------
    # Build the new-format report
    # ------------------------------------------------------------------
    lines: List[str] = []
    lines.append(f"# 美股 AI 焦點股廣播 | {date_str}")
    if wk_note:
        lines.append(wk_note)
    lines.append("")

    # --- Section 一：市場風向儀表板 ---
    ctx = fetch_market_context()
    lines.extend(build_market_context_lines(ctx))
    lines.append(f"氛圍結論：{get_mood_label(ctx)}")
    lines.append("")

    # --- Section 二：選股狀態（純文字圖標版） ---
    lines.append("## 二、選股狀態")
    lines.append("狀態燈：突破｜回踩｜壓力區｜轉弱｜過熱｜觀望")
    lines.append("")

    display_tickers = [t for t, _ in qualified[:MAX_FOCUS_STOCKS]]
    if not display_tickers:
        lines.append("今日無符合條件的焦點股。")
    elif all_promoted and not [t for t, _ in qualified if t in all_scores]:
        # qualified only has promoted tickers — still show them
        pass

    # Ensure all display + promoted tickers have stock_info entry
    for ticker in display_tickers:
        if ticker not in stock_info and ticker in all_scores:
            # find the holdings entry for this ticker
            for h_candidate in holdings_data:
                tk = str(h_candidate.get("ticker", h_candidate.get("代碼", ""))).strip().upper()
                if tk == ticker:
                    stock_info[ticker] = {
                        "data": stocks_data.get(ticker),
                        "h": h_candidate,
                        "score": all_scores[ticker],
                    }
                    break

    for ticker in display_tickers:
        if ticker not in stock_info:
            continue
        d = stock_info[ticker]["data"]
        h = stock_info[ticker]["h"]
        score = stock_info[ticker]["score"]
        short_name = str(h.get("短名", "")).strip() or ticker
        price = d.current_price
        chg   = d.day_change_pct
        status = _status_for_stock(d)
        scenario = _scenario_for_stock(d)

        sup_lo = d.low_20d
        sup_hi = round(d.close_20d * 0.97, 2) if d.close_20d else 0
        res_lo = round(d.high_20d * 0.98, 2)
        res_hi = d.high_20d
        if sup_lo and sup_hi and sup_lo > sup_hi:
            sup_lo, sup_hi = sup_hi, sup_lo
        if res_lo and res_hi and res_lo > res_hi:
            res_lo, res_hi = res_hi, res_lo
        sup_band = f"{sup_lo:,.2f}～{sup_hi:,.2f}" if sup_lo and sup_hi else "N/A"
        res_band = f"{res_lo:,.2f}～{res_hi:,.2f}" if res_lo and res_hi else "N/A"

        n_analysts = d.analysts
        analyst_str = str(n_analysts) if n_analysts > 0 else "待確認"

        target = d.mean_target
        target_line = ""
        target_gap_note = ""
        if target > 0 and price > 0:
            gap_pct = (target - price) / price * 100
            long_note = "（長期參考）" if gap_pct > 50 else ""
            target_line = f"🎯 目標價：${target:,.2f}（{gap_pct:+.1f}%）{long_note}"
            if price > target:
                over_pct = (price / target - 1) * 100
                target_gap_note = f"｜⚠️ 已高於目標價 {over_pct:.0f}%"

        rec_label = d.rec_label or ""
        analyst_line = f"📝 分析師：{rec_label}（{analyst_str}家）" if rec_label else f"📝 分析師：{analyst_str}家"

        sentiment = score.get("category", "中性")
        if target > 0 and price > 0 and price > target * 1.1:
            sentiment = "偏空（現價已領先目標價）"

        bull_parts = []
        bear_parts = []
        vol_ratio = d.volume / d.avg_volume_20d if d.avg_volume_20d > 0 else 0
        if target > price and gap_pct > 5:
            bull_parts.append(f"目標價上行空間 {gap_pct:.0f}%")
        if vol_ratio > 1.5 and d.day_change_pct < -3:
            bear_parts.append(f"爆量下跌：量放大 {vol_ratio:.1f} 倍但跌 {d.day_change_pct:.1f}%，注意籌碼出脫")
        elif vol_ratio < 0.7:
            bear_parts.append(f"量縮至 {vol_ratio:.1f} 倍，動能不足")
        notes = str(h.get("notes", "")).strip()
        if notes:
            bull_parts.append(notes[:30])

        lines.append(f"\n📊 {ticker} {short_name}")
        lines.append(f"📈 當前價：${price:,.2f}（{chg:+.1f}%）")
        lines.append(f"⬆️ 支撐區：${sup_band}")
        lines.append(f"⬇️ 壓力區：${res_band}")
        if target_line:
            lines.append(target_line + target_gap_note)
        lines.append(analyst_line)
        lines.append(f"💬 市場情緒：{sentiment}")
        if bull_parts:
            lines.append(f"🟢 利多：{'; '.join(bull_parts[:3])}")
        if bear_parts:
            lines.append(f"🔴 利空：{'; '.join(bear_parts[:3])}")
        if d.earnings_date:
            lines.append(f"📅 財報：{d.earnings_date}")
        lines.append(f"⚡ 情境：{scenario}")

    lines.append("")
    lines.append("分析師覆蓋僅供參考，不顯示 0→N 變化。")
    lines.append("")

    # --- Section 三：今日重點變化 ---
    lines.append("## 三、今日重點變化")
    notable = []
    for ticker in display_tickers:
        d = stock_info[ticker]["data"]
        status = _status_for_stock(d)
        chg = d.day_change_pct
        if status in ("轉弱", "過熱", "壓力區") or abs(chg) > 3:
            note = f"{ticker}：日漲跌 {chg:+.2f}%，狀態「{status}」"
            if status in ("壓力區", "過熱"):
                note += "，不追高；等回踩再觀察"
            elif status == "轉弱":
                note += "，先降低關注度"
            notable.append(note)
    if notable:
        for n in notable:
            lines.append(f"- {n}")
    else:
        lines.append("- 今日無重大狀態變化。")
    lines.append("")

    # Exa-scraped target changes (still displayed, but formatted cleanly)
    if exa_changes:
        lines.append("## 📢 法人目標價即時異動（新聞爬蟲）")
        for ticker, chg_info in exa_changes.items():
            name = stock_info.get(ticker, {}).get("h", {}).get("短名", ticker)
            c = chg_info.get("curr", {})
            p = chg_info.get("prev")
            new_tgt = c.get("target", 0)
            new_broker = c.get("broker", "")
            new_date = c.get("date", "")
            if p:
                old_tgt = p.get("target", 0)
                lines.append(
                    f"  {name} ({ticker}) 目標價 ${old_tgt:.2f} → ${new_tgt:.2f}"
                    f"{' (' + new_broker + ')' if new_broker else ''}"
                    f"{'（' + chg_info.get('reason', '') + '）' if chg_info.get('reason') else ''}"
                )
            else:
                lines.append(
                    f"  {name} ({ticker}) 新目標價 ${new_tgt:.2f}"
                    f"{' (' + new_broker + ')' if new_broker else ''}"
                    f"（{new_date}）"
                )
        lines.append("")

    if analyst_added:
        lines.append("## 📈 分析師追蹤人數增加")
        for ticker, info in sorted(analyst_added.items(),
                                    key=lambda kv: kv[1]["delta"], reverse=True):
            name = stock_info.get(ticker, {}).get("h", {}).get("短名", ticker)
            lines.append(
                f"  {name} ({ticker}) 分析師覆蓋 {info['prev']} → {info['curr']} 家（+{info['delta']}）"
            )
        lines.append("")

    # --- Section 四：情境腳本 ---
    lines.extend(scenario_lines(ctx))

    # --- Section 五：備註 ---
    lines.extend(disclaimer_lines())

    return chr(10).join(lines), [t for t, _ in qualified], stocks_data, stock_info

def update_sheet_focus_scores(manager, stocks_data, stock_info, qualified_tickers):
    try:
        rows = manager.worksheet.get_all_values() if manager.worksheet else manager.client.open(manager.sheet_name).worksheet("Holdings").get_all_values()
        headers = [str(h).strip() for h in rows[0]]
        ticker_col = tracking_col = focus_score_col = None
        for i, h in enumerate(headers):
            if h in ("ticker", "代碼"): ticker_col = i
            elif h in ("tracking", "追蹤", "焦點股"): tracking_col = i
            elif "focus" in h.lower() or "score" in h.lower(): focus_score_col = i
        if ticker_col is None: return
        updates = []
        for row_idx, row in enumerate(rows[1:], start=2):
            if ticker_col >= len(row): continue
            ticker = str(row[ticker_col]).strip().upper()
            if not ticker: continue
            si = stock_info.get(ticker, {}).get("score")
            if not si: continue
            sv = si.get("focus_score", 0)
            is_q = ticker in qualified_tickers
            tracking = "焦點股" if (is_q and sv >= FOCUS_THRESHOLD) or sv >= 70 else ""
            update = {}
            if tracking_col is not None: update[tracking_col] = tracking
            if focus_score_col is not None: update[focus_score_col] = round(sv, 1)
            if update: updates.append((row_idx, update, ticker))
        if not updates: return
        worksheet = manager.client.open(manager.sheet_name).worksheet("Holdings")
        col_updates = {}
        for ri, u, ticker in updates:
            for ci, val in u.items(): col_updates.setdefault(ci, []).append((ri, val))
        for ci, pairs in col_updates.items():
            cl = chr(65 + ci)
            rng = f"{cl}2:{cl}{len(rows)}"
            vals = [[v] for _, v in pairs]
            try:
                worksheet.update(rng, vals)
                logger.info("Updated sheet column %s with %d values", cl, len(vals))
            except Exception as e:
                logger.warning("Failed to update column %s: %s", cl, e)
    except Exception as exc:
        logger.warning("Failed to update focus scores to sheet: %s", exc)


def main():
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger.info("US Stock FocusScore Report starting...")
    manager = GoogleSheetsManager()
    data = manager.load_config()
    holdings = data["holdings"]
    if not holdings:
        logger.warning("No holdings data, aborting.")
        sys.exit(0)
    logger.info("Processing %d US stock(s)...", len(holdings))

    # --- Exa-based analyst target scraping ---
    exa_changes = {}
    analyst_added = {}
    if EXA_AVAILABLE:
        logger.info("Fetching latest US analyst targets via exa...")
        exa_results = fetch_targets_for_watchlist(holdings, kind="us", limit=20)
        if exa_results:
            prev_snapshot = load_prev_snapshot()
            exa_changes = diff_targets(prev_snapshot, exa_results)
            logger.info("Exa US diff: %d tickers", len(exa_changes))
            save_snapshot(exa_results)

    # Generate report first (which fetches market data),
    # then use its stocks_data to track analyst coverage counts.
    report_text, qualified_tickers, stocks_data, stock_info = build_daily_report(
        holdings, exa_changes=exa_changes, analyst_added=None)

    # --- Analyst coverage count tracking ---
    if EXA_AVAILABLE:
        curr_counts = {
            t: sd.analysts for t, sd in stocks_data.items()
            if sd.analysts > 0
        }
        prev_counts = load_analyst_snapshot()
        analyst_added = diff_analyst_counts(prev_counts, curr_counts)
        if analyst_added:
            logger.info("Analyst count increased for %d US tickers: %s",
                        len(analyst_added),
                        {t: v["delta"] for t, v in analyst_added.items()})
        save_analyst_snapshot(curr_counts)
        # Rebuild report with analyst_added so those tickers get promoted
        if analyst_added:
            report_text, qualified_tickers, stocks_data, stock_info = \
                build_daily_report(
                    holdings, exa_changes=exa_changes,
                    analyst_added=analyst_added)
    print(report_text)
    update_sheet_focus_scores(manager, stocks_data, stock_info, qualified_tickers)

    notifier = LineNotifier()
    _send_report_chunks(notifier, report_text)
    logger.info("US daily report pushed successfully.")


def _send_report_chunks(notifier, message, max_length=4800):
    if len(message) <= max_length:
        notifier.send_push_message(message)
        return
    lines = message.split(chr(10))
    chunk, total = [], 0
    for line in lines:
        ll = len(line) + 1
        if total + ll > max_length and chunk:
            notifier.send_push_message(chr(10).join(chunk))
            chunk, total = [], 0
        chunk.append(line)
        total += ll
    if chunk: notifier.send_push_message(chr(10).join(chunk))


if __name__ == "__main__":
    main()