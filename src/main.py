"""Daily monitoring main entry point."""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz
import yfinance as yf

from config import GoogleSheetsManager
from line_notifier import LineNotifier

logger = logging.getLogger(__name__)

TW_TZ = pytz.timezone("Asia/Taipei")
US_EASTERN = pytz.timezone("America/New_York")


def fetch_stock_info(ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch stock info including news."""
    try:
        stock = yf.Ticker(ticker)
        info = {}

        try:
            news_list = stock.news
            if news_list:
                info["news"] = []
                for item in news_list[:10]:
                    info["news"].append({
                        "title": item.get("title", ""),
                        "publisher": item.get("publisher", ""),
                        "link": item.get("link", ""),
                    })
        except Exception:
            info["news"] = []

        return info if info.get("news") else None

    except Exception as exc:
        logger.warning("Failed to fetch info for %s: %s", ticker, exc)
        return None


def translate_news_to_chinese(title: str, publisher: str = "") -> str:
    """Translate English news title to Traditional Chinese summary."""
    title_lower = title.lower()
    
    translations = {
        "earnings": "財報",
        "revenue": "營收",
        "profit": "獲利",
        "loss": "虧損",
        "beat": "超越",
        "miss": "低於",
        "expect": "預期",
        "guidance": "展望",
        "raise": "上調",
        "cut": "下調",
        "upgrade": "上調評級",
        "downgrade": "下調評級",
        "approval": "批准",
        "fda": "FDA",
        "phase 3": "三期臨床",
        "phase ii": "二期臨床",
        "phase i": "一期臨床",
        "trial": "試驗",
        "results": "結果",
        "positive": "正面",
        "negative": "負面",
        "launch": "推出",
        "product": "產品",
        "partnership": "合作",
        "acquisition": "收購",
        "buyback": "回購",
        "dividend": "配息",
        "increase": "增加",
        "decrease": "減少",
        "growth": "成長",
        "demand": "需求",
        "supply": "供應",
        "chip": "晶片",
        "ai": "人工智慧",
        "quantum": "量子",
        "drug": "藥物",
        "cancer": "癌症",
        "obesity": "肥胖",
        "diabetes": "糖尿病",
        "cardiovascular": "心血管",
        "record": "創紀錄",
        "strong": "強勁",
        "weak": "疲弱",
        "solid": "穩健",
        "robust": "強勁",
        "surge": "飆升",
        "plunge": "暴跌",
        "rally": "反彈",
        "pullback": "回調",
        "volatility": "波動",
        "risk": "風險",
        "opportunity": "機會",
        "challenge": "挑戰",
        "momentum": "動能",
        "tailwind": "助力",
        "headwind": "逆風",
    }
    
    chinese_parts: List[str] = []
    remaining = title
    
    for en_word, zh_word in sorted(translations.items(), key=lambda x: -len(x[0])):
        if en_word in remaining.lower():
            chinese_parts.append(zh_word)
            remaining = re.sub(re.escape(en_word), "", remaining, flags=re.IGNORECASE).strip()
    
    remaining = re.sub(r'[^\w\s\-]', '', remaining).strip()
    
    if chinese_parts:
        summary = " ".join(chinese_parts)
        if remaining and len(remaining) < 50:
            summary += f"：{remaining}"
        return summary
    else:
        cleaned = re.sub(r'[^\w\s\-]', '', remaining).strip()
        return cleaned[:60] if len(cleaned) > 60 else cleaned


def classify_catalyst(date_str: str, notes: str = "") -> str:
    """Classify a catalyst date into an event type."""
    notes_lower = notes.lower() if notes else ""
    
    keywords = {
        "財報": ["財報", "earnings", "quarterly", "q1", "q2", "q3", "q4", "季報", "年報"],
        "FDA批准": ["fda", "批准", "認證", "regulatory", "nda", "bla"],
        "臨床試驗": ["phase 3", "phase ii", "phase i", "臨床", "試驗", "data readout"],
        "量產": ["量產", "production", "manufacturing", "產能"],
        "合作": ["合作", "partnership", "collaboration", "alliance"],
        "產品發布": ["推出", "launch", "release", "新品"],
        "融資/上市": ["ipo", "listing", "上市", "融資", "fundraising"],
        "評級調整": ["upgrade", "downgrade", "目標價", "rating", "分析師"],
    }
    
    for event_type, kws in keywords.items():
        if any(kw in notes_lower for kw in kws):
            return event_type
    
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if dt.month in [2, 5, 8, 11]:
            return "財報"
    except ValueError:
        pass
    
    return "重要事件"


def check_event_with_detail(event_date_str: str, event_type: str) -> Optional[str]:
    """Check if an event is within 30 days and return formatted string.
    
    Uses colored icons:
    - <= 7 days: red alarm clock (urgent)
    - <= 30 days: blue/grey calendar (reminder)
    """
    try:
        event_dt = datetime.strptime(str(event_date_str), "%Y-%m-%d")
    except ValueError:
        return None
    
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (event_dt - today).days
    
    if delta < 0:
        return None
    
    display = event_type
    
    if 0 <= delta <= 7:
        return f"⏰ {display} — {delta} 天後 ({event_date_str})"
    if delta <= 30:
        return f"📅 {display} — {delta} 天後 ({event_date_str})"
    return None


def build_daily_report(
    holdings_data: List[Dict[str, Any]],
) -> str:
    """Generate a structured daily investment report in Traditional Chinese.
    
    Layout:
    1. Header with date
    2. ESSENTIALS: Stocks with buy/sell signals or events <= 7 days
    3. VOLATILE: Stocks with >5% gain or <0% loss
    4. STABLE: Stocks with 0~5% gain (compact list)
    5. Footer
    """
    now_tw = datetime.now(TW_TZ)
    date_str: str = now_tw.strftime("%Y-%m-%d (%A)")

    # Collect all stock data first
    stock_data: List[Dict[str, Any]] = []

    for idx, h in enumerate(holdings_data, start=1):
        ticker: str = str(h.get("ticker", h.get("代碼", "?"))).strip().upper()
        if not ticker:
            continue

        shares: float = float(h.get("shares", h.get("股數", 0)))
        avg_cost: float = float(h.get("avgcost", h.get("均價", 0)))
        buy_zone_raw = h.get("buyzone", h.get("買進區間", ""))
        sell_zone_raw = h.get("sellzone", h.get("賣出區間", ""))
        catalyst_raw = h.get("catalystdate", h.get("催化劑日期", ""))
        notes: str = str(h.get("notes", h.get("備註", ""))).strip()

        buy_zones: List[float] = []
        if buy_zone_raw:
            for bz in str(buy_zone_raw).split(","):
                try:
                    buy_zones.append(float(bz.strip()))
                except ValueError:
                    pass

        sell_zones: List[float] = []
        if sell_zone_raw:
            for sz in str(sell_zone_raw).split(","):
                try:
                    sell_zones.append(float(sz.strip()))
                except ValueError:
                    pass

        stock_info = fetch_stock_info(ticker)

        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5d")
            current_price = float(hist["Close"].iloc[-1]) if not hist.empty else None
        except Exception:
            current_price = None

        if current_price is None:
            stock_data.append({
                "idx": idx,
                "ticker": ticker,
                "current_price": None,
                "avg_cost": avg_cost,
                "shares": shares,
                "pnl_pct": 0,
                "total_pnl": 0,
                "buy_zones": buy_zones,
                "sell_zones": sell_zones,
                "catalyst_raw": catalyst_raw,
                "notes": notes,
                "news": stock_info.get("news", []) if stock_info else [],
                "signal": "無法取得股價",
            })
            continue

        pnl_per_share: float = current_price - avg_cost
        pnl_pct: float = (pnl_per_share / avg_cost * 100) if avg_cost > 0 else 0.0
        total_pnl: float = pnl_per_share * shares

        # Determine signal
        signal = ""
        if buy_zones and current_price <= buy_zones[0]:
            signal = "buy"
        elif sell_zones and current_price >= sell_zones[0]:
            signal = "sell"
        
        # Check for urgent events (<= 7 days)
        has_urgent_event = False
        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            for cd in dates:
                event_reminder = check_event_with_detail(cd, classify_catalyst(cd, notes))
                if event_reminder and "⏰" in event_reminder:
                    has_urgent_event = True
                    break

        stock_data.append({
            "idx": idx,
            "ticker": ticker,
            "current_price": current_price,
            "avg_cost": avg_cost,
            "shares": shares,
            "pnl_pct": pnl_pct,
            "total_pnl": total_pnl,
            "buy_zones": buy_zones,
            "sell_zones": sell_zones,
            "catalyst_raw": catalyst_raw,
            "notes": notes,
            "news": stock_info.get("news", []) if stock_info else [],
            "signal": signal,
            "has_urgent_event": has_urgent_event,
        })

    # Sort into 3 tiers
    essentials: List[Dict] = []  # buy/sell signals or urgent events
    volatile: List[Dict] = []     # >5% gain or <0% loss
    stable: List[Dict] = []       # 0~5% gain

    for sd in stock_data:
        if sd["signal"] in ("buy", "sell") or sd["has_urgent_event"]:
            essentials.append(sd)
        elif sd["pnl_pct"] > 5 or sd["pnl_pct"] < 0:
            volatile.append(sd)
        else:
            stable.append(sd)

    # Build report
    report_lines: List[str] = [
        "📈 美股投資日報 | " + date_str,
        "=" * 40,
        "",
    ]

    # Tier 1: ESSENTIALS
    if essentials:
        report_lines.append("🔥 精華區 — 立即關注")
        report_lines.append("-" * 30)
        for sd in essentials:
            ticker = sd["ticker"]
            cp = sd["current_price"]
            ac = sd["avg_cost"]
            shares = int(sd["shares"])
            total_pnl = sd["total_pnl"]
            pnl_pct = sd["pnl_pct"]

            if cp is None:
                report_lines.append(f"• {ticker} — ⚠️ 無法取得股價")
                continue

            # Price emoji
            if pnl_pct >= 5:
                emoji = "🟢"
            elif pnl_pct >= 0:
                emoji = "⚪"
            else:
                emoji = "🔴"

            report_lines.append(f"{emoji} {ticker} | 當前$: {cp:.2f} | 均價$: {ac:.4f} | 持倉: {shares} 股 | 損益$: {total_pnl:+,.2f} ({pnl_pct:+.2f}%)")

            # Buy signal
            if sd["signal"] == "buy" and sd["buy_zones"]:
                zone_str = ", ".join(f"${bz:.2f}" for bz in sd["buy_zones"])
                report_lines.append(f"  🔺 買進訊號 — ${cp:.2f} ≤ [{zone_str}]")

            # Sell signal
            if sd["signal"] == "sell" and sd["sell_zones"]:
                zone_str = ", ".join(f"${sz:.2f}" for sz in sd["sell_zones"])
                report_lines.append(f"  🔻 賣出訊號 — ${cp:.2f} ≥ [{zone_str}]")

            # Urgent events
            if sd["has_urgent_event"] and sd["catalyst_raw"]:
                dates = [d.strip() for d in str(sd["catalyst_raw"]).split(",") if d.strip()]
                for cd in dates:
                    event_type = classify_catalyst(cd, sd["notes"])
                    event_reminder = check_event_with_detail(cd, event_type)
                    if event_reminder and "⏰" in event_reminder:
                        report_lines.append(f"  {event_reminder}")

            # Notes
            if sd["notes"]:
                report_lines.append(f"  💬 {sd['notes']}")

            # News
            if sd["news"]:
                for news_item in sd["news"][:2]:
                    title = news_item.get("title", "").strip()
                    if title:
                        chinese_summary = translate_news_to_chinese(title)
                        if chinese_summary:
                            report_lines.append(f"  📰 {chinese_summary}")

            report_lines.append("")

    # Tier 2: VOLATILE
    if volatile:
        report_lines.append("📊 波動區 — 漲跌幅 >5% 或 <0%")
        report_lines.append("-" * 30)
        for sd in volatile:
            ticker = sd["ticker"]
            cp = sd["current_price"]
            ac = sd["avg_cost"]
            shares = int(sd["shares"])
            total_pnl = sd["total_pnl"]
            pnl_pct = sd["pnl_pct"]

            if cp is None:
                report_lines.append(f"• {ticker} — ⚠️ 無法取得股價")
                continue

            if pnl_pct >= 5:
                emoji = "🟢"
            elif pnl_pct >= 0:
                emoji = "⚪"
            else:
                emoji = "🔴"

            report_lines.append(f"{emoji} {ticker} | 當前$: {cp:.2f} | 均價$: {ac:.4f} | 持倉: {shares} 股 | 損益$: {total_pnl:+,.2f} ({pnl_pct:+.2f}%)")

            if sd["notes"]:
                report_lines.append(f"  💬 {sd['notes']}")

            report_lines.append("")

    # Tier 3: STABLE (compact list)
    if stable:
        report_lines.append("📋 平穩區 — 0~5% 波動")
        report_lines.append("-" * 30)
        stable_items: List[str] = []
        for sd in stable:
            ticker = sd["ticker"]
            cp = sd["current_price"]
            ac = sd["avg_cost"]
            shares = int(sd["shares"])
            total_pnl = sd["total_pnl"]
            pnl_pct = sd["pnl_pct"]

            if cp is None:
                stable_items.append(f"{ticker} — ⚠️ 無法取得股價")
                continue

            if pnl_pct >= 5:
                emoji = "🟢"
            elif pnl_pct >= 0:
                emoji = "⚪"
            else:
                emoji = "🔴"

            stable_items.append(f"{emoji} {ticker}: {cp:.2f} | 持倉: {shares} 股 | 損益$: {total_pnl:+,.2f} ({pnl_pct:+.2f}%)")

        report_lines.extend(stable_items)
        report_lines.append("")

    report_lines.append("=" * 40)
    report_lines.append("💡 以上為自動化產生，投資有風險，操作須謹慎。")

    return "\n".join(report_lines)


def main() -> None:
    """Main entry point for the daily monitor."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info("Stock Monitor — Daily Report starting…")

    manager = GoogleSheetsManager()
    data = manager.load_config()
    holdings = data["holdings"]

    if not holdings:
        logger.warning("No holdings found.")
        return

    report = build_daily_report(holdings)
    print(report)

    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("Daily report sent successfully.")


if __name__ == "__main__":
    main()