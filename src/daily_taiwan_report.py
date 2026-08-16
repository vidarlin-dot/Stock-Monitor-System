# -*- coding: utf-8 -*-
"""Daily Taiwan stock focus report."""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz
import yfinance as yf

from config import GoogleSheetsManager
from line_notifier import LineNotifier

logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone("Asia/Taipei")
TW_SUFFIX = ".TW"


# Taiwan Stock Exchange 2026 holidays
TW_HOLIDAYS: Dict[str, str] = {
    "2026-01-01": "\u5143\u65e6",
    "2026-01-28": "\u6625\u7ae5\u591c",
    "2026-01-29": "\u6625\u7ae5\u521d\u4e00",
    "2026-01-30": "\u6625\u7ae5\u521d\u4e8c",
    "2026-02-02": "\u6625\u7ae5\u8865\u5047",
    "2026-03-03": "\u4e8c\u4e8c\u516b\u7d00\u5ff5\u65e5",
    "2026-04-04": "\u6e05\u660e\u7bc0",
    "2026-04-05": "\u6e05\u660e\u8865\u5047",
    "2026-05-01": "\u52de\u52d5\u7bc0",
    "2026-05-05": "\u7aef\u5348\u7bc0",
    "2026-06-19": "\u7aef\u5348\u8865\u5047",
    "2026-09-25": "\u4e2d\u79cb\u7bc0",
    "2026-10-10": "\u570b\u6109\u65e5",
    "2026-10-12": "\u570b\u6109\u8865\u5047",
}

def is_taiwan_trading_day():
    now = datetime.now(TW_TZ)
    date_str = now.strftime("%Y-%m-%d")
    if now.weekday() >= 5:
        return False
    if date_str in TW_HOLIDAYS:
        logger.info("Taiwan market holiday: %s (%s)", date_str, TW_HOLIDAYS[date_str])
        return False
    return True

POS_KW = {"buy", "upgrade", "strong", "bullish", "growth", "positive",
          "record", "profit", "accelerate", "surge", "beat", "ahead"}
NEG_KW = {"sell", "downgrade", "weak", "bearish", "risk", "decline",
          "miss", "loss", "concern", "cut", "warn", "drop"}


def _fetch_stock_data(ticker):
    """Fetch price, target, recommendation, news for a Taiwan stock."""
    yf_ticker = ticker + TW_SUFFIX
    try:
        stock = yf.Ticker(yf_ticker)
        info = stock.info
        current = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        prev_close = info.get("previousClose") or 0
        mean_target = info.get("targetMeanPrice") or 0
        high_target = info.get("targetHighPrice") or 0
        low_target = info.get("targetLowPrice") or 0
        median_target = info.get("targetMedianPrice") or 0
        analysts = info.get("numberOfAnalystOpinions") or 0
        rec_key = (info.get("recommendationKey") or "").lower()
        news_list = []
        try:
            news_list = stock.news[:5] if hasattr(stock, "news") and stock.news else []
        except Exception:
            pass
        rec_map = {
            "strong_buy": "\u5061\u591a",
            "buy": "\u8cb7\u5165",
            "outperform": "\u512a\u65bc\u5927\u76e4",
            "overweight": "\u8d85\u914d",
            "hold": "\u6301\u6709",
            "equal_weight": "\u4e2d\u6027",
            "underweight": "\u4f4e\u914d",
            "sell": "\u8ca7\u51fa",
        }
        analyst_rec = rec_map.get(rec_key, "\u89c0\u671b")
        sentiment = _news_sentiment(news_list)
        short_name = info.get("shortName") or info.get("symbol", ticker)
        volume = info.get("volume") or 0
        avg_volume = info.get("averageVolume") or 0
        return {
            "ticker": ticker, "short_name": short_name,
            "current_price": current, "previous_close": prev_close,
            "mean_target": mean_target, "high_target": high_target,
            "low_target": low_target, "median_target": median_target,
            "analysts": analysts, "recommendation": analyst_rec,
            "sentiment": sentiment, "volume": volume,
            "avg_volume": avg_volume, "rec_key": rec_key,
        }
    except Exception as exc:
        logger.debug("Failed to fetch data for %s: %s", yf_ticker, exc)
        return None


