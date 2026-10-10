# -*- coding: utf-8 -*-
"""Taiwan stock daily focus report builder.

FocusScore pipeline:
  1. Fetch market data for watchlist
  2. Compute FocusScore = 0.20*Heat + 0.30*Inst + 0.30*Cat
                         + 0.10*Trend - Risk
  3. Filter: must be in watchlist AND score >= 60
  4. Auto-focus from sheet (tracking=焦點股) bypasses threshold
  5. Special focus: score >= 70 but not auto-focus
  6. QFII target change alert at top
  7. Build compact report with operation suggestions
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytz

from tw_market_context import (
    fetch_tw_market_context,
    build_tw_context_lines,
    get_mood_label,
    tw_scenario_lines,
    tw_disclaimer_lines,
    tw_weekend_note,
)
from config import GoogleSheetsManager
from line_notifier import LineNotifier
from taiwan_market_data import (
    StockMarketData,
    compute_focus_score,
    fetch_all_stock_data,
    is_taiwan_trading_day,
    load_cnyes_ratings,
    merge_cnyes_into_data,
)
try:
    from analyst_target_scraper import (
        fetch_targets_for_watchlist,
        load_prev_snapshot,
        save_snapshot,
        diff_targets,
        merge_with_cnyes as merge_exa_cnyes,
        load_analyst_snapshot,
        save_analyst_snapshot,
        diff_analyst_counts,
    )
    EXA_AVAILABLE = True
except ImportError:
    EXA_AVAILABLE = False

logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone("Asia/Taipei")

def _extract_ticker_code(raw: str) -> tuple:
    raw = str(raw).strip()
    m = re.match(r"(\d+)(.*)", raw)
    if m:
        return m.group(1), m.group(2).strip()
    return raw, ""

FOCUS_THRESHOLD = 60
MAX_FOCUS_STOCKS = 10
MIN_FOCUS_STOCKS = 3


def _fmt_price(val: float) -> str:
    if val <= 0:
        return "N/A"
    return f"{val:,.0f}"


def _fmt_pct(val: float) -> str:
    if val == 0:
        return "0.00%"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.2f}%"


def _safe_truncate(text: str, max_len: int) -> str:
    """Truncate text at a sentence boundary when possible, avoiding mid-char cuts."""
    if len(text) <= max_len:
        return text
    # Search backward from max_len for a safe break point
    for delim in ("。", "；", "，", "、", ".", ";", ",", " ", "\n", "）"):
        pos = text.rfind(delim, 0, max_len)
        if pos > max_len * 0.5:  # require at least halfway for meaningful truncation
            return text[:pos + 1].strip()
    # Fallback: hard truncate but strip trailing whitespace
    return text[:max_len].rstrip()


def _effective_target(data: StockMarketData) -> float:
    """Return the authoritative target price: QFII first, then Yahoo mean."""
    if data.qfii_target > 0:
        return data.qfii_target
    return data.mean_target


def _build_focus_detail(ticker: str, data: StockMarketData,
                        h: Dict[str, Any],
                        score_info: Dict[str, Any],
                        qfii: Dict[str, Any] = None) -> str:
    lines = []
    price = data.current_price
    target = data.mean_target
    rec_label = data.rec_label
    notes_raw = str(h.get("備註", "")).strip()
    catalyst_raw = str(h.get("催化劑日期", "")).strip()
    category = score_info["category"]
    qfii = qfii or {}
    short_name = str(h.get("短名", "")).strip() or data.short_name or ticker
    tracking = str(h.get("追蹤", "")).strip()

    lines.append(f"\n📊 {ticker} {short_name}")
    lines.append(f"📈 當前價：{_fmt_price(price)} 元")

    buy_range, _, sell_range, _ = _compute_trade_range(data)
    if buy_range:
        lines.append(f"⬆️ 買進區間：{buy_range}")
    if sell_range:
        lines.append(f"⬇️ 賣出區間：{sell_range}")

    event_text = catalyst_raw[:50] if catalyst_raw else ""
    if notes_raw and not event_text:
        event_text = notes_raw[:50]
    if event_text:
        lines.append(f"📅 事件：{event_text}")

    analyst_info = []
    if rec_label:
        analyst_info.append(f"{rec_label}({data.analysts}家)")
    qfii_target = qfii.get("qfii_target", 0)
    qfii_upside = qfii.get("qfii_upside", 0)
    qfii_broker = qfii.get("qfii_broker", "")
    if qfii_target > 0:
        ups = f"{qfii_upside:+.1f}%" if qfii_upside != 0 else "持平"
        broker = f" {qfii_broker}" if qfii_broker else ""
        analyst_info.append(f"目標價{_fmt_price(qfii_target)}({ups}{broker})")
    if analyst_info:
        lines.append(f"📝 分析師建議：{' | '.join(analyst_info)}")

    # 若 QFII 目標價顯著低於現價（跌幅 >10%），強制標示偏空
    qfii_target_check = qfii.get("qfii_target", 0)
    sentiment_override = None
    if qfii_target_check > 0 and price > 0:
        qfii_upside_pct = (qfii_target_check - price) / price * 100
        if qfii_upside_pct < -10:
            sentiment_override = "偏空"
    sentiment_map = {"偏多": "偏多", "偏空": "偏空", "中性": "中性"}
    displayed_sentiment = sentiment_override or sentiment_map.get(category, "偏多")
    lines.append(f"💬 市場情緒：{displayed_sentiment}")

    bull_factors = _extract_bull_factors(data, score_info, h)
    if bull_factors:
        lines.append(f"🟢 利多：{'; '.join(bull_factors[:3])}")

    bear_factors = _extract_bear_factors(data, score_info)
    if bear_factors:
        lines.append(f"🔴 利空：{'; '.join(bear_factors[:3])}")

    events = _extract_recent_events(data, h)
    if events:
        lines.append(f"📆 營運焦點：{'；'.join(events[:2])}")

    op_suggestion = _get_operation_suggestion(data, score_info, h)
    if op_suggestion:
        lines.append(f"📝 操作建議：{op_suggestion}")

    if qfii_target > 0 and price > 0:
        ups_pct = (qfii_target - price) / price * 100
        direction = f"上行 {ups_pct:+.1f}%" if ups_pct > 0 else f"下行 {abs(ups_pct):.1f}%"
        lines.append(f"📝 備註：外資目標價 {_fmt_price(qfii_target)} 元，距現價 {direction}")

    return "\n".join(lines)


def _compute_trade_range(data: StockMarketData) -> tuple:
    price = data.current_price
    prev_close = data.previous_close
    target = data.mean_target
    high_20d = data.high_20d
    ma20 = data.close_20d
    ma5 = data.close_5d

    buy_range = None
    sell_range = None

    if prev_close > 0 and price > 0:
        pct = (price - prev_close) / prev_close * 100
    else:
        pct = 0

    if ma20 > 0 and price > ma20 * 1.02:
        buy_range = f"{_fmt_price(ma20 * 0.98)}～{_fmt_price(price * 0.97)} 元"
    elif ma5 > 0:
        buy_range = f"{_fmt_price(ma5 * 0.97)}～{_fmt_price(price * 0.98)} 元"
    else:
        buy_range = f"現價附近或回測 MA20 ({_fmt_price(ma20)} 元) 時留意"

    if target > 0:
        if price < target:
            # 價格低於目標價（尚有上行空間）：賣出區間以目標價為基準
            sell_range = f"{_fmt_price(target * 0.95)}～{_fmt_price(target * 1.05)} 元"
        else:
            # 價格已超過或接近目標價：賣出區間改以現價或近期高點為基準
            ref = high_20d if high_20d > 0 else price
            sell_range = f"{_fmt_price(ref * 0.97)}～{_fmt_price(ref * 1.03)} 元"
    elif high_20d > 0:
        sell_range = f"{_fmt_price(high_20d)}～{_fmt_price(high_20d * 1.05)} 元（近期高點區間）"
    else:
        sell_range = "現價附近或近期高點為賣出參考"

    return buy_range, None, sell_range, None


def _extract_bull_factors(data: StockMarketData, score_info: Dict[str, Any],
                          h: Dict[str, Any]) -> List[str]:
    factors = []
    price = data.current_price
    target = data.mean_target
    rec_label = data.rec_label

    if rec_label in ("買進", "強力買進", "strong_buy", "buy"):
        factors.append(f"法人看好({rec_label}，{data.analysts}家追蹤)")

    if target > 0 and price > 0:
        ups = (target - price) / price * 100
        if ups > 5:
            factors.append(f"目標價{_fmt_price(target)}，距現價 {_fmt_pct(ups)} 上行空間")

    if price > data.close_5d > data.close_20d:
        factors.append("均線多頭排列 MA5 > MA20，趨勢向上")
    elif price > data.close_5d:
        factors.append("股價站上新鮮 MA5，短線偏強")

    # Bug 5 fix: heavy volume with price drop = distribution warning (not bullish)
    vol_ratio = data.volume / data.avg_volume_20d if data.avg_volume_20d > 0 else 0
    if vol_ratio > 1.5 and data.day_change_pct < -3:
        factors.append(f"爆量下跌：成交量放大至 {vol_ratio:.1f} 倍但單日下跌 {data.day_change_pct:.1f}%，注意籌碼出脫")
    elif vol_ratio > 1.5:
        factors.append(f"成交量放大至 20 日均量 {vol_ratio:.1f} 倍，籌碼活絡")

    notes = str(h.get("備註", "")).strip()
    if notes and len(notes) > 3:
        factors.append(_safe_truncate(notes, 30))

    if data.earnings_history:
        latest = data.earnings_history[0]
        if latest.get("actual_eps", 0) > latest.get("est_eps", 0) * 1.05:
            factors.append(f"上季 EPS {_fmt_price(latest['actual_eps'])} 優於預期 {_fmt_price(latest['est_eps'])}")

    return factors


def _extract_bear_factors(data: StockMarketData, score_info: Dict[str, Any]) -> List[str]:
    factors = []
    price = data.current_price
    # Bug 1 & 2 fix: use QFII target as authoritative when available
    target = _effective_target(data)
    change = data.day_change_pct
    rp = score_info["risk_penalty"]

    if target > 0 and price > target * 1.1:
        over = (price / target - 1) * 100
        factors.append(f"股價已超過目標價 {over:.0f}%，注意回調風險")

    if change > 5:
        factors.append(f"單日大漲 {change:.1f}%，追價需谨慎")

    if data.high_20d > 0:
        dist_to_high = (data.high_20d - price) / data.high_20d * 100
        if 0 < dist_to_high <= 2:
            # 股價接近但尚未突破 20 日高點：列入利空
            factors.append(f"接近 20 日高點 {_fmt_price(data.high_20d)}，突破後方可續持")
        # price >= high_20d: 已突破高點，屬強勢利多訊號，不列入利空

    if data.avg_volume_20d > 0 and data.volume < data.avg_volume_20d * 0.7:
        factors.append(f"成交量萎縮至 20 日均量 {data.volume/data.avg_volume_20d:.1f} 倍，缺乏動能")

    if data.rec_label in ("賣出", "持有", "sell", "underperform"):
        factors.append(f"法人評等：{data.rec_label}")

    if rp >= 5:
        factors.append(f"風險扣分 {rp:.0f} 分，注意相關風險")

    if change < -5:
        factors.append(f"單日大跌 {change:.1f}%，確認支撐再進場")

    # Bug 5 fix: heavy volume with significant drop = distribution warning
    avg_vol = data.avg_volume_20d
    if avg_vol > 0 and data.volume > avg_vol * 1.5 and change < -5:
        vol_ratio = data.volume / avg_vol
        factors.append(f"爆量跌停訊號：成交量放大至 {vol_ratio:.1f} 倍且單日大跌 {change:.1f}%，主力出貨風險高")

    return factors


def _extract_recent_events(data: StockMarketData, h: Dict[str, Any]) -> List[str]:
    events = []
    now = datetime.now(TW_TZ)

    if data.next_earnings_date:
        try:
            earn_dt = datetime.strptime(str(data.next_earnings_date), "%Y-%m-%d")
            earn_dt = earn_dt.replace(tzinfo=TW_TZ)
            days_left = (earn_dt - now).days
            if days_left > 0:
                events.append(f"財報日 {data.next_earnings_date}（{days_left} 天後）")
            elif days_left == 0:
                events.append(f"財報日 {data.next_earnings_date}（今日）")
            # days_left < 0：已過日期不列入營運焦點
        except (ValueError, TypeError):
            pass

    if data.eps_estimate > 0:
        events.append(f"EPS 預估 {_fmt_price(data.eps_estimate)} 元")

    notes = str(h.get("備註", "")).strip()
    if notes:
        events.append(_safe_truncate(notes, 25))

    return events


def _get_operation_suggestion(data: StockMarketData, score_info: Dict[str, Any],
                              h: Dict[str, Any]) -> str:
    price = data.current_price
    target = data.mean_target
    change = data.day_change_pct
    category = score_info["category"]
    focus_score = score_info["focus_score"]

    suggestions = []

    if target > 0 and price > 0:
        ups = (target - price) / price * 100
        if ups > 15:
            suggestions.append(f"距目標價 {ups:.0f}% 空間，可考慮分批布局")
        elif ups > 5:
            suggestions.append(f"距目標價 {ups:.0f}%，觀察回調買點")
        else:
            suggestions.append(f"已接近目標價，注意獲利了結時機")

    if change > 5:
        suggestions.append("漲幅過大，建議等待回調再進場")
    elif change < -5:
        suggestions.append("跌幅較大，確認支撐站穩後再考慮接單")

    if category == "偏多" and focus_score >= 70:
        suggestions.append("整體評價偏多，可逢低留意")
    elif category == "偏空":
        suggestions.append("整體評價偏空，建議保守操作")

    if not suggestions:
        suggestions.append("觀察量價配合，等待明確訊號")

    return "; ".join(suggestions[:2])



def _tw_status_for_stock(data) -> str:
    """Classify a TW stock's status relative to 20-day range."""
    price = data.current_price
    if price <= 0:
        return "觀望"
    sup_lo = data.low_20d
    sup_hi = round(data.close_20d * 0.97, 2) if data.close_20d else 0
    res_lo = round(data.high_20d * 0.98, 2)
    res_hi = data.high_20d
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


