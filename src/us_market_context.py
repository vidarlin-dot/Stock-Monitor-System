# -*- coding: utf-8 -*-
"""US market context (sector ETFs, VIX, 10Y yield, DXY) for the daily monitor.

Data source: yfinance. Caches to cache/us_context.json with 24h TTL.
"""

from __future__ import annotations

import json
import logging
import os
import pytz
from datetime import datetime
from typing import Any, Dict, List, Optional

import yfinance as yf

logger = logging.getLogger(__name__)
TICKER = pytz.timezone("America/New_York")
TZ = pytz.timezone("America/New_York")

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "cache", "us_context.json")

CONTEXT_TICKERS = {
    # --- Sector / index ETFs (price + daily change + 20/50MA) ---
    "SMH": "半導體 ETF",
    "SOXX": "半導體 ETF (SPDR)",
    "QQQ": "納指 100 ETF",
    "SPY": "標普 500 ETF",
    "XLK": "科技 ETF",
    "XLE": "能源 ETF",
    "XLU": "公用事業 ETF",
}
# VIX uses ticker ^VIX, 10Y yield is ^TNX (10-yr TNote), DXY is DX-Y.NYB
VIX_TK   = "^VIX"
TNX_TK   = "^TNX"
DXY_TK   = "DX-Y.NYB"

def _cache_path() -> str:
    os.makedirs(os.path.dirname(CACHE_DIR), exist_ok=True)
    return CACHE_DIR


def _load_cache() -> Optional[Dict[str, Any]]:
    try:
        if not os.path.exists(CACHE_DIR):
            return None
        with open(CACHE_DIR, encoding="utf-8") as f:
            data = json.load(f)
        fetched_at = data.get("fetched_at", "")
        if not fetched_at:
            return None
        dt = datetime.strptime(fetched_at, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        age_h = (datetime.now(TZ) - dt).total_seconds() / 3600
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
    """Fetch price + 20d/50d MA for a single ticker via yfinance."""
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
        current  = closes[-1]
        prev     = closes[-2]
        out["price"] = round(current, 4)
        out["day_change_pct"] = round((current - prev) / prev * 100, 2) if prev else 0.0
        out["ma20"] = round(sum(closes[-20:]) / min(len(closes), 20), 4)
        out["ma50"] = round(sum(closes[-50:]) / min(len(closes), 50), 4) if len(closes) >= 50 else out["ma20"]
    except Exception as exc:
        logger.warning("context fetch failed for %s: %s", ticker, exc)
    return out


def _fetch_vix() -> Dict[str, Any]:
    out: Dict[str, Any] = {"price": 0.0, "day_change_pct": 0.0, "ma20": 0.0, "ma50": 0.0}
    try:
        stock = yf.Ticker(VIX_TK)
        hist = stock.history(period="1mo")
        if not hist.empty and len(hist) > 1:
            out["price"] = round(float(hist["Close"].iloc[-1]), 2)
            out["day_change_pct"] = round(
                (float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[-2]))
                / float(hist["Close"].iloc[-2]) * 100, 2)
    except Exception as exc:
        logger.warning("VIX fetch failed: %s", exc)
    return out


def _fetch_10y() -> Dict[str, Any]:
    out: Dict[str, Any] = {"price": 0.0, "day_change_pct": 0.0}
    try:
        stock = yf.Ticker(TNX_TK)
        hist = stock.history(period="5d")
        if not hist.empty and len(hist) > 1:
            out["price"] = round(float(hist["Close"].iloc[-1]), 2)
            out["day_change_pct"] = round(
                (float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[-2]))
                / 100, 2)  # yield expressed in bps
    except Exception as exc:
        logger.warning("10Y fetch failed: %s", exc)
    return out


