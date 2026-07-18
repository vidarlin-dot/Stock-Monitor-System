"""Test if GCP credentials work with correct scopes."""
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

print("Testing credentials with correct scopes...")

try:
    from google.oauth2.service_account import Credentials
    credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    print("[OK] Credentials created with scopes")
    
    from google.auth.transport.requests import Request
    credentials.refresh(Request())
    print("[OK] Token refreshed successfully!")
    print(f"[OK] Access Token: {credentials.token[:30]}...")
    
    # Try to access Sheets API
    import gspread
    client = gspread.authorize(credentials)
    print("[OK] gspread client created")
    
    # Try to open the sheet
    sheet = client.open("Portfolio")
    print(f"[OK] Opened sheet: {sheet.title}")
    
    ws = sheet.worksheet("Holdings")
    print(f"[OK] Opened worksheet: {ws.title}")
    
    rows = ws.get_all_values()
    print(f"[OK] Read {len(rows)} rows from Holdings sheet")
    if rows:
        print(f"  Headers: {rows[0]}")
    
    print("\n[SUCCESS] Everything works!")
    
except Exception as e:
    print(f"\n[ERROR] {e}")
    print("\nPossible causes:")
    print("  1. Service Account has no IAM role")
    print("  2. Sheet not shared with Service Account")
    print("  3. Google Sheets API not enabled")