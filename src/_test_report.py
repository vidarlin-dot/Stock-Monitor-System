import sys; sys.stdout.reconfigure(encoding='utf-8')
import os
os.environ['GCP_SERVICE_ACCOUNT_JSON'] = open(r'C:\PROGRAM\美股\stock-monitor-502815-d2e7cdb6f0a2.json', encoding='utf-8').read()
os.environ['SHEET_NAME'] = 'Portfolio'
import json, time
from config import GoogleSheetsManager
from taiwan_market_data import (
    StockMarketData,
    compute_focus_score,
    is_taiwan_trading_day,
    load_cnyes_ratings,
    merge_cnyes_into_data,
    _load_cache,
    _save_cache,
)
from daily_taiwan_report import (
    _extract_ticker_code,
    build_taiwan_focus_report,
    FOCUS_THRESHOLD,
    MAX_FOCUS_STOCKS,
)
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Check trading day
if not is_taiwan_trading_day():
    print("今日為休市日，不派發")
    sys.exit(0)

# Load watchlist from sheet
manager = GoogleSheetsManager()
watchlist = manager.load_taiwan_stocks()
logger.info("Loaded %d Taiwan stock(s).", len(watchlist))

# Extract tickers
tickers = [_extract_ticker_code(h.get("ticker", h.get("代碼", "")))[0] for h in watchlist]
logger.info("Tickers: %s", tickers)

# Load QFII ratings
cnyes = load_cnyes_ratings()
logger.info("QFII ratings: %d", len(cnyes))

# Try to load cached data
stocks_data = {}
for ticker in tickers:
    cached = _load_cache(ticker)
    if cached and cached.get('current_price', 0) > 0:
        stocks_data[ticker] = StockMarketData(**cached)
        logger.info("%s: loaded from cache (price=%.2f)", ticker, cached['current_price'])
    else:
        logger.warning("%s: no cached data", ticker)

logger.info("Cached stocks: %d/%d", len(stocks_data), len(tickers))

if not stocks_data:
    logger.error("No cached data available.")
    sys.exit(1)

# Merge QFII
qfii_merged = merge_cnyes_into_data(tickers, cnyes)
for ticker, qfii_info in qfii_merged.items():
    if ticker in stocks_data:
        sd = stocks_data[ticker]
        sd.qfii_target = qfii_info["qfii_target"]
        sd.qfii_rating = qfii_info["qfii_rating"]
        sd.qfii_upside = qfii_info["qfii_upside"]
        sd.qfii_broker = qfii_info["qfii_broker"]

# Build report
report = build_taiwan_focus_report(stocks_data, watchlist, qfii_data=qfii_merged)
print(report)
