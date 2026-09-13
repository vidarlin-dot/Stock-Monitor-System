# -*- coding: utf-8 -*-
"""US stock FocusScore computation — mirrors taiwan_market_data.py logic.

FocusScore = 0.20*Heat + 0.30*Inst + 0.30*Catalyst
           + 0.10*Trend - RiskPenalty

Each dimension 0-100, risk penalty 0-20.

Data sources: Yahoo Finance (price, volume, MA, analyst targets) +
              Earnings Whispers / Finviz / TradingView (financial summaries).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz
import requests
import yfinance as yf

logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone("Asia/Taipei")

CACHE_DIR = __import__("os").path.join(
    __import__("os").path.dirname(__import__("os").path.abspath(__file__)),
    "..", "cache", "us"
)


@dataclass
class StockMarketData:
    """All market data needed for US FocusScore computation."""
    ticker: str
    company_name: str = ""
    current_price: float = 0.0
    previous_close: float = 0.0
    day_change_pct: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0
    volume: int = 0
    avg_volume_20d: int = 0
    close_5d: float = 0.0
    close_20d: float = 0.0
    close_60d: float = 0.0
    high_20d: float = 0.0
    low_20d: float = 0.0
    mean_target: float = 0.0
    high_target: float = 0.0
    low_target: float = 0.0
    analysts: int = 0
    rec_key: str = ""
    rec_label: str = ""
    news_list: List[Dict[str, Any]] = field(default_factory=list)
    fetched_at: str = ""

    # From financial_sources
    summary: str = ""
    earnings_date: str = ""
    eps_estimate: float = 0.0
    pe_ratio: str = ""
    revenue_growth: str = ""

    # Tracking column (from sheet, decides auto-focus)
    tracking: str = ""


def _load_cache(ticker: str) -> Optional[Dict[str, Any]]:
    import json, os
    path = os.path.join(CACHE_DIR, ticker + ".json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(ticker: str, data: Dict[str, Any]) -> None:
    import json, os
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(path := os.path.join(CACHE_DIR, ticker + ".json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
    except Exception:
        pass


def _is_cache_fresh(cache_data, max_age_hours=24) -> bool:
    fetched_at = cache_data.get("fetched_at", "")
    if not fetched_at:
        return False
    price = cache_data.get("current_price", 0)
    if price <= 0:
        return False
    try:
        dt = datetime.strptime(fetched_at, "%Y-%m-%d %H:%M")
        dt = dt.replace(tzinfo=TW_TZ)
        return (datetime.now(TW_TZ) - dt).total_seconds() < max_age_hours * 3600
    except Exception:
        return False


def fetch_us_stock_data(ticker: str) -> Optional[StockMarketData]:
    """Fetch complete market data for one US stock."""
    cached = _load_cache(ticker)
    if cached and _is_cache_fresh(cached):
        sd = StockMarketData(**cached)
        logger.info("%s: using fresh cache", ticker)
        return sd

    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        news_list = []
        try:
            news_list = stock.news[:5] if hasattr(stock, "news") and stock.news else []
        except Exception:
            pass

        hist = stock.history(period="3mo")
        if hist.empty:
            current = info.get("currentPrice") or info.get("regularMarketPrice") or 0
            prev_close = info.get("previousClose") or 0
            high_20d = low_20d = current or 0
            close_5d = close_20d = close_60d = current or 0
            avg_vol_20d = info.get("averageVolume") or 0
            volume = info.get("volume") or 0
        else:
            current = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else current
            highs = hist["High"].tolist()
            lows = hist["Low"].tolist()
            closes = hist["Close"].tolist()
            volumes = hist["Volume"].tolist()
            high_20d = max(highs[-20:]) if len(highs) >= 20 else max(highs)
            low_20d = min(lows[-20:]) if len(lows) >= 20 else min(lows)
            close_5d = sum(closes[-5:]) / 5 if len(closes) >= 5 else closes[-1]
            close_20d = sum(closes[-20:]) / 20 if len(closes) >= 20 else closes[-1]
            close_60d = sum(closes[-60:]) / 60 if len(closes) >= 60 else closes[-1]
            avg_vol_20d = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else (volumes[-1] if volumes else 0)
            volume = int(volumes[-1]) if volumes else 0

        rec_key = (info.get("recommendationKey") or "").lower()
        rec_map = {
            "strong_buy": "強力買進", "buy": "買進", "outperform": "優於大盤",
            "overweight": "超配", "hold": "持有", "equal_weight": "中性",
            "underweight": "低配", "sell": "賣出",
        }
        day_change_pct = ((current - prev_close) / prev_close * 100) if prev_close > 0 else 0

        data = StockMarketData(
            ticker=ticker,
            company_name=info.get("shortName") or info.get("longName") or ticker,
            current_price=current,
            previous_close=prev_close,
            day_change_pct=day_change_pct,
            day_high=current,
            day_low=current,
            volume=volume,
            avg_volume_20d=int(avg_vol_20d),
            close_5d=close_5d,
            close_20d=close_20d,
            close_60d=close_60d,
            high_20d=high_20d,
            low_20d=low_20d,
            mean_target=info.get("targetMeanPrice") or 0,
            high_target=info.get("targetHighPrice") or 0,
            low_target=info.get("targetLowPrice") or 0,
            analysts=info.get("numberOfAnalystOpinions") or 0,
            rec_key=rec_key,
            rec_label=rec_map.get(rec_key, "觀望"),
            news_list=news_list,
            fetched_at=datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M"),
        )
        _save_cache(ticker, data.__dict__)
        return data
    except Exception as exc:
        logger.warning("fetch_us_stock_data failed for %s: %s", ticker, exc)
        # Fallback to cache
        if cached:
            sd = StockMarketData(**cached)
            logger.info("%s: using stale cache", ticker)
            return sd
        return None


def fetch_all_us_stock_data(tickers: List[str]) -> Dict[str, StockMarketData]:
    """Fetch market data for multiple US tickers with retry + cache."""
    results: Dict[str, StockMarketData] = {}
    for ticker in tickers:
        data = None
        for attempt in range(2):
            data = fetch_us_stock_data(ticker)
            if data and data.current_price > 0:
                break
            if attempt < 1:
                wait = (attempt + 1) * 2
                logger.warning("%s: fetch failed, retrying in %ds...", ticker, wait)
                time.sleep(wait)
        if data and data.current_price > 0:
            results[ticker] = data
        else:
            logger.error("%s: no data available", ticker)
    return results


# ---------------------------------------------------------------------------
# Scoring dimensions (same logic as taiwan_market_data.py)
# ---------------------------------------------------------------------------

def _score_heat(data: StockMarketData) -> float:
    score = 0.0
    vol_ratio = data.volume / data.avg_volume_20d if data.avg_volume_20d > 0 else 0
    if vol_ratio >= 5.0: score += 30
    elif vol_ratio >= 3.0: score += 27
    elif vol_ratio >= 2.0: score += 22
    elif vol_ratio >= 1.5: score += 15
    elif vol_ratio >= 1.2: score += 12
    else: score += max(0, vol_ratio * 7)

    abs_chg = abs(data.day_change_pct)
    if abs_chg >= 7.0: score += 25
    elif abs_chg >= 5.0: score += 22
    elif abs_chg >= 3.0: score += 18
    elif abs_chg >= 2.0: score += 13
    elif abs_chg >= 1.0: score += 12
    else: score += abs_chg * 6

    if data.high_20d > 0 and data.current_price > 0:
        dist = (data.high_20d - data.current_price) / data.high_20d * 100
        if dist <= 2: score += 20
        elif dist <= 5: score += 15
        elif dist <= 10: score += 8

    if vol_ratio >= 4.0: score += 10
    elif vol_ratio >= 2.5: score += 6
    return min(100.0, score)


def _score_institutional(data: StockMarketData) -> float:
    score = 0.0
    rec = data.rec_key
    if rec in ("strong_buy",): score += 40
    elif rec in ("buy",): score += 30
    elif rec in ("outperform", "overweight"): score += 25
    elif rec in ("hold", "equal_weight"): score += 10

    n = data.analysts
    if n >= 5: score += 30
    elif n >= 3: score += 20
    elif n >= 1: score += 10

    if data.mean_target > 0 and data.current_price > 0:
        upside = (data.mean_target - data.current_price) / data.current_price * 100
        if upside >= 20: score += 30
        elif upside >= 10: score += 22
        elif upside >= 5: score += 15
        elif upside >= 0: score += 8
        else: score += max(0, 8 + upside)
    return min(100.0, score)


def _score_catalyst(data: StockMarketData) -> float:
    score = 0.0
    if data.mean_target > 0 and data.current_price > 0:
        upside = (data.mean_target - data.current_price) / data.current_price * 100
        if upside >= 15: score += 35
        elif upside >= 8: score += 28
        elif upside >= 3: score += 20
        else: score += 10

    # News sentiment
    _POS = {"beat", "surge", "upgrade", "buy", "growth", "profit", "record",
            "strong", "bullish", "win", "expand"}
    score += max(0, min(30, 15 + sum(1 for kw in _POS
              if kw in " ".join(i.get("title","") for i in data.news_list[:5]).lower()) * 3))
    return min(100.0, score)


def _score_trend(data: StockMarketData) -> float:
    score = 0.0
    price = data.current_price
    if price <= 0: return 0.0
    if data.close_5d > 0 and price > data.close_5d: score += 8
    if data.close_20d > 0 and price > data.close_20d: score += 12
    if data.close_60d > 0 and price > data.close_60d: score += 15
    if data.close_5d > 0 and data.close_20d > 0:
        score += 25 if data.close_5d > data.close_20d else 5
    if data.high_20d > 0 and price > 0:
        dist = (data.high_20d - price) / data.high_20d * 100
        if dist <= 1: score += 20
        elif dist <= 3: score += 16
        elif dist <= 8: score += 10
        elif dist <= 15: score += 5
    if data.avg_volume_20d > 0 and data.volume > 0 and data.day_change_pct > 0:
        ratio = data.volume / data.avg_volume_20d
        score += 20 if ratio >= 1.5 else (12 if ratio >= 1.0 else 5)
    return min(100.0, score)


def _risk_penalty(data: StockMarketData) -> float:
    penalty = 0.0
    if data.day_change_pct <= -5.0: penalty += 5
    elif data.day_change_pct <= -3.0: penalty += 3
    elif data.day_change_pct <= -2.0: penalty += 1
    price = data.current_price
    if price > 0:
        below_all = True
        if data.close_5d > 0 and price > data.close_5d: below_all = False
        if data.close_20d > 0 and price > data.close_20d: below_all = False
        if data.close_60d > 0 and price > data.close_60d: below_all = False
        if below_all: penalty += 4
    if data.mean_target > 0 and price > 0 and price > data.mean_target * 1.2:
        penalty += 3
    return min(20.0, penalty)


def compute_focus_score(data: StockMarketData,
                        h: Dict[str, Any] = None) -> Dict[str, Any]:
    """Compute the full FocusScore breakdown for a US stock."""
    h = h or {}
    mh = _score_heat(data)
    inst = _score_institutional(data)
    cat = _score_catalyst(data)
    tr = _score_trend(data)
    rp = _risk_penalty(data)

    focus_score = 0.20*mh + 0.30*inst + 0.30*cat + 0.10*tr - rp
    focus_score = max(0.0, min(100.0, focus_score))

    if rp >= 8: category = "風險焦點"
    elif focus_score >= 70: category = "偏多焦點"
    elif focus_score >= 60: category = "中性觀察"
    else: category = "一般追蹤"

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
# Public API
# ---------------------------------------------------------------------------

def build_us_focus_report(
    stocks_data: Dict[str, StockMarketData],
    watchlist: List[Dict[str, Any]],
) -> tuple:
    """Build US focus report data. Returns (report_text, qualified_tickers)."""
    from line_notifier import LineNotifier

    FOCUS_THRESHOLD = 60
    MAX_FOCUS = 10

    now_tw = datetime.now(TW_TZ)
    date_str = now_tw.strftime("%Y-%m-%d (%a)")

    all_scores: Dict[str, Dict[str, Any]] = {}
    stock_info: Dict[str, Dict[str, Any]] = {}

    for h in watchlist:
        ticker = str(h.get("ticker", h.get("代碼", ""))).strip().upper()
        if not ticker:
            continue
        h["短名"] = h.get("company_name", h.get("名稱", ""))
        h["tracking"] = str(h.get("tracking", h.get("追蹤", ""))).strip()
        data = stocks_data.get(ticker)
        if data is None or data.current_price <= 0:
            continue
        score_info = compute_focus_score(data, h)
        all_scores[ticker] = score_info
        stock_info[ticker] = {"data": data, "h": h, "score": score_info}

    # Auto-focus from sheet (tracking=焦點股)
    auto_focus: List[str] = []
    for h in watchlist:
        ticker = str(h.get("ticker", h.get("代碼", ""))).strip().upper()
        if not ticker or ticker not in all_scores:
            continue
        tracking = str(h.get("tracking", "")).strip()
        if tracking == "焦點股" and ticker not in [t for t, _ in all_scores.items()]:
            auto_focus.append(ticker)

    qualified = [(t, s) for t, s in all_scores.items() if s["focus_score"] >= FOCUS_THRESHOLD]
    qualified.sort(key=lambda x: x[1]["focus_score"], reverse=True)
    if auto_focus:
        existing = [(t, s) for t, s in qualified if t not in auto_focus]
        auto_entries = [(t, all_scores[t]) for t in auto_focus]
        qualified = auto_entries + existing

    # Build report
    lines = []
    lines.append(f"# 美股焦點股票 | {date_str}")
    lines.append("")

    if qualified:
        for ticker, s in qualified[:MAX_FOCUS]:
            d = stock_info[ticker]["data"]
            h = stock_info[ticker]["h"]
            name = str(h.get("短名", "")).strip() or d.company_name or ticker
            score = s["focus_score"]
            cat = s["category"]
            price = d.current_price
            target = d.mean_target
            rec = d.rec_label

            lines.append(f"📊 {ticker} {name}")
            lines.append(f"💰 現價：${price:.2f}  ({d.day_change_pct:+.2f}%)")
            if target > 0:
                ups = (target - price) / price * 100
                lines.append(f"🎯 目標價：${target:.2f}  (上行空間 {ups:+.1f}%)")
            if rec:
                lines.append(f"📝 分析師建議：{rec} ({d.analysts}家)")
            lines.append(f"🔥 FocusScore：{score:.0f} 分 | {cat}")
            lines.append("")
    else:
        lines.append("今日無符合條件的焦點股。")

    report_text = "\n".join(lines)
    qualified_tickers = [t for t, _ in qualified]
    return report_text, qualified_tickers
