'''Earnings Calendar and Social Sentiment helpers via yfinance.

Uses yfinance for earnings calendar data and news-driven
retail sentiment analysis (no third-party API required).
'''

from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional

import yfinance as yf

logger = logging.getLogger(__name__)

_POSITIVE_KEYWORDS: List[str] = [
    "beat", "surge", "upgrade", "buy", "growth", "profit",
    "record", "strong", "bullish", "\u8d85\u9810\u671f",
    "\u521b\u7eaa\u9304", "\u5347\u7d1a", "\u8cb7\u5165",
]

_NEGATIVE_KEYWORDS: List[str] = [
    "miss", "plunge", "downgrade", "sell", "loss", "warn",
    "decline", "weak", "bearish", "\u4f4e\u65bc\u9810\u671f",
    "\u4e0b\u8abf", "\u8ca3\u51fa", "\u64a4\u8cc7",
]


def _extract_title(item: Dict[str, Any]) -> str:
    '''Extract headline title from yfinance news item.

    Newer yfinance versions nest title under 'content.title'.
    Older versions put it directly as 'title'.
    '''
    if isinstance(item.get("content"), dict):
        return item["content"].get("title", "") or ""
    return item.get("title", "") or ""


def fetch_earnings_calendar(ticker: str) -> Optional[Dict[str, Any]]:
    '''Fetch the next earnings date and EPS estimate via yfinance.'''
    try:
        stock = yf.Ticker(ticker)
        cal = stock.calendar
        info = stock.info

        # Earnings date
        next_earnings_raw = cal.get("Earnings Date", [None])[0] if cal else None
        earnings_str = ""
        if next_earnings_raw is not None:
            if isinstance(next_earnings_raw, str):
                earnings_str = next_earnings_raw.split(" (")[0].strip()
            elif isinstance(next_earnings_raw, date):
                earnings_str = next_earnings_raw.strftime("%Y-%m-%d")

        # EPS estimate: try forwardEps first, then infer from calendar
        eps_est = info.get("forwardEps") if info else None
        if eps_est is None:
            eps_avg = cal.get("Earnings Average") if cal else None
            if eps_avg is not None:
                eps_est = float(eps_avg)

        return {
            "next_earnings": earnings_str if earnings_str else "TBA",
            "eps_estimate": f"${eps_est:.2f}" if eps_est is not None else "N/A",
        }
    except Exception as exc:
        logger.debug("Earnings fetch failed for %s: %s", ticker, exc)
        return None


def _score_headlines(headlines: List[str]) -> tuple:
    '''Score a list of headlines for positive/negative keywords.'''
    score = 0
    matched_titles: List[str] = []

    for title in headlines:
        title_lower = title.lower()
        has_pos = any(kw in title_lower for kw in _POSITIVE_KEYWORDS)
        has_neg = any(kw in title_lower for kw in _NEGATIVE_KEYWORDS)

        if has_pos:
            score += 1
            matched_titles.append(title)
        if has_neg:
            score -= 1
            matched_titles.append(title)

    return score, matched_titles


def fetch_social_sentiment(ticker: str) -> Optional[Dict[str, Any]]:
    '''Analyze retail sentiment from recent Yahoo Finance news.

    Uses the latest financial news headlines as a proxy for market
    sentiment since Reddit/StockTwits APIs are restricted.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Dict with total_mentions, sentiment_label, summary.
    '''
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        if not news:
            logger.info("No recent news for %s.", ticker)
            return {
                "total_mentions": 0,
                "sentiment_label": "\u8a0a\u606f\u4e0d\u8db3 (\u4e2d\u6027)",
                "summary": "\u7121\u6700\u8fd1\u65b0\u805e\u53ef\u5206\u6790",
            }

        # Extract titles using the correct nested structure
        headlines: List[str] = []
        for item in news[:10]:
            title = _extract_title(item)
            if title:
                headlines.append(title)

        if not headlines:
            return {
                "total_mentions": 0,
                "sentiment_label": "\u8a0a\u606f\u4e0d\u8db3 (\u4e2d\u6027)",
                "summary": "\u9732\u5931\u65b0\u805e\u6a19\u984c",
            }

        score, matched = _score_headlines(headlines)

        if score >= 3:
            label = "\u504f\u591a \U0001f7e2"
        elif score <= -3:
            label = "\u504f\u50ac \U0001f7e4"
        else:
            label = "\u4e2d\u6027 \U0001f532"

        summary = (
            f"\u6839\u64da\u6700\u8fd1 {len(headlines)} \u7bc7\u65b0\u805e\u6a19\u984c\u5206\u6790\uff1a"
            f"{label}"
        )

        logger.info("Sentiment for %s: score=%d, label=%s", ticker, score, label)
        return {
            "total_mentions": len(matched),
            "sentiment_label": label,
            "summary": summary,
            "news_snippets": matched[:3],
        }

    except Exception as exc:
        logger.error("Failed to fetch sentiment for %s: %s", ticker, exc)
        return {
            "total_mentions": 0,
            "sentiment_label": "\u5206\u6790\u5931\u6557",
            "summary": "\u81ea\u52d5\u5206\u6790\u5931\u6557\uff0c\u8acb\u624b\u52d5\u6aa2\u67e5",
        }