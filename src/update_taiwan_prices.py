#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch fresh Taiwan stock prices and analyst ratings, update Google Sheet.

Runs BEFORE daily_taiwan_report.py to ensure sheet data is current.
Uses GCP_SERVICE_ACCOUNT_JSON env var (set from GitHub secrets).
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os, json, re, time, logging
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))

def _get_service_account_credentials():
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    sa_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "").strip()
    if sa_json:
        sa_json = sa_json.lstrip(chr(0xfeff))
        creds_dict = json.loads(sa_json)
        return Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    _PROJECT_ROOT = os.path.dirname(_BASE)
    KEY_PATH = os.path.abspath(os.path.join(_PROJECT_ROOT, "..", "stock-monitor-502815-d2e7cdb6f0a2.json"))
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, encoding="utf-8") as f:
            creds_dict = json.load(f)
        return Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    raise ValueError("GCP_SERVICE_ACCOUNT_JSON not set and key file not found")

SPREADSHEET_ID = "1Zy2eWaRT9lXcA42A_r1yGlVaxOOtYRPSVv5cp0C23hE"
WORKSHEET_NAME = "Taiwan_Stock"

ALL_TICKERS = [
    '6669','3017','7610','8069','4906','5009','5536','3056','3037','3712',
    '2344','4979','6147','6121','8027','3231','6676','1717','6405','1815',
    '1303','3062','2317','3031','2356','1712','1229','1904','2002','1101',
    '2616','2618','2880','2891','2881','2884','2890','3048','3162','3088',
    '3711','4904','2303','2812','2834','2851','2449','2852','2883','2886',
    '2887','2889','2892','3036','3211','3289','3455','6282','7530','8074',
    '3260','7856','3481','3443','3661','6664','6156','5289','6515','6598','8299'
]

REC_LABELS = {
    'strong_buy': '強力買進',
    'buy': '買進',
    'outperform': '優於大盤',
    'hold': '持有/觀望',
    'underperform': '落後大盤',
    'sell': '賣出',
    'neutral': '中立',
}

MANUAL_PRICES = {
    '5289': {'price': 1570, 'target': 1800, 'rec': 'buy', 'analysts': 2},
    '8299': {'price': 2100, 'target': 2750, 'rec': 'outperform', 'analysts': 3},
}


def fetch_from_yahoo():
    results = {}
    for t in ALL_TICKERS:
        try:
            stock = yf.Ticker(f'{t}.TW')
            info = stock.info
            price = info.get('currentPrice') or info.get('previousClose') or info.get('regularMarketPrice')
            target = info.get('targetMeanPrice') or info.get('targetHighPrice')
            rec = info.get('recommendationKey') or ''
            analysts = info.get('numberOfAnalystOpinions') or 0
            results[t] = {'price': price, 'target': target, 'rec': rec, 'analysts': analysts}
            status = 'OK' if price else 'NO_PRICE'
            logger.info('%s %s: price=%s target=%s rec=%s', status, t, price, target, rec)
        except Exception as e:
            results[t] = {'price': None, 'target': None, 'rec': '', 'analysts': None}
            logger.warning('ERR %s: %s', t, e)
        time.sleep(0.08)
    return results


def calc_ranges(price, target):
    if not price or price <= 0:
        return None, None
    if target and target > 0:
        buy_low = round(price * 0.90)
        buy_high = round(price * 0.97)
        if target > price * 1.1:
            sell_low = round(target * 0.90)
            sell_high = round(target * 1.05)
        else:
            sell_low = round(price * 1.10)
            sell_high = round(price * 1.20)
    else:
        buy_low = round(price * 0.85)
        buy_high = round(price * 0.95)
        sell_low = round(price * 1.10)
        sell_high = round(price * 1.25)
    return f'{buy_low}～{buy_high}', f'{sell_low}～{sell_high}'


def build_j_text(rec, analysts, target):
    label = REC_LABELS.get(rec, rec) if rec else ''
    if not label:
        return ''
    text = f'{label} ({analysts or 0}位)'
    if target:
        text += f', 目標{int(target)}'
    return text


def update_sheet(gsheet_ws, results):
    rows = gsheet_ws.get_all_values()
    header = rows[0]
    logger.info('Headers: %s', header)

    col_map = {}
    for i, h in enumerate(header):
        if h and '買進' in h: col_map['buy'] = i
        if h and '賣出' in h: col_map['sell'] = i
        if h and ('土洋' in h or '評等' in h): col_map['j'] = i
        if h and '目標價' in h: col_map['k'] = i

    logger.info('Col map: %s', col_map)

    updates = []
    j_updates = []

    for row_idx, row in enumerate(rows[1:], start=2):
        if not row or not row[0]:
            continue
        ticker = ''.join(c for c in row[0].strip() if c.isdigit())
        if not ticker:
            continue

        price_data = results.get(ticker, {})
        price = price_data.get('price')
        target = price_data.get('target')
        rec = price_data.get('rec', '')
        analysts = price_data.get('analysts')

        if ticker in MANUAL_PRICES:
            price = MANUAL_PRICES[ticker]['price']
            target = MANUAL_PRICES[ticker].get('target')
            rec = MANUAL_PRICES[ticker].get('rec', rec)
            analysts = MANUAL_PRICES[ticker].get('analysts', analysts)

        buy_r, sell_r = calc_ranges(price, target)
        if buy_r:
            cur_buy = row[col_map.get('buy', 4)] if 'buy' in col_map else ''
            cur_sell = row[col_map.get('sell', 5)] if 'sell' in col_map else ''
            if cur_buy != buy_r or cur_sell != sell_r:
                updates.append((row_idx, col_map.get('buy', 4) + 1, buy_r))
                updates.append((row_idx, col_map.get('sell', 5) + 1, sell_r))
                logger.info('  BUY/SELL Row %d (%s): %s / %s', row_idx, ticker, buy_r, sell_r)

        if rec or target:
            j_text = build_j_text(rec, analysts, target)
            k_text = str(int(target)) if target else ''
            cur_j = row[col_map.get('j', 9)] if 'j' in col_map else ''
            cur_k = row[col_map.get('k', 10)] if 'k' in col_map else ''
            if cur_j != j_text or cur_k != k_text:
                j_updates.append((row_idx, col_map.get('j', 9) + 1, j_text))
                j_updates.append((row_idx, col_map.get('k', 10) + 1, k_text))
                logger.info('  J/K Row %d (%s): J=%s K=%s', row_idx, ticker, j_text, k_text)

    for row_idx, col, val in updates:
        gsheet_ws.update_cell(row_idx, col, val)
        time.sleep(0.15)
    logger.info('Updated %d buy/sell cells', len(updates) // 2)

    for row_idx, col, val in j_updates:
        gsheet_ws.update_cell(row_idx, col, val)
        time.sleep(0.15)
    logger.info('Updated %d J/K cells', len(j_updates) // 2)


def main():
    logger.info('Taiwan price/rating sync starting...')
    results = fetch_from_yahoo()
    ok_count = sum(1 for v in results.values() if v.get('price'))
    logger.info('Fetched %d/%d stocks with prices', ok_count, len(results))

    creds = _get_service_account_credentials()
    client = gspread.authorize(creds)
    ws = client.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)
    update_sheet(ws, results)

    data_dir = os.path.join(_BASE, '..', 'data')
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, 'fresh_prices.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info('Saved fresh_prices.json')
    logger.info('Taiwan price/rating sync done.')


if __name__ == '__main__':
    main()