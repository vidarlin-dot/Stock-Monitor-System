"""Quick diagnostic script to test GCP authentication."""
import json
import os
import sys

# Force UTF-8 encoding
sys.stdout.reconfigure(encoding='utf-8')

json_path = r"C:\PROGRAM\美股\stock-monitor-502815-d2e7cdb6f0a2.json"

if not os.path.exists(json_path):
    print(f"File not found: {json_path}")
    exit(1)

with open(json_path, 'r', encoding='utf-8-sig') as f:
    creds = json.load(f)

print("[OK] JSON loaded successfully")
print(f"  Project ID: {creds.get('project_id')}")
print(f"  Client Email: {creds.get('client_email')}")

try:
    from google.oauth2.service_account import Credentials
    credentials = Credentials.from_service_account_info(creds)
    print(f"\n[OK] Credentials created")
    
    from google.auth.transport.requests import Request
    credentials.refresh(Request())
    print(f"[OK] Token refreshed successfully!")
    print(f"  Token: {credentials.token[:30]}...")
except Exception as e:
    print(f"\n[ERROR] {e}")
    print("\nPossible causes:")
    print("  1. Service Account has no IAM role")
    print("  2. Google Sheets API not enabled")
    print("  3. JSON file is outdated")