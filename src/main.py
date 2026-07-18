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


def fetch_price(ticker: str) -> Optional[float]:
    """Fetch the latest closing price for a ticker via yfinance."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        if hist.empty:
            logger.warning("No price data for %s", ticker)
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:
        logger.error("Failed to fetch price for %s: %s", ticker, exc)
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
        return f"\U0001f570\ufe0f \u50ac\u5316\u5287\u65e5\u671f\u5df2\u904e\u671f ({catalyst_date_str})"
    if delta <= 30:
        return f"\u26a1 \u50ac\u5316\u5287\u5012\u6578 \u2014 {delta} \u5929\u5f8c ({catalyst_date_str})"
    return None


def build_daily_report(
    holdings_data: List[Dict[str, Any]],
) -> str:
    """Generate a structured daily investment report in Traditional Chinese."""
    now_et = datetime.now(US_EASTERN)
    date_str: str = now_et.strftime("%Y-%m-%d (%A)")

    report_lines: List[str] = [
        f"\U0001f4c8 \u7f8e\u80a1\u6295\u8cc7\u65e5\u5831 | {date_str}",
        "=" * 40,
        "",
    ]

    for idx, h in enumerate(holdings_data, start=1):
        # Support both English and Chinese headers
        ticker: str = str(h.get("ticker", h.get("\u4ee3\u78bc", "?"))).strip().upper()
        if not ticker:
            continue

        shares: float = float(h.get("shares", h.get("\u80a1\u6578", 0)))
        avg_cost: float = float(h.get("avgcost", h.get("\u5747\u50f9", 0)))
        buy_zone_raw = h.get("buyzone", h.get("\u8cb7\u9032\u5340\u9593", ""))
        sell_zone_raw = h.get("sellzone", h.get("\u8ce3\u51fa\u5340\u9593", ""))
        catalyst_raw = h.get("catalystdate", h.get("\u50ac\u5316\u5287\u65e5\u671f", ""))
        notes: str = str(h.get("notes", h.get("\u5099\u8a3b", ""))).strip()

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

        current_price = fetch_price(ticker)
        if current_price is None:
            report_lines.append(f"{idx}. {ticker} \u2014 \u26a0\ufe0f \u7121\u6cd5\u53d6\u5f97\u80a1\u50f9")
            report_lines.append("")
            continue

        pnl_per_share: float = current_price - avg_cost
        pnl_pct: float = (pnl_per_share / avg_cost * 100) if avg_cost > 0 else 0.0
        total_pnl: float = pnl_per_share * shares

        if pnl_pct >= 5:
            emoji = "\U0001f7e2"
        elif pnl_pct >= 0:
            emoji = "\U0001f7e1"
        else:
            emoji = "\U0001f534"

        report_lines.append(f"{idx}. {emoji} {ticker}")
        report_lines.append(f"   \u7576\u524d\u50f9\u683c: ${current_price:.2f} | \u5747\u50f9: ${avg_cost:.4f}")
        report_lines.append(
            f"   \u6301\u4ec6: {int(shares)} \u80a1 | \u64ca\u76ca: ${total_pnl:+,.2f} ({pnl_pct:+.2f}%)"
        )

        if buy_zones:
            zone_str = ", ".join(f"${bz:.2f}" for bz in buy_zones)
            if current_price <= buy_zones[0]:
                report_lines.append(f"   \U0001f7e2 \u8cb7\u9032\u8a0a\u865f \u2014 ${current_price:.2f} \u2264 [{zone_str}]")
            else:
                report_lines.append(f"   \U0001f4cc \u8cb7\u9032\u5340\u9593: {zone_str}")

        if sell_zones:
            zone_str = ", ".join(f"${sz:.2f}" for sz in sell_zones)
            if current_price >= sell_zones[0]:
                report_lines.append(f"   \U0001f534 \u8ce3\u51fa\u8a0a\u865f \u2014 ${current_price:.2f} \u2265 [{zone_str}]")
            else:
                report_lines.append(f"   \U0001f4cc \u8ce3\u51fa\u5340\u9593: {zone_str}")

        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            for cd in dates:
                cat_reminder = check_catalyst(cd)
                if cat_reminder:
                    report_lines.append(f"   \U0001f333 {cat_reminder}")

        if notes:
            report_lines.append(f"   \U0001f4ac {notes}")

        report_lines.append("")

    report_lines.append("=" * 40)
    report_lines.append("\U0001f4a1 \u4ee5\u4e0a\u70ba\u81ea\u52d5\u5316\u7522\u751f\uff0c\u6295\u8cc7\u6709\u98a8\u96aa\uff0c\u64cd\u4f5c\u9808\u8b18\u614e\u3002")

    return "\n".join(report_lines)


def main() -> None:
    """Main entry point for the daily monitor."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info("Stock Monitor \u2014 Daily Report starting\u2026")

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