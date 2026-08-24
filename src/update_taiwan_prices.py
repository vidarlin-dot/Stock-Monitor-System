#!/usr/bin/env python3
import json, time, gspread, yfinance as yf
from google.oauth2.service_account import Credentials

KEY_PATH = r'C:\PROGRAM\美股\stock-monitor-502815-d2e7cdb6f0a2.json'
SPREAD_ID = '1Zy2eWaRT9lXcA42A_r1yGlVaxOOtYRPSVv5cp0C23hE'
ALL_TICKERS = ['6669','3017','7610','8069','4906','5009','5536','3056','3037','3712','2344','4979','6147','6121','8027','3231','6676','1717','6405','1815','1303','3062','2317','3031','2356','1712','1229','1904','2002','1101','2616','2618','2880','2891','2881','2884','2890','3048','3162','3088','3711','4904','2303','2812','2834','2851','2449','2852','2883','2886','2887','2889','2892','3036','3211','3289','3455','6282','7530','8074','3260','7856','3481','3443','3661','6664','6156','5289','6515','6598','8299']

def fetch_prices():
    results = {}
    for t in ALL_TICKERS:
        try:
            stock = yf.Ticker(f'{t}.TW')
            info = stock.info
            price = info.get('currentPrice') or info.get('previousClose') or info.get('regularMarketPrice')
            target = info.get('targetMeanPrice') or info.get('targetHighPrice')
            rec = info.get('recommendationKey') or ''
            name = info.get('longName') or info.get('shortName') or ''
            results[t] = {'price': price, 'target': target, 'rec': rec, 'name': name}
            print(f'OK {t}: price={price}, target={target}, rec={rec}')
        except Exception as e:
            results[t] = {'price': None, 'error': str(e)}
            print(f'ERR {t}: {e}')
        time.sleep(0.1)
    return results

def calc_ranges(price, target):
    if not price or price <= 0: return None, None
    if target and target > 0:
        buy_low, buy_high = round(price*0.90), round(price*0.97)
        sell_low = round(target*0.90) if target > price*1.1 else round(price*1.10)
        sell_high = round(target*1.05) if target > price*1.1 else round(price*1.20)
    else:
        buy_low, buy_high = round(price*0.85), round(price*0.95)
        sell_low, sell_high = round(price*1.10), round(price*1.25)
    return f'{buy_low}～{buy_high}', f'{sell_low}～{sell_high}'

with open(KEY_PATH, encoding='utf-8') as f:
    key = json.load(f)
creds = Credentials.from_service_account_info(key, scopes=['https://www.googleapis.com/auth/spreadsheets'])
client = gspread.authorize(creds)
ws = client.open_by_key(SPREAD_ID).worksheet('Taiwan_Stock')
rows = ws.get_all_values()
header = rows[0]
print('Headers:', header)
buy_col = sell_col = None
for i, h in enumerate(header):
    if h and '買進' in h: buy_col = i
    if h and '賣出' in h: sell_col = i
print(f'Buy col: {buy_col}, Sell col: {sell_col}')

results = fetch_prices()
updates = []
for row_idx, row in enumerate(rows[1:], start=2):
    if not row or not row[0]: continue
    ticker = ''.join(c for c in row[0].strip() if c.isdigit())
    if not ticker: continue
    if ticker in results and results[ticker].get('price'):
        price = results[ticker]['price']
        target = results[ticker].get('target')
        buy_r, sell_r = calc_ranges(price, target)
        if buy_r and sell_r:
            updates.append((row_idx, buy_col+1, buy_r, sell_col+1, sell_r))
            print(f'  Row {row_idx} ({ticker}): buy={buy_r}, sell={sell_r}')

for row_idx, bc, bv, sc, sv in updates:
    if bc: ws.update_cell(row_idx, bc, bv)
    if sc: ws.update_cell(row_idx, sv)
    time.sleep(0.2)
print(f'Updated {len(updates)} rows')