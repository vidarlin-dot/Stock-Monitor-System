with open(r'C:\PROGRAM\Stock-Monitor-System\src\main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix empty f-string
content = content.replace('eps_est = f""', 'eps_est = "N/A"')

with open(r'C:\PROGRAM\Stock-Monitor-System\src\main.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed empty f-string')
