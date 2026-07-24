"""Daily monitoring main entry point for Stock Monitor System."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytz
import yfinance as yf

from config import GoogleSheetsManager
from financial_sources import FinancialDataFetcher
from line_notifier import LineNotifier
from sentiment_earnings import fetch_earnings_calendar, fetch_social_sentiment

logger = logging.getLogger(__name__)

TW_TZ = pytz.timezone("Asia/Taipei")


def build_daily_report(
    holdings_data: List[Dict[str, Any]],
    fetcher: FinancialDataFetcher,
) -> str:
    """Build a structured daily investment report in Traditional Chinese."""
    now_tw: datetime = datetime.now(TW_TZ)
    date_str: str = now_tw.strftime("%Y-%m-%d (%a)")

    day_of_week: int = now_tw.weekday()
    is_weekend: bool = day_of_week >= 5

    last_trading_date: str = now_tw.strftime("%m-%d")
    if is_weekend:
        today_naive: datetime = now_tw.replace(tzinfo=None)
        days_since_fri: int = (today_naive - datetime(today_naive.year, today_naive.month, today_naive.day)).days
        if days_since_fri == 1:
            last_trading_date = (today_naive - timedelta(days=1)).strftime("%m-%d")
        elif days_since_fri == 2:
            last_trading_date = (today_naive - timedelta(days=2)).strftime("%m-%d")
        else:
            last_trading_date = (today_naive - timedelta(days=days_since_fri - 1)).strftime("%m-%d")

    W = chr(0x1F4C8)
    E = chr(0x26A0) + chr(0xFE0F)
    report_lines: List[str] = [
        f"{W} \u7f8e\u80a1\u6295\u8cc7\u7b56\u7565\u65e5\u5831 | {date_str}",
    ]

    if is_weekend:
        report_lines.append(
            f"{E} \u8a3b\uff1a\u9031\u672b\u4f11\u5e02\uff0c\u4ee5\u4e0b\u70ba\u4e0a\u9031\u4e94 ({last_trading_date}) \u6536\u76e4\u53c3\u8003\u50f9"
        )
    else:
        report_lines.append(f"{E} \u8a3b\uff1a\u70ba\u5be6\u6642\u4ea4\u6613\u6642\u6bb5\u6d88\u606f")

    report_lines.append("=" * 40)

    urgent_stocks: List[Dict[str, Any]] = []
    major_events_list: List[Dict[str, Any]] = []

    for h in holdings_data:
        stock_entry = _process_holding(h, fetcher)
        if stock_entry is None:
            continue

        has_urgent: bool = False
        cp = stock_entry["current_price"]
        buy_zones = stock_entry.get("buy_zones", [])
        sell_zones = stock_entry.get("sell_zones", [])
        pnl_pct = stock_entry["pnl_pct"]

        buy_triggered = bool(buy_zones and cp <= buy_zones[0] * 1.03)
        sell_triggered = bool(sell_zones and cp >= sell_zones[0] * 0.97)
        stop_loss_triggered = pnl_pct < -10.0

        if buy_triggered or sell_triggered or stop_loss_triggered:
            has_urgent = True

        catalyst_raw = stock_entry.get("catalyst_raw", "")
        analyst_comment = stock_entry.get("analyst_comment", "")
        stock_entry["major_events_info"] = ""
        stock_entry["upcoming_event"] = ""
        has_major_event = False
        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            event_details: List[str] = []
            for cd in dates:
                try:
                    event_dt = datetime.strptime(cd, "%Y-%m-%d").replace(tzinfo=None)
                    today_naive = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    delta = (event_dt - today_naive).days
                    if 0 <= delta <= 30:
                        event_type = classify_catalyst(cd, stock_entry.get("notes", ""))
                        icon = chr(0x1F514) if delta <= 7 else chr(0x1F4C5)
                        evt_line = (f"{chr(0x1F4C5)} \u4e8b\u4ef6\uff1a{event_type} ({cd}) "
                                    f"[{icon} \u5012\u6578 {delta} \u5929]")
                        if delta == 0:
                            evt_line += f" [{chr(0x1F525)} \u4eca\u5929!]"
                        elif delta <= 1:
                            evt_line += f" [{chr(0x1F50D)} \u660e\u5929!]"
                        event_details.append(evt_line)
                        has_major_event = True
                except ValueError:
                    pass
            stock_entry["major_events_info"] = chr(10).join(event_details)

        if event_details:
            stock_entry["upcoming_event"] = event_details[0]

        if has_urgent:
            triggers = []
            if buy_triggered:
                triggers.append(f"{chr(0x2705)} \u8cb7\u9032\u8a0a\u865f\uff1a\u5df2\u843d\u5165\u8cb7\u9032\u5340\u9593")
            if sell_triggered:
                triggers.append(f"{chr(0x1F534)} \u8ca3\u51fa\u8a0a\u865f\uff1a\u5df2\u9ad8\u65bc\u8ca3\u51fa\u5340\u9593")
            if stop_loss_triggered:
                triggers.append(f"{chr(0x1F6A8)} \u505c\u640d\u8a0a\u865f\uff1a\u640d\u5931\u8d85\u904e 10%")
            stock_entry["trigger_reasons"] = triggers
            urgent_stocks.append(stock_entry)
        elif has_major_event:
            major_events_list.append(stock_entry)

    report_lines.append("")
    report_lines.append(f"{chr(0x1F6A8)} [\u9700\u7acb\u5373\u884c\u52d5] (\u89f8\u767c\u8cb7\u9032/\u8ca3\u51fa/\u505c\u640d/\u91cd\u5927\u4e8b\u4ef6)")
    report_lines.append("-" * 40)

    if urgent_stocks or major_events_list:
        for s in urgent_stocks + major_events_list:
            _add_stock_block(report_lines, s)
    else:
        report_lines.append(f"{chr(0x2705)} \u7576\u524d\u6c92\u6709\u9700\u8981\u7acb\u5373\u884c\u52d5\u7684\u6301\u80a1\u3002")

    return chr(10).join(report_lines)


def _process_holding(
    h: Dict[str, Any],
    fetcher: FinancialDataFetcher,
) -> Optional[Dict[str, Any]]:
    """Process a single holding row and enrich it with market data."""
    ticker = str(h.get("ticker", h.get("\u4ee3\u78bc", ""))).strip().upper()
    if not ticker:
        return None

    shares = float(h.get("shares", h.get("\u80a1\u6578", 0)))
    avg_cost = float(h.get("avgcost", h.get("\u5747\u50f9", 0)))
    buy_zone_raw = str(h.get("buyzone", h.get("\u8cb7\u76df\u5340\u9593", "")))
    sell_zone_raw = str(h.get("sellzone", h.get("\u8ca3\u51fa\u5340\u9593", "")))
    catalyst_raw = str(h.get("catalystdate", h.get("\u50ac\u5316\u5287\u65e5\u671f", "")))
    notes = str(h.get("notes", h.get("\u5099\u8a3b", ""))).strip()
    analyst_comment = str(h.get("analyst_comment", h.get("\u5206\u6790\u5e08\u8a55\u8ad6", ""))).strip()

    common_notes = ["\u898b\u5099\u8a3b", "see notes", "(\u898b\u5099\u8a3b)", "N/A", "", "\u2014"]
    if notes in common_notes:
        notes = ""

    buy_zones: List[float] = []
    if buy_zone_raw:
        for bz in buy_zone_raw.split(","):
            try:
                buy_zones.append(float(bz.strip()))
            except ValueError:
                pass

    sell_zones: List[float] = []
    if sell_zone_raw:
        for sz in sell_zone_raw.split(","):
            try:
                sell_zones.append(float(sz.strip()))
            except ValueError:
                pass

    fin_data = fetcher.fetch_all(ticker)
    earnings_data = fetch_earnings_calendar(ticker)
    sentiment_data = fetch_social_sentiment(ticker)

    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        current_price: Optional[float] = float(hist["Close"].iloc[-1]) if not hist.empty else None
    except Exception:
        current_price = None

    if current_price is None:
        logger.warning("No price data for %s, skipping.", ticker)
        return None

    pnl_per_share = current_price - avg_cost
    pnl_pct = (pnl_per_share / avg_cost * 100) if avg_cost > 0 else 0.0
    total_pnl = pnl_per_share * shares

    return {
        "ticker": ticker,
        "company_name": _get_company_name(ticker),
        "current_price": current_price,
        "avg_cost": avg_cost,
        "shares": int(shares),
        "pnl_pct": pnl_pct,
        "total_pnl": total_pnl,
        "buy_zones": buy_zones,
        "sell_zones": sell_zones,
        "catalyst_raw": catalyst_raw,
        "notes": notes,
        "analyst_comment": analyst_comment,
        "fin_data": fin_data,
        "earnings_data": earnings_data,
        "sentiment_data": sentiment_data,
    }


def _add_stock_block(lines: List[str], s: Dict[str, Any]) -> None:
    """Append a compact stock block to the report lines."""
    ticker = s["ticker"]
    company = s["company_name"]
    cp = s["current_price"]
    ac = s["avg_cost"]
    shares_count = s["shares"]
    pnl_pct = s["pnl_pct"]
    total_pnl = s["total_pnl"]

    lines.append(f"{chr(0x1F4C9)} {ticker} ({company})")
    lines.append(
        f"   {chr(0x1F4B0)} \u6301\u80a1\uff1a{shares_count}\u80a1 | "
        f"\u5747\u50f9\uff1a${ac:.2f} | "
        f"\u640d\u76ca\uff1a${total_pnl:+,.2f} ({pnl_pct:+.1f}%)"
    )
    lines.append(f"   {chr(0x1F4C2)} \u7576\u524d\u50f9\uff1a${cp:.2f}")

    trigger_reasons = s.get("trigger_reasons", [])
    for reason in trigger_reasons:
        lines.append(f"   {reason}")

    buy_zones = s.get("buy_zones", [])
    sell_zones = s.get("sell_zones", [])

    if buy_zones:
        zone_str = ", ".join(f"${bz:.2f}" for bz in buy_zones)
        lines.append(f"   {chr(0x21E3)} \u8cb7\u9032\u5340\u9593\uff1a{zone_str}")

    if sell_zones:
        zone_str = ", ".join(f"${sz:.2f}" for sz in sell_zones)
        lines.append(f"   {chr(0x21E0)} \u8ca3\u51fa\u5340\u9593\uff1a{zone_str}")

    evt_info = s.get("major_events_info", "")
    if evt_info:
        for eline in evt_info.split(chr(10)):
            lines.append(f"   {eline}")

    analyst_comment = s.get("analyst_comment", "")
    if analyst_comment:
        lines.append(f"   {chr(0x1F4DD)} \u5206\u6790\u5e08\u898b\u89e3\uff1a{analyst_comment}")

    fin_data = s.get("fin_data")
    if fin_data and fin_data.get("summary"):
        summary_text = fin_data.get("summary", "")
        lines.append(f"   {chr(0x1F4F0)} \u8ca1\u52d9\u8981\u9ede\uff1a{summary_text}")

    edata = s.get("earnings_data") or {}
    next_earn = edata.get("next_earnings", "TBA")
    eps_est = edata.get("eps_estimate", "N/A")
    lines.append(f"   {chr(0x1F4C5)} \u4e0b\u6b21\u8ca1\u5831\uff1a{next_earn} | EPS\u9810\u671f\uff1a{eps_est}")

    sdata = s.get("sentiment_data") or {}
    mentions = sdata.get("total_mentions", 0)
    sent_label = sdata.get("sentiment_label", "\u8a0a\u606f\u4e0d\u8db3")
    lines.append(f"   {chr(0x1F4AC)} \u6563\u6236\u60c5\u7dd2\uff1a{sent_label} (\u63d0\u53ca\u6b21\u6578\uff1a{mentions})")

    notes = s.get("notes", "")
    if notes:
        lines.append(f"   {chr(0x1F4DD)} \u5099\u8a3b\uff1a{notes}")

    lines.append("")


def classify_catalyst(date_str: str, notes: str = "") -> str:
    """Classify a catalyst date into an event type."""
    notes_lower = notes.lower() if notes else ""

    keywords: Dict[str, List[str]] = {
        "\u8ca1\u5831": ["\u8ca1\u5831", "earnings", "quarterly", "q1", "q2", "q3", "q4", "\u5b63\u5831"],
        "FDA\u5be9\u6279": ["fda", "\u5be9\u6279", "regulatory", "nda", "bla"],
        "\u81e8\u5eb7\u6578\u64da": ["phase 3", "phase ii", "phase i", "\u6578\u64da", "readout", "data"],
        "\u91cf\u7522": ["\u91cf\u7522", "production", "manufacturing", "launch"],
        "\u5408\u4f5c\u5ba2\u6236": ["\u5408\u4f5c", "partnership", "collaboration", "alliance"],
        "\u4e0a\u5e02": ["ipo", "listing", "\u4e0a\u5e02"],
        "\u5206\u6790\u5e08\u8abf\u6574": ["upgrade", "downgrade", "rating", "\u8abf\u6574"],
    }

    for event_type, kws in keywords.items():
        if any(kw in notes_lower for kw in kws):
            return event_type

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if dt.month in [2, 5, 8, 11]:
            return "\u8ca1\u5831"
    except ValueError:
        pass

    return "\u5176\u4ed6\u4e8b\u4ef6"


def _get_company_name(ticker: str) -> str:
    """Return display name for known tickers."""
    names: Dict[str, str] = {
        "BEAM": "Beam Therapeutics",
        "NVDA": "NVIDIA",
        "GOOG": "Alphabet (Google)",
        "TSM": "\u53f0\u96fb\u7a4d",
        "AMD": "Advanced Micro Devices",
        "IONQ": "IonQ Inc.",
        "GLW": "Corning",
        "MU": "Micron Technology",
        "MRVL": "Marvell Technology",
        "ONDS": "Oncology Systems",
        "RCAT": "Red Cat Holdings",
        "SKHY": "SK Hynix Inc.",
        "SNDK": "SanDisk Corp",
        "SPCX": "SpaceX Capital ETF",
        "UNH": "UnitedHealth Group",
        "APP": "AppLovin Corp",
        "LITE": "Lumentum Holdings",
        "NVO": "Novo Nordisk",
        "EWY": "iShares MSCI South Korea ETF",
    }
    return names.get(ticker, ticker)


def main() -> None:
    """Entry point for the daily monitor workflow."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info("Stock Monitor - Daily Report starting...")

    manager = GoogleSheetsManager()
    data = manager.load_config()
    holdings = data["holdings"]

    if not holdings:
        logger.warning("No holdings data, aborting.")
        sys.exit(0)

    fetcher = FinancialDataFetcher()
    report = build_daily_report(holdings, fetcher)

    print(report)

    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("Daily report pushed successfully.")


if __name__ == "__main__":
    main()

