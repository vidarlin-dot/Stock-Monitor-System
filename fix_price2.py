with open(r'C:\PROGRAM\Stock-Monitor-System\src\main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the average price line - add back the emoji and proper formatting
old_line = '        f"\\u5747\\u50f9\\uff1a${ac:.2f} | "'
new_line = '        f"   {chr(0x1F4B0)} \\u5747\\u50f9\\uff1a${ac:.2f} | "'

content = content.replace(old_line, new_line)

with open(r'C:\PROGRAM\Stock-Monitor-System\src\main.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed average price line with emoji')
