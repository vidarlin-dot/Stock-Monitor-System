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


def classify_catalyst(date_str: str, notes: str = "") -> str:
    """Classify a catalyst date into an event type based on notes.

    Args:
        date_str: The date string.
        notes: The notes field from the sheet.

    Returns:
        Event type like '財報', 'FDA批准', '臨床試驗', etc.
    """
    notes_lower = notes.lower() if notes else ""
    
    # Keywords to detect event types
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
    
    # Default: check if date is near typical earnings months
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        # Feb, May, Aug, Nov are typical earnings months
        if dt.month in [2, 5, 8, 11]:
            return "財報"
    except ValueError:
        pass
    
    return "重要事件"


def get_tradingview_earnings_link(ticker: str) -> str:
    """Generate TradingView earnings calendar link for a ticker."""
    return f"https://tw.tradingview.com/symbols/{ticker}/financials-earnings/"


def get_investing_earnings_link(ticker: str) -> str:
    """Generate Investing.com earnings link for a ticker."""
    return f"https://cn.investing.com/equities/{ticker.lower()}-earnings"


def check_event_with_detail(event_date_str: str, event_type: str, notes: str = "") -> Optional[str]:
    """Check if an event is within 30 days and return formatted string.

    Args:
        event_date_str: Date in YYYY-MM-DD format.
        event_type: Event type like '財報', 'FDA批准'.
        notes: Notes for generating links.

    Returns:
        Formatted reminder or None if expired.
    """
    try:
        event_dt = datetime.strptime(str(event_date_str), "%Y-%m-%d")
    except ValueError:
        return None
    
    today = datetime.now(TW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (event_dt - today).days
    
    if delta < 0:
        return None  # Expired
    
    # Build display with event type
    display = f"{event_type}"
    
    if 0 <= delta <= 7:
        return f"🔥 {display} — {delta} 天後 ({event_date_str})"
    if delta <= 30:
        return f"⚡ {display} — {delta} 天後 ({event_date_str})"
    return None


def build_daily_report(
    holdings_data: List[Dict[str, Any]],
) -> tuple[str, List[Dict[str, Any]]]:
    """Generate a structured daily investment report in Traditional Chinese."""
    now_tw = datetime.now(TW_TZ)
    date_str: str = now_tw.strftime("%Y-%m-%d (%A)")

    report_lines: List[str] = [
        "📈 美股投資日報 | " + date_str,
        "=" * 40,
        "",
    ]

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
            report_lines.append(f"{idx}. {ticker} — ⚠️ 無法取得股價")
            report_lines.append("")
            continue

        pnl_per_share: float = current_price - avg_cost
        pnl_pct: float = (pnl_per_share / avg_cost * 100) if avg_cost > 0 else 0.0
        total_pnl: float = pnl_per_share * shares

        if pnl_pct >= 5:
            emoji = "🟢"
        elif pnl_pct >= 0:
            emoji = "🟡"
        else:
            emoji = "🔴"

        report_lines.append(f"{idx}. {emoji} {ticker}")
        report_lines.append(f"   當前價格: ${current_price:.2f} | 均價: ${avg_cost:.4f}")
        report_lines.append(
            f"   持倉: {int(shares)} 股 | 損益: ${total_pnl:+,.2f} ({pnl_pct:+.2f}%)"
        )

        if buy_zones:
            zone_str = ", ".join(f"${bz:.2f}" for bz in buy_zones)
            if current_price <= buy_zones[0]:
                report_lines.append(f"   🟢 買進訊號 — ${current_price:.2f} ≤ [{zone_str}]")
            else:
                report_lines.append(f"   📌 買進區間: {zone_str}")

        if sell_zones:
            zone_str = ", ".join(f"${sz:.2f}" for sz in sell_zones)
            if current_price >= sell_zones[0]:
                report_lines.append(f"   🔴 賣出訊號 — ${current_price:.2f} ≥ [{zone_str}]")
            else:
                report_lines.append(f"   📌 賣出區間: {zone_str}")

        # Show catalysts with event type
        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            for cd in dates:
                event_type = classify_catalyst(cd, notes)
                event_reminder = check_event_with_detail(cd, event_type, notes)
                if event_reminder:
                    report_lines.append(f"   {event_reminder}")

        if notes:
            report_lines.append(f"   💬 {notes}")

        # Show news
        if stock_info and stock_info.get("news"):
            for news_item in stock_info["news"][:3]:
                title = news_item.get("title", "").strip()
                if title:
                    if len(title) > 80:
                        title = title[:77] + "..."
                    report_lines.append(f"   📰 {title}")

        # Show earnings calendar links
        tv_link = get_tradingview_earnings_link(ticker)
        inv_link = get_investing_earnings_link(ticker)
        report_lines.append(f"   🔗 [TradingView財報]({tv_link}) | [Investing財報]({inv_link})")

        report_lines.append("")

    report_lines.append("=" * 40)
    report_lines.append("💡 以上為自動化產生，投資有風險，操作須謹慎。")

    report = "\n".join(report_lines)
    return report, holdings


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

    report, _ = build_daily_report(holdings)
    print(report)

    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("Daily report sent successfully.")


if __name__ == "__main__":
    main()