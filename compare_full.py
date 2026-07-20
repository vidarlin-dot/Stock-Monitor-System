"""Complete comparison: Google Sheet targets vs yfinance analyst targets."""
import yfinance as yf
import sys

sys.stdout.reconfigure(encoding='utf-8')

# Your Google Sheet targets
sheet_targets = {
    "BEAM": {"buy": [28.00, 15.60], "sell": [38.50, 50.00], "avg_cost": 30.63},
    "NVDA": {"buy": [165.00, 150.00], "sell": [210.00, 240.00], "avg_cost": 184.65},
    "GOOG": {"buy": [290.00, 270.00], "sell": [360.00, 400.00], "avg_cost": 317.49},
    "TSM": {"buy": [360.00, 330.00], "sell": [460.00, 520.00], "avg_cost": 402.43},
    "AMD": {"buy": [320.00, 290.00], "sell": [420.00, 480.00], "avg_cost": 362.00},
}

print("=" * 100)
print("Google Sheet 目標區間 vs yfinance 分析師目標價 對比報告")
print("=" * 100)

for ticker, sheet in sheet_targets.items():
    print(f"\n{'-'*100}")
    print(f"STOCK: {ticker}")
    print(f"{'-'*100}")
    
    stock = yf.Ticker(ticker)
    info = stock.info
    
    current = info.get('currentPrice', 0)
    mean = info.get('targetMeanPrice', 0)
    high = info.get('targetHighPrice', 0)
    low = info.get('targetLowPrice', 0)
    median = info.get('targetMedianPrice', 0)
    analysts = info.get('numberOfAnalystOpinions', 0)
    rec = info.get('recommendationKey', '')
    
    print(f"  Current Price: ${current:.2f}")
    print(f"  Your Avg Cost: ${sheet['avg_cost']:.2f}")
    print(f"  P&L: {(current - sheet['avg_cost'])/sheet['avg_cost']*100:+.1f}%")
    
    print(f"\n  YOUR GOOGLE SHEET TARGETS:")
    print(f"     Buy Zone: ${sheet['buy'][0]:.2f}, ${sheet['buy'][1]:.2f}")
    print(f"     Sell Zone: ${sheet['sell'][0]:.2f}, ${sheet['sell'][1]:.2f}")
    
    print(f"\n  YFINANCE ANALYST TARGETS ({analysts} analysts, Rating: {rec}):")
    if mean > 0:
        print(f"     Mean Target: ${mean:.2f} (Upside {((mean-current)/current)*100:+.1f}%)")
    if high > 0:
        print(f"     High Target: ${high:.2f} (Upside {((high-current)/current)*100:+.1f}%)")
    if low > 0:
        print(f"     Low Target: ${low:.2f} (Downside {((low-current)/current):+.1f}%)")
    if median > 0:
        print(f"     Median: ${median:.2f} (Upside {((median-current)/current)*100:+.1f}%)")
    
    print(f"\n  COMPARISON ANALYSIS:")
    
    # Is current price within your buy zone?
    if current <= sheet['buy'][0]:
        print(f"     [OK] Current price已进入您的買進區間 (<= ${sheet['buy'][0]:.2f})")
    elif current >= sheet['sell'][0]:
        print(f"     [WARNING] Current price已達您的賣出區間 (>= ${sheet['sell'][0]:.2f})")
    else:
        print(f"     [NORMAL] Current price在您設定的區間內")
    
    # Compare your buy zone vs analyst low
    if low > 0:
        if low < sheet['buy'][0]:
            print(f"     [INSIGHT] 分析師最低目標(${low:.2f}) < 您的買進區間(${sheet['buy'][0]:.2f})")
            print(f"               分析師認為可能再跌 {((sheet['buy'][0] - low)/low)*100:.1f}%")
        else:
            print(f"     [SAFE] 您的買進區間比分析師最低目標更保守")
    
    # Compare your sell zone vs analyst high
    if high > 0:
        if high > sheet['sell'][0]:
            print(f"     [OPPORTUNITY] 分析師最高目標(${high:.2f}) > 您的賣出區間(${sheet['sell'][0]:.2f})")
            print(f"                   還有 {((high - sheet['sell'][0])/sheet['sell'][0])*100:.1f}% 上漲空間")
        else:
            print(f"     [AGGRESSIVE] 您的賣出目標比分析師最高目標更高")