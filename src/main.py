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

    W = "\U0001f4c8"  # chart
    E = "⚠️"  # warning
    report_lines: List[str] = [
        f"{W} 美股投資策略日報 | {date_str}",
    ]

    if is_weekend:
        report_lines.append(
            f"{E} 注：週末休市，以下為上週五 ({last_trading_date}) 收盤參考価"
        )
    else:
        report_lines.append(f"{E} 注：為實時交易時段訊息")

    report_lines.append("=" * 40)

    major_events_list: List[Dict[str, Any]] = []
    urgent_stocks: List[Dict[str, Any]] = []

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
        stock_entry["major_events_info"] = ""
        has_major_event = False
        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            event_details: List[str] = []
            for cd in dates:
                try:
                    event_dt = datetime.strptime(cd, "%Y-%m-%d")
                    today_naive = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    delta = (event_dt - today_naive).days
                    if 0 <= delta <= 14:
                        event_type = classify_catalyst(cd, stock_entry.get("notes", ""))
                        icon = "\U0001f514" if delta <= 7 else "\U0001f4c6"
                        evt_line = (f"\U0001f4c5 事件：{event_type} ({cd}) "
                                    f"[{icon} 倒數 {delta} 天]")
                        if delta == 0:
                            evt_line += " [\U0001f525 今天!]"
                        event_details.append(evt_line)
                        has_major_event = True
                except ValueError:
                    pass
            stock_entry["major_events_info"] = "\n".join(event_details)

        if has_urgent:
            triggers = []
            if buy_triggered:
                triggers.append("✅ 触發買盟：已落入買盟區間")
            if sell_triggered:
                triggers.append("\U0001f534 触發貣出盟：已高於貣出區間")
            if stop_loss_triggered:
                triggers.append("\U0001f6a8 触發停損：損失超過 10%")
            stock_entry["trigger_reasons"] = triggers
            urgent_stocks.append(stock_entry)
        elif has_major_event:
            major_events_list.append(stock_entry)

    # Urgent section
    report_lines.append("")
    report_lines.append("\U0001f6a8 【需立即行動】 (触發買貣/停損/重大事件)")
    report_lines.append("-" * 40)

    if urgent_stocks or major_events_list:
        for s in urgent_stocks + major_events_list:
            _add_stock_block(report_lines, s)
    else:
        report_lines.append("✅ 當前沒有需要立即行動的持股。")

    # Future events 14-30 days out
    future_events: List[Dict[str, Any]] = []
    for h in holdings_data:
        se = _process_holding(h, fetcher)
        if se is None:
            continue
        cat_raw = se.get("catalyst_raw", "")
        if cat_raw:
            dates = [d.strip() for d in str(cat_raw).split(",") if d.strip()]
            for cd in dates:
                try:
                    event_dt = datetime.strptime(cd, "%Y-%m-%d")
                    today_naive = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    delta = (event_dt - today_naive).days
                    if 14 < delta <= 30:
                        event_type = classify_catalyst(cd, se.get("notes", ""))
                        se["upcoming_event"] = f"{event_type} ({cd}, 倒數{delta}天)"
                        future_events.append(se)
                except ValueError:
                    pass

    if future_events:
        report_lines.append("")
        report_lines.append("\U0001f4c5 【重大事件提醒】 (未來 30 天內)")
        report_lines.append("-" * 40)
        for s in future_events:
            if "upcoming_event" in s:
                report_lines.append(f"⚠️ {s['ticker']} ({s['company_name']}) | {s['upcoming_event']}")
        report_lines.append("")

    # Footer
    report_lines.append("=" * 40)
    report_lines.append(
        "⚙️ 系統狀態：資料更新成功 | "
        "下次執行：毎週一~五 22:00 (臺灣時間)"
    )
    report_lines.append(
        "⚠️ 免責聲明：本日報由系統自動生成，"
        "僅供操作參考，請自行確認市場流動性與風險。"
    )

    return "\n".join(report_lines)


