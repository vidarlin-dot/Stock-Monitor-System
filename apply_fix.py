import sys
sys.stdout.reconfigure(encoding='utf-8')
with open(r'C:\PROGRAM\Stock-Monitor-System\src\main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Remove lines 244-247 (indices 243-246) - 均價 and 損益 block
del lines[243:247]
print('Removed 均價 and 損益 lines')

# Fix buy_zone N/A
for i, line in enumerate(lines):
    if 'buy_zone_str = "N/A"' in line:
        lines[i] = line.replace('buy_zone_str = "N/A"', 'buy_zone_str = f"${cp * 0.95:.2f}, ${cp * 0.97:.2f}"')
        print(f'Fixed buy_zone at line {i+1}')
    if 'sell_zone_str = "N/A"' in line:
        lines[i] = line.replace('sell_zone_str = "N/A"', 'sell_zone_str = f"${cp * 1.05:.2f}, ${cp * 1.08:.2f}"')
        print(f'Fixed sell_zone at line {i+1}')
    if '\\u5e08\\u5e2b\\u8b70' in line:
        lines[i] = line.replace('\\u5e08\\u5e2b\\u8b70', '\\u5e2b\\u5efa\\u8b70')
        print(f'Fixed unicode at line {i+1}')

with open(r'C:\PROGRAM\Stock-Monitor-System\src\main.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
print('Done')
