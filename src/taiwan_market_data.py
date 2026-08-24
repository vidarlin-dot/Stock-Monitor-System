# -*- coding: utf-8 -*-
"""Taiwan stock market data fetching and FocusScore computation.

FocusScore = 0.30*MarketHeat + 0.25*Institutional + 0.25*Catalyst
           + 0.10*Trend + 0.10*WatchlistFit - RiskPenalty

Each dimension is 0-100.  RiskPenalty is 0-20.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytz
import requests
import yfinance as yf

logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone("Asia/Taipei")
TW_SUFFIX = ".TW"

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache", "taiwan")
MANUAL_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "manual_taiwan.json")
os.makedirs(CACHE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(ticker: str) -> str:
    return ticker + ".json"

def _load_cache(ticker: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(CACHE_DIR, _cache_key(ticker))
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _save_cache(ticker: str, data: Dict[str, Any]) -> None:
    path = os.path.join(CACHE_DIR, _cache_key(ticker))
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
    except Exception as exc:
        logger.debug("Cache save failed for %s: %s", ticker, exc)

# Taiwan Stock Exchange 2026 holidays
TW_HOLIDAYS: Dict[str, str] = {
    '2026-01-01': '\u5143\u65e6',
    '2026-01-28': '\u6625\u7ae5\u591c',
    '2026-01-29': '\u6625\u7ae5\u521d\u4e00',
    '2026-01-30': '\u6625\u7ae5\u521d\u4e8c',
    '2026-02-02': '\u6625\u7ae5\u8865\u5047',
    '2026-03-03': '\u4e8c\u4e8c\u516b\u7d00\u5ff5\u65e5',
    '2026-04-04': '\u6e05\u660e\u7bc0',
    '2026-04-05': '\u6e05\u660e\u8865\u5047',
    '2026-05-01': '\u52de\u52d5\u7bc0',
    '2026-05-05': '\u7aef\u5348\u7bc0',
    '2026-06-19': '\u7aef\u5348\u8865\u5047',
    '2026-09-25': '\u4e2d\u79cb\u7bc0',
    '2026-10-10': '\u570b\u6109\u65e5',
    '2026-10-12': '\u570b\u6109\u8865\u5047',
}


def is_taiwan_trading_day():
    now = datetime.now(TW_TZ)
    date_str = now.strftime('%Y-%m-%d')
    if now.weekday() >= 5:
        return False
    if date_str in TW_HOLIDAYS:
        logger.info('Taiwan market holiday: %s (%s)', date_str, TW_HOLIDAYS[date_str])
        return False
    return True



# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

@dataclass
class StockMarketData:
    """All market data needed for FocusScore computation."""
    ticker: str
    short_name: str = ""
    current_price: float = 0.0
    previous_close: float = 0.0
    day_change_pct: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0
    volume: int = 0
    avg_volume_5d: int = 0
    avg_volume_20d: int = 0
    avg_volume_60d: int = 0
    close_5d: float = 0.0
    close_20d: float = 0.0
    close_60d: float = 0.0
    high_20d: float = 0.0
    low_20d: float = 0.0
    mean_target: float = 0.0
    high_target: float = 0.0
    low_target: float = 0.0
    median_target: float = 0.0
    analysts: int = 0
    rec_key: str = ""
    rec_label: str = ""
    news_list: List[Dict[str, Any]] = field(default_factory=list)
    institutional: Dict[str, Any] = field(default_factory=dict)
    fetched_at: str = ""

    # Earnings/event data (from yfinance or manual entry)
    revenue_est: float = 0.0
    eps_estimate: float = 0.0
    next_earnings_date: str = ""
    earnings_history: List[Dict[str, Any]] = field(default_factory=list)

    # QFII / cnyes analyst target data
    qfii_target: float = 0.0
    qfii_rating: str = ''
    qfii_upside: float = 0.0
    qfii_broker: str = ""


def _fetch_price_history(yf_ticker: str, period: str = "6mo") -> Optional[Dict[str, Any]]:
    """Fetch price history via Yahoo Finance API (fast, with timeout)."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}?interval=1d&range={period}"
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.debug("Yahoo API failed for %s: %s", yf_ticker, exc)
        return None