def _fetch_dxy() -> Dict[str, Any]:
    out: Dict[str, Any] = {"price": 0.0, "day_change_pct": 0.0}
    try:
        stock = yf.Ticker(DXY_TK)
        hist = stock.history(period="5d")
        if not hist.empty and len(hist) > 1:
            out["price"] = round(float(hist["Close"].iloc[-1]), 2)
            out["day_change_pct"] = round(
                (float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[-2]))
                / float(hist["Close"].iloc[-2]) * 100, 2)
    except Exception as exc:
        logger.warning("DXY fetch failed: %s", exc)
    return out


def fetch_market_context() -> Dict[str, Any]:
    """Return a dict with keys: etfs, vix, tnx10, dxy, fetched_at."""
    cached = _load_cache()
    if cached:
        logger.info("US context: using fresh cache")
        return cached

    etfs: Dict[str, Dict[str, Any]] = {}
    for tk, label in CONTEXT_TICKERS.items():
        data = _fetch_one(tk)
        data["label"] = label
        etfs[tk] = data

    # Sector rotation: XLK (tech) vs XLE (energy) day change
    xlk_chg = etfs.get("XLK", {}).get("day_change_pct", 0)
    xle_chg = etfs.get("XLE", {}).get("day_change_pct", 0)
    sector_rotation = "XLE 較強（防禦）" if xle_chg > xlk_chg else "XLK 較強（風險偏好）"

    payload: Dict[str, Any] = {
        "etfs": etfs,
        "vix": _fetch_vix(),
        "tnx10": _fetch_10y(),
        "dxy": _fetch_dxy(),
        "sector_rotation": sector_rotation,
        "fetched_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
    }
    _save_cache(payload)
    logger.info("US market context fetched and cached")
    return payload


# ---------------------------------------------------------------------------
# Report rendering helpers
# ---------------------------------------------------------------------------

def _fmt_pct(v: float) -> str:
    return f"{v:+.1f}%" if v else "N/A"


def _fmt_num(v: float, dec: int = 2) -> str:
    return f"{v:,.{dec}f}" if v else "N/A"


def _ma_side(price: float, ma: float) -> str:
    if not price or not ma:
        return "N/A"
    return "上方" if price > ma * 1.01 else ("下方" if price < ma * 0.99 else "附近")


def build_market_context_lines(ctx: Dict[str, Any]) -> List[str]:
    """Render the compact 市場風向 lines (SMH + VIX/10Y/DXY)."""
    lines: List[str] = []
    etfs = ctx.get("etfs", {})
    vix  = ctx.get("vix", {})
    tnx  = ctx.get("tnx10", {})
    dxy  = ctx.get("dxy", {})

    lines.append("🌐 市場風向")

    smh = etfs.get("SMH", {})
    if smh.get("price"):
        s20 = _ma_side(smh["price"], smh.get("ma20", 0))
        s50 = _ma_side(smh["price"], smh.get("ma50", 0))
        lines.append(
            f"📉 SMH 半導體ETF {smh['price']:,.2f}（{_fmt_pct(smh.get('day_change_pct', 0))}）"
            f"｜MA20 {s20}｜MA50 {s50}"
        )

    vix_parts: List[str] = []
    if vix.get("price"):
        vix_parts.append(f"😨 VIX {vix['price']}")
    if tnx.get("price"):
        vix_parts.append(f"10Y {tnx['price']}%")
    if dxy.get("price"):
        vix_parts.append(f"DXY {dxy['price']}")
    if vix_parts:
        lines.append("｜".join(vix_parts))

    rot = ctx.get("sector_rotation", "")
    if rot:
        lines.append(f"🔄 主題輪動：{rot}")

    lines.append("")
    return lines


