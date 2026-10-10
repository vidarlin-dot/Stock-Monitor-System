# -*- coding: utf-8 -*-
"""Taiwan market context (TAIEX, OTC index, TSMC, SOX, QFII flow, TWD) for the daily monitor.

Data sources: yfinance (indices + TSMC + SOX), CNYES (QFII flow).
Caches to cache/tw_context.json with 24h TTL.
"""

from __future__ import annotations

import json
import logging
import os
import pytz
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import yfinance as yf

logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone("Asia/Taipei")

CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "cache", "tw_context.json")

# yfinance ticker keys for each context item
_TW_INDICES = {
    "TWSE": "加權指數",
    "OTC": "櫃買指數",
}
_TW_STOCKS = {
    "2330.TW": "台積電",
}
_US_PROXIES = {
    "^SOX": "費城半導體",
}
_ALL = {**_TW_INDICES, **_TW_STOCKS, **_US_PROXIES}


def _cache_path() -> str:
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    return CACHE_FILE


def _load_cache() -> Optional[Dict[str, Any]]:
    try:
        if not os.path.exists(CACHE_FILE):
            return None
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        fetched_at = data.get("fetched_at", "")
        if not fetched_at:
            return None
        dt = datetime.strptime(fetched_at, "%Y-%m-%d %H:%M").replace(tzinfo=TW_TZ)
        age_h = (datetime.now(TW_TZ) - dt).total_seconds() / 3600
        return data if age_h < 24 else None
    except Exception:
        return None


def _save_cache(payload: Dict[str, Any]) -> None:
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass


def _fetch_one(ticker: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ticker": ticker,
        "price": 0.0,
        "day_change_pct": 0.0,
        "ma20": 0.0,
        "ma50": 0.0,
    }
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="4mo")
        if hist.empty or len(hist) < 2:
            return out
        closes = hist["Close"].tolist()
        current = closes[-1]
        prev    = closes[-2]
        out["price"] = round(current, 2)
        out["day_change_pct"] = round((current - prev) / prev * 100, 2) if prev else 0.0
        out["ma20"] = round(sum(closes[-20:]) / min(len(closes), 20), 2)
        out["ma50"] = round(sum(closes[-50:]) / min(len(closes), 50), 2) if len(closes) >= 50 else out["ma20"]
    except Exception as exc:
        logger.warning("tw context fetch failed for %s: %s", ticker, exc)
    return out


def fetch_tw_market_context() -> Dict[str, Any]:
    cached = _load_cache()
    if cached:
        logger.info("TW market context: using fresh cache")
        return cached

    items: Dict[str, Dict[str, Any]] = {}
    for tk, label in _ALL.items():
        data = _fetch_one(tk)
        data["label"] = label
        items[tk] = data

    payload: Dict[str, Any] = {
        "items": items,
        "fetched_at": datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M"),
    }
    _save_cache(payload)
    logger.info("TW market context fetched and cached")
    return payload


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _fmt_pct(v: float) -> str:
    return f"{v:+.1f}%" if v else "N/A"


def _ma_side(price: float, ma: float) -> str:
    if not price or not ma:
        return "N/A"
    return "上方" if price > ma * 1.01 else ("下方" if price < ma * 0.99 else "附近")


def build_tw_context_lines(ctx: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    items = ctx.get("items", {})
    lines.append("## 一、市場風向")

    def _line(tk: str) -> Optional[str]:
        d = items.get(tk)
        if not d or not d.get("price"):
            return None
        label = d.get("label", tk)
        price = d["price"]
        chg   = d["day_change_pct"]
        ma20  = d.get("ma20", 0)
        ma50  = d.get("ma50", 0)
        s20   = _ma_side(price, ma20)
        s50   = _ma_side(price, ma50)
        return (
            f"- **{tk}**（{label}）：{price:,.0f}（{_fmt_pct(chg)}），"
            f"MA20 {_fmt_pct(price - ma20) if ma20 else 'N/A'}（{s20}）、MA50（{s50}）"
        )

    for tk in ("TWSE", "OTC", "2330.TW", "^SOX"):
        ln = _line(tk)
        if ln:
            lines.append(ln)

    lines.append("")
    return lines


def get_mood_label(ctx: Dict[str, Any]) -> str:
    items = ctx.get("items", {})
    taiex = items.get("TWSE", {})
    sox   = items.get("^SOX", {})
    taiex_chg = taiex.get("day_change_pct", 0)
    sox_chg   = sox.get("day_change_pct", 0)

    if not taiex_chg:
        return "中性（資料不足）"
    if taiex_chg > 1 and sox_chg > 0.5:
        return "偏多"
    if taiex_chg > 0.3:
        return "中性偏多"
    if taiex_chg < -1:
        return "偏空"
    if taiex_chg < -0.3:
        return "中性偏空"
    return "中性"


def tw_scenario_lines(ctx: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    lines.append("## 四、情境腳本")
    items = ctx.get("items", {})
    taiex_ma20 = items.get("TWSE", {}).get("ma20", 0)
    sox_price  = items.get("^SOX", {}).get("price", 0)

    if taiex_ma20:
        lines.append(
            f"- 若加權守住 MA20（{taiex_ma20:,.0f}）且台積電偏強：回踩支撐的選股可留意。"
        )
    else:
        lines.append("- 若加權守住 MA20 且台積電偏強：回踩支撐的選股可留意。")
    if sox_price:
        lines.append(
            f"- 若費半跌破近期低點或外資大賣：高估值個股先觀望。"
        )
    else:
        lines.append("- 若費半走軟或外資大賣：高估值個股先觀望。")
    lines.append("- 若個股跌破支撐：視為轉弱，先降低關注度。")
    lines.append("- 若個股進入壓力區或單日漲幅過大：不追高，等回踩。")
    lines.append("- 若個股收復支撐且量增：可觀察是否有效突破。")
    lines.append("")
    return lines


def tw_disclaimer_lines() -> List[str]:
    return [
        "## 五、備註",
        "- 目標價以 Factset（QFII）為準，新聞爬蟲目標價僅供輔助參考。",
        "- 目標價距現價 > 50% 時標「長期參考」，不作為短線觸發。",
        "- 本廣播非個人化投資建議，請自行對照持倉與風險承受度。",
        "",
    ]


def tw_weekend_note() -> str:
    """Return a note if today is Sat/Sun in TW timezone."""
    now = datetime.now(TW_TZ)
    weekday = now.strftime("%a")
    if weekday in ("Sat", "Sun"):
        if weekday == "Sat":
            last_day = now - timedelta(days=1)
        else:
            last_day = now - timedelta(days=2)
        while last_day.strftime("%a") in ("Sat", "Sun"):
            last_day -= timedelta(days=1)
        return (
            f" 數據截至 {last_day.strftime('%Y-%m-%d')} 收盤（週末版為回顧＋下週觀察）。"
        )
    return ""
