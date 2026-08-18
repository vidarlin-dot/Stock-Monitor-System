# -*- coding: utf-8 -*-
"""Update Taiwan_Stock sheet with QFII (外資) target prices from cnyes.

Usage:
    python src/update_qfii_sheet.py
"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, json, re, logging
import pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone("Asia/Taipei")

_BASE = os.path.dirname(os.path.abspath(__file__))

def _get_service_account_credentials():
    """Get service account credentials from env var or file."""
    import json
    from google.oauth2.service_account import Credentials
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    
    # Try environment variable first
    sa_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "").strip()
    if sa_json:
        # Remove BOM if present
        sa_json = sa_json.lstrip(chr(0xfeff))
        creds_dict = json.loads(sa_json)
        return Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    
    # Fallback to file
    _PROJECT_ROOT = os.path.dirname(_BASE)
    KEY_PATH = os.path.abspath(os.path.join(_PROJECT_ROOT, "..", "stock-monitor-502815-d2e7cdb6f0a2.json"))
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, encoding="utf-8") as f:
            creds_dict = json.load(f)
        return Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    
    raise ValueError("GCP_SERVICE_ACCOUNT_JSON not set and key file not found")

SPREADSHEET_ID = "1Zy2eWaRT9lXcA42A_r1yGlVaxOOtYRPSVv5cp0C23hE"
WORKSHEET_NAME = "Taiwan_Stock"

QFII_COLS = {"外資目標價": 11, "外資評等": 12, "外資上調幅度": 13}


def get_worksheet():
    """Connect to Google Sheets and return the Taiwan_Stock worksheet."""
    import gspread
    credentials = _get_service_account_credentials()
    client = gspread.authorize(credentials)
    return client.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)


def update_qfii_in_sheet():
    """Main: fetch cnyes ratings, read sheet, write QFII data back."""
    # Import the new scraper
    sys.path.insert(0, _BASE)
    from fetch_cnyes import load_cnyes_ratings
    cnyes = load_cnyes_ratings()
    logger.info("Loaded %d QFII ratings", len(cnyes))

    ws = get_worksheet()
    all_rows = ws.get_all_values()
    headers = [str(h).strip() for h in all_rows[0]]
    logger.info("Sheet headers: %s", headers)
    logger.info("Total rows: %d", len(all_rows))

    # Find column indices
    col_map = {}
    for name in ["代碼"] + list(QFII_COLS.keys()):
        for i, h in enumerate(headers):
            if h == name:
                col_map[name] = i + 1
                break
    if "代碼" not in col_map:
        logger.error("代碼 column not found in headers: %s", headers)
        sys.exit(1)

    # Prepare QFII data for all rows
    qfii_data = []
    for row_idx, row in enumerate(all_rows[1:], start=2):
        code_cell = row[col_map["代碼"] - 1] if col_map["代碼"] - 1 < len(row) else ""
        m = re.match(r"(\d+)", str(code_cell).strip())
        if not m:
            qfii_data.append(["", "", ""])
            continue
        ticker = m.group(1)
        if ticker in cnyes:
            r = cnyes[ticker]
            tgt = r.get("new_target", "")
            rat = r.get("new_rating", "")
            try:
                tgt_f = float(tgt) if tgt else 0
                price_f = float(r.get("current_price", "") or 0)
                ups = (tgt_f - price_f) / price_f * 100 if price_f > 0 else 0
                ups_str = f"{ups:+.1f}%" if tgt_f > 0 else ""
            except (ValueError, TypeError):
                ups_str = ""
            qfii_data.append([tgt, rat, ups_str])
            logger.info("  %s: target=%s rating=%s upside=%s", ticker, tgt, rat, ups_str)
        else:
            qfii_data.append(["", "", ""])

    # Batch write QFII columns (K, L, M)
    if qfii_data:
        ws.update("K2:M{}".format(len(all_rows)), qfii_data)
        logger.info("Wrote QFII data for %d rows", len(qfii_data))

    # Ensure 追蹤 column header exists
    if "追蹤" not in headers:
        ws.update("B1", [["追蹤"]])
        logger.info("Added 追蹤 column header")

    logger.info("Done.")


if __name__ == "__main__":
    update_qfii_in_sheet()
