"""Daily monitoring main entry point.

Loads portfolio config from Google Sheets, fetches latest prices via
yfinance, runs strategy & catalyst engines, and pushes a structured
daily report through LINE.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytz
import yfinance as yf

from config import GoogleSheetsManager
from line_notifier import LineNotifier

logger = logging.getLogger(__name__)

# US market timezone for date-aware calculations
US_EASTERN = pytz.timezone("America/New_York")


def fetch_price(ticker: str) -> Optional[float]:
    """Fetch the latest closing price for a ticker via yfinance.

    Args:
        ticker: Stock ticker symbol (e.g. ``"AAPL"``).

    Returns:
        The most recent closing price as a float, or ``None`` on failure.
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        if hist.empty:
            logger.warning("No price data for %s", ticker)
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch price for %s: %s", ticker, exc)
        return None


def check_signals(
    current_price: float,
    buy_zone: Optional[float],
    sell_zone: Optional[float],
) -> List[str]:
    """Evaluate buy / sell / stop-loss signals.

    Args:
        current_price: Latest market price.
        buy_zone: Price at or below which a buy signal triggers.
        sell_zone: Price at or above which a sell signal triggers.

    Returns:
        A list of signal strings (may be empty).
    """
    signals: List[str] = []

    if buy_zone is not None and current_price <= buy_zone:
        signals.append(f"🟢 買進訊號 — 當前價格 ${current_price:.2f} ≤ 買進區間 ${buy_zone:.2f}")

    if sell_zone is not None and current_price >= sell_zone:
        signals.append(f"🔴 賣出訊號 — 當前價格 ${current_price:.2f} ≥ 賣出區間 ${sell_zone:.2f}")

    # Stop-loss: if price drops below 80 % of avg cost
    return signals


def check_catalyst(catalyst_date_str: Optional[str]) -> Optional[str]:
    """Check if a catalyst event is within 30 days.

    Args:
        catalyst_date_str: Date string in ``YYYY-MM-DD`` format.

    Returns:
        A reminder string if the catalyst is near, or ``None``.
    """
    if not catalyst_date_str:
        return None

    try:
        catalyst_dt = datetime.strptime(str(catalyst_date_str), "%Y-%m-%d")
    except ValueError:
        logger.warning("Invalid catalyst date format: %s", catalyst_date_str)
        return None

    today = datetime.now(US_EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (catalyst_dt - today).days

    if delta < 0:
        return f"⏰ 催化劑日期已過期 ({catalyst_date_str})"
    if delta <= 30:
        return f"⚡ 催化劑倒數 — {delta} 天後 ({catalyst_date_str})"
    return None


def build_daily_report(
    holdings_data: List[Dict[str, Any]],
) -> str:
    """Generate a structured daily investment report in Traditional Chinese.

    Args:
        holdings_data: List of holding dicts from Google Sheets.

    Returns:
        A formatted report string suitable for LINE push notification.
    """
    now_et = datetime.now(US_EASTERN)
    date_str: str = now_et.strftime("%Y-%m-%d (%A)")

    report_lines: List[str] = [
        f"📈 美股投資日報 | {date_str}",
        "=" * 40,
        "",
    ]

    for idx, h in enumerate(holdings_data, start=1):
        ticker: str = str(h.get("ticker", "?")).strip().upper()
        if not ticker:
            continue

        shares: float = float(h.get("shares", 0))
        avg_cost: float = float(h.get("avgcost", 0))
        buy_zone_raw = h.get("buyzone", "")
        sell_zone_raw = h.get("sellzone", "")
        catalyst_raw = h.get("catalystdate", "")
        notes: str = str(h.get("notes", "")).strip()

        buy_zone: Optional[float] = float(buy_zone_raw) if buy_zone_raw else None
        sell_zone: Optional[float] = float(sell_zone_raw) if sell_zone_raw else None

        current_price = fetch_price(ticker)
        if current_price is None:
            report_lines.append(f"{idx}. {ticker} — ⚠️ 無法取得股價")
            report_lines.append("")
            continue

        pnl_per_share: float = current_price - avg_cost
        pnl_pct: float = (pnl_per_share / avg_cost * 100) if avg_cost > 0 else 0.0
        total_pnl: float = pnl_per_share * shares

        # Emoji based on P&L
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

        # Signals
        signals = check_signals(current_price, buy_zone, sell_zone)
        if signals:
            for sig in signals:
                report_lines.append(f"   📌 {sig}")

        # Catalyst
        cat_reminder = check_catalyst(catalyst_raw)
        if cat_reminder:
            report_lines.append(f"   🗓 {cat_reminder}")

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

    # Load holdings
    manager = GoogleSheetsManager()
    data = manager.load_config()
    holdings = data["holdings"]

    if not holdings:
        logger.warning("No holdings found. Nothing to report.")
        return

    # Build & send report
    report = build_daily_report(holdings)
    print(report)

    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("Daily report sent successfully.")


if __name__ == "__main__":
    main()
