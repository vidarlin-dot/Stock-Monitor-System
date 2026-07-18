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

        try:
            calendar = stock.calendar
            if calendar and isinstance(calendar, dict) and calendar:
                dates_list = list(calendar.values())[0] if calendar else []
                info["earnings_dates"] = [d for d in dates_list if d][:3]
        except Exception:
            info["earnings_dates"] = []

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
    """Classify news title into event category."""
    title_lower = title.lower()
    
    if any(kw in title_lower for kw in ["earnings", "quarterly", "q1", "q2", "q3", "q4", "財報", "季報", "年報"]):
        return "財報"
    if any(kw in title_lower for kw in ["fda", "approval", "批准", "認證", "regulatory", "nda", "bla"]):
        return "FDA批准"
    if any(kw in title_lower for kw in ["phase 3", "phase ii", "phase i", "clinical", "試驗", "結果"]):
        return "臨床試驗"
    if any(kw in title_lower for kw in ["production", "量產", "manufacturing", "產能"]):
        return "量產"
    if any(kw in title_lower for kw in ["partnership", "合作", "collaboration", "alliance"]):
        return "合作"
    if any(kw in title_lower for kw in ["launch", "推出", "release", "新品"]):
        return "產品發布"
    if any(kw in title_lower for kw in ["investment", "融資", "fundraising", "ipo", "listing", "上市"]):
        return "融資/上市"
    if any(kw in title_lower for kw in ["upgrade", "downgrade", "目標價", "rating", "分析師"]):
        return "評級調整"
    return "重要公告"


def extract_event_date(title: str) -> Optional[str]:
    """Extract potential event date from news title."""
    match = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(\d{4})', title, re.IGNORECASE)
    if match:
        month_str, day_str, year_str = match.groups()
        month_map = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                     "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        month = month_map.get(month_str.lower()[:3])
        if month:
            try:
                dt = datetime(int(year_str), month, int(day_str))
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
    
    match = re.search(r'Q(\d)\s+(\d{4})', title, re.IGNORECASE)
    if match:
        quarter, year = match.groups()
        quarter_months = {"1": (1, 31), "2": (4, 30), "3": (7, 31), "4": (10, 31)}
        if quarter in quarter_months:
            start_month, _ = quarter_months[quarter]
            try:
                dt = datetime(int(year), start_month, 15)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
    
    match = re.search(r'FY(\d{4})', title, re.IGNORECASE)
    if match:
        year = match.group(1)
        try:
            dt = datetime(int(year), 12, 31)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    
    return None


def check_catalyst_with_detail(catalyst_date_str: Optional[str], event_name: str = "", event_detail: str = "") -> Optional[str]:
    """Check if a catalyst event is within 30 days and return formatted string.

    Args:
        catalyst_date_str: Date string in YYYY-MM-DD format.
        event_name: Event category (e.g., '財報', 'FDA批准').
        event_detail: Additional detail about the event.

    Returns:
        Formatted reminder string or None if expired.
    """
    if not catalyst_date_str:
        return None
    try:
        catalyst_dt = datetime.strptime(str(catalyst_date_str), "%Y-%m-%d")
    except ValueError:
        return None
    
    today = datetime.now(TW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (catalyst_dt - today).days
    
    if delta < 0:
        return None  # Expired
    
    # Build event display with detail
    if event_detail:
        display = f"{event_name}（{event_detail}）"
    elif event_name and event_name != "催化劑":
        display = event_name
    else:
        display = "催化劑"
    
    if 0 <= delta <= 7:
        return f"🔥 {display}倒數 — {delta} 天後 ({catalyst_date_str})"
    if delta <= 30:
        return f"⚡ {display}倒數 — {delta} 天後 ({catalyst_date_str})"
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

        # Process catalysts with event names and details
        new_catalysts: List[Dict[str, str]] = []
        
        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            for cd in dates:
                try:
                    catalyst_dt = datetime.strptime(cd, "%Y-%m-%d")
                    today_dt = datetime.now(TW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
                    if catalyst_dt >= today_dt:
                        new_catalysts.append({"date": cd, "name": "催化劑", "detail": ""})
                except ValueError:
                    pass

        if stock_info and stock_info.get("news"):
            for news_item in stock_info["news"]:
                title = news_item.get("title", "")
                if not title:
                    continue
                
                event_date = extract_event_date(title)
                if event_date:
                    event_category = classify_event(title)
                    # Extract key detail from title (first 30 chars)
                    event_detail = title[:50] + "..." if len(title) > 50 else title
                    new_catalysts.append({"date": event_date, "name": event_category, "detail": event_detail})

        # Deduplicate
        seen_dates: set = set()
        unique_catalysts: List[Dict[str, str]] = []
        for c in new_catalysts:
            if c["date"] not in seen_dates:
                seen_dates.add(c["date"])
                unique_catalysts.append(c)

        # Show catalyst reminders with event details
        for c in unique_catalysts:
            cat_reminder = check_catalyst_with_detail(c["date"], c["name"], c["detail"])
            if cat_reminder:
                report_lines.append(f"   {cat_reminder}")

        # Update holding
        h_updated = dict(h)
        h_updated["catalystdate"] = ",".join(c["date"] for c in unique_catalysts)
        updated_holdings.append(h_updated)

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

    report, updated_holdings = build_daily_report(holdings)
    print(report)

    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("Daily report sent successfully.")

    # Update Sheet
    try:
        worksheet = manager.client.open(manager.sheet_name).worksheet("Holdings")
        rows = worksheet.get_all_values()
        
        if len(rows) >= 2:
            headers = rows[0]
            cat_col_idx = headers.index("catalystdate") if "catalystdate" in headers else -1
            
            if cat_col_idx >= 0:
                for upd_h in updated_holdings:
                    ticker = str(upd_h.get("ticker", upd_h.get("代碼", ""))).strip().upper()
                    if not ticker:
                        continue
                    
                    for i, row in enumerate(rows[1:], start=2):
                        if row[0] == ticker:
                            worksheet.update_cell(i, cat_col_idx + 1, upd_h["catalystdate"])
                            break
        logger.info("Sheet updated with cleaned catalyst dates.")
    except Exception as exc:
        logger.warning("Failed to update Sheet: %s", exc)


if __name__ == "__main__":
    main()