import sys; sys.stdout.reconfigure(encoding='utf-8')
import gspread, json, re
from google.oauth2.service_account import Credentials

KEY_PATH = r'C:\PROGRAM\美股\stock-monitor-502815-d2e7cdb6f0a2.json'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
with open(KEY_PATH, encoding='utf-8') as f:
    creds_dict = json.load(f)
credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
client = gspread.authorize(credentials)
ws = client.open_by_key('1Zy2eWaRT9lXcA42A_r1yGlVaxOOtYRPSVv5cp0C23hE').worksheet('Taiwan_Stock')

# Verify the sheet is correct
all_rows = ws.get_all_values()
headers = [str(h).strip() for h in all_rows[0]]
print('Headers:', headers)
print(f'Total rows: {len(all_rows)}')

# Check first 3 data rows
for row in all_rows[1:4]:
    print(row)

# Verify QFII columns have data
qfii_col = headers.index('外資目標價')
qfii_filled = sum(1 for row in all_rows[1:] if qfii_col < len(row) and row[qfii_col])
print(f'QFII targets filled: {qfii_filled}/{len(all_rows)-1}')
