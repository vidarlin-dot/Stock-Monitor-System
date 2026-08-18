import sys; sys.stdout.reconfigure(encoding='utf-8')
import gspread, json, re, time
from google.oauth2.service_account import Credentials

KEY_PATH = r'C:\PROGRAM\美股\stock-monitor-502815-d2e7cdb6f0a2.json'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
with open(KEY_PATH, encoding='utf-8') as f:
    creds_dict = json.load(f)
credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
client = gspread.authorize(credentials)
ws = client.open_by_key('1Zy2eWaRT9lXcA42A_r1yGlVaxOOtYRPSVv5cp0C23hE').worksheet('Taiwan_Stock')

# Load cnyes cache
cnyes_path = r'C:\PROGRAM\美股\Stock-Monitor-System\cache\cnyes\latest.json'
with open(cnyes_path, encoding='utf-8') as f:
    cnyes = json.load(f)

# Read all rows to understand structure
all_rows = ws.get_all_values()
print(f'Total rows: {len(all_rows)}')
print(f'Headers: {all_rows[0]}')

# For each data row, find the stock code and restore + write QFII
for row_idx, row in enumerate(all_rows[1:], start=2):
    # Find stock code - look for pattern like "XXXX Name"
    code = ''
    for cell in row:
        s = str(cell).strip()
        m = re.match(r'^(\d{4})\s+', s)
        if m:
            code = m.group(1)
            break
        # Also check if the whole cell is just a code
        m2 = re.match(r'^(\d{4})$', s)
        if m2:
            code = m2.group(1)
            break
    
    if not code:
        print(f'Row {row_idx}: no code found, row={row[:3]}')
        continue
    
    # Restore column A if empty
    if not row or not str(row[0]).strip():
        ws.update_cell(row_idx, 1, code)
        time.sleep(0.1)
    
    # Write QFII data
    if code in cnyes:
        r = cnyes[code]
        tgt = r.get('new_target', '')
        rat = r.get('new_rating', '')
        try:
            tgt_f = float(tgt) if tgt else 0
            price_f = float(r.get('current_price', '') or 0)
            if price_f > 0 and tgt_f > 0:
                ups = (tgt_f - price_f) / price_f * 100
                ups_str = f'{ups:+.1f}%'
            else:
                ups_str = ''
        except:
            ups_str = ''
        ws.update_cell(row_idx, 11, tgt)  # K
        time.sleep(0.05)
        ws.update_cell(row_idx, 12, rat)  # L
        time.sleep(0.05)
        ws.update_cell(row_idx, 13, ups_str)  # M
        time.sleep(0.05)
        print(f'Row {row_idx}: {code} -> tgt={tgt} rat={rat} ups={ups_str}')
    else:
        ws.update_cell(row_idx, 11, '')
        time.sleep(0.05)
        ws.update_cell(row_idx, 12, '')
        time.sleep(0.05)
        ws.update_cell(row_idx, 13, '')
        time.sleep(0.05)
        print(f'Row {row_idx}: {code} -> no QFII')

print('Done!')