def _news_sentiment(news_list):
    """Classify sentiment from recent news headlines."""
    if not news_list:
        return "\u4e2d\u6027"
    score = 0
    for item in news_list:
        title = ""
        if isinstance(item.get("content"), dict):
            title = item["content"].get("title", "") or ""
        elif item.get("title"):
            title = item.get("title", "")
        tl = title.lower()
        if any(kw in tl for kw in POS_KW):
            score += 1
        if any(kw in tl for kw in NEG_KW):
            score -= 1
    if score >= 2:
        return "\u5061\u591a"
    elif score <= -2:
        return "\u5061\u7a7a"
    return "\u4e2d\u6027"


def _calc_activity_score(data, h):
    """Calculate activity score for ranking stocks."""
    score = 0.0
    price = data.get("current_price") or 0
    prev = data.get("previous_close") or 0
    if prev > 0 and price > 0:
        pct_change = (price - prev) / prev * 100
        score += abs(pct_change) * 2
    vol = data.get("volume") or 0
    avg_vol = data.get("avg_volume") or 0
    if avg_vol > 0 and vol > 0:
        vol_ratio = vol / avg_vol
        score += vol_ratio * 3
    analysts = data.get("analysts") or 0
    score += analysts * 0.5
    rec = data.get("rec_key", "")
    if rec in ("strong_buy", "buy"):
        score += 3
    elif rec in ("sell", "underweight"):
        score += 2
    return score


def _build_taiwan_report(stocks_data, watchlist):
    """Build the Taiwan stock focus report."""
    now_tw = datetime.now(TW_TZ)
    date_str = now_tw.strftime("%Y-%m-%d (%a)")
    day_of_week = now_tw.weekday()
    is_weekend = day_of_week >= 5

    lines = []
    lines.append("")
    lines.append(chr(0x1F4CA) + " \u53f0\u80a1\u71b1\u9ede\u80a1\u65e5\u5831 | " + date_str)
    if is_weekend:
        lines.append(chr(0x26A0) + chr(0xFE0F) + " \u8a3b\uff1a\u9031\u672b\u4f11\u5e02")
    else:
        lines.append(chr(0x26A0) + chr(0xFE0F) + " \u8a3b\uff1a\u70ba\u5be6\u6642\u4ea4\u6613\u6642\u6bb5\u6d88\u606f")
    lines.append("=" * 40)

    scored = []
    for h in watchlist:
        ticker = str(h.get("ticker", h.get("\u4ee3\u78bc", ""))).strip()
        if not ticker:
            continue
        data = stocks_data.get(ticker)
        if data is None:
            continue
        price = data.get("current_price", 0)
        if price <= 0:
            continue
        activity = _calc_activity_score(data, h)
        scored.append((activity, ticker, data, h))

    if not scored:
        lines.append("\u2705 \u4eca\u65e5\u71b1\u9ede\u80a1\u4fe1\u606f\u4e0d\u8db3")
        return chr(10).join(lines)

    scored.sort(key=lambda x: x[0], reverse=True)

    for activity, ticker, data, h in scored[:10]:
        lines.append("")
        lines.append(chr(0x1F50D) + " " + ticker + " " + data["short_name"])
        lines.append("   " + chr(0x1F4C8) + " \u7576\u524d\u50f9\uff1a" + "{:,}".format(data["current_price"]) + " \u5143")

        buy_raw = str(h.get("buyzone", h.get("\u8cb7\u76df\u5340\u9593", ""))).strip()
        sell_raw = str(h.get("sellzone", h.get("\u8ce3\u51fa\u5340\u9593", ""))).strip()

        if buy_raw:
            buy_zones = []
            for bz in buy_raw.split(","):
                try:
                    buy_zones.append(float(bz.strip()))
                except ValueError:
                    pass
            if buy_zones:
                lines.append("   " + chr(0x21E3) + chr(0xFE0F) + " \u8cb7\u9032\u5340\u9593\uff1a" + "{:.0f}\u300e{:.0f} \u5143".format(buy_zones[0], buy_zones[-1]))

        if sell_raw:
            sell_zones = []
            for sz in sell_raw.split(","):
                try:
                    sell_zones.append(float(sz.strip()))
                except ValueError:
                    pass
            if sell_zones:
                lines.append("   " + chr(0x21E0) + chr(0xFE0F) + " \u8ce3\u51fa\u5340\u9593\uff1a" + "{:.0f}\u300e{:.0f} \u5143".format(sell_zones[0], sell_zones[-1]))

        catalyst_raw = str(h.get("catalystdate", h.get("\u50ac\u5316\u5287\u65e5\u671f", ""))).strip()
        notes = str(h.get("notes", h.get("\u5099\u8a3b", ""))).strip()

        if catalyst_raw or notes:
            event_parts = []
            if catalyst_raw:
                try:
                    dt = datetime.strptime(catalyst_raw, "%Y-%m-%d")
                    delta = (dt - now_tw.replace(hour=0, minute=0, second=0, microsecond=0)).days
                    icon = chr(0x1F525) if delta <= 7 else chr(0x1F4C5)
                    event_parts.append(icon + " " + catalyst_raw + " (" + str(delta) + "\u5929)")
                except ValueError:
                    event_parts.append(catalyst_raw)
            if notes:
                event_parts.append(notes)
            lines.append("   " + chr(0x1F4C5) + " \u4e8b\u4ef6\uff1a" + " | ".join(event_parts))

        analyst_comment = str(h.get(
            "analyst_comment",
            h.get("\u5206\u6790\u5e08\u8a55\u8ad6",
                  h.get("\u5206\u6790\u5e08\u898b\u89e3",
                        h.get("\u5206\u6790\u5e08\u5061\u8b70", ""))))) .strip()
        if analyst_comment:
            lines.append("   " + chr(0x1F4DD) + " \u5206\u6790\u5e08\u5061\u8ad6\uff1a" + analyst_comment)
        elif data.get("recommendation"):
            lines.append("   " + chr(0x1F4DD) + " \u5206\u6790\u5e08\u5061\u8ad6\uff1a" + data["recommendation"])

        lines.append("   " + chr(0x1F4C6) + " \u5546\u6a5f\u71b1\u9ede\uff1a" + data["sentiment"])
        if notes:
            lines.append("   " + chr(0x1F4DD) + " \u5099\u8a3b\uff1a" + notes)
        lines.append("")

    return chr(10).join(lines)


