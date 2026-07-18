import json
import sys
import traceback

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

sheet_id = "1Zy2eWaRT9lXcA42A_r1yGlVaxOOtYRPSVv5cp0C23hE"
try:
    sheet = client.open_by_key(sheet_id)
    print(f"[OK] Opened: {sheet.title}")
except Exception as e:
    print(f"[ERROR] {e}")
    traceback.print_exc()