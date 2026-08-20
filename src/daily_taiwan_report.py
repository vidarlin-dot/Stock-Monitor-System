# -*- coding: utf-8 -*-
"""Taiwan stock daily focus report — 四段式結構化日報.

FocusScore pipeline:
  1. Fetch market data for watchlist
  2. Compute FocusScore = 0.30*Heat + 0.25*Inst + 0.25*Cat
                         + 0.10*Trend + 0.10*WatchFit - Risk
  3. Filter: must be in watchlist AND score >= 65
  4. Select top 5 (max), mark category
  5. Build structured report with 4 sections
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytz

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

logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone("Asia/Taipei")

def _extract_ticker_code(raw: str) -> tuple:
    raw = str(raw).strip()
    m = re.match(r"(\d+)(.*)", raw)
    if m:
        return m.group(1), m.group(2).strip()
    return raw, 

FOCUS_THRESHOLD = 60
MAX_FOCUS_STOCKS = 10
MIN_FOCUS_STOCKS = 3


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def _fmt_price(val: float) -> str:
    if val <= 0:
        return "N/A"
    return f"{val:,.0f}"


def _fmt_pct(val: float) -> str:
    if val == 0:
        return "0.00%"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.2f}%"


def _build_focus_detail(ticker: str, data: StockMarketData,
                        h: Dict[str, Any],
                        score_info: Dict[str, Any],
                        qfii: Dict[str, Any] = None) -> str:
    """Build a compact card for one focus stock."""
    lines = []
    price = data.current_price
    target = data.mean_target
    rec_label = data.rec_label
    notes_raw = str(h.get("備註", "")).strip()
    catalyst_raw = str(h.get("催化劑日期", "")).strip()
    category = score_info["category"]
    qfii = qfii or {}
    short_name = str(h.get("短名", "")).strip() or data.short_name or ticker

    lines.append(f"\n📊 {ticker} {short_name}")
    lines.append(f"📈 當前價：{_fmt_price(price)} 元")

    # Trade ranges (compact)
    buy_range, _, sell_range, _ = _compute_trade_range(data)
    if buy_range:
        lines.append(f"⬆️ 買進區間：{buy_range}")
    if sell_range:
        lines.append(f"⬇️ 賣出區間：{sell_range}")

    # Event (compact)
    event_text = catalyst_raw[:50] if catalyst_raw else ""
    if notes_raw and not event_text:
        event_text = notes_raw[:50]
    if event_text:
        lines.append(f"📅 事件：{event_text}")

    # Analyst recommendation + QFII target (merged)
    analyst_info = []
    if rec_label:
        analyst_info.append(f"{rec_label}({data.analysts}位)")
    qfii_target = qfii.get("qfii_target", 0)
    qfii_upside = qfii.get("qfii_upside", 0)
    qfii_broker = qfii.get("qfii_broker", "")
    if qfii_target > 0:
        ups = f"{qfii_upside:+.1f}%" if qfii_upside != 0 else "持平"
        broker = f" {qfii_broker}" if qfii_broker else ""
        analyst_info.append(f"目標{_fmt_price(qfii_target)}({ups}{broker})")
    if analyst_info:
        lines.append(f"📝 建議：{' | '.join(analyst_info)}")

    # Sentiment
    sentiment_map = {"偏多焦點": "偏多", "風險焦點": "偏空", "中性觀察": "中性"}
    lines.append(f"💬 情緒：{sentiment_map.get(category, '偏多')}")

    # Key insight (compact)
    insights = []
    if target > 0 and price > 0:
        ups = (target - price) / price * 100
        insights.append(f"目標距現{_fmt_pct(ups)}")
    if notes_raw:
        insights.append(notes_raw[:40])
    if insights:
        lines.append(f"📝 備註：{'；'.join(insights[:2])}")

    return "\n".join(lines)

def _compute_trade_range(data: StockMarketData) -> tuple:
    price = data.current_price
    prev_close = data.previous_close
    target = data.mean_target
    low_20d = data.low_20d
    high_20d = data.high_20d
    ma20 = data.close_20d
    ma5 = data.close_5d

    buy_range = None
    hold_range = None
    sell_range = None
    stop_loss = None

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

    if target > 0 and price < target * 0.95:
        hold_range = f"{_fmt_price(price)}～{_fmt_price(target * 0.95)} 元"
    elif target > 0:
        hold_range = f"現價 {_fmt_price(price)} 元 附近持有，觀察能否突破 {_fmt_price(target * 0.95)} 元"
    else:
        hold_range = f"現價 {_fmt_price(price)} 元 附近持有"

    if target > 0:
        sell_range = f"{_fmt_price(target * 0.95)}～{_fmt_price(target * 1.05)} 元"
    elif high_20d > 0:
        sell_range = f"{_fmt_price(high_20d)}～{_fmt_price(high_20d * 1.05)} 元（近期高點區間）"
    else:
        sell_range = "目標價或近期高點區間"

    if ma20 > 0 and ma20 < price * 0.95:
        stop_loss = f"{_fmt_price(ma20 * 0.97)} 元（跌破 MA20 支撐）"
    else:
        sl_pct = 5.0 if pct > 2 else 7.0
        stop_loss = f"{_fmt_price(price * (1 - sl_pct/100))} 元（約跌 {sl_pct}%）"

    return buy_range, hold_range, sell_range, stop_loss


def _extract_bull_factors(data: StockMarketData, score_info: Dict[str, Any],
                          h: Dict[str, Any]) -> List[str]:
    factors = []
    price = data.current_price
    target = data.mean_target
    rec_label = data.rec_label

    if rec_label in ("買進", "強烈買進", "strong_buy", "buy"):
        factors.append(f"分析師評等偏多：{rec_label}（{data.analysts} 位分析師）")

    if target > 0 and price > 0:
        ups = (target - price) / price * 100
        if ups > 5:
            factors.append(f"目標價{_fmt_price(target)} 元，潛在上漲空間 {_fmt_pct(ups)}")

    if price > data.close_5d > data.close_20d:
        factors.append("均線排列向上（MA5 > MA20），多頭排列初步成形")
    elif price > data.close_5d:
        factors.append("股價站穩 MA5，短線動能偏多")

    if data.avg_volume_20d > 0 and data.volume > data.avg_volume_20d * 1.5:
        factors.append(f"成交量放大至 20 日均量 {data.volume/data.avg_volume_20d:.1f} 倍，資金積極進場")

    notes = str(h.get("notes", h.get("備註", ""))).strip()
    if notes and len(notes) > 3:
        factors.append(f"追蹤備註：{notes}")

    if data.earnings_history:
        latest = data.earnings_history[0]
        if latest.get("actual_eps", 0) > latest.get("est_eps", 0) * 1.05:
            factors.append(f"上一季 EPS {_fmt_price(latest['actual_eps'])} 元超預期待遇（預估 {_fmt_price(latest['est_eps'])} 元）")

    return factors


def _extract_bear_factors(data: StockMarketData, score_info: Dict[str, Any]) -> List[str]:
    factors = []
    price = data.current_price
    target = data.mean_target
    change = data.day_change_pct
    rp = score_info["risk_penalty"]

    if target > 0 and price > target * 1.1:
        over = (price / target - 1) * 100
        factors.append(f"股價已超出目標價 {over:.0f}%，估值偏高")

    if change > 5:
        factors.append(f"當日大漲 {change:.1f}%，追價風險較高")

    if data.high_20d > 0 and price > data.high_20d * 0.98:
        factors.append(f"股價接近 20 日高點 {_fmt_price(data.high_20d)} 元，注意獲利了結賣壓")

    if data.avg_volume_20d > 0 and data.volume < data.avg_volume_20d * 0.7:
        factors.append(f"成交量萎縮至均量 {data.volume/data.avg_volume_20d:.1f} 倍，資金關注度低")

    if data.rec_label in ("賣出", "减持", "sell", "underperform"):
        factors.append(f"分析師評等偏空：{data.rec_label}")

    if rp >= 5:
        factors.append(f"風險扣分 {rp:.0f} 分，請留意潛在負面因素")

    if change < -5:
        factors.append(f"當日大跌 {change:.1f}%，需觀察是否續跌")

    return factors


def _extract_recent_events(data: StockMarketData, h: Dict[str, Any]) -> List[str]:
    events = []
    now = datetime.now(TW_TZ)

    if data.next_earnings_date:
        try:
            earn_dt = datetime.strptime(str(data.next_earnings_date), "%Y-%m-%d")
            earn_dt = earn_dt.replace(tzinfo=TW_TZ)
            days_left = (earn_dt - now).days
            if days_left >= 0:
                events.append(f"📅 下一季財報發布日：{data.next_earnings_date}（{days_left} 天後）")
            else:
                events.append(f"📅 上一季財報發布日：{data.next_earnings_date}（已過）")
        except (ValueError, TypeError):
            events.append(f"📅 財報日期：{data.next_earnings_date}")

    if data.eps_estimate > 0:
        events.append(f"💰 本季度 EPS 預估：{_fmt_price(data.eps_estimate)} 元")
    if data.revenue_est > 0:
        rev_t = data.revenue_est / 1e8
        events.append(f"💰 本季度營收預估：約 {rev_t:.1f} 億元")

    if data.earnings_history:
        for eh in data.earnings_history[:2]:
            actual = eh.get("actual_eps", 0)
            est = eh.get("est_eps", 0)
            date = eh.get("date", "")
            if actual > 0 and est > 0:
                diff_pct = (actual - est) / est * 100
                direction = "超" if diff_pct > 0 else "遜"
                events.append(f"📊 {date} 財報：EPS {_fmt_price(actual)} 元，{direction}預估 {_fmt_price(est)} 元 ({_fmt_pct(diff_pct)})")

    catalyst_date = str(h.get("catalystdate", h.get("催化劑日期", ""))).strip()
    if catalyst_date:
        events.append(f"🔥 催化事件：{catalyst_date}")

    notes = str(h.get("notes", h.get("備註", ""))).strip()
    if notes and len(notes) > 5:
        events.append(f"🔥 {notes}")

    return events



def _extract_title(item):
    if isinstance(item.get("content"), dict):
        return item["content"].get("title", "") or ""
    return item.get("title", "") or ""


def build_taiwan_focus_report(stocks_data: Dict[str, StockMarketData],
                               watchlist: List[Dict[str, Any]],
                               qfii_data: Dict[str, Dict[str, Any]] = None,
                                 cnyes_ratings: Dict[str, Dict[str, Any]] = None) -> str:
    """Build the compact Taiwan focus report."""
    qfii_data = qfii_data or {}
    now_tw = datetime.now(TW_TZ)
    date_str = now_tw.strftime("%Y-%m-%d (%a)")
    prev_tw = now_tw - timedelta(days=1)
    prev_date_str = prev_tw.strftime("%Y-%m-%d")

    lines = []
    lines.append("")
    lines.append(f"# 台股AI摘要｜{date_str}")
    lines.append(f"資料基準：{prev_date_str} 收盤")
    lines.append("")

    # Compute scores for all stocks
    all_scores: Dict[str, Dict[str, Any]] = {}
    stock_info: Dict[str, Dict[str, Any]] = {}
    for h in watchlist:
        ticker, sheet_name = _extract_ticker_code(h.get("ticker", h.get("代碼", ""))); h["短名"] = sheet_name or h.get("短名", "")
        if not ticker:
            continue
        data = stocks_data.get(ticker)
        if data is None or data.current_price <= 0:
            continue
        score_info = compute_focus_score(data, h)
        all_scores[ticker] = score_info
        stock_info[ticker] = {"data": data, "h": h, "score": score_info}

    qualified = [(t, s) for t, s in all_scores.items() if s["focus_score"] >= FOCUS_THRESHOLD]
    qualified.sort(key=lambda x: x[1]["focus_score"], reverse=True)

    # Auto-include 焦點股 from sheet (bypasses FocusScore threshold)
    auto_focus: List[str] = []
    for h in watchlist:
        ticker = _extract_ticker_code(h.get("ticker", h.get("代碼", "")))[0]
        if not ticker or ticker not in all_scores:
            continue
        tracking = str(h.get("追蹤", "")).strip()
        if tracking == "焦點股" and ticker not in [t for t, _ in qualified]:
            auto_focus.append(ticker)
            # Ensure score info exists
            if ticker not in all_scores:
                score_info = compute_focus_score(stocks_data[ticker], h)
                all_scores[ticker] = score_info
                stock_info[ticker] = {"data": stocks_data[ticker], "h": h, "score": score_info}
    if auto_focus:
        # Insert auto-focus stocks at top, preserving score order for rest
        existing = [(t, s) for t, s in qualified if t not in auto_focus]
        auto_entries = [(t, all_scores[t]) for t in auto_focus]
        qualified = auto_entries + existing

    # --- QFII target change alert (today) ---
    today_str = datetime.now(TW_TZ).strftime("%Y%m%d")
    today_alerts = []
    for ticker, qfii in qfii_data.items():
        if ticker in cnyes_ratings:
            rating = cnyes_ratings[ticker]
            rating_date = rating.get("date", "")
            if rating_date == today_str:
                name = str(stock_info.get(ticker, {}).get("h", {}).get("短名", "")).strip() or ticker
                target = qfii.get("qfii_target", 0)
                if target > 0:
                    today_alerts.append(f"{name} ({ticker}) - 外資調整目標價至 {_fmt_price(target)} 元")
    if today_alerts:
        lines.append("今日外資調整目標價焦點股")
        for alert in today_alerts:
            lines.append(alert)
        lines.append("")

    # --- Special focus stocks (non-auto, high score) ---
    special_focus = [(t, s) for t, s in qualified if t not in auto_focus and s.get("focus_score", 0) >= 70]
    if special_focus:
        lines.append("")
        lines.append("【特別焦點股】")
        for ticker, s in special_focus[:3]:
            d = stock_info[ticker]["data"]
            h = stock_info[ticker]["h"]
            name = str(h.get("短名", "")).strip() or d.short_name or ticker
            lines.append(f"?? {ticker} {name} | {s.get(chr(39)+chr(102)+chr(111)+chr(99)+chr(117)+chr(115)+chr(95)+chr(115)+chr(99)+chr(111)+chr(114)+chr(101)+chr(39), 0):.0f} 分 | {s.get(chr(39)+chr(99)+chr(97)+chr(116)+chr(101)+chr(103)+chr(111)+chr(114)+chr(121)+chr(39), chr(39)+chr(39))}")

    # --- Detail cards ---
    if qualified:
        for ticker, s in qualified[:MAX_FOCUS_STOCKS]:
            d = stock_info[ticker]["data"]
            h = stock_info[ticker]["h"]
            lines.append(_build_focus_detail(ticker, d, h, s, qfii=qfii_data.get(ticker)))
    else:
        lines.append("本日報暫無符合焦點門檻的個股。")

    return "\n".join(lines)


def _build_summary_reason(data: StockMarketData, score_info: Dict[str, Any],
                          h: Dict[str, Any]) -> str:
    parts = []
    notes = str(h.get("notes", h.get("備註", ""))).strip()
    tracking = str(h.get("追蹤", "")).strip()
    if tracking == "焦點股":
        parts.append("【自動焦點股】")
    if notes:
        parts.append(notes[:30])
    if data.day_change_pct > 0:
        parts.append(f"漲{_fmt_pct(data.day_change_pct)}")
    if data.rec_label:
        parts.append(f"分析師{data.rec_label}")
    if score_info["market_heat"] >= 60:
        parts.append("量價活絡")
    if score_info["catalyst"] >= 60:
        parts.append("有催化")
    reason = "、".join(parts) if parts else "綜合表現突出"
    return reason

def main():
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
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

    # Fetch cnyes QFII ratings first
    cnyes_ratings = load_cnyes_ratings()
    logger.info("Loaded %d QFII ratings from cnyes.", len(cnyes_ratings))

    # Extract tickers and fetch market data
    tickers = [_extract_ticker_code(h.get("ticker", h.get("代碼", "")))[0] for h in watchlist]
    stocks_data = fetch_all_stock_data(tickers)

    # Merge QFII data into stock data
    qfii_merged = merge_cnyes_into_data(tickers, cnyes_ratings)
    for ticker, qfii_info in qfii_merged.items():
        if ticker in stocks_data:
            sd = stocks_data[ticker]
            sd.qfii_target = qfii_info["qfii_target"]
            sd.qfii_rating = qfii_info["qfii_rating"]
            sd.qfii_upside = qfii_info["qfii_upside"]
            # Recalculate upside using actual stock price
            if sd.current_price > 0 and qfii_info["qfii_target"] > 0:
                sd.qfii_upside = round((qfii_info["qfii_target"] - sd.current_price) / sd.current_price * 100, 1)
            sd.qfii_broker = qfii_info["qfii_broker"]
            logger.info("%s: QFII target=%s rating=%s upside=%s%%", ticker, qfii_info["qfii_target"], qfii_info["qfii_rating"], sd.qfii_upside)

    if not stocks_data:
        # Try cache
        from taiwan_market_data import _load_cache
        cached = _load_cache("__ALL__")
        if cached:
            logger.info("Using cached data from previous trading day.")
            # Reconstruct StockMarketData objects
            from taiwan_market_data import StockMarketData
            stocks_data = {
                t: StockMarketData(**d) for t, d in cached.items()
                if isinstance(d, dict)
            }
        else:
            logger.error("No stock data available, aborting.")
            sys.exit(1)

    # Build and send report
    report = build_taiwan_focus_report(stocks_data, watchlist, qfii_data=qfii_merged, cnyes_ratings=cnyes_ratings)
    print(report)

    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("Taiwan focus report pushed successfully.")


if __name__ == "__main__":
    main()