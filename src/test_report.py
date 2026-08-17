import sys
sys.path.insert(0, 'src')
sys.stdout.reconfigure(encoding='utf-8')

from datetime import datetime
import pytz
from taiwan_market_data import StockMarketData, compute_focus_score, fetch_all_stock_data, _load_cache, _save_cache

tw_tz = pytz.timezone('Asia/Taipei')
now = datetime.now(tw_tz)

# Mock market data for 5 test stocks
stocks = [
    dict(ticker='3017', short_name='奇鋐', current_price=3235, previous_close=3200,
         day_change_pct=1.09, day_high=3250, day_low=3190, volume=50000,
         avg_volume_5d=40000, avg_volume_20d=35000, avg_volume_60d=30000,
         close_5d=3180, close_20d=3100, close_60d=2900,
         high_20d=3300, low_20d=2800,
         mean_target=3890, high_target=4200, low_target=3200, median_target=3850,
         analysts=5, rec_key='buy', rec_label='買進', news_list=[], institutional={},
         fetched_at=now.strftime('%Y-%m-%d %H:%M')),
    dict(ticker='3481', short_name='友訊', current_price=890, previous_close=850,
         day_change_pct=4.71, day_high=900, day_low=845, volume=500000,
         avg_volume_5d=200000, avg_volume_20d=180000, avg_volume_60d=150000,
         close_5d=860, close_20d=800, close_60d=700,
         high_20d=910, low_20d=680,
         mean_target=1100, high_target=1300, low_target=800, median_target=1050,
         analysts=8, rec_key='strong_buy', rec_label='強力買進', news_list=[], institutional={},
         fetched_at=now.strftime('%Y-%m-%d %H:%M')),
    dict(ticker='2454', short_name='友達', current_price=78, previous_close=77,
         day_change_pct=1.30, day_high=79, day_low=77, volume=800000,
         avg_volume_5d=700000, avg_volume_20d=650000, avg_volume_60d=600000,
         close_5d=76, close_20d=74, close_60d=70,
         high_20d=80, low_20d=68,
         mean_target=90, high_target=100, low_target=70, median_target=88,
         analysts=4, rec_key='buy', rec_label='買進', news_list=[], institutional={},
         fetched_at=now.strftime('%Y-%m-%d %H:%M')),
    dict(ticker='2317', short_name='群創', current_price=520, previous_close=519,
         day_change_pct=0.19, day_high=522, day_low=518, volume=15000,
         avg_volume_5d=18000, avg_volume_20d=20000, avg_volume_60d=22000,
         close_5d=525, close_20d=530, close_60d=510,
         high_20d=540, low_20d=500,
         mean_target=550, high_target=600, low_target=480, median_target=545,
         analysts=2, rec_key='hold', rec_label='持有', news_list=[], institutional={},
         fetched_at=now.strftime('%Y-%m-%d %H:%M')),
    dict(ticker='9910', short_name='測試空頭', current_price=180, previous_close=195,
         day_change_pct=-7.69, day_high=190, day_low=178, volume=200000,
         avg_volume_5d=80000, avg_volume_20d=60000, avg_volume_60d=50000,
         close_5d=195, close_20d=210, close_60d=230,
         high_20d=250, low_20d=175,
         mean_target=150, high_target=200, low_target=120, median_target=155,
         analysts=3, rec_key='sell', rec_label='賣出', news_list=[], institutional={},
         fetched_at=now.strftime('%Y-%m-%d %H:%M')),
]

watchlist = [
    dict(ticker='3017', tier='core', catalystdate='2026-09-01', notes='Rubin 平台水冷板量產'),
    dict(ticker='3481', tier='core', catalystdate='2026-08-20', notes='AI 伺服器出貨放量'),
    dict(ticker='2454', tier='general', catalystdate='', notes='面板廠產能調整'),
    dict(ticker='2317', tier='general', catalystdate='', notes=''),
    dict(ticker='9910', tier='watch', catalystdate='2026-08-15', notes='財報低於預期'),
]

stocks_data = {}
for s in stocks:
    data = StockMarketData(**s)
    stocks_data[data.ticker] = data

from daily_taiwan_report import build_taiwan_focus_report
report = build_taiwan_focus_report(stocks_data, watchlist)
print(report)