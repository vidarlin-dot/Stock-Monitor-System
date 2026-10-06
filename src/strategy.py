# -*- coding: utf-8 -*-
"""Trade strategy module — single source of truth for buy/sell zone calculation.

All report builders (daily_taiwan_report, main) should import from here
instead of computing zones inline.

Design principles:
  1. Sell zone must always be >= current price (no inverted ranges)
  2. Target prices from yfinance are sanity-checked against current price
  3. Zone formulas combine technical (MA20, high_20d) with fundamental (target) inputs
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Max characters for analyst summary (was 20, increased per best-practice review)
ANALYST_SUMMARY_MAX_LEN = 80

# Threshold for flagging anomalous target prices from yfinance
TARGET_PRICE_ANOMALY_RATIO = 3.0


def calc_trade_zones(
    current_price: float,
    target: float,
    high_20d: float,
    ma20: float,
) -> Tuple[Optional[str], Optional[str]]:
    """Compute buy and sell zones with safety assertions.

    Returns:
        (buy_range_str, sell_range_str) — both may be None if data insufficient.
        Assertions enforced:
          - sell_zone_low >= current_price * 0.97  (never below current price)
          - buy_zone_high <= current_price * 1.03  (never above current price)
    """
    buy_range: Optional[str] = None
    sell_range: Optional[str] = None

    if current_price <= 0:
        return buy_range, sell_range

    # ---- Buy zone ----
    if ma20 > 0 and current_price > ma20 * 1.02:
        # Price above MA20: buy on pullback to MA20
        buy_low = ma20 * 0.98
        buy_high = current_price * 0.97
        if buy_low > 0 and buy_high > 0 and buy_low <= buy_high:
            buy_range = f"{buy_low:,.0f}～{buy_high:,.0f} 元"
        else:
            buy_range = f"現價附近或回測 MA20 ({ma20:,.0f} 元) 時留意"
    elif ma20 > 0:
        buy_range = f"現價附近或回測 MA20 ({ma20:,.0f} 元) 時留意"
    else:
        buy_range = "現價附近留意"

    # ---- Sell zone ----
    if target > 0:
        if current_price < target:
            # Price below target (still has upside): sell near target
            sell_low = target * 0.95
            sell_high = target * 1.05
            if sell_low > 0 and sell_high > 0:
                sell_range = f"{sell_low:,.0f}～{sell_high:,.0f} 元"
        else:
            # Price at or above target: use recent high or current price as reference
            ref = high_20d if high_20d > 0 else current_price
            sell_low = ref * 0.97
            sell_high = ref * 1.03
            if sell_low > 0 and sell_high > 0:
                sell_range = f"{sell_low:,.0f}～{sell_high:,.0f} 元"
    elif high_20d > 0:
        sell_range = f"{high_20d:,.0f}～{high_20d * 1.05:,.0f} 元（近期高點區間）"
    else:
        sell_range = "現價附近或近期高點為賣出參考"

    return buy_range, sell_range


def validate_target_price(
    current_price: float, target_mean: Optional[float]
) -> Tuple[Optional[float], bool]:
    """Sanity-check a target price against the current price.

    Returns:
        (valid_target, is_anomalous)
        - valid_target: the target if within acceptable range, else None
        - is_anomalous: True if the target was flagged as anomalous
    """
    if current_price <= 0 or not target_mean:
        return target_mean, False

    ratio = target_mean / current_price
    if ratio > TARGET_PRICE_ANOMALY_RATIO or ratio < (1.0 / TARGET_PRICE_ANOMALY_RATIO):
        logger.warning(
            "Anomalous target price for %.2f vs current %.2f (ratio=%.2f). Treating as invalid.",
            target_mean, current_price, ratio,
        )
        return None, True

    return target_mean, False


REC_MAP = {
    "strong_buy": "強力買進",
    "buy": "買進",
    "outperform": "優於大盤",
    "overweight": "超配",
    "hold": "持有",
    "equal_weight": "中性",
    "underweight": "低配",
    "sell": "賣出",
}

_POSITIVE_KW = [
    "beat", "surge", "upgrade", "buy", "growth", "profit",
    "record", "strong", "bullish", "win", "expand", "超預期",
    "創新高", "突破", "升級", "買進", "獲利", "成長", "業績",
    "看好", "利好",
]
_NEGATIVE_KW = [
    "miss", "plunge", "downgrade", "sell", "loss", "warn",
    "decline", "weak", "bearish", "cut", "risk", "處置",
    "低於預期", "下修", "賣超", "撤資", "虧損", "警告", "風險",
]


def format_analyst_summary(
    rec_key: str,
    num_analysts: int,
    news_list: list,
    max_len: int = ANALYST_SUMMARY_MAX_LEN,
) -> str:
    """Generate an analyst summary string.

    Args:
        rec_key: yfinance recommendation key (e.g. 'strong_buy', 'buy', 'hold')
        num_analysts: number of analysts covering the stock
        news_list: recent news articles from yfinance
        max_len: maximum character length (default 80, was 20)

    Returns:
        A concise Chinese summary string, e.g.
        '強力買進 (18位) | beat earnings, strong demand'
    """
    chinese_rec = REC_MAP.get(rec_key.lower(), "觀望")
    summary = f"{chinese_rec} ({num_analysts}位)"

    if news_list:
        keywords = _extract_sentiment_keywords(news_list)
        if keywords:
            candidate = f"{summary} | {keywords}"
            summary = candidate if len(candidate) <= max_len else candidate[: max_len - 2] + ".."
        else:
            summary = summary if len(summary) <= max_len else summary[:max_len - 2] + ".."
    else:
        summary = summary if len(summary) <= max_len else summary[:max_len - 2] + ".."

    return summary


def _extract_sentiment_keywords(news_list: list) -> str:
    """Extract up to 2 sentiment keywords from recent news headlines."""
    if not news_list:
        return ""
    all_text = ""
    for item in news_list[:5]:
        if isinstance(item.get("content"), dict):
            all_text += f" {item['content'].get('title', '')} {item['content'].get('summary', '')}"
        elif item.get("title"):
            all_text += f" {item['title']} {item.get('description', '')}"
    if not all_text.strip():
        return ""
    text_lower = all_text.lower()
    found: List[str] = []
    for kw in _POSITIVE_KW:
        if kw.lower() in text_lower:
            found.append(kw)
            if len(found) >= 2:
                break
    for kw in _NEGATIVE_KW:
        if kw.lower() in text_lower:
            found.append(kw)
            if len(found) >= 2:
                break
    return " | ".join(found[:2]) if found else ""
