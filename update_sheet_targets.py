"""Update Google Sheet with analyst targets from yfinance."""
import json
import sys
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

sys.stdout.reconfigure(encoding='utf-8')

# Load service account
json_path = r"C:\PROGRAM\美股\stock-monitor-502815-d2e7cdb6f0a2.json"
with open(json_path, 'r', encoding='utf-8-sig') as f:
    creds_dict = json.load(f)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
client = gspread.authorize(credentials)

# Open sheet
sheet = client.open("Portfolio").worksheet("Holdings")
rows = sheet.get_all_values()
headers = [h.strip() for h in rows[0]]

# Find column indices
ticker_idx = headers.index("代碼") if "代碼" in headers else headers.index("ticker")
buyzone_idx = headers.index("買進區間") if "買進區間" in headers else headers.index("buyzone")
sellzone_idx = headers.index("賣出區間") if "賣出區間" in headers else headers.index("sellzone")

print("Updating Google Sheet with analyst targets...")
print("=" * 80)

# Update each holding
for i, row in enumerate(rows[1:], start=2):
    if not any(row):
        continue
    
    ticker = row[ticker_idx].strip().upper()
    if not ticker:
        continue
    
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        current = info.get('currentPrice', 0)
        mean = info.get('targetMeanPrice', 0)
        high = info.get('targetHighPrice', 0)
        low = info.get('targetLowPrice', 0)
        median = info.get('targetMedianPrice', 0)
        analysts = info.get('numberOfAnalystOpinions', 0)
        
        if not mean or not low or not high:
            print(f"{ticker}: No analyst data available")
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
        sheet.update_cell(i, buyzone_idx + 1, buy_zone_str)
        sheet.update_cell(i, sellzone_idx + 1, sell_zone_str)
        
        print(f"{ticker}:")
        print(f"  Current: ${current:.2f}")
        print(f"  Old Buy Zone -> New: {buy_zone_str}")
        print(f"  Old Sell Zone -> New: {sell_zone_str}")
        print(f"  Analysts: {analysts}, Rating: {info.get('recommendationKey', 'N/A')}")
        print()
        
    except Exception as e:
        print(f"{ticker}: Error - {e}")

print("=" * 80)
print("Update complete!")