def _fetch_yf_info(yf_ticker: str) -> Dict[str, Any]:
    """Fetch ticker info via yfinance (slower but richer)."""
    try:
        stock = yf.Ticker(yf_ticker)
        info = stock.info
        news_list = []
        try:
            news_list = stock.news[:5] if hasattr(stock, "news") and stock.news else []
        except Exception:
            pass
        return {"info": info, "news": news_list}
    except Exception as exc:
        logger.debug("yfinance failed for %s: %s", yf_ticker, exc)
        return {"info": {}, "news": []}


def fetch_taiwan_stock_data(ticker: str) -> Optional[StockMarketData]:
    """Fetch complete market data for one Taiwan stock."""
    yf_ticker = ticker + TW_SUFFIX
    cached = _load_cache(ticker)

    # --- Initialize all variables to avoid UnboundLocalError ---
    current = prev_close = volume = 0
    day_high = day_low = 0
    close_5d = close_20d = close_60d = 0
    high_20d = low_20d = 0
    avg_vol_5d = avg_vol_20d = avg_vol_60d = 0
    short_name = ticker
    rec_key = ""
    mean_target = high_target = low_target = median_target = 0
    analysts = 0
    news_list: List[Dict[str, Any]] = []
    revenue_est = 0
    eps_estimate = 0

    # --- Price history (fast API) ---
    history_result = _fetch_price_history(yf_ticker, period="3mo")
    if history_result is None:
        # Fallback to yfinance
        yf_data = _fetch_yf_info(yf_ticker)
        info = yf_data["info"]
        current = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        prev_close = info.get("previousClose") or 0
        volume = info.get("volume") or 0
        avg_vol = info.get("averageVolume") or 0
        rec_key = (info.get("recommendationKey") or "").lower()
        short_name = info.get("shortName") or info.get("symbol", ticker)
        mean_target = info.get("targetMeanPrice") or 0
        high_target = info.get("targetHighPrice") or 0
        low_target = info.get("targetLowPrice") or 0
        median_target = info.get("targetMedianPrice") or 0
        analysts = info.get("numberOfAnalystOpinions") or 0
        news_list = yf_data["news"]
        day_high = current or 0
        day_low = current or 0
        close_5d = current or 0
        close_20d = current or 0
        close_60d = current or 0
        high_20d = current or 0
        low_20d = current or 0
        avg_vol_5d = avg_vol or 0
        avg_vol_20d = avg_vol or 0
        avg_vol_60d = avg_vol or 0
    else:
        result = history_result.get("result", [{}])[0]
        meta = result.get("meta", {})
        timestamps = result.get("timestamp", [])
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        volumes = result.get("indicators", {}).get("quote", [{}])[0].get("volume", [])
        highs = result.get("indicators", {}).get("quote", [{}])[0].get("high", [])
        lows = result.get("indicators", {}).get("quote", [{}])[0].get("low", [])

        current = meta.get("regularMarketPrice") or 0
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose") or 0
        volume = volumes[-1] if volumes else 0
        short_name = meta.get("symbol") or ticker

        # Compute moving averages from recent closes
        close_5d = sum(closes[-5:]) / 5 if len(closes) >= 5 else (closes[-1] if closes else 0)
        close_20d = sum(closes[-20:]) / 20 if len(closes) >= 20 else (closes[-1] if closes else 0)
        close_60d = sum(closes[-60:]) / 60 if len(closes) >= 60 else (closes[-1] if closes else 0)
        high_20d = max(highs[-20:]) if len(highs) >= 20 else (max(highs) if highs else 0)
        low_20d = min(lows[-20:]) if len(lows) >= 20 else (min(lows) if lows else 0)

        avg_vol_5d = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else (volume or 0)
        avg_vol_20d = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else (volume or 0)
        avg_vol_60d = sum(volumes[-60:]) / 60 if len(volumes) >= 60 else (volume or 0)

        day_high = max(highs[-1:]) if highs else current
        day_low = min(lows[-1:]) if lows else current

        # Get richer info from yfinance
        yf_data = _fetch_yf_info(yf_ticker)
        info = yf_data["info"]
        rec_key = (info.get("recommendationKey") or "").lower()
        mean_target = info.get("targetMeanPrice") or 0
        high_target = info.get("targetHighPrice") or 0
        low_target = info.get("targetLowPrice") or 0
        median_target = info.get("targetMedianPrice") or 0
        analysts = info.get("numberOfAnalystOpinions") or 0
        news_list = yf_data["news"]
        if not short_name:
            short_name = info.get("shortName") or info.get("symbol", ticker)
        if not volume:
            volume = info.get("volume") or 0
            avg_vol_5d = info.get("averageVolume") or 0
            avg_vol_20d = info.get("averageVolume") or 0

    day_change_pct = ((current - prev_close) / prev_close * 100) if prev_close > 0 else 0

    rec_map = {
        "strong_buy": "強力買進",
        "buy": "買進",
        "outperform": "優於大盤",
        "overweight": "超配",
        "hold": "持有",
        "equal_weight": "中性",
        "underweight": "低配",
        "sell": "賣出",
    }
    rec_label = rec_map.get(rec_key, "觀望")

    data = StockMarketData(
        ticker=ticker,
        short_name=short_name,
        current_price=current,
        previous_close=prev_close,
        day_change_pct=day_change_pct,
        day_high=day_high,
        day_low=day_low,
        volume=volume,
        avg_volume_5d=int(avg_vol_5d),
        avg_volume_20d=int(avg_vol_20d),
        avg_volume_60d=int(avg_vol_60d),
        close_5d=close_5d,
        close_20d=close_20d,
        close_60d=close_60d,
        high_20d=high_20d,
        low_20d=low_20d,
        mean_target=mean_target,
        high_target=high_target,
        low_target=low_target,
        median_target=median_target,
        analysts=analysts,
        rec_key=rec_key,
        rec_label=rec_label,
        news_list=news_list,
        institutional={},  # Taiwan 3-institutional data not available via Yahoo
        fetched_at=datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M"),
        revenue_est=info.get("revenueEstimate") or 0,
        eps_estimate=(info.get("epsTrend") or {}).get("current") or 0,
        next_earnings_date="",
        earnings_history=[],
    )
    _save_cache(ticker, data.__dict__)
    return data


