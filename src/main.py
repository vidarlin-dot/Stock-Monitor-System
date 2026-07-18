"""Daily monitoring main entry point."""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytz
import yfinance as yf

from config import GoogleSheetsManager
from line_notifier import LineNotifier

logger = logging.getLogger(__name__)

TW_TZ = pytz.timezone("Asia/Taipei")
US_EASTERN = pytz.timezone("America/New_York")


def fetch_stock_info(ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch stock info including news and earnings dates."""
    try:
        stock = yf.Ticker(ticker)
        info = {}

        # Get earnings dates
        try:
            calendar = stock.calendar
            if calendar and isinstance(calendar, dict) and calendar:
                dates_list = list(calendar.values())[0] if calendar else []
                info["earnings_dates"] = [d for d in dates_list if d][:3]
        except Exception:
            info["earnings_dates"] = []

        # Get news
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

        return info if info.get("earnings_dates") or info.get("news") else None

    except Exception as exc:
        logger.warning("Failed to fetch info for %s: %s", ticker, exc)
        return None


def classify_event(title: str) -> str:
    """Classify news title into event category.

    Returns:
        Event category string like '財報', 'FDA批准', '量產', etc.
    """
    title_lower = title.lower()
    
    # Earnings
    if any(kw in title_lower for kw in ["earnings", "quarterly", "q1", "q2", "q3", "q4", "財報", "季報", "年報"]):
        return "財報"
    
    # FDA / Regulatory approval
    if any(kw in title_lower for kw in ["fda", "approval", "批准", "認證", "regulatory", "nda", "bla", "pma"]):
        return "FDA批准"
    
    # Clinical trial results
    if any(kw in title_lower for kw in ["phase 3", "phase ii", "phase i", "clinical", "試驗", "結果", "data readout"]):
        return "臨床試驗"
    
    # Production / Manufacturing
    if any(kw in title_lower for kw in ["production", "量產", "manufacturing", "產能", "mass production"]):
        return "量產"
    
    # Partnership / Collaboration
    if any(kw in title_lower for kw in ["partnership", "合作", "collaboration", "alliance", "joint venture"]):
        return "合作"
    
    # Product launch
    if any(kw in title_lower for kw in ["launch", "推出", "release", "新品", "product launch"]):
        return "產品發布"
    
    # Financial / Investment
    if any(kw in title_lower for kw in ["investment", "融資", "fundraising", "ipo", "listing", "上市"]):
        return "融資/上市"
    
    # Analyst rating
    if any(kw in title_lower for kw in ["upgrade", "downgrade", "目標價", "rating", "分析師", "consensus"]):
        return "評級調整"
    
    # Generic important event
    return "重要公告"


def extract_event_date(title: str) -> Optional[str]:
    """Extract potential event date from news title.

    Returns:
        Date string in YYYY-MM-DD format, or None.
    """
    # Pattern: Month Day, Year
    match = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(\d{4})', title, re.IGNORECASE)
    if match:
        month_str, day_str, year_str = match.groups()
        month_map = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        month = month_map.get(month_str.lower()[:3])
        if month:
            try:
                dt = datetime(int(year_str), month, int(day_str))
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
    
    # Pattern: Q1 2026, Q3 2026
    match = re.search(r'Q(\d)\s+(\d{4})', title, re.IGNORECASE)
    if match:
        quarter, year = match.groups()
        quarter_months = {
            "1": (1, 31), "2": (4, 30), "3": (7, 31), "4": (10, 31)
        }
        if quarter in quarter_months:
            start_month, _ = quarter_months[quarter]
            try:
                dt = datetime(int(year), start_month, 15)  # Mid-month approximation
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
    
    # Pattern: FY2026
    match = re.search(r'FY(\d{4})', title, re.IGNORECASE)
    if match:
        year = match.group(1)
        try:
            dt = datetime(int(year), 12, 31)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    
    return None


def check_catalyst(catalyst_date_str: Optional[str], event_name: str = "") -> Optional[str]:
    """Check if a catalyst event is within 30 days.

    Args:
        catalyst_date_str: Date string in YYYY-MM-DD format.
        event_name: Name of the event for display.

    Returns:
        Reminder string or None if expired.
    """
    if not catalyst_date_str:
        return None
    try:
        catalyst_dt = datetime.strptime(str(catalyst_date_str), "%Y-%m-%d")
    except ValueError:
        logger.warning("Invalid catalyst date format: %s", catalyst_date_str)
        return None
    
    today = datetime.now(TW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (catalyst_dt - today).days
    
    # Build event name display
    event_display = event_name if event_name else "催化劑"
    
    # Highlight if within 1 week
    if 0 <= delta <= 7:
        return f"🔥 {event_display}倒數 — {delta} 天後 ({catalyst_date_str})"
    if delta < 0:
        return None  # Expired, will be cleaned
    if delta <= 30:
        return f"⚡ {event_display}倒數 — {delta} 天後 ({catalyst_date_str})"
    return None


def build_daily_report(
    holdings_data: List[Dict[str, Any]],
    manager: Optional[GoogleSheetsManager] = None,
) -> tuple[str, List[Dict[str, Any]]]:
    """Generate a structured daily investment report in Traditional Chinese.

    Returns:
        (report_string, updated_holdings_list)
    """
    now_tw = datetime.now(TW_TZ)
    date_str: str = now_tw.strftime("%Y-%m-%d (%A)")

    report_lines: List[str] = [
        "📈 美股投資日報 | " + date_str,
        "=" * 40,
        "",
    ]

    updated_holdings: List[Dict[str, Any]] = []

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

        # Fetch stock info
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
            updated_holdings.append(h)
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

        # Clean expired catalysts and collect new ones with event names
        new_catalysts: List[Dict[str, str]] = []  # List of {"date": str, "name": str}
        
        # Add existing catalysts
        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            for cd in dates:
                try:
                    catalyst_dt = datetime.strptime(cd, "%Y-%m-%d")
                    today_dt = datetime.now(TW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
                    if catalyst_dt >= today_dt:
                        new_catalysts.append({"date": cd, "name": "催化劑"})
                except ValueError:
                    pass  # Skip invalid dates

        # Add event dates from news
        if stock_info and stock_info.get("news"):
            for news_item in stock_info["news"]:
                title = news_item.get("title", "")
                if not title:
                    continue
                
                event_date = extract_event_date(title)
                if event_date:
                    event_category = classify_event(title)
                    new_catalysts.append({"date": event_date, "name": event_category})
                    
                    # Show event in report
                    today_dt = datetime.now(TW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
                    try:
                        evt_dt = datetime.strptime(event_date, "%Y-%m-%d")
                        delta = (evt_dt - today_dt).days
                        if 0 <= delta <= 30:
                            event_display = f"{event_category}"
                            if 0 <= delta <= 7:
                                report_lines.append(f"   🔥 {event_display}倒數 — {delta} 天後 ({event_date})")
                            else:
                                report_lines.append(f"   ⚡ {event_display}倒數 — {delta} 天後 ({event_date})")
                    except ValueError:
                        pass

        # Deduplicate catalysts by date
        seen_dates: set = set()
        unique_catalysts: List[Dict[str, str]] = []
        for c in new_catalysts:
            if c["date"] not in seen_dates:
                seen_dates.add(c["date"])
                unique_catalysts.append(c)

        # Build display text for catalysts and update holding
        catalyst_parts: List[str] = []
        for c in unique_catalysts:
            cat_reminder = check_catalyst(c["date"], c["name"])
            if cat_reminder:
                report_lines.append(f"   {cat_reminder}")
            catalyst_parts.append(c["date"])

        h_updated = dict(h)
        h_updated["catalystdate"] = ",".join(c["date"] for c in unique_catalysts)
        updated_holdings.append(h_updated)

        # News
        if stock_info and stock_info.get("news"):
            for news_item in stock_info["news"][:3]:
                title = news_item.get("title", "").strip()
                if title:
                    if len(title) > 80:
                        title = title[:77] + "..."
                    report_lines.append(f"   📰 {title}")

        if notes:
            report_lines.append(f"   💬 {notes}")

        report_lines.append("")

    report_lines.append("=" * 40)
    report_lines.append("💡 以上為自動化產生，投資有風險，操作須謹慎。")

    report = "\n".join(report_lines)
    return report, updated_holdings


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

    # Build report
    report, updated_holdings = build_daily_report(holdings, manager)
    print(report)

    # Send LINE notification
    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("Daily report sent successfully.")

    # Update Sheet (clean expired catalysts, add new event dates)
    try:
        worksheet = manager.client.open(manager.sheet_name).worksheet("Holdings")
        rows = worksheet.get_all_values()
        
        if len(rows) >= 2:
            headers = rows[0]
            cat_col_idx = headers.index("catalystdate") if "catalystdate" in headers else -1
            
            if cat_col_idx < 0:
                logger.warning("catalystdate column not found in sheet.")
            
            for upd_h in updated_holdings:
                ticker = str(upd_h.get("ticker", upd_h.get("代碼", ""))).strip().upper()
                if not ticker:
                    continue
                
                for i, row in enumerate(rows[1:], start=2):
                    if row[0] == ticker:
                        if cat_col_idx >= 0 and "catalystdate" in upd_h:
                            worksheet.update_cell(i, cat_col_idx + 1, upd_h["catalystdate"])
                        break
        logger.info("Sheet updated with cleaned catalyst dates.")
    except Exception as exc:
        logger.warning("Failed to update Sheet: %s", exc)


if __name__ == "__main__":
    main()