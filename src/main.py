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


def translate_news_to_chinese(title: str) -> str:
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
        "ai": "AI",
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
    """Check if an event is within 30 days and return formatted string."""
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


def get_event_description(ticker: str, catalyst_raw: str, notes: str, news: List[Dict]) -> str:
    """Build a detailed event description from multiple sources.
    
    Returns a concise Chinese description of upcoming events.
    """
    descriptions: List[str] = []
    
    # From notes field (user-defined)
    if notes and notes not in ("見備註", "see notes", "(見備註)"):
        descriptions.append(notes)
    
    # From news headlines
    if news:
        for news_item in news[:3]:
            title = news_item.get("title", "").strip()
            if title:
                chinese_summary = translate_news_to_chinese(title)
                if chinese_summary and chinese_summary not in descriptions:
                    descriptions.append(chinese_summary)
    
    # From catalyst dates
    if catalyst_raw:
        dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
        for cd in dates:
            event_type = classify_catalyst(cd, notes)
            event_reminder = check_event_with_detail(cd, event_type)
            if event_reminder:
                descriptions.append(event_reminder)
    
    return " | ".join(descriptions[:3])  # Max 3 items


def build_daily_report(
    holdings_data: List[Dict[str, Any]],
) -> str:
    """Generate a concise daily report with only essential alerts."""
    now_tw = datetime.now(TW_TZ)
    date_str: str = now_tw.strftime("%Y-%m-%d (%A)")

    report_lines: List[str] = [
        "📈 美股投資日報 | " + date_str,
        "=" * 40,
        "",
    ]

    # Track what we found
    has_alerts = False

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

        # Clean up notes that have no meaning
        if notes in ("見備註", "see notes", "(見備註)", "N/A"):
            notes = ""

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
            continue

        pnl_per_share: float = current_price - avg_cost
        pnl_pct: float = (pnl_per_share / avg_cost * 100) if avg_cost > 0 else 0.0
        total_pnl: float = pnl_per_share * shares

        # Check for alerts
        alert_items: List[str] = []

        # Buy signal
        if buy_zones and current_price <= buy_zones[0]:
            zone_str = ", ".join(f"${bz:.2f}" for bz in buy_zones)
            alert_items.append(f"🔺 買進: ${current_price:.2f} ≤ [{zone_str}]")

        # Sell signal
        if sell_zones and current_price >= sell_zones[0]:
            zone_str = ", ".join(f"${sz:.2f}" for sz in sell_zones)
            alert_items.append(f"🔻 賣出: ${current_price:.2f} ≥ [{zone_str}]")

        # Urgent events (<= 7 days)
        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            for cd in dates:
                event_type = classify_catalyst(cd, notes)
                event_reminder = check_event_with_detail(cd, event_type)
                if event_reminder and "⏰" in event_reminder:
                    alert_items.append(event_reminder)

        # Significant P&L (>5% gain or any loss)
        if pnl_pct > 5:
            alert_items.append(f"🟢 大漲 {pnl_pct:+.2f}%")
        elif pnl_pct < 0:
            alert_items.append(f"🔴 下跌 {pnl_pct:+.2f}%")

        # Only show if there are alerts
        if not alert_items:
            continue

        has_alerts = True

        # P&L emoji
        if pnl_pct >= 5:
            pnl_emoji = "🟢"
        elif pnl_pct >= 0:
            pnl_emoji = "⚪"
        else:
            pnl_emoji = "🔴"

        report_lines.append(f"{pnl_emoji} {ticker} | 當前$: {current_price:.2f} | 均價$: {avg_cost:.2f} | 持倉: {int(shares)} 股 | 損益$: {total_pnl:+,.2f} ({pnl_pct:+.2f}%)")

        for alert in alert_items:
            report_lines.append(f"  {alert}")

        # Event description from news
        event_desc = get_event_description(ticker, catalyst_raw, notes, stock_info.get("news", []) if stock_info else [])
        if event_desc:
            report_lines.append(f"  📝 {event_desc}")

        report_lines.append("")

    if not has_alerts:
        report_lines.append("✅ 今日無特別關注事項")
        report_lines.append("")

    report_lines.append("=" * 40)
    report_lines.append("💡 投資有風險，操作須謹慎。")

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