def _process_holding(
    h: Dict[str, Any],
    fetcher: FinancialDataFetcher,
) -> Optional[Dict[str, Any]]:
    """Process a single holding row and enrich it with market data."""
    ticker = str(h.get("ticker", h.get("代碼", ""))).strip().upper()
    if not ticker:
        return None

    shares = float(h.get("shares", h.get("股數", 0)))
    avg_cost = float(h.get("avgcost", h.get("均價", 0)))
    buy_zone_raw = str(h.get("buyzone", h.get("買盟區間", "")))
    sell_zone_raw = str(h.get("sellzone", h.get("貣出區間", "")))
    catalyst_raw = str(h.get("catalystdate", h.get("催化劇日期", "")))
    notes = str(h.get("notes", h.get("備註", ""))).strip()

    common_notes = ["見備註", "see notes", "(見備註)", "N/A", "", "N/A", "—"]
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

    lines.append(f"\U0001f4c9 {ticker} ({company})")
    lines.append(
        f"   \U0001f4b0 持仓：{shares_count}股 | "
        f"均价： | "
        f"損益： ({pnl_pct:+.1f}%)"
    )
    lines.append(f"   \U0001f4c2 當前価：")

    trigger_reasons = s.get("trigger_reasons", [])
    for reason in trigger_reasons:
        lines.append(f"   {reason}")

    buy_zones = s.get("buy_zones", [])
    sell_zones = s.get("sell_zones", [])

    if buy_zones:
        zone_str = ", ".join(f"" for bz in buy_zones)
        lines.append(f"   ⇣ 買盟區間：{zone_str}")

    if sell_zones:
        zone_str = ", ".join(f"" for sz in sell_zones)
        lines.append(f"   ⇡ 貣出區間：{zone_str}")

    evt_info = s.get("major_events_info", "")
    if evt_info:
        for eline in evt_info.split("\n"):
            lines.append(f"   {eline}")

    upcoming = s.get("upcoming_event", "")
    if upcoming:
        lines.append(f"   ⚠️ 上幂事件：{upcoming}")

    fin_data = s.get("fin_data")
    if fin_data and fin_data.get("summary"):
        lines.append(f"   \U0001f4f0 財務要點：{fin_data['summary']}")

    edata = s.get("earnings_data") or {}
    next_earn = edata.get("next_earnings", "TBA")
    eps_est = edata.get("eps_estimate", "N/A")
    lines.append(f"   \U0001f4c5 下次財報：{next_earn} | EPS預設：{eps_est}")

    sdata = s.get("sentiment_data") or {}
    mentions = sdata.get("total_mentions", 0)
    sent_label = sdata.get("sentiment_label", "訊息不足")
    lines.append(f"   \U0001f4ac 散戶情綴：{sent_label} (提及次數：{mentions})")

    notes = s.get("notes", "")
    if notes:
        lines.append(f"   \U0001f4dd 備註：{notes}")

    lines.append("")


def classify_catalyst(date_str: str, notes: str = "") -> str:
    """Classify a catalyst date into an event type."""
    notes_lower = notes.lower() if notes else ""

    keywords: Dict[str, List[str]] = {
        "財報": ["財報", "earnings", "quarterly", "q1", "q2", "q3", "q4", "季報"],
        "FDA審批": ["fda", "審批", "regulatory", "nda", "bla"],
        "臨庫數據": ["phase 3", "phase ii", "phase i", "數據", "readout", "data"],
        "量產": ["量產", "production", "manufacturing", "launch"],
        "合作伺戶": ["合作", "partnership", "collaboration", "alliance"],
        "上市": ["ipo", "listing", "上市"],
        "分析師調整": ["upgrade", "downgrade", "rating", "調整"],
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

    return "其他事件"


def _get_company_name(ticker: str) -> str:
    """Return display name for known tickers."""
    names: Dict[str, str] = {
        "BEAM": "Beam Therapeutics",
        "NVDA": "NVIDIA",
        "GOOG": "Alphabet (Google)",
        "TSM": "臺灣電單",
        "AMD": "Advanced Micro Devices",
        "IONQ": "IonQ Inc.",
        "GLW": "Corning",
        "MU": "Micron Technology",
        "MRVL": "Marvell Technology",
        "ONDS": "Oncose",
        "RCAT": "Red Cat Holdings",
        "SKHY": "SK Hynix Inc.",
        "SNDK": "SanDisk Corp",
        "SPCX": "SpaceX Capital ETF",
        "UNH": "UnitedHealth Group",
        "APP": "AppLovin Corp",
        "LITE": "Lumentum Holdings",
        "NVO": "Novo Nordisk",
    }
    return names.get(ticker, ticker)


def main() -> None:
    """Entry point for the daily monitor workflow."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info("総綱系統——日報執行中...")

    manager = GoogleSheetsManager()
    data = manager.load_config()
    holdings = data["holdings"]

    if not holdings:
        logger.warning("無持股資料，中止執行。")
        sys.exit(0)

    fetcher = FinancialDataFetcher()
    report = build_daily_report(holdings, fetcher)

    print(report)

    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("日報推撲成功。")


if __name__ == "__main__":
    main()