def get_mood_label(ctx: Dict[str, Any]) -> str:
    """Simple sentiment label from SMH + VIX."""
    etfs = ctx.get("etfs", {})
    vix  = ctx.get("vix", {})
    smh  = etfs.get("SMH", {})
    smh_chg = smh.get("day_change_pct", 0)
    vix_price = vix.get("price", 0)

    if not smh_chg and not vix_price:
        return "中性（資料不足）"

    # VIX threshold + SMH relative move
    if smh_chg > 1 and vix_price and vix_price < 17:
        return "偏多"
    if smh_chg > 0.5 and vix_price and vix_price < 20:
        return "中性偏多"
    if smh_chg < -1 and (vix_price or 0) > 19:
        return "偏空"
    if smh_chg < -0.5 and (vix_price or 0) > 16:
        return "中性偏空"
    return "中性"


def scenario_lines(ctx: Dict[str, Any]) -> List[str]:
    """Render 情境腳本 with real numbers where available."""
    etfs = ctx.get("etfs", {})
    vix  = ctx.get("vix", {})
    smh  = etfs.get("SMH", {})
    smh_ma20 = smh.get("ma20", 0)
    smh_ma50 = smh.get("ma50", 0)

    lines: List[str] = []
    lines.append("⚡ 情境腳本")
    if smh_ma20:
        lines.append(
            f"- SMH 守 MA20 {smh_ma20:,.0f} 且 VIX < 16：回踩支撐可留意。"
        )
    else:
        lines.append("- SMH 守 MA20 且 VIX < 16：回踩支撐可留意。")
    if smh_ma50:
        lines.append(
            f"- SMH 跌破 MA50 {smh_ma50:,.0f} 或 VIX > 20：高 Beta、高估值先觀望。"
        )
    else:
        lines.append("- SMH 跌破 MA50 或 VIX > 20：高 Beta、高估值先觀望。")
    lines.append("- NVDA 弱、MU / LITE 強：可能只是輪動。")
    lines.append("- 進壓力區或單日漲幅過大：不追高，等回踩。")
    lines.append("- 跌破支撐：視為轉弱，降低關注。")
    lines.append("")
    return lines


def top_analyst_lines(all_scores: Dict[str, Dict[str, Any]], top_n: int = 5) -> List[str]:
    """Render the 分析師最熱 Top N block (by analyst count desc)."""
    ranked = [
        (t, s.get("analysts", 0))
        for t, s in all_scores.items()
        if s.get("analysts", 0) > 0
    ]
    ranked.sort(key=lambda x: x[1], reverse=True)
    ranked = ranked[:top_n]
    if not ranked:
        return ["📈 分析師最熱 Top 5", "- 無資料", ""]
    top = max(n for _, n in ranked)
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = ["📈 分析師最熱 Top 5"]
    for i, (tk, n) in enumerate(ranked):
        prefix = medals[i] if i < len(medals) else f"{i+1}."
        bar_len = max(1, round(n * 12 / top)) if top else 1
        lines.append(f"{prefix} {tk} {n}家 {'█' * bar_len}")
    lines.append("")
    return lines


def disclaimer_lines() -> List[str]:
    return [
        "📝 備註",
        "- 分析師覆蓋僅供參考，不顯示 0→N 變化；前值為 0 時標「待確認」。",
        "- 目標價距現價 > 50% 時標「長期參考」，不作為短線觸發。",
        "- 本廣播非個人化投資建議，請自行對照持倉與風險承受度。",
        "",
    ]


def weekend_note() -> str:
    """Return a note if today is a US weekend (Sat/Sun in America/New_York)."""
    now = datetime.now(TZ)
    weekday = now.strftime("%a")
    if weekday in ("Sat", "Sun"):
        # Find most recent Friday
        days_ahead = 0
        if weekday == "Sat":
            days_ahead = 0
        else:
            days_ahead = 1
        from datetime import timedelta
        last_friday = now - timedelta(days=days_ahead)
        while last_friday.strftime("%a") in ("Sat", "Sun"):
            last_friday -= timedelta(days=1)
        return (
            f" 數據截至 {last_friday.strftime('%Y-%m-%d')} 美股收盤（週六版為回顧＋下週觀察）。"
        )
    return ""
