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

    sentiment_map = {"偏多": "偏多", "偏空": "偏空", "中性": "中性"}
    lines.append(f"💬 市場情緒：{sentiment_map.get(category, '偏多')}")

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
        lines.append(f"📝 備註：外資目標價 {_fmt_price(qfii_target)} 元，距現價 {ups_pct:+.1f}% 空間")

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
        sell_range = f"{_fmt_price(target * 0.95)}～{_fmt_price(target * 1.05)} 元"
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

    if data.avg_volume_20d > 0 and data.volume > data.avg_volume_20d * 1.5:
        factors.append(f"成交量放大至 20 日均量 {data.volume/data.avg_volume_20d:.1f} 倍，籌碼活絡")

    notes = str(h.get("備註", "")).strip()
    if notes and len(notes) > 3:
        factors.append(notes[:30])

    if data.earnings_history:
        latest = data.earnings_history[0]
        if latest.get("actual_eps", 0) > latest.get("est_eps", 0) * 1.05:
            factors.append(f"上季 EPS {_fmt_price(latest['actual_eps'])} 優於預期 {_fmt_price(latest['est_eps'])}")

    return factors


def _extract_bear_factors(data: StockMarketData, score_info: Dict[str, Any]) -> List[str]:
    factors = []
    price = data.current_price
    target = data.mean_target
    change = data.day_change_pct
    rp = score_info["risk_penalty"]

    if target > 0 and price > target * 1.1:
        over = (price / target - 1) * 100
        factors.append(f"股價已超過目標價 {over:.0f}%，注意回調風險")

    if change > 5:
        factors.append(f"單日大漲 {change:.1f}%，追價需谨慎")

    if data.high_20d > 0 and price > data.high_20d * 0.98:
        factors.append(f"接近 20 日高點 {_fmt_price(data.high_20d)}，突破後方可續持")

    if data.avg_volume_20d > 0 and data.volume < data.avg_volume_20d * 0.7:
        factors.append(f"成交量萎縮至 20 日均量 {data.volume/data.avg_volume_20d:.1f} 倍，缺乏動能")

    if data.rec_label in ("賣出", "持有", "sell", "underperform"):
        factors.append(f"法人評等：{data.rec_label}")

    if rp >= 5:
        factors.append(f"風險扣分 {rp:.0f} 分，注意相關風險")

    if change < -5:
        factors.append(f"單日大跌 {change:.1f}%，確認支撐再進場")

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
                events.append(f"財報日 {data.next_earnings_date}（{days_left} 天後）")
            else:
                events.append(f"財報日 {data.next_earnings_date}（已過）")
        except (ValueError, TypeError):
            events.append(f"財報日 {data.next_earnings_date}")

    if data.eps_estimate > 0:
        events.append(f"EPS 預估 {_fmt_price(data.eps_estimate)} 元")

    notes = str(h.get("備註", "")).strip()
    if notes:
        events.append(notes[:25])

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


def build_taiwan_focus_report(stocks_data: Dict[str, StockMarketData],
                               watchlist: List[Dict[str, Any]],
                               qfii_data: Dict[str, Dict[str, Any]] = None,
                               cnyes_ratings: Dict[str, Dict[str, Any]] = None) -> str:
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

    auto_focus: List[str] = []
    for h in watchlist:
        ticker = _extract_ticker_code(h.get("ticker", h.get("代碼", "")))[0]
        if not ticker or ticker not in all_scores:
            continue
        tracking = str(h.get("追蹤", "")).strip()
        if tracking == "焦點股" and ticker not in [t for t, _ in qualified]:
            auto_focus.append(ticker)
            if ticker not in all_scores:
                score_info = compute_focus_score(stocks_data[ticker], h)
                all_scores[ticker] = score_info
                stock_info[ticker] = {"data": stocks_data[ticker], "h": h, "score": score_info}
    if auto_focus:
        existing = [(t, s) for t, s in qualified if t not in auto_focus]
        auto_entries = [(t, all_scores[t]) for t in auto_focus]
        qualified = auto_entries + existing

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
                    today_alerts.append(f"{name} ({ticker}) - 外資調降目標價至 {_fmt_price(target)} 元")
    if today_alerts:
        lines.append("⚠️ 今日外資調整目標價重點股")
        for alert in today_alerts:
            lines.append(alert)
        lines.append("")

    special_focus = [(t, s) for t, s in qualified if t not in auto_focus and s.get("focus_score", 0) >= 70]
    if special_focus:
        lines.append("📌 特別焦點股（市場熱絡）")
        for ticker, s in special_focus[:3]:
            d = stock_info[ticker]["data"]
            h = stock_info[ticker]["h"]
            name = str(h.get("短名", "")).strip() or d.short_name or ticker
            score = s.get("focus_score", 0)
            cat = s.get("category", "")
            lines.append(f"📊 {ticker} {name} | {score:.0f} 分 | {cat}")

    if qualified:
        for ticker, s in qualified[:MAX_FOCUS_STOCKS]:
            d = stock_info[ticker]["data"]
            h = stock_info[ticker]["h"]
            lines.append(_build_focus_detail(ticker, d, h, s, qfii=qfii_data.get(ticker)))
    else:
        lines.append("今日無符合條件的焦點股，請留意後續市場變化。")

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

    report = build_taiwan_focus_report(stocks_data, watchlist, qfii_data=qfii_merged, cnyes_ratings=cnyes_ratings)
    print(report)

    notifier = LineNotifier()
    _send_report_chunks(notifier, report)
    logger.info("Taiwan focus report pushed successfully.")


def _send_report_chunks(notifier, message: str, max_length: int = 4800) -> None:
    if len(message) <= max_length:
        notifier.send_push_message(message)
        return

    lines = message.split("\n")
    current_chunk = []
    current_length = 0

    for line in lines:
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
