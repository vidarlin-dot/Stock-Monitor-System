"""Monthly update of analyst targets in Google Sheets.

Runs only on the last day of the month to fetch latest yfinance analyst targets
and update buy/sell zones in the portfolio sheet.
"""

from __future__ import annotations

import calendar
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
TW_TZ = pytz.timezone("Asia/Taipei")


def is_last_day_of_month() -> bool:
    """Check if today is the last day of the current month."""
    now_tw = datetime.now(TW_TZ)
    # Replace time with midnight for safe comparison
    today = now_tw.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    return tomorrow.month != today.month


def get_credentials() -> gspread.Client:
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


def update_analyst_targets() -> None:
    """Fetch analyst targets from yfinance and update Google Sheet on month-end."""
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

    ticker_idx = None
    buyzone_idx = None
    sellzone_idx = None

    for idx, h in enumerate(headers):
        hl = h.lower()
        if hl in ("ticker", "代碼"):
            ticker_idx = idx
        elif hl in ("buyzone", "買進區間"):
            buyzone_idx = idx
        elif hl in ("sellzone", "賣出區間"):
            sellzone_idx = idx

    if ticker_idx is None:
        raise ValueError("Ticker column not found.")

    logger.info("Starting monthly analyst targets update (Last day of month confirmed)...")

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

            if not mean or not low or not high:
                logger.debug("%s: No analyst data available", ticker)
                skipped_count += 1
                continue

            buy_zone_low = low * 0.9
            buy_zone_high = low * 0.85
            buy_zone_str = f"{buy_zone_low:.2f},{buy_zone_high:.2f}"

            sell_zone_mean = mean * 1.0
            sell_zone_median = median * 1.0
            sell_zone_str = f"{sell_zone_mean:.2f},{sell_zone_median:.2f}"

            if buyzone_idx is not None:
                worksheet.update_cell(i, buyzone_idx + 1, buy_zone_str)
            if sellzone_idx is not None:
                worksheet.update_cell(i, sellzone_idx + 1, sell_zone_str)

            logger.info(
                "%s: Buy=[%s], Sell=[%s] (%d analysts, %s)",
                ticker,
                buy_zone_str,
                sell_zone_str,
                analysts,
                info.get("recommendationKey", "N/A"),
            )
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