"""Monthly update of analyst targets in Google Sheets.

Fetches latest analyst targets from yfinance and updates
the portfolio sheet with new buy/sell zones.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, List, Optional

import gspread
import yfinance as yf
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)


def get_credentials() -> gspread.Client:
    """Authenticate with Google Sheets using service account."""
    service_account_json: Optional[str] = None
    sheet_name: Optional[str] = None

    import os
    service_account_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "")
    sheet_name = os.environ.get("SHEET_NAME", "Portfolio")

    if not service_account_json:
        raise ValueError("GCP_SERVICE_ACCOUNT_JSON environment variable not set.")

    # Strip BOM if present
    service_account_json = service_account_json.lstrip("\ufeff")
    creds_dict: Dict[str, Any] = json.loads(service_account_json)

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(credentials), sheet_name


def update_analyst_targets() -> None:
    """Fetch analyst targets from yfinance and update Google Sheet."""
    client, sheet_name = get_credentials()
    worksheet = client.open(sheet_name).worksheet("Holdings")

    rows = worksheet.get_all_values()
    if len(rows) < 2:
        logger.warning("Sheet has fewer than 2 rows.")
        return

    headers = [h.strip() for h in rows[0]]

    # Find column indices
    ticker_idx = None
    buyzone_idx = None
    sellzone_idx = None

    for idx, h in enumerate(headers):
        if h.lower() in ("ticker", "代碼"):
            ticker_idx = idx
        elif h.lower() in ("buyzone", "買進區間"):
            buyzone_idx = idx
        elif h.lower() in ("sellzone", "賣出區間"):
            sellzone_idx = idx

    if ticker_idx is None:
        raise ValueError("Ticker column not found.")

    logger.info("Starting analyst targets update...")

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

            # Set buy zone at low target (with 10% buffer)
            buy_zone_low = low * 0.9
            buy_zone_high = low * 0.85
            buy_zone_str = f"{buy_zone_low:.2f},{buy_zone_high:.2f}"

            # Set sell zone at mean and median targets
            sell_zone_mean = mean * 1.0
            sell_zone_median = median * 1.0
            sell_zone_str = f"{sell_zone_mean:.2f},{sell_zone_median:.2f}"

            # Update sheet
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

    logger.info(
        "Update complete: %d updated, %d skipped",
        updated_count,
        skipped_count,
    )


def main() -> None:
    """Main entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info("Monthly Analyst Targets Update starting...")
    update_analyst_targets()
    logger.info("Monthly Analyst Targets Update completed.")


if __name__ == "__main__":
    main()