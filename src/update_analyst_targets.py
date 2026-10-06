"""Monthly update of analyst targets in Google Sheets.
Runs on last day of month to fetch latest yfinance analyst data
and update buy/sell zones and generate analyst summary notes.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import gspread
import pytz
import yfinance as yf
from google.oauth2.service_account import Credentials
from strategy import calc_trade_zones, validate_target_price, format_analyst_summary

logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone('Asia/Taipei')


def is_last_day_of_month() -> bool:
    """Check if today is the last day of the current month (Taipei timezone)."""
    now_tw = datetime.now(TW_TZ)
    import calendar
    _, last_day = calendar.monthrange(now_tw.year, now_tw.month)
    return now_tw.day == last_day


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


def _find_column_indices(headers: List[str]) -> Dict[str, int]:
    """Find column indices supporting both English and Chinese headers."""
    mapping = {}
    for idx, h in enumerate(headers):
        hl = h.lower()
        if hl in ("ticker", "代碼"):
            mapping["ticker"] = idx
        elif hl in ("buyzone", "買進區間"):
            mapping["buyzone"] = idx
        elif hl in ("sellzone", "賣出區間"):
            mapping["sellzone"] = idx
        elif hl in ("notes", "備註"):
            mapping["notes"] = idx
        elif hl in ("analyst_comment", "分析師評論", "分析師見解", "分析師建議"):
            mapping["analyst_comment"] = idx
        elif hl in ("updated", "更新時間"):
            mapping["updated"] = idx
    return mapping


def _update_notes_column(
    worksheet, row_idx: int, notes_idx: int,
    existing_notes: str, summary_note: str, ticker: str,
) -> None:
    """Update notes column only if placeholder or auto-generated pattern changed."""
    placeholders = ["see notes", "(見備註)", "N/A", "", "N/A", "\u2014"]
    is_placeholder = existing_notes in placeholders
    is_auto_pattern = bool(re.search(r"\(\d+位\)", existing_notes))

    should_update = False
    update_reason = ""
    if is_placeholder:
        should_update = True
        update_reason = "empty placeholder"
    elif is_auto_pattern and summary_note != existing_notes:
        should_update = True
        update_reason = f"auto pattern changed ({existing_notes} -> {summary_note})"

    if should_update:
        worksheet.update_cell(row_idx, notes_idx + 1, summary_note)
        logger.info("%s: Updated notes (%s): %s -> %s",
            ticker, update_reason, repr(existing_notes), repr(summary_note))
    else:
        logger.info("%s: Preserving manual remarks (len=%d): %s",
            ticker, len(existing_notes), repr(existing_notes))


def _process_stock_row(
    stock, ticker: str, rows: list, i: int,
    cols: Dict[str, int], is_month_end: bool,
) -> None:
    """Process a single stock row: update analyst_comment, timestamps, zones, notes."""
    worksheet = stock._worksheet  # set by caller
    info = stock.info

    current = info.get("currentPrice", 0)
    mean = info.get("targetMeanPrice", 0)
    high = info.get("targetHighPrice", 0)
    low = info.get("targetLowPrice", 0)
    analysts = info.get("numberOfAnalystOpinions", 0)
    rec_key = info.get("recommendationKey", "")

    # Always update analyst_comment column
    if "analyst_comment" in cols:
        news_list = []
        if hasattr(stock, "news") and stock.news:
            news_list = stock.news[:5]
        new_summary = format_analyst_summary(rec_key, analysts, news_list)
        worksheet.update_cell(i, cols["analyst_comment"] + 1, new_summary)
        logger.info("%s: Updated analyst_comment to: %s", ticker, new_summary)

    # Always update timestamp
    if "updated" in cols:
        worksheet.update_cell(i, cols["updated"] + 1, datetime.now().isoformat())

    if not is_month_end:
        return

    if not mean or not low or not high:
        logger.debug("%s: No analyst data available for zone update", ticker)
        return

    # Validate target prices
    valid_mean, mean_anomalous = validate_target_price(current, mean)
    if mean_anomalous:
        valid_mean = None
        logger.warning("%s: anomalous mean target %.2f vs price %.2f, skipping zone calc",
            ticker, mean, current)
    if not valid_mean or not low or not high:
        logger.debug("%s: No valid analyst data for zone update", ticker)
        return

    # Unified buy/sell zone calculation (single source of truth from strategy.py)
    buy_zone_str, sell_zone_str = calc_trade_zones(current, valid_mean, high, ma20_override=None)
    if not buy_zone_str or not sell_zone_str:
        buy_zone_str = f"{low * 0.9:.2f},{low * 0.85:.2f}"
        sell_zone_str = f"{valid_mean:.2f},{valid_mean * 1.05:.2f}"

    if "buyzone" in cols:
        worksheet.update_cell(i, cols["buyzone"] + 1, buy_zone_str)
    if "sellzone" in cols:
        worksheet.update_cell(i, cols["sellzone"] + 1, sell_zone_str)

    if "notes" in cols:
        news_list = []
        if hasattr(stock, "news") and stock.news:
            news_list = stock.news[:5]
        summary_note = format_analyst_summary(rec_key, analysts, news_list)
        existing_notes_row = rows[i - 1]
        existing_notes = (str(existing_notes_row[cols["notes"]]).strip()
                          if cols["notes"] < len(existing_notes_row) else '')
        _update_notes_column(worksheet, i, cols["notes"], existing_notes, summary_note, ticker)

    logger.info("%s: Buy=[%s], Sell=[%s] (%d analysts, %s)",
        ticker, buy_zone_str, sell_zone_str, analysts, rec_key)


def update_analyst_targets() -> None:
    """Fetch analyst targets from yfinance and update Google Sheet (Holdings) on month-end."""
    client, sheet_name = get_credentials()
    worksheet = client.open(sheet_name).worksheet("Holdings")

    rows = worksheet.get_all_values()
    if len(rows) < 2:
        logger.warning("Sheet has fewer than 2 rows.")
        return

    headers = [h.strip() for h in rows[0]]
    cols = _find_column_indices(headers)

    if "ticker" not in cols:
        raise ValueError("Ticker column not found.")

    logger.info("Starting monthly analyst targets update (Holdings)...")

    updated_count = 0
    skipped_count = 0

    for i, row in enumerate(rows[1:], start=2):
        if not any(row):
            continue

        ticker = row[cols["ticker"]].strip().upper()
        if not ticker:
            continue

        try:
            stock = yf.Ticker(ticker)
            stock._worksheet = worksheet  # inject for _process_stock_row
            is_month_end = is_last_day_of_month()

            _process_stock_row(stock, ticker, rows, i, cols, is_month_end)
            updated_count += 1

        except Exception as exc:
            logger.error("%s: Error - %s", ticker, exc)
            skipped_count += 1

    logger.info("Monthly update complete: %d updated, %d skipped", updated_count, skipped_count)


def update_taiwan_analyst_targets() -> None:
    """Fetch Taiwan analyst targets and update Google Sheet (Taiwan_Stock) on month-end."""
    client, sheet_name = get_credentials()
    worksheet = client.open(sheet_name).worksheet("Taiwan_Stock")

    rows = worksheet.get_all_values()
    if len(rows) < 2:
        logger.warning("Taiwan_Stock sheet has fewer than 2 rows.")
        return

    headers = [h.strip() for h in rows[0]]
    cols = _find_column_indices(headers)

    if "ticker" not in cols:
        raise ValueError("Ticker column not found in Taiwan_Stock sheet.")

    logger.info("Starting Taiwan monthly analyst targets update...")

    updated_count = 0
    skipped_count = 0

    for i, row in enumerate(rows[1:], start=2):
        if not any(row):
            continue

        ticker_raw = row[cols["ticker"]].strip()
        if not ticker_raw:
            continue

        yf_ticker = ticker_raw + ".TW"
        try:
            stock = yf.Ticker(yf_ticker)
            stock._worksheet = worksheet
            is_month_end = is_last_day_of_month()

            _process_stock_row(stock, yf_ticker, rows, i, cols, is_month_end)
            updated_count += 1

        except Exception as exc:
            logger.error("%s: Error - %s", yf_ticker, exc)
            skipped_count += 1

    logger.info("Taiwan monthly update complete: %d updated, %d skipped", updated_count, skipped_count)


def main() -> None:
    """Main entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("Monthly Analyst Targets Update starting...")
    update_analyst_targets()
    update_taiwan_analyst_targets()
    logger.info("Monthly Analyst Targets Update finished.")


if __name__ == "__main__":
    main()
