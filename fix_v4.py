# Fix main.py
import sys
sys.stdout.reconfigure(encoding="utf-8")

with open(r"src\main.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix 1: Remove А基 and 穕痲 lines
lines = content.split("\n")
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    # Skip lines with А基 and 穕痲
    if "А基" in line or ("穕痲" in line and "total_pnl" in line):
        # Skip this line and any continuation
        i += 1
        while i < len(lines) and not lines[i].strip().startswith("lines.append"):
            i += 1
        continue
    new_lines.append(line)
    i += 1

content = "\n".join(new_lines)
print("Fixed 1: Removed А基 and 穕痲")

# Fix 2: Generate buy zones if empty  
old = 'buy_zone_str = "N/A"'
new = 'buy_zone_str = f"${cp * 0.95:.2f}, ${cp * 0.97:.2f}"'
content = content.replace(old, new)
print("Fixed 2: Buy zone N/A removed")

# Fix 3: Fix unicode for だ猂畍某
# Current: \u5206\u6790\u5e08\u5e2b\u8b70 (?畍某 - wrong)
# Should be: \u5206\u6790\u5e2b\u5efa\u8b70 (だ猂畍某)
old_unicode = "\\u5206\\u6790\\u5e08\\u5e2b\\u8b70"
new_unicode = "\\u5206\\u6790\\u5e2b\\u5efa\\u8b70"
content = content.replace(old_unicode, new_unicode)
print("Fixed 3: Unicode corrected")

with open(r"src\main.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Done")
