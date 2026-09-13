with open(r'C:\PROGRAM\Stock-Monitor-System\src\main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip_next = False
for i, line in enumerate(lines):
    if skip_next:
        skip_next = False
        continue
    
    # Skip the 持股 line
    if '持股' in line and 'shares_count' in line:
        # This line will be removed, merge with next line
        if i + 1 < len(lines):
            next_line = lines[i + 1]
            # Check if next line starts with f"平均價
            if '平均價' in next_line:
                # Merge: remove the pipe from end
                next_line = next_line.replace(' | \n', '\n')
                new_lines.append(next_line)
                skip_next = True
                continue
        continue
    
    new_lines.append(line)

with open(r'C:\PROGRAM\Stock-Monitor-System\src\main.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print('File updated - 持股 removed')
