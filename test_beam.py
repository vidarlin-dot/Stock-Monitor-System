"""Test yfinance earnings dates for BEAM."""
import yfinance as yf

stock = yf.Ticker("BEAM")

# Try to get calendar/earnings dates
print("=== Calendar ===")
try:
    calendar = stock.calendar
    print(f"Type: {type(calendar)}")
    print(f"Value: {calendar}")
except Exception as e:
    print(f"Error: {e}")

print("\n=== News ===")
try:
    news = stock.news
    for item in news[:5]:
        print(f"Title: {item.get('title', '')}")
        print(f"Publisher: {item.get('publisher', '')}")
        print("---")
except Exception as e:
    print(f"Error: {e}")

print("\n=== Info ===")
try:
    info = stock.info
    print(f"Earnings Date: {info.get('earningsDate')}")
    print(f"Next Earnings: {info.get('nextFiscalYearEnd')}")
except Exception as e:
    print(f"Error: {e}")