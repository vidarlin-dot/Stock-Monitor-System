"""Debug sheet access."""
import json
import os
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

print("Listing all accessible spreadsheets...")
try:
    spreadsheets = client.list_spreadsheet_files()
    print(f"Found {len(spreadsheets)} spreadsheet(s):")
    for s in spreadsheets:
        print(f"  - {s['id']}: {s['name']}")
except Exception as e:
    print(f"Error listing spreadsheets: {e}")