def _tw_scenario_for_stock(data, qfii: dict = None) -> str:
    """Generate a specific scenario-based action note for one stock."""
    price = data.current_price
    target = _effective_target(data)
    qfii = qfii or {}
    qfii_target = qfii.get("qfii_target", 0)
    # use qfii_target if present (single source of truth)
    if qfii_target > 0:
        target = qfii_target
    sup_lo = data.low_20d
    sup_hi = round(data.close_20d * 0.97, 2) if data.close_20d else 0
    res_lo = round(data.high_20d * 0.98, 2)
    res_hi = data.high_20d

    if price <= 0:
        return "觀望"

    if target > 0 and price > target:
        gap = (price / target - 1) * 100
        return f"已高於目標價 {gap:.0f}%，注意回調"

    if price > res_hi:
        return "已過壓力區，不追高，等回踩"
    if price >= res_lo:
        return f"壓力區（{res_lo:,.0f}～{res_hi:,.0f}），突破前不追高"
    if price <= sup_hi:
        return f"回踩支撐（{sup_lo:,.0f}～{sup_hi:,.0f}），量縮守穩可觀察"
    return "現價在支撐與壓力之間，觀望"


def build_taiwan_focus_report(stocks_data, watchlist,
                               qfii_data=None, cnyes_ratings=None,
                               exa_changes=None, analyst_added=None) -> str:
    qfii_data = qfii_data or {}
    exa_changes = exa_changes or {}
    analyst_added = analyst_added or {}
    now_tw = datetime.now(TW_TZ)
    date_str = now_tw.strftime("%Y-%m-%d (%a)")
    prev_tw = now_tw - timedelta(days=1)
    prev_date_str = prev_tw.strftime("%Y-%m-%d")
    wk_note = tw_weekend_note().strip()

    all_scores: Dict[str, Dict[str, Any]] = {}
    stock_info: Dict[str, Dict[str, Any]] = {}
    for h in watchlist:
        ticker, sheet_name = _extract_ticker_code(h.get("ticker", h.get("代碼", "")))
        h["短名"] = sheet_name or h.get("短名", "")
        if not ticker:
            continue
        data = stocks_data.get(ticker)
        if data is None or data.current_price <= 0:
            continue
        score_info = compute_focus_score(data, h)
        all_scores[ticker] = score_info
        stock_info[ticker] = {"data": data, "h": h, "score": score_info}

    qualified = [(t, s) for t, s in all_scores.items()
                 if s["focus_score"] >= FOCUS_THRESHOLD]
    qualified.sort(key=lambda x: x[1]["focus_score"], reverse=True)

    auto_focus: List[str] = []
    for h in watchlist:
        ticker = _extract_ticker_code(h.get("ticker", h.get("代碼", "")))[0]
        if not ticker or ticker not in all_scores:
            continue
        tracking = str(h.get("追蹤", "")).strip()
        if tracking == "焦點股" and ticker not in [t for t, _ in qualified]:
            auto_focus.append(ticker)
    if auto_focus:
        existing = [(t, s) for t, s in qualified if t not in auto_focus]
        auto_entries = [(t, all_scores[t]) for t in auto_focus]
        qualified = auto_entries + existing

    # --- Load previous-day QFII snapshot for change detection ---
    import os, json
    prev_targets_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "prev_qfii_targets.json")
    prev_targets: Dict[str, float] = {}
    try:
        with open(prev_targets_path, encoding="utf-8") as pf:
            prev_targets = json.load(pf)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    today_str = datetime.now(TW_TZ).strftime("%Y%m%d")
    target_change_alerts: List[str] = []
    today_target_alerts: List[str] = []
    for ticker, qfii in qfii_data.items():
        target = qfii.get("qfii_target", 0)
        if target <= 0:
            continue
        name = str(stock_info.get(ticker, {}).get("h", {}).get("短名", "")).strip() or ticker
        if ticker in (cnyes_ratings or {}):
            rating = (cnyes_ratings or {}).get(ticker, {})
            if rating.get("date", "") == today_str:
                today_target_alerts.append(
                    f"{name} ({ticker}) 外資今日調整目標價至 {_fmt_price(target)} 元")
        prev_tgt = prev_targets.get(ticker, 0)
        if prev_tgt > 0 and abs(target - prev_tgt) > 0.01:
            direction = "上調" if target > prev_tgt else "下調"
            diff = target - prev_tgt
            target_change_alerts.append(
                f"{name} ({ticker}) 外資{direction}目標價 "
                f"{_fmt_price(prev_tgt)} → {_fmt_price(target)}（{_fmt_price(abs(diff))}）")

    current_targets: Dict[str, float] = {}
    for ticker, qfii in qfii_data.items():
        tgt = qfii.get("qfii_target", 0)
        if tgt > 0:
            current_targets[ticker] = tgt
    try:
        os.makedirs(os.path.dirname(prev_targets_path), exist_ok=True)
        with open(prev_targets_path, "w", encoding="utf-8") as pf:
            json.dump(current_targets, pf, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to save prev targets: %s", e)

    # --- Build report ---
    lines: List[str] = []
    lines.append(f"# 台股AI摘要｜{date_str}")
    lines.append(f"資料基準：{prev_date_str} 收盤")
    if wk_note:
        lines.append(wk_note)
    lines.append("")

    # Section 一：市場風向
    ctx = fetch_tw_market_context()
    lines.extend(build_tw_context_lines(ctx))
    lines.append(f"氛圍結論：{get_mood_label(ctx)}")
    lines.append("")

    # QFII target alerts
    if today_target_alerts:
        lines.append("## 今日外資目標價調整")
        for alert in today_target_alerts:
            lines.append(f"- {alert}")
        lines.append("")
    elif target_change_alerts:
        lines.append("## 外資目標價異動股")
        for alert in target_change_alerts[:5]:
            lines.append(f"- {alert}")
        lines.append("")

    # Exa-scraped target changes
    if exa_changes:
        lines.append("## 📢 法人目標價即時異動（新聞爬蟲，輔助參考）")
        for ticker, chg in exa_changes.items():
            name = str(stock_info.get(ticker, {}).get("h", {}).get("短名", "")).strip() or ticker
            c = chg.get("curr", {})
            p = chg.get("prev")
            new_tgt = c.get("target", 0)
            new_broker = c.get("broker", "")
            new_date = c.get("date", "")
            if p:
                old_tgt = p.get("target", 0)
                lines.append(
                    f"  {name} ({ticker}) 新聞目標價 {old_tgt:.0f} → {new_tgt:.0f}"
                    f"{' (' + new_broker + ')' if new_broker else ''}"
                    f" {'（' + chg.get('reason', '') + '）' if chg.get('reason') else ''}")
            else:
                lines.append(
                    f"  {name} ({ticker}) 新聞新目標價 {new_tgt:.0f}"
                    f"{' (' + new_broker + ')' if new_broker else ''}（{new_date}）")
        lines.append("")

    if analyst_added:
        lines.append("## 📈 分析師追蹤人數增加")
        for ticker, info in sorted(analyst_added.items(),
                                    key=lambda kv: kv[1]["delta"], reverse=True):
            name = str(stock_info.get(ticker, {}).get("h", {}).get("短名", "")).strip() or ticker
            lines.append(
                f"  {name} ({ticker}) 分析師覆蓋 {info['prev']} → {info['curr']} 家（+{info['delta']}）")
        lines.append("")

    # Section 二：選股狀態
    lines.append("## 二、選股狀態總覽")
    lines.append("狀態燈：突破｜回踩｜壓力區｜轉弱｜過熱｜觀望")
    lines.append("")
    lines.append(
        "| 代號 | 收盤 | 日% | 狀態 | 支撐 | 壓力 | "
        "目標價(Factset) | 催化/風險 | 情境 |")
    lines.append("|---|---:|---:|---|---:|---:|---:|---|---|")

    target_change_tickers: set = set()
    for ticker, qfii in qfii_data.items():
        target = qfii.get("qfii_target", 0)
        prev_tgt = prev_targets.get(ticker, 0)
        if prev_tgt > 0 and abs(target - prev_tgt) > 0.01 and ticker in all_scores:
            target_change_tickers.add(ticker)
    for ticker in exa_changes:
        if ticker in all_scores:
            target_change_tickers.add(ticker)
    for ticker in analyst_added:
        if ticker in all_scores:
            target_change_tickers.add(ticker)
    if target_change_tickers:
        existing = [(t, s) for t, s in qualified if t not in target_change_tickers]
        change_entries = [(t, all_scores[t]) for t in target_change_tickers]
        qualified = change_entries + existing

    display_tickers = [t for t, _ in qualified[:MAX_FOCUS_STOCKS]]
    if not display_tickers:
        lines.append("| — | — | — | — | — | — | — | — | — |")

    for ticker in display_tickers:
        d = stock_info[ticker]["data"]
        h = stock_info[ticker]["h"]
        qfii = qfii_data.get(ticker, {})
        price = d.current_price
        chg = d.day_change_pct
        status = _tw_status_for_stock(d)
        scenario = _tw_scenario_for_stock(d, qfii)

        sup_lo = d.low_20d
        sup_hi = round(d.close_20d * 0.97, 2) if d.close_20d else 0
        res_lo = round(d.high_20d * 0.98, 2)
        res_hi = d.high_20d
        if sup_lo and sup_hi and sup_lo > sup_hi:
            sup_lo, sup_hi = sup_hi, sup_lo
        if res_lo and res_hi and res_lo > res_hi:
            res_lo, res_hi = res_hi, res_lo
        sup_band = f"{sup_lo:,.0f}～{sup_hi:,.0f}" if sup_lo and sup_hi else "N/A"
        res_band = f"{res_lo:,.0f}～{res_hi:,.0f}" if res_lo and res_hi else "N/A"

        # Target: single source of truth = QFII/Factset
        qfii_target = qfii.get("qfii_target", 0)
        target_disp = ""
        if qfii_target > 0:
            gap_pct = (qfii_target - price) / price * 100 if price > 0 else 0
            long_note = "（長期參考）" if gap_pct > 50 else ""
            target_disp = f"{qfii_target:,.0f}{long_note}"

        notes = str(h.get("備註", "")).strip()
        catalyst = notes[:25] if notes else "—"
        lines.append(
            f"| {ticker} | {price:,.0f} | {chg:+.1f}% | {status} "
            f"| {sup_band} | {res_band} | {target_disp} | {catalyst} | {scenario} |")

    lines.append("")
    lines.append("目標價以 Factset（QFII）為準；新聞爬蟲目標價僅供輔助參考。")
    lines.append("目標價距現價 > 50% 時標「長期參考」，不作為短線觸發。")
    lines.append("")

    # Section 三：今日重點變化
    lines.append("## 三、今日重點變化")
    notable = []
    for ticker in display_tickers:
        d = stock_info[ticker]["data"]
        qfii = qfii_data.get(ticker, {})
        status = _tw_status_for_stock(d)
        scenario = _tw_scenario_for_stock(d, qfii)
        chg = d.day_change_pct
        if status in ("轉弱", "過熱", "壓力區") or abs(chg) > 3:
            notable.append(f"- {ticker}：日漲跌 {chg:+.1f}%，狀態「{status}」，{scenario}")
    if notable:
        lines.extend(notable[:5])
    else:
        lines.append("- 今日無重大狀態變化。")
    lines.append("")

    # Section 四：情境腳本
    lines.extend(tw_scenario_lines(ctx))

    # Section 五：備註
    lines.extend(tw_disclaimer_lines())

    return "\n".join(lines)



def main():
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("Taiwan Focus Report starting...")

    if not is_taiwan_trading_day():
        now_tw = datetime.now(TW_TZ)
        date_str = now_tw.strftime("%Y-%m-%d (%a)")
        msg = (f"# 台股AI摘要｜{date_str}\n\n"
               f"⚠️ 註：今日處於休市日，今日不派發。\n")
        print(msg)
        logger.info("Taiwan market is closed today, skipping report.")
        sys.exit(0)

    manager = GoogleSheetsManager()
    watchlist = manager.load_taiwan_stocks()
    if not watchlist:
        logger.warning("No Taiwan stock data, aborting.")
        sys.exit(0)

    logger.info("Processing %d Taiwan stock(s)...", len(watchlist))

    cnyes_ratings = load_cnyes_ratings()
    logger.info("Loaded %d QFII ratings from cnyes.", len(cnyes_ratings))

    tickers = [_extract_ticker_code(h.get("ticker", h.get("代碼", "")))[0] for h in watchlist]
    stocks_data = fetch_all_stock_data(tickers)

    qfii_merged = merge_cnyes_into_data(tickers, cnyes_ratings)
    for ticker, qfii_info in qfii_merged.items():
        if ticker in stocks_data:
            sd = stocks_data[ticker]
            sd.qfii_target = qfii_info["qfii_target"]
            sd.qfii_rating = qfii_info["qfii_rating"]
            sd.qfii_upside = qfii_info["qfii_upside"]
            if sd.current_price > 0 and qfii_info["qfii_target"] > 0:
                sd.qfii_upside = round((qfii_info["qfii_target"] - sd.current_price) / sd.current_price * 100, 1)
            sd.qfii_broker = qfii_info["qfii_broker"]
            logger.info("%s: QFII target=%s rating=%s upside=%s%%", ticker, qfii_info["qfii_target"], qfii_info["qfii_rating"], sd.qfii_upside)

    if not stocks_data:
        from taiwan_market_data import _load_cache
        cached = _load_cache("__ALL__")
        if cached:
            logger.info("Using cached data from previous trading day.")
            from taiwan_market_data import StockMarketData
            stocks_data = {
                t: StockMarketData(**d) for t, d in cached.items()
                if isinstance(d, dict)
            }
        else:
            logger.error("No stock data available, aborting.")
            sys.exit(1)

    # --- Exa-based analyst target scraping ---
    exa_changes: Dict[str, Dict[str, Any]] = {}
    if EXA_AVAILABLE:
        logger.info("Fetching latest analyst targets via exa...")
        exa_results = fetch_targets_for_watchlist(watchlist, kind="tw", limit=15)
        if exa_results:
            prev_snapshot = load_prev_snapshot()
            exa_changes = diff_targets(prev_snapshot, exa_results)
            logger.info("Exa diff: %d tickers with target change", len(exa_changes))
            # Save new snapshot for next run
            save_snapshot(exa_results)
        # Merge exa into qfii_merged: cnyes wins, exa fills gaps
        qfii_merged = merge_exa_cnyes(qfii_merged, exa_results)

    # --- Analyst coverage count tracking ---
    analyst_added: Dict[str, Dict[str, Any]] = {}
    if EXA_AVAILABLE:
        curr_counts = {
            t: sd.analysts for t, sd in stocks_data.items()
            if sd.analysts > 0
        }
        prev_counts = load_analyst_snapshot()
        analyst_added = diff_analyst_counts(prev_counts, curr_counts)
        if analyst_added:
            logger.info("Analyst count increased for %d tickers: %s",
                        len(analyst_added),
                        {t: v["delta"] for t, v in analyst_added.items()})
        save_analyst_snapshot(curr_counts)

    report = build_taiwan_focus_report(
        stocks_data, watchlist,
        qfii_data=qfii_merged,
        cnyes_ratings=cnyes_ratings,
        exa_changes=exa_changes,
        analyst_added=analyst_added,
    )
    print(report)

    notifier = LineNotifier()
    _send_report_chunks(notifier, report)
    logger.info("Taiwan focus report pushed successfully.")


def _send_report_chunks(notifier, message: str, max_length: int = 4800) -> None:
    """Send report in chunks.  Long lines are auto-wrapped to prevent
    LINE _truncate_message from cutting a sentence mid-word."""
    # Wrap any line longer than max_length into smaller segments
    raw_lines = message.split("\n")
    wrapped_lines = []
    for line in raw_lines:
        if len(line) > max_length:
            start = 0
            while start < len(line):
                end = min(start + max_length, len(line))
                # Search backward for a safe break point at punctuation
                break_at = end
                for delim in (" ", "，", "。", "；", "、", "|", "）", "」", "\n"):
                    pos = line.rfind(delim, start, end)
                    if pos > start:
                        break_at = pos
                        break
                # Bug 3 fix: if no delimiter found, look for any whitespace/punct
                # to avoid mid-word CJK splits
                if break_at == end:
                    seg = line[start:end]
                    for delim in (" ", "，", "。", "；", "、", "|", "）", "」", "\n", "-"):
                        pos = seg.rfind(delim)
                        if pos > 0:
                            break_at = start + pos
                            break
                wrapped_lines.append(line[start:break_at])
                start = break_at if break_at == end else break_at + 1
        else:
            wrapped_lines.append(line)

    current_chunk = []
    current_length = 0
    for line in wrapped_lines:
        line_len = len(line) + 1
        if current_length + line_len > max_length and current_chunk:
            notifier.send_push_message("\n".join(current_chunk))
            current_chunk = []
            current_length = 0
        current_chunk.append(line)
        current_length += line_len

    if current_chunk:
        notifier.send_push_message("\n".join(current_chunk))


if __name__ == "__main__":
    main()
