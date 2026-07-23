"""Monthly update of analyst targets in Google Sheets.
Runs on last day of month to fetch latest yfinance analyst data
and update buy/sell zones and generate analyst summary notes.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import gspread
import pytz
import yfinance as yf
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone('Asia/Taipei')


def is_last_day_of_month() -> bool:
    """Check if today is the last day of the current month."""
    now_tw = datetime.now(TW_TZ)
    today = now_tw.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    return tomorrow.month != today.month


def get_credentials():
    """Authenticate with Google Sheets using service account."""
    import os
    service_account_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "")
    sheet_name = os.environ.get("SHEET_NAME", "Portfolio")

    if not service_account_json:
        raise ValueError("GCP_SERVICE_ACCOUNT_JSON environment variable not set.")

    service_account_json = service_account_json.lstrip("\ufeff")
    creds_dict: Dict[str, Any] = json.loads(service_account_json)

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(credentials), sheet_name


def _generate_analyst_summary(recommendation_key: str, num_analysts: int, news_list: list) -> str:
    """Generate a 20-character max summary from analyst data.

    Args:
        recommendation_key: e.g., strong_buy, buy, hold, sell
        num_analysts: Number of analysts covering the stock
        news_list: List of recent news articles from yfinance

    Returns:
        A concise summary string <= 20 characters
    """
    rec_map = {
        "strong_buy": "\u5927\u8cb7",     # \u5927\u8cb7
        "buy": "\u8cb7\u5165",           # \u8cb7\u5165
        "outperform": "\u8d85\u908a\u5e02\u5834",  # \u8d85\u908a\u5e02\u5834
        "overweight": "\u904e\u91cd",           # \u904e\u91cd
        "hold": "\u6301\u6709",          # \u6301\u6709
        "equal_weight": "\u7b49\u91cd",         # \u7b49\u91cd
        "underweight": "\u4f4e\u914d",   # \u4f4e\u914d
        "sell": "\u8ca7\u51fa",          # \u8ca7\u51fa
    }

    chinese_rec = rec_map.get(recommendation_key.lower(), "\u89c0\u671b")
    summary = f"{chinese_rec} ({num_analysts}\u4f4d)"

    if news_list:
        keywords = _extract_sentiment_keywords(news_list)
        if keywords and len(summary + " | " + keywords) <= 20:
            summary += " | " + keywords

    if len(summary) > 20:
        summary = summary[:18] + ".."

    return summary


def _extract_sentiment_keywords(news_list: list) -> str:
    """Extract key sentiment words from recent news headlines.

    Returns:
        Comma-separated English keywords found in recent news
    """
    positive_words = [
        "buy", "upgrade", "strong", "bullish", "growth", "positive",
        "record", "profit", "accelerate",
    ]

    negative_words = [
        "sell", "downgrade", "weak", "bearish", "risk", "decline",
        "miss", "loss", "concern", "cut",
    ]

    all_text = ""
    for item in news_list[:5]:
        title = ""
        summary_text = ""
        if isinstance(item.get("content"), dict):
            title = item["content"].get("title", "")
            summary_text = item["content"].get("summary", "")
        elif item.get("title"):
            title = item.get("title", "")
            summary_text = item.get("description", "")
        all_text += f" {title} {summary_text}"

    if not all_text.strip():
        return ""

    text_lower = all_text.lower()
    found = []

    for kw in positive_words:
        if kw in text_lower:
            found.append(kw)
            if len(found) >= 2:
                break

    for kw in negative_words:
        if kw in text_lower:
            found.append(kw)
            if len(found) >= 2:
                break

    return " | ".join(found[:2]) if found else ""


def update_analyst_targets() -> None:
    """Fetch analyst targets from yfinance and update Google Sheet on month-end.

    Updates:
    1. Buy zone (based on target low price)
    2. Sell zone (based on mean/median target prices)
    3. Notes column (analyst rating summary)
    """
    if not is_last_day_of_month():
        logger.info("Not the last day of the month. Skipping update.")
        return

    client, sheet_name = get_credentials()
    worksheet = client.open(sheet_name).worksheet("Holdings")

    rows = worksheet.get_all_values()
    if len(rows) < 2:
        logger.warning("Sheet has fewer than 2 rows.")
        return

    headers = [h.strip() for h in rows[0]]

    # Find column indices (support both English and Chinese headers)
    ticker_idx = None
    buyzone_idx = None
    sellzone_idx = None
    notes_idx = None
    updated_idx = None

    for idx, h in enumerate(headers):
        hl = h.lower()
        if hl in ("ticker", "\u4ee3\u78bc"):
            ticker_idx = idx
        elif hl in ("buyzone", "\u8cb7\u76df\u5340\u9593"):
            buyzone_idx = idx
        elif hl in ("sellzone", "\u8ca3\u51fa\u5340\u9593"):
            sellzone_idx = idx
        elif hl in ("notes", "\u5099\u8a3b"):
            notes_idx = idx
        elif hl in ("updated", "\u66f4\u65b0\u6642\u9593"):
            updated_idx = idx

    if ticker_idx is None:
        raise ValueError("Ticker column not found.")

    logger.info("Starting monthly analyst targets update...")

    updated_count = 0
    skipped_count = 0

    for i, row in enumerate(rows[1:], start=2):
        if not any(row):
            continue

        ticker = row[ticker_idx].strip().upper()
        if not ticker:
            continue

        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            current = info.get("currentPrice", 0)
            mean = info.get("targetMeanPrice", 0)
            high = info.get("targetHighPrice", 0)
            low = info.get("targetLowPrice", 0)
            median = info.get("targetMedianPrice", 0)
            analysts = info.get("numberOfAnalystOpinions", 0)
            rec_key = info.get("recommendationKey", "")

            if not mean or not low or not high:
                logger.debug("%s: No analyst data available", ticker)
                skipped_count += 1
                continue

            # Calculate buy zone from target low price
            buy_zone_low = low * 0.9
            buy_zone_high = low * 0.85
            buy_zone_str = f"{buy_zone_low:.2f},{buy_zone_high:.2f}"

            # Calculate sell zone from mean and median target prices
            sell_zone_mean = mean * 1.0
            sell_zone_median = median * 1.0
            sell_zone_str = f"{sell_zone_mean:.2f},{sell_zone_median:.2f}"

            # Update buy/sell zones in sheet
            if buyzone_idx is not None:
                worksheet.update_cell(i, buyzone_idx + 1, buy_zone_str)
            if sellzone_idx is not None:
                worksheet.update_cell(i, sellzone_idx + 1, sell_zone_str)

            # --- NEW: Generate and update analyst summary note ---
            if notes_idx is not None:
                # Get recent news for keyword extraction
                news_list = []
                if hasattr(stock, "news") and stock.news:
                    news_list = stock.news[:5]

                summary_note = _generate_analyst_summary(rec_key, analysts, news_list)

                # Read existing notes first
                existing_notes_row = rows[i - 1]
                existing_notes = str(existing_notes_row[notes_idx]).strip() if notes_idx < len(existing_notes_row) else ''

                # Only auto-update if current notes are empty/unhelpful
                common_empty = ["\u898b\u5099\u8a3b", "see notes", "(\u898b\u5099\u8a3b)", "N/A", "", "N/A", "\u2014"]
                if existing_notes not in common_empty:
                    logger.info("%s: Keeping existing notes: %s", ticker, existing_notes)
                else:
                    worksheet.update_cell(i, notes_idx + 1, summary_note)
                    logger.info("%s: Updated notes to: %s", ticker, summary_note)

            ts = datetime.now().isoformat()

            # Update zones and notes
            if buyzone_idx is not None:
                worksheet.update_cell(i, buyzone_idx + 1, buy_zone_str)
            if sellzone_idx is not None:
                worksheet.update_cell(i, sellzone_idx + 1, sell_zone_str)

            if notes_idx is not None:
                news_list = []
                if hasattr(stock, "news") and stock.news:
                    news_list = stock.news[:5]
                summary_note = _generate_analyst_summary(rec_key, analysts, news_list)
                existing_notes_row = rows[i - 1]
                existing_notes = str(existing_notes_row[notes_idx]).strip() if notes_idx < len(existing_notes_row) else ""
                common_empty = ["見備註", "see notes", "(見備註)", "N/A", "", "N/A", "—"]
                if existing_notes not in common_empty:
                    logger.info("%s: Keeping existing notes: %s", ticker, existing_notes)
                else:
                    worksheet.update_cell(i, notes_idx + 1, summary_note)
                    logger.info("%s: Updated notes to: %s", ticker, summary_note)

            # Write timestamp
            if updated_idx is not None:
                worksheet.update_cell(i, updated_idx + 1, ts)
                logger.debug("%s: Timestamp updated to %s", ticker, ts)

            logger.info("%s: Buy=[%s], Sell=[%s] (%d analysts, %s)",
                ticker, buy_zone_str, sell_zone_str, analysts, rec_key)

            updated_count += 1

        except Exception as exc:
            logger.error("%s: Error - %s", ticker, exc)
            skipped_count += 1

    logger.info("Monthly update complete: %d updated, %d skipped", updated_count, skipped_count)


def main() -> None:
    """Main entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info("Monthly Analyst Targets Update starting...")
    update_analyst_targets()
    logger.info("Monthly Analyst Targets Update finished.")


if __name__ == "__main__":
    main()
