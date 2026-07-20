"""Test TradingView technical analysis data."""
import requests
from bs4 import BeautifulSoup

ticker = "NVDA"
url = f"https://www.tradingview.com/symbols/{ticker}/technicals/"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

response = requests.get(url, headers=headers, timeout=10)
soup = BeautifulSoup(response.text, "html.parser")

# Find technical summary box
summary_box = soup.find("div", class_="js-technicalsSummaryContainer")
if summary_box:
    print("=== Technical Summary ===")
    print(summary_box.get_text(strip=True))
else:
    print("No summary box found")
    # Try to find any div with technical data
    all_divs = soup.find_all("div", class_=True)
    for div in all_divs[:10]:
        cls = div.get("class", [])
        if any("technical" in str(c).lower() for c in cls):
            print(f"Found: {cls} - {div.get_text(strip=True)[:100]}")

# Find recommendation table
rec_table = soup.find("table", class_=lambda x: x and "technical" in str(x).lower() if x else False)
if rec_table:
    print("\n=== Recommendation Table ===")
    for row in rec_table.find_all("tr")[:5]:
        cells = row.find_all(["td", "th"])
        if cells:
            print([c.get_text(strip=True) for c in cells])