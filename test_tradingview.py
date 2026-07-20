"""Test TradingView data fetching."""
import requests
from bs4 import BeautifulSoup

ticker = "NVDA"
url = f"https://www.tradingview.com/symbols/{ticker}/technicals/"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

try:
    response = requests.get(url, headers=headers, timeout=10)
    print(f"Status: {response.status_code}")
    print(f"Content length: {len(response.text)}")
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Look for technical summary
    summary = soup.find("div", class_=lambda x: x and "technicalSummary" in x if x else False)
    if summary:
        print(f"\nTechnical Summary: {summary.get_text(strip=True)[:200]}")
    
    # Look for recommendation
    rec = soup.find("div", class_=lambda x: x and "recommendation" in x.lower() if x else False)
    if rec:
        print(f"\nRecommendation: {rec.get_text(strip=True)[:200]}")
    
    # Look for any table with data
    tables = soup.find_all("table")
    print(f"\nFound {len(tables)} tables")
    
    # Print first 500 chars of HTML for debugging
    print("\n--- HTML Preview ---")
    print(response.text[:500])
    
except Exception as e:
    print(f"Error: {e}")