def main():
    """Entry point for the Taiwan stock daily report."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("Taiwan Stock Monitor - Daily Report starting...")

    if not is_taiwan_trading_day():
        now_tw = datetime.now(TW_TZ)
        date_str = now_tw.strftime("%Y-%m-%d (%a)")
        msg = chr(0x1F4CA) + " \u53f0\u80a1\u71b1\u9ede\u80a1\u65e5\u5831 | " + date_str + chr(10)
        msg += chr(0x26A0) + chr(0xFE0F) + " \u8a3b\uff1a\u4eca\u65e5\u8655\u65bc\u4f11\u5e02\u65e5\uff0c\u4eca\u65e5\u4e0d\u6d3e\u767c\u3002" + chr(10)
        print(msg)
        logger.info("Taiwan market is closed today, skipping report.")
        sys.exit(0)

    manager = GoogleSheetsManager()
    watchlist = manager.load_taiwan_stocks()
    if not watchlist:
        logger.warning("No Taiwan stock data, aborting.")
        sys.exit(0)

    logger.info("Processing %d Taiwan stock(s)...", len(watchlist))

    stocks_data = {}
    for h in watchlist:
        ticker = str(h.get("ticker", h.get("\u4ee3\u78bc", ""))).strip()
        if not ticker:
            continue
        for attempt in range(3):
            data = _fetch_stock_data(ticker)
            if data:
                stocks_data[ticker] = data
                logger.info("%s: price=%.2f, rec=%s", ticker, data["current_price"], data["recommendation"])
                break
            if attempt < 2:
                wait = (attempt + 1) * 2
                logger.warning("%s: fetch failed, retrying in %ds...", ticker, wait)
                time.sleep(wait)
        else:
            logger.error("%s: failed to fetch after 3 attempts", ticker)

    if not stocks_data:
        logger.error("No stock data available, aborting.")
        sys.exit(1)

    report = _build_taiwan_report(stocks_data, watchlist)
    print(report)

    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("Taiwan daily report pushed successfully.")


if __name__ == "__main__":
    main()
