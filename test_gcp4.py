"""Try opening by Sheet ID directly."""
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

json_path = r"C:\PROGRAM\美股\stock-monitor-502815-d2e7cdb6f0a2.json"
with open(json_path, 'r', encoding='utf-8-sig') as f:
    creds_dict = json.load(f)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

from google.oauth2.service_account import Credentials
credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

import gspread
client = gspread.authorize(credentials)

# Try opening by ID
sheet_id = "1Zy2eWaRT9lXcA42A_r1yGlVaxOOtYRPSVv5cp0C23hE"
print(f"Trying to open sheet by ID: {sheet_id}")

try:
    sheet = client.open_by_key(sheet_id)
    print(f"[OK] Opened: {sheet.title}")
    
    worksheets = sheet.worksheets()
    print(f"Found {len(worksheets)} worksheet(s):")
    for ws in worksheets:
        print(f"  - {ws.title}")
    
    # Try to read Holdings
    ws = sheet.worksheet("Holdings")
    rows = ws.get_all_values()
    print(f"\n[OK] Holdings sheet has {len(rows)} rows")
    if rows:
        print(f"Headers: {rows[0]}")
        for row in rows[1:]:
            if any(row):
                print(f"  Data: {row}")
    
except Exception as e:
    print(f"[ERROR] {e}")