# ---------------------------------------------------------------------------
# Scoring dimensions
# ---------------------------------------------------------------------------

def _score_market_heat(data: StockMarketData) -> float:
    """MarketHeat 0-100: volume activity, price move, sector heat, anomaly."""
    score = 0.0

    # Volume activity (30%)
    vol_ratio_20d = data.volume / data.avg_volume_20d if data.avg_volume_20d > 0 else 0
    if vol_ratio_20d >= 5.0:
        score += 30
    elif vol_ratio_20d >= 3.0:
        score += 27
    elif vol_ratio_20d >= 2.0:
        score += 22
    elif vol_ratio_20d >= 1.5:
        score += 15
    elif vol_ratio_20d >= 1.2:
        score += 12
    else:
        score += max(0, vol_ratio_20d * 7)

    # Price change & amplitude (25%)
    abs_chg = abs(data.day_change_pct)
    if abs_chg >= 7.0:
        score += 25
    elif abs_chg >= 5.0:
        score += 22
    elif abs_chg >= 3.0:
        score += 18
    elif abs_chg >= 2.0:
        score += 13
    elif abs_chg >= 1.0:
        score += 12
    else:
        score += abs_chg * 6

    # Near 20-day high / low (20%)
    if data.high_20d > 0 and data.current_price > 0:
        dist_to_high = (data.high_20d - data.current_price) / data.high_20d * 100
        if dist_to_high <= 2:
            score += 20
        elif dist_to_high <= 5:
            score += 15
        elif dist_to_high <= 10:
            score += 8

    # Sector / group heat via news keywords (15%)
    sector_score = _news_sector_score(data.news_list)
    score += min(15, sector_score)

    # Abnormal volume anomaly (10%)
    if vol_ratio_20d >= 4.0:
        score += 10
    elif vol_ratio_20d >= 2.5:
        score += 6

    return min(100.0, score)


