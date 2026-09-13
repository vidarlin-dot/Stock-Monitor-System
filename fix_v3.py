# Fix main.py - comprehensive fix
import sys
sys.stdout.reconfigure(encoding='utf-8')

with open(r'src\main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix 1: Remove А基 and 穕痲 lines
# The lines are:
#     lines.append(
#         f"   {chr(0x1F4B0)} \u5747\u50f9\uff1a${ac:.2f} | "
#         f"\u640d\u76ey\uff1a${total_pnl:+,.2f} ({pnl_pct:+.1f}%)")
#     lines.append(...)
# We need to remove the first append and keep only the current price line

# Find and remove the multi-line append with А基
import re
# Pattern to match the А基 line block
pattern = r'    lines\.append\(\s*\n\s*f"   \{chr\(0x1F4B0\)\} \\u5747\\u50f9\\uff1a\$\{ac:'.replace('{', '\\{').replace('}', '\\}') + r'\\.2f\} \\| ".*?\\)\s*\n'
# This is getting complex, let's do it line by line

lines = content.split('\n')
new_lines = []
skip_next = False
i = 0
while i < len(lines):
    line = lines[i]
    # Skip the А基 line
    if 'А基' in line and 'chr(0x1F4B0)' in line:
        # Skip this line and the next continuation line
        i += 1
        # Skip the next line (穕痲)
        if i < len(lines) and '穕痲' in lines[i]:
            i += 1
        # Skip the closing paren line
        if i < len(lines) and lines[i].strip() == ')':
            i += 1
        continue
    new_lines.append(line)
    i += 1

content = '\n'.join(new_lines)
print('Fixed 1: Removed А基 and 穕痲')

# Fix 2: Generate buy zones if empty
old_buy = 'buy_zone_str = "N/A"'
new_buy = 'buy_zone_str = f"${cp * 0.95:.2f}, ${cp * 0.97:.2f}"'
content = content.replace(old_buy, new_buy)
print('Fixed 2: Buy zone N/A -> generated')

# Fix 3: だ猂畍某 unicode
# Current has \\u5e08\\u5e2b which is ?畍 (wrong)
# Should be \\u5e2b for 畍
old_analyst = '\\u5206\\u6790\\u5e08\\u5e2b\\u8b70'
new_analyst = '\\u5206\\u6790\\u5e2b\\u5efa\\u8b70'
content = content.replace(old_analyst, new_analyst)
print('Fixed 3: だ猂畍某 unicode')

with open(r'src\main.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('File saved')
