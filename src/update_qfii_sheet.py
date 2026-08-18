# -*- coding: utf-8 -*-
"""Update Taiwan_Stock sheet with QFII (外資) target prices from cnyes.

Usage:
    python src/update_qfii_sheet.py
"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, json, re, logging
import requests
import pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone("Asia/Taipei")

# --- Configuration ---
# Path to service account key (relative to project root)
_BASE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_BASE)
KEY_PATH = os.path.join(_PROJECT_ROOT, "..", "stock-monitor-502815-d2e7cdb6f0a2.json")
# Resolve to absolute path
KEY_PATH = os.path.abspath(KEY_PATH)
CACHE_DIR = os.path.join(_BASE, "..", "cache", "cnyes")
os.makedirs(CACHE_DIR, exist_ok=True)

SPREADSHEET_ID = "1Zy2eWaRT9lXcA42A_r1yGlVaxOOtYRPSVv5cp0C23hE"
WORKSHEET_NAME = "Taiwan_Stock"

CNYES_URL = "https://www.cnyes.com/twstock/board/ratediff.aspx"
CNYES_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

# Columns to write (1-based indices for gspread)
QFII_COLS = {"外資目標價": 11, "外資評等": 12, "外資上調幅度": 13}


def fetch_cnyes() -> dict:
    """Fetch QFII ratings from cnyes, cache, return latest-per-ticker dict."""
    cached_path = os.path.join(CACHE_DIR, "latest.json")
    if os.path.exists(cached_path):
        try:
            with open(cached_path, encoding="utf-8") as f:
                cached = json.load(f)
            logger.info("Loaded %d cached QFII ratings", len(cached))
            return cached
        except Exception as e:
            logger.warning("Cache load failed: %s", e)

    logger.info("Fetching cnyes ratings from %s", CNYES_URL)
    resp = requests.get(CNYES_URL, headers=CNYES_HEADERS, timeout=20, verify=False)
    content = resp.content.decode("utf-8", errors="replace")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.DOTALL | re.IGNORECASE)
    ratings = []
    for row in rows[1:]:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if len(cells) >= 9 and cells[0] and cells[0] != "評等日期":
            code_name = cells[1]
            m = re.match(r"(\d+)-?(.*)", code_name.strip())
            ticker = m.group(1) if m else code_name.strip()
            ratings.append({
                "date": cells[0], "ticker": ticker,
                "broker": cells[2], "new_rating": cells[5],
                "new_target": cells[7], "current_price": cells[8],
            })
    # Deduplicate: keep latest per ticker
    latest: dict = {}
    for r in ratings:
        t = r["ticker"]
        if t not in latest or r["date"] > latest[t]["date"]:
            latest[t] = r
    with open(cached_path, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    logger.info("Fetched and saved %d unique QFII ratings", len(latest))
    return latest


def get_worksheet():
    """Connect to Google Sheets and return the Taiwan_Stock worksheet."""
    import gspread
    from google.oauth2.service_account import Credentials
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    with open(KEY_PATH, encoding="utf-8") as f:
        creds_dict = json.load(f)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(credentials)
    return client.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)


def update_qfii_in_sheet():
    """Main: fetch cnyes, read sheet, write QFII data back."""
    import time
    ws = get_worksheet()

    # Read all values
    all_rows = ws.get_all_values()
    headers = [str(h).strip() for h in all_rows[0]]
    logger.info("Sheet headers: %s", headers)
    logger.info("Total rows: %d", len(all_rows))

    # Find column indices
    col_map = {}
    for name in ["代碼"] + list(QFII_COLS.keys()):
        for i, h in enumerate(headers):
            if h == name:
                col_map[name] = i + 1  # 1-based
                break
    if "代碼" not in col_map:
        logger.error("代碼 column not found in headers: %s", headers)
        sys.exit(1)

    # Fetch cnyes ratings
    cnyes = fetch_cnyes()

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

    # Ensure 追蹤 column header exists (column B)
    if "追蹤" not in headers:
        ws.update("B1", [["追蹤"]])
        logger.info("Added 追蹤 column header")

    logger.info("Done.")


if __name__ == "__main__":
    update_qfii_in_sheet()