def _score_institutional(data: StockMarketData) -> float:
    """Institutional 0-100.

    NOTE: Taiwan 三大法人 (外資/投信/自營商) daily trade data is NOT
    available via Yahoo Finance or yfinance.  We proxy with analyst
    recommendations and news sentiment as a fallback.
    """
    score = 0.0

    # Analyst recommendation strength (proxy for institutional view)
    rec = data.rec_key
    if rec in ("strong_buy",):
        score += 40
    elif rec in ("buy",):
        score += 30
    elif rec in ("outperform", "overweight"):
        score += 25
    elif rec in ("hold", "equal_weight"):
        score += 10
    elif rec in ("underweight",):
        score += 0
    elif rec in ("sell",):
        score += 0

    # Number of analysts (consensus strength)
    n = data.analysts
    if n >= 5:
        score += 30
    elif n >= 3:
        score += 20
    elif n >= 1:
        score += 10

    # Target price upside (30%)
    if data.mean_target > 0 and data.current_price > 0:
        upside = (data.mean_target - data.current_price) / data.current_price * 100
        if upside >= 20:
            score += 30
        elif upside >= 10:
            score += 22
        elif upside >= 5:
            score += 15
        elif upside >= 0:
            score += 8
        else:
            score += max(0, 8 + upside)

    return min(100.0, score)


def _score_catalyst(data: StockMarketData) -> float:
    """Catalyst 0-100: earnings, news, sector themes, freshness."""
    score = 0.0

    # Analyst target changes / price target activity (35%)
    if data.mean_target > 0 and data.current_price > 0:
        upside = (data.mean_target - data.current_price) / data.current_price * 100
        if upside >= 15:
            score += 35
        elif upside >= 8:
            score += 28
        elif upside >= 3:
            score += 20
        else:
            score += 10

    # News sentiment (30%)
    news_score = _news_sentiment_score(data.news_list)
    score += news_score

    # Sector heat from news (20%)
    sector_score = _news_sector_score(data.news_list)
    score += min(20, sector_score)

    # Freshness of news (15%)
    now_tw = datetime.now(TW_TZ)
    fresh_score = 0
    for item in data.news_list[:3]:
        title = _extract_title(item)
        if title:
            tl = title.lower()
            if any(kw in tl for kw in ["today", "now", "recently", "just", "announced",
                                        "今日", "昨天", "近日", "最新", "公告"]):
                fresh_score += 5
    score += min(15, fresh_score)

    return min(100.0, score)


def _score_trend(data: StockMarketData) -> float:
    """Trend 0-100: moving averages, breakout, volume-price alignment."""
    score = 0.0
    price = data.current_price
    if price <= 0:
        return 0.0

    # Price above MAs (35%)
    if data.close_5d > 0:
        if price > data.close_5d:
            score += 8
        if price > data.close_20d:
            score += 12
        if data.close_60d > 0 and price > data.close_60d:
            score += 15
    elif data.close_20d > 0 and price > data.close_20d:
        score += 20

    # MA5 > MA20 (bullish alignment) (25%)
    if data.close_5d > 0 and data.close_20d > 0:
        if data.close_5d > data.close_20d:
            score += 25
        else:
            score += 5

    # Near 20-day high (breakout) (20%)
    if data.high_20d > 0 and price > 0:
        dist = (data.high_20d - price) / data.high_20d * 100
        if dist <= 1:
            score += 20
        elif dist <= 3:
            score += 16
        elif dist <= 8:
            score += 10
        elif dist <= 15:
            score += 5

    # Volume supporting price (20%)
    if data.avg_volume_20d > 0 and data.volume > 0:
        vol_ratio = data.volume / data.avg_volume_20d
        if data.day_change_pct > 0 and vol_ratio >= 1.5:
            score += 20
        elif data.day_change_pct > 0 and vol_ratio >= 1.0:
            score += 12
        elif data.day_change_pct < 0 and vol_ratio >= 2.0:
            score += 8  # heavy selling = bearish signal
        else:
            score += max(0, vol_ratio * 5)

    return min(100.0, score)



