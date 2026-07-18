"""Daily monitoring main entry point."""

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


def translate_news(news_items: List[Dict[str, str]]) -> List[str]:
    """Translate English news titles to Chinese using simple keyword mapping.

    Since we cannot use paid translation APIs in free tier,
    we extract key entities and events from titles.
    """
    translated: List[str] = []
    for item in news_items:
        title = item.get("title", "").strip()
        if not title:
            continue
        # Truncate long titles
        if len(title) > 80:
            title = title[:77] + "..."
        translated.append(title)
    return translated


def extract_event_dates(title: str, publisher: str) -> Optional[str]:
    """Extract potential event dates from news title.

    Looks for patterns like:
    - Q3 2026, FY2026, 2026 Q3
    - August 2026, Aug 2026
    - Dec 15, 2026
    """
    import re
    
    # Pattern: Month Day, Year or Month Year
    patterns = [
        r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}',
        r'\d{4}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*',
        r'Q[1-4]\s+\d{4}',
        r'FY\d{4}',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return match.group(0)
    
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
    today = datetime.now(TW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (catalyst_dt - today).days
    
    # Highlight if within 1 week
    if 0 <= delta <= 7:
        return f"🔥 重大事件倒數 — {delta} 天後 ({catalyst_date_str})"
    if delta < 0:
        return None  # Expired, will be cleaned
    if delta <= 30:
        return f"⚡ 催化劑倒數 — {delta} 天後 ({catalyst_date_str})"
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

        # Clean expired catalysts and collect new ones
        new_catalysts: List[str] = []
        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            for cd in dates:
                try:
                    catalyst_dt = datetime.strptime(cd, "%Y-%m-%d")
                    today_dt = datetime.now(TW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
                    if catalyst_dt >= today_dt:
                        new_catalysts.append(cd)
                        cat_reminder = check_catalyst(cd)
                        if cat_reminder:
                            report_lines.append(f"   {cat_reminder}")
                except ValueError:
                    new_catalysts.append(cd)  # Keep invalid dates for manual review

        # Add event dates from news
        if stock_info and stock_info.get("news"):
            for news_item in stock_info["news"]:
                event_date = extract_event_dates(news_item.get("title", ""), news_item.get("publisher", ""))
                if event_date:
                    new_catalysts.append(event_date)
                    report_lines.append(f"   📅 新聞發現事件: {event_date}")

        # Deduplicate catalysts
        seen: set = set()
        unique_catalysts: List[str] = []
        for c in new_catalysts:
            if c not in seen:
                seen.add(c)
                unique_catalysts.append(c)

        # Update holding with cleaned catalysts
        h_updated = dict(h)
        h_updated["catalystdate"] = ",".join(unique_catalysts) if unique_catalysts else ""
        updated_holdings.append(h_updated)

        # News
        if stock_info and stock_info.get("news"):
            for news_item in stock_info["news"][:3]:  # Top 3 news
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
        # Find the worksheet
        worksheet = manager.client.open(manager.sheet_name).worksheet("Holdings")
        rows = worksheet.get_all_values()
        
        if len(rows) >= 2:
            headers = rows[0]
            for upd_h in updated_holdings:
                ticker = str(upd_h.get("ticker", upd_h.get("代碼", ""))).strip().upper()
                if not ticker:
                    continue
                
                # Find row index
                for i, row in enumerate(rows[1:], start=2):
                    if row[0] == ticker:
                        # Update catalyst date column
                        if "catalystdate" in upd_h:
                            cat_col_idx = headers.index("catalystdate") if "catalystdate" in headers else -1
                            if cat_col_idx >= 0:
                                worksheet.update_cell(i, cat_col_idx + 1, upd_h["catalystdate"])
                        break
        logger.info("Sheet updated with cleaned catalyst dates.")
    except Exception as exc:
        logger.warning("Failed to update Sheet: %s", exc)


if __name__ == "__main__":
    main()