"""Compare Google Sheet targets vs yfinance analyst targets."""
import yfinance as yf

tickers = ["BEAM", "NVDA", "GOOG", "TSM", "AMD"]

for t in tickers:
    print(f"\n{'='*60}")
    print(f"Ticker: {t}")
    print('='*60)
    
    stock = yf.Ticker(t)
    info = stock.info
    
    # Key target data
    print(f"Current Price: ${info.get('currentPrice', 'N/A')}")
    print(f"Target Mean: ${info.get('targetMeanPrice', 'N/A')}")
    print(f"Target High: ${info.get('targetHighPrice', 'N/A')}")
    print(f"Target Low: ${info.get('targetLowPrice', 'N/A')}")
    print(f"Target Median: ${info.get('targetMedianPrice', 'N/A')}")
    print(f"Number of Analysts: {info.get('numberOfAnalystOpinions', 'N/A')}")
    print(f"Recommendation: {info.get('recommendationKey', 'N/A')}")
    print(f"Recommendation Trend: {info.get('recommendationTrend', 'N/A')}")
    
    # Calculate upside/downside
    if info.get('currentPrice') and info.get('targetMeanPrice'):
        upside = ((info['targetMeanPrice'] - info['currentPrice']) / info['currentPrice']) * 100
        print(f"Upside/Downside: {upside:+.1f}%")