def _risk_penalty(data: StockMarketData, h: Dict[str, Any]) -> float:
    """RiskPenalty 0-20.  Deduct for negative signals."""
    penalty = 0.0

    # Major drop day
    if data.day_change_pct <= -5.0:
        penalty += 5
    elif data.day_change_pct <= -3.0:
        penalty += 3
    elif data.day_change_pct <= -2.0:
        penalty += 1

    # Below all MAs (strong downtrend)
    price = data.current_price
    if price > 0:
        below_all = True
        if data.close_5d > 0 and price > data.close_5d:
            below_all = False
        if data.close_20d > 0 and price > data.close_20d:
            below_all = False
        if data.close_60d > 0 and price > data.close_60d:
            below_all = False
        if below_all:
            penalty += 4

    # Negative news
    neg_score = _news_negative_score(data.news_list)
    penalty += min(5, neg_score)

    # Overvalued (price far above target)
    if data.mean_target > 0 and price > 0:
        if price > data.mean_target * 1.2:
            penalty += 3

    return min(20.0, penalty)


def compute_focus_score(data: StockMarketData, h: Dict[str, Any]) -> Dict[str, Any]:
    """Compute the full FocusScore breakdown.

    Returns dict with:
        focus_score, market_heat, institutional, catalyst, trend,
        risk_penalty, category
    """
    mh = _score_market_heat(data)
    inst = _score_institutional(data)
    cat = _score_catalyst(data)
    tr = _score_trend(data)
    wf = 0  # tier removed per user request
    rp = _risk_penalty(data, h)

    focus_score = (0.20 * mh + 0.30 * inst + 0.30 * cat
                   + 0.10 * tr - rp)
    focus_score = max(0.0, min(100.0, focus_score))

    # Category
    if rp >= 8:
        category = "風險焦點"
    elif focus_score >= 70:
        category = "偏多焦點"
    elif focus_score >= 60:
        category = "中性觀察"
    else:
        category = "一般追蹤"

    return {
        "focus_score": round(focus_score, 1),
        "market_heat": round(mh, 1),
        "institutional": round(inst, 1),
        "catalyst": round(cat, 1),
        "trend": round(tr, 1),
        "risk_penalty": round(rp, 1),
        "category": category,
    }


# ---------------------------------------------------------------------------
# News helpers
# ---------------------------------------------------------------------------

_POSITIVE_KW = {"beat", "surge", "upgrade", "buy", "growth", "profit",
                "record", "strong", "bullish", "win", "expand", "订单",
                "超預期的", "創新高", "突破", "升級", "買進", "獲利",
                "成長", "業績", "看好", "利好", "利好消息"}
_NEGATIVE_KW = {"miss", "plunge", "downgrade", "sell", "loss", "warn",
                "decline", "weak", "bearish", "cut", "risk", "lawsuit",
                "處置", "低於預期", "下修", "賣超", "撤資", "虧損",
                "警告", "風險", "訴訟", "負面", "利空"}
_SECTOR_KW = {"ai", "artificial intelligence", "ai晶片", "ai機櫃",
              "液冷", "水冷", "ev", "電動車", "5g", "6g",
              "伺服器", "雲端", "半導體", "封測", "ic设计",
              "renewable", "green energy", "green energy",
              "gpu", "nvidia", "amd", "intel",
              "光學", "鏡頭", "rbp", "rbp", "rbp"}


def _extract_title(item: Dict[str, Any]) -> str:
    if isinstance(item.get("content"), dict):
        return item["content"].get("title", "") or ""
    return item.get("title", "") or ""


def _news_sentiment_score(news_list: List[Dict[str, Any]]) -> float:
    """Score news sentiment 0-30."""
    if not news_list:
        return 5
    score = 0
    for item in news_list[:5]:
        title = _extract_title(item).lower()
        if not title:
            continue
        pos = sum(1 for kw in _POSITIVE_KW if kw in title)
        neg = sum(1 for kw in _NEGATIVE_KW if kw in title)
        score += pos - neg
    # Map -5..+5 to 0..30
    return max(0, min(30, 15 + score * 3))


def _news_negative_score(news_list: List[Dict[str, Any]]) -> float:
    """Count negative signals 0-5."""
    if not news_list:
        return 0
    neg = 0
    for item in news_list[:5]:
        title = _extract_title(item).lower()
        if any(kw in title for kw in _NEGATIVE_KW):
            neg += 1
    return min(5, neg * 1.5)


