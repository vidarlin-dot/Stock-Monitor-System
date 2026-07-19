"""Daily monitoring main entry point."""

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

logger = logging.getLogger(__name__)

TW_TZ = pytz.timezone("Asia/Taipei")


def build_daily_report(
    holdings_data: List[Dict[str, Any]],
    fetcher: FinancialDataFetcher,
) -> str:
    """Generate a structured daily investment report.
    
    Layout:
    1. Header with date and market status
    2. MAJOR EVENTS (置顶) - Events within 14 days
    3. URGENT - Buy/sell/stop-loss signals
    4. Footer
    """
    now_tw = datetime.now(TW_TZ)
    date_str: str = now_tw.strftime("%Y-%m-%d (%a)")
    
    # Check if weekend
    day_of_week = now_tw.weekday()  # 0=Monday, 6=Sunday
    is_weekend = day_of_week >= 5
    
    # Get last trading day date
    last_trading_date = now_tw.strftime("%m-%d")
    if is_weekend:
        today_naive = now_tw.replace(tzinfo=None)
        days_since_friday = (today_naive - datetime(today_naive.year, today_naive.month, today_naive.day)).days
        if days_since_friday == 1:  # Saturday
            last_trading_date = (today_naive - timedelta(days=1)).strftime("%m-%d")
        elif days_since_friday == 2:  # Sunday
            last_trading_date = (today_naive - timedelta(days=2)).strftime("%m-%d")
        else:
            last_trading_date = (today_naive - timedelta(days=days_since_friday - 1)).strftime("%m-%d")
    
    report_lines: List[str] = [
        "📈 美股投資策略日報 | " + date_str,
    ]
    
    if is_weekend:
        report_lines.append(f"💡 註：週{['一','二','三','四','五','六','日'][day_of_week]}休市，以下為上週五 ({last_trading_date}) 收盤參考價")
    else:
        report_lines.append("💡 註：以下為今日收盤價")
    
    report_lines.append("=" * 40)

    # Collect all stock data
    major_events_list: List[Dict] = []  # Events <= 14 days
    urgent_stocks: List[Dict] = []  # Buy/sell/stop-loss signals

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

        # Clean up meaningless notes
        if notes in ("見備註", "see notes", "(見備註)", "N/A", ""):
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

        fin_data = fetcher.fetch_all(ticker)

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

        stock_entry = {
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
        }

        # Categorize
        is_major_event = False
        is_urgent = False

        # Check major events (<= 14 days)
        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            for cd in dates:
                try:
                    event_dt = datetime.strptime(cd, "%Y-%m-%d")
                    today_naive = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    delta = (event_dt - today_naive).days
                    if 0 <= delta <= 14:
                        is_major_event = True
                        stock_entry["event_type"] = classify_catalyst(cd, notes)
                        stock_entry["event_date"] = cd
                        stock_entry["event_delta"] = delta
                except ValueError:
                    pass

        # Check buy/sell signals (only if outside 0-3% range)
        if pnl_pct > 3 or pnl_pct < -3:
            if buy_zones and current_price <= buy_zones[0]:
                is_urgent = True
            if sell_zones and current_price >= sell_zones[0]:
                is_urgent = True
            if pnl_pct < -10:  # Stop-loss
                is_urgent = True

        if is_major_event:
            major_events_list.append(stock_entry)
        elif is_urgent:
            urgent_stocks.append(stock_entry)

    # Section 1: MAJOR EVENTS (置頂)
    if major_events_list:
        report_lines.append("")
        report_lines.append("🔔 【重大事件提醒】 (未來 14 天內)")
        report_lines.append("─" * 40)
        
        for s in major_events_list:
            ticker = s["ticker"]
            event_type = s.get("event_type", "重要事件")
            event_date = s.get("event_date", "")
            event_delta = s.get("event_delta", 0)
            notes = s.get("notes", "")

            report_lines.append(f"⚠️ {ticker} ({s['company_name']}) | 倒數 {event_delta} 天")
            report_lines.append(f"   📅 事件: {event_type} ({event_date})")
            
            # Show financial summary
            if s.get("fin_data") and s["fin_data"].get("summary"):
                report_lines.append(f"   📊 財報摘要: {s['fin_data']['summary']}")
            
            if notes:
                report_lines.append(f"   💬 備註: {notes}")
            report_lines.append("")

    # Section 2: URGENT
    if urgent_stocks:
        report_lines.append("🚨 【需立即行動】 (觸發買賣/停損條件)")
        report_lines.append("─" * 40)
        
        for s in urgent_stocks:
            ticker = s["ticker"]
            cp = s["current_price"]
            ac = s["avg_cost"]
            shares = s["shares"]
            pnl_pct = s["pnl_pct"]

            if pnl_pct >= 5:
                emoji = "🟢"
            elif pnl_pct >= 0:
                emoji = "⚪"
            else:
                emoji = "🔴"

            report_lines.append(f"{emoji} {ticker} ({s['company_name']})")
            report_lines.append(f"   💰 持倉: {shares}股 | 均價: ${ac:.2f} | 損益: {pnl_pct:+.1f}%")
            report_lines.append(f"   📉 當前價: ${cp:.2f}")

            if s["buy_zones"] and cp <= s["buy_zones"][0]:
                zone_str = ", ".join(f"${bz:.2f}" for bz in s["buy_zones"])
                report_lines.append(f"   ✅ 觸發買入：已落入第一買入區間 [{zone_str}]")

            if s["sell_zones"] and cp >= s["sell_zones"][0]:
                zone_str = ", ".join(f"${sz:.2f}" for sz in s["sell_zones"])
                report_lines.append(f"   🔻 觸發賣出：已達到賣出區間 [{zone_str}]")

            if pnl_pct < -10:
                report_lines.append(f"   🛑 觸發停損：已跌破停損點 (${ac * 0.9:.2f})")

            if s.get("fin_data") and s["fin_data"].get("summary"):
                report_lines.append(f"   📊 財報摘要: {s['fin_data']['summary']}")

            if s["notes"]:
                report_lines.append(f"   💬 {s['notes']}")

            report_lines.append("")

    # Footer
    report_lines.append("=" * 40)
    report_lines.append("⚙️ 系統狀態: 資料更新成功 | 下次執行: 每週一至五 22:00 (台灣時間)")
    report_lines.append("⚠️ 免責聲明: 本日報由系統自動生成，僅供操作參考，請自行確認市場流動性與風險。")

    return "\n".join(report_lines)


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


def _get_company_name(ticker: str) -> str:
    """Return company name for common tickers."""
    names = {
        "BEAM": "Beam Therapeutics",
        "NVDA": "NVIDIA",
        "GOOG": "Alphabet",
        "TSM": "台積電",
        "AMD": "Advanced Micro Devices",
        "IONQ": "IonQ Inc.",
        "GLW": "Corning",
        "MU": "Micron",
        "MRVL": "Marvell",
        "ONDS": "Oncose",
        "RCAT": "Red Cat Holdings",
        "SKHY": "Skywater Technology",
        "SNDK": "SanDisk",
        "SPCX": "SpaceX",
        "UNH": "UnitedHealth",
        "APP": "AppLovin",
        "LITE": "Lightwave Logic",
        "NVO": "Novo Nordisk",
    }
    return names.get(ticker, ticker)


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

    fetcher = FinancialDataFetcher()
    report = build_daily_report(holdings, fetcher)
    print(report)

    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("Daily report sent successfully.")


if __name__ == "__main__":
    main()