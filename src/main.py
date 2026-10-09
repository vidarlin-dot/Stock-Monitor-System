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


def build_daily_report(holdings_data, exa_changes: dict = None, analyst_added: dict = None):
    now_tw = datetime.now(TW_TZ)
    date_str = now_tw.strftime("%Y-%m-%d (%a)")

    auto_focus_tickers = []
    scored_tickers = []
    for h in holdings_data:
        ticker = str(h.get("ticker", h.get("代碼", ""))).strip().upper()
        if not ticker: continue
        tracking = str(h.get("tracking", h.get("追蹤", ""))).strip()
        if tracking == "焦點股": auto_focus_tickers.append(ticker)
        else: scored_tickers.append(ticker)

    all_tickers = auto_focus_tickers + scored_tickers
    stocks_data = fetch_all_us_stock_data(all_tickers)
    if not stocks_data:
        return f"# 美股AI摘要｜{date_str}\n\n⚠️ 無法取得股票資料，請稍後再試。", [], {}, {}

    all_scores = {}
    stock_info = {}
    for h in holdings_data:
        ticker = str(h.get("ticker", h.get("代碼", ""))).strip().upper()
        if not ticker: continue
        h["短名"] = h.get("company_name", h.get("名稱", ""))
        h["tracking"] = str(h.get("tracking", h.get("追蹤", ""))).strip()
        data = stocks_data.get(ticker)
        if data is None or data.current_price <= 0: continue
        score_info = compute_focus_score(data, h)
        all_scores[ticker] = score_info
        stock_info[ticker] = {"data": data, "h": h, "score": score_info}

    qualified = [(t, s) for t, s in all_scores.items() if s["focus_score"] >= FOCUS_THRESHOLD]
    qualified.sort(key=lambda x: x[1]["focus_score"], reverse=True)
    auto_added = []
    for t in auto_focus_tickers:
        if t in all_scores and t not in [x[0] for x in qualified]:
            auto_added.append((t, all_scores[t]))
    if auto_added: qualified = auto_added + qualified

    # Auto-promote tickers whose exa-scraped target price changed
    exa_changes = exa_changes or {}
    analyst_added = analyst_added or {}
    exa_added = []
    for ticker, chg in exa_changes.items():
        if ticker not in [x[0] for x in qualified]:
            s = all_scores.get(ticker)
            if s:
                exa_added.append((ticker, s))
    # Also promote tickers whose analyst coverage count increased
    analyst_added_tickers = [
        (t, all_scores[t]) for t in analyst_added
        if t in all_scores and t not in [x[0] for x in qualified]
    ]
    all_promoted = exa_added + analyst_added_tickers
    lines = [f"# 美股 AI 焦點股票 | {date_str}", ""]
    if all_promoted:
        qualified = all_promoted + qualified
        lines.append("")
        if exa_changes:
            lines.append("📢 法人目標價即時異動（新聞爬蟲）")
        for ticker, chg in exa_changes.items():
            name = stock_info.get(ticker, {}).get("h", {}).get("短名", ticker)
            c = chg.get("curr", {})
            p = chg.get("prev")
            new_tgt = c.get("target", 0)
            new_broker = c.get("broker", "")
            new_date = c.get("date", "")
            if p:
                old_tgt = p.get("target", 0)
                lines.append(
                    f"  {name} ({ticker}) 目標價 ${old_tgt:.2f} → ${new_tgt:.2f}"
                    f"{' (' + new_broker + ')' if new_broker else ''}"
                    f"{'（' + chg.get('reason','') + '）' if chg.get('reason') else ''}"
                )
            else:
                lines.append(
                    f"  {name} ({ticker}) 新目標價 ${new_tgt:.2f}"
                    f"{' (' + new_broker + ')' if new_broker else ''}"
                    f"（{new_date}）"
                )
        if analyst_added:
            lines.append("")
            lines.append("📈 法人追蹤人數增加")
            for ticker, info in sorted(
                    analyst_added.items(),
                    key=lambda kv: kv[1]["delta"], reverse=True):
                name = stock_info.get(ticker, {}).get("h", {}).get("短名", ticker)
                n_prev = info["prev"]
                n_curr = info["curr"]
                delta = info["delta"]
                lines.append(
                    f"  {name} ({ticker}) 追蹤法人 {n_prev} → {n_curr} 家（+{delta}）")

    if qualified:
        for ticker, s in qualified[:MAX_FOCUS_STOCKS]:
            d = stock_info[ticker]["data"]
            h = stock_info[ticker]["h"]
            lines.append(_build_stock_block(ticker, d, h, s))
    else:
        lines.append("今日無符合條件的焦點股，請留意後續市場變化。")

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