def _news_sector_score(news_list: List[Dict[str, Any]]) -> float:
    """Score sector heat from news 0-15."""
    if not news_list:
        return 3
    sector_hits = 0
    for item in news_list[:5]:
        title = _extract_title(item).lower()
        if any(kw in title for kw in _SECTOR_KW):
            sector_hits += 1
    return min(15, sector_hits * 3 + 3)


# ---------------------------------------------------------------------------
# Bulk fetch
# ---------------------------------------------------------------------------

def load_cnyes_ratings() -> Dict[str, Dict[str, Any]]:
    """Load QFII analyst ratings - scrape all pages + manual fallback."""
    try:
        from fetch_cnyes import load_cnyes_ratings as _load
        return _load()
    except Exception as exc:
        logger.warning('Failed to load cnyes ratings from fetch_cnyes: %s', exc)
        # Fallback to cache
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'cache', 'cnyes')
        path = os.path.join(cache_dir, 'latest.json')
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}


def merge_cnyes_into_data(tickers: List[str], cnyes: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Merge QFII rating data into per-ticker dicts."""
    merged: Dict[str, Dict[str, Any]] = {}
    for ticker in tickers:
        if ticker in cnyes:
            r = cnyes[ticker]
            try:
                tgt = float(r.get('new_target') or 0)
                price = float(r.get('current_price') or 0)
                upside = ((tgt - price) / price * 100) if price > 0 else 0
                merged[ticker] = {
                    'qfii_target': tgt,
                    'qfii_rating': str(r.get('new_rating', '')).strip(),
                    'qfii_upside': round(upside, 1),
                    'qfii_broker': str(r.get('broker', '')).strip(),
                }
            except (ValueError, TypeError):
                pass
    return merged


def load_manual_data() -> Dict[str, Dict[str, Any]]:
    """Load manually-entered stock data for tickers not available on Yahoo Finance."""
    if not os.path.exists(MANUAL_DATA_PATH):
        return {}
    try:
        with open(MANUAL_DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Failed to load manual data: %s", exc)
        return {}


def _is_cache_fresh(cache_data, max_age_hours=24):
    """Check if cached data is within max_age_hours."""
    fetched_at = cache_data.get("fetched_at", "")
    if not fetched_at:
        return False
    # Check if price is valid
    price = cache_data.get("current_price", 0)
    if price <= 0:
        return False
    
    try:
        dt = datetime.strptime(fetched_at, "%Y-%m-%d %H:%M")
        dt = TW_TZ.localize(dt)
        return (datetime.now(TW_TZ) - dt).total_seconds() < max_age_hours * 3600
    except Exception:
        return False


def fetch_all_stock_data(tickers: List[str]) -> Dict[str, StockMarketData]:
    """Fetch market data for multiple tickers with retry + cache fallback."""
    results: Dict[str, StockMarketData] = {}
    for ticker in tickers:
        # Step 1: Try cache first (fast, no network)
        cached = _load_cache(ticker)
        if cached and _is_cache_fresh(cached):
            cd = StockMarketData(**cached)
            results[ticker] = cd
            logger.info("%s: using fresh cache", ticker)
            continue

        # Step 2: Fetch from Yahoo Finance
        data = None
        for attempt in range(2):
            data = fetch_taiwan_stock_data(ticker)
            if data and data.current_price > 0:
                break
            if attempt < 1:
                wait = (attempt + 1) * 2
                logger.warning("%s: fetch failed, retrying in %ds...", ticker, wait)
                time.sleep(wait)
        if data and data.current_price > 0:
            results[ticker] = data
            logger.info("%s: price=%.2f rec=%s", ticker, data.current_price, data.rec_label)
            continue

        # Step 3: Fallback to cache (even if stale)
        if cached and cached.get("current_price", 0) > 0:
            cd = StockMarketData(**cached)
            results[ticker] = cd
            logger.info("%s: using stale cache (price=%.2f)", ticker, cached.get("current_price"))
            continue

        # Step 4: Fallback to manual data
        manual = load_manual_data()
        if ticker in manual:
            md = manual[ticker]
            cd = StockMarketData(**md)
            results[ticker] = cd
            logger.info("%s: using manual data", ticker)
        else:
            logger.error("%s: no data available", ticker)
    return results