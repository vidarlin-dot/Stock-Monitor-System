"""Daily monitoring main entry point."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz
import yfinance as yf

from config import GoogleSheetsManager
from line_notifier import LineNotifier

logger = logging.getLogger(__name__)

US_EASTERN = pytz.timezone("America/New_York")


def fetch_stock_info(ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch stock info including news and earnings dates via yfinance."""
    try:
        stock = yf.Ticker(ticker)
        
        info = {}
        
        # Get earnings dates
        try:
            calendar = stock.calendar
            if calendar and isinstance(calendar, dict) and calendar:
                dates_list = list(calendar.values())[0] if calendar else []
                if dates_list:
                    info["earnings_dates"] = dates_list[:3]  # Next 3 dates
        except Exception:
            info["earnings_dates"] = []
        
        # Get news
        try:
            news_list = stock.news
            if news_list:
                info["news"] = []
                for item in news_list[:5]:  # Top 5 news
                    info["news"].append({
                        "title": item.get("title", ""),
                        "publisher": item.get("publisher", ""),
                        "published_at": item.get("providerPublishTime", ""),
                    })
        except Exception:
            info["news"] = []
        
        return info if info.get("earnings_dates") or info.get("news") else None
        
    except Exception as exc:
        logger.warning("Failed to fetch info for %s: %s", ticker, exc)
        return None


def check_catalyst(catalyst_date_str: Optional[str]) -> Optional[str]:
    """Check if a catalyst event is within 30 days."""
    if not catalyst_date_str:
        return None
    try:
        catalyst_dt = datetime.strptime(str(catalyst_date_str), "%Y-%m-%d")
    except ValueError:
        logger.warning("Invalid catalyst date format: %s", catalyst_date_str)
        return None
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (catalyst_dt - today).days
    if delta < 0:
        return f"⏰ 催化劑日期已過期 ({catalyst_date_str})"
    if delta <= 30:
        return f"⚡ 催化劑倒數 — {delta} 天後 ({catalyst_date_str})"
    return None


def build_daily_report(
    holdings_data: List[Dict[str, Any]],
) -> str:
    """Generate a structured daily investment report in Traditional Chinese."""
    now_et = datetime.now(US_EASTERN)
    date_str: str = now_et.strftime("%Y-%m-%d (%A)")

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

        # Fetch stock info (earnings dates + news)
        stock_info = fetch_stock_info(ticker)
        
        # Get price
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

        # Buy/Sell signals
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

        # Catalyst from sheet
        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            for cd in dates:
                cat_reminder = check_catalyst(cd)
                if cat_reminder:
                    report_lines.append(f"   🗓 {cat_reminder}")

        # Earnings dates from yfinance
        if stock_info and stock_info.get("earnings_dates"):
            for edate in stock_info["earnings_dates"]:
                if isinstance(edate, str) and edate:
                    report_lines.append(f"   📊 財報日: {edate}")

        # News
        if stock_info and stock_info.get("news"):
            for news_item in stock_info["news"]:
                title = news_item.get("title", "").strip()
                publisher = news_item.get("publisher", "")
                if title:
                    # Truncate long titles
                    if len(title) > 60:
                        title = title[:57] + "..."
                    report_lines.append(f"   📰 {title}")
                    if publisher:
                        report_lines.append(f"      ({publisher})")

        if notes:
            report_lines.append(f"   💬 {notes}")

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