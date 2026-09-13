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
    lines.append(f"📈 现价：{_fmt_price(price)}  ({data.day_change_pct:+.2f}%)")

    ma20 = data.close_20d
    ma5 = data.close_5d
    buy_zone = None
    sell_zone = None
    if ma20 > 0 and price > ma20 * 1.02:
        buy_zone = f"{_fmt_price(ma20 * 0.98)}～{_fmt_price(price * 0.97)}"
    elif ma5 > 0:
        buy_zone = f"{_fmt_price(ma5 * 0.97)}～{_fmt_price(price * 0.98)}"
    else:
        buy_zone = f"现价附近或回测 MA20 ({_fmt_price(ma20)})"
    if target > 0:
        sell_zone = f"{_fmt_price(target * 0.95)}～{_fmt_price(target * 1.05)}"
    elif data.high_20d > 0:
        sell_zone = f"{_fmt_price(data.high_20d)}～{_fmt_price(data.high_20d * 1.05)}"
    if buy_zone: lines.append(f"⬆️ 买进区间：{buy_zone}")
    if sell_zone: lines.append(f"⬇️ 卖出区间：{sell_zone}")

    ups = 0.0
    if target > 0 and price > 0:
        ups = (target - price) / price * 100
    if rec: lines.append(f"📝 分析师建议：{rec} ({data.analysts}家追蹤)")
    if target > 0: lines.append(f"🎯 目标价：{_fmt_price(target)} (距现价 {ups:+.1f}%)")
    if notes: lines.append(f"📌 备注：{notes[:40]}")
    if analyst_comment: lines.append(f"📌 分析师评论：{analyst_comment[:40]}")

    cat = score_info["category"]
    score = score_info["focus_score"]
    lines.append(f"🔥 FocusScore：{score:.0f} 分 | {cat}")

    ops = []
    if ups > 15: ops.append(f"距目标价 {ups:.0f}% 空间，可考虑分批布局")
    elif ups > 5: ops.append(f"距目标价 {ups:.0f}%，观察回调买点")
    elif ups < -5: ops.append("股价已超过目标价，注意获利了结时机")
    if data.day_change_pct > 5: ops.append("涨幅过大，建议等待回调再进场")
    elif data.day_change_pct < -5: ops.append("跌幅较大，确认支撑站稳后再考虑接单")
    if cat == "偏多焦点" and score >= 70: ops.append("整体评价偏多，可逢低留意")
    elif cat == "风险焦点": ops.append("风险扣分较高，建议保守操作")
    if not ops: ops.append("观察量价配合，等待明确信号")
    lines.append(f"📝 操作建议：{'; '.join(ops[:2])}")
    return chr(10).join(lines)


def build_daily_report(holdings_data):
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

    lines = [f"# 美股 AI 焦點股票 | {date_str}", ""]
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

    report_text, qualified_tickers, stocks_data, stock_info = build_daily_report(holdings)
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