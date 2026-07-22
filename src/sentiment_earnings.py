"""Earnings Calendar & Social Sentiment integration."""

from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

POSITIVE_KEYWORDS = ["beat", "surge", "upgrade", "buy", "growth", "profit", "record", "strong", "bullish"]
NEGATIVE_KEYWORDS = ["miss", "plunge", "downgrade", "sell", "loss", "warn", "decline", "weak", "bearish", "slump"]


def fetch_earnings_calendar(ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch upcoming earnings date and estimates via yfinance."""
    try:
        stock = yf.Ticker(ticker)
        cal = stock.calendar
        info = stock.info
        
        next_earnings_raw = cal.get("Earnings Date", [None])[0] if cal else None
        eps_est = info.get("forwardEps") or info.get("trailingEps")
        rev_est = info.get("estimatedRevenue")
        
        earnings_str = ""
        if isinstance(next_earnings_raw, str):
            earnings_str = next_earnings_raw.split(" (")[0].strip()
        elif hasattr(next_earnings_raw, "strftime"):
            earnings_str = next_earnings_raw.strftime("%Y-%m-%d")
            
        return {
            "next_earnings": earnings_str,
            "eps_estimate": f"${eps_est:.2f}" if eps_est else "N/A"
        }
    except Exception as exc:
        logger.debug("Earnings fetch failed for %s: %s", ticker, exc)
        return None


def fetch_social_sentiment(ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch retail sentiment from Reddit & StockTwits."""
    try:
        # Reddit mentions
        r_url = f"https://www.reddit.com/search.json?q=${ticker}&limit=10"
        r_headers = {"User-Agent": "StockMonitor/1.0"}
        r_res = requests.get(r_url, headers=r_headers, timeout=10, allow_redirects=True)
        reddit_count = 0
        if r_res.status_code == 200:
            reddit_count = len(r_res.json().get("data", {}).get("children", []))
            
        # StockTwits mentions
        s_url = f"https://api.stocktwits.com/api/2/symbols/{ticker.upper()}/mentions.json"
        s_res = requests.get(s_url, timeout=10)
        st_count = 0
        if s_res.status_code == 200:
            st_count = s_res.json().get("mentions", {}).get("total", 0)
            
        total = reddit_count + st_count
        
        # Sentiment scoring from recent news
        stock = yf.Ticker(ticker)
        news = stock.news[:5]
        score = 0
        for item in news:
            text = f"{item.get('title', '').lower()} {item.get('content', '').lower()}"
            for kw in POSITIVE_KEYWORDS:
                if kw in text: score += 1
            for kw in NEGATIVE_KEYWORDS:
                if kw in text: score -= 1
                
        label = "中性"
        if score >= 2: label = "偏多 🟢"
        elif score <= -2: label = "偏空 🔴"
            
        return {
            "reddit_mentions": reddit_count,
            "stocktwits_mentions": st_count,
            "total_mentions": total,
            "sentiment_label": label,
            "sentiment_score": score
        }
    except Exception as exc:
        logger.debug("Sentiment fetch failed for %s: %s", ticker, exc)
        return None