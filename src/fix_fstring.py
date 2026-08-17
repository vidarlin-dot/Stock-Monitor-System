with open("src/daily_taiwan_report.py", "r", encoding="utf-8") as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 'lines.append("- 收盤表現：收盤 {_fmt_price(data.current_price)} 元，")' in line:
        lines[i] = line.replace('lines.append("- 收盤表現：收盤 {_fmt_price(data.current_price)} 元，")', 'lines.append(f"- 收盤表現：收盤 {_fmt_price(data.current_price)} 元，")')
        print(f"Fixed line {i+1}")
with open("src/daily_taiwan_report.py", "w", encoding="utf-8") as f:
    f.writelines(lines)
print("Done")