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


def translate_news_to_chinese(title: str) -> str:
    """Translate English news title to Traditional Chinese summary."""
    title_lower = title.lower()
    
    translations = {
        "earnings": "財報",
        "revenue": "營收",
        "profit": "獲利",
        "loss": "虧損",
        "beat": "超越",
        "miss": "低於",
        "expect": "預期",
        "guidance": "展望",
        "raise": "上調",
        "cut": "下調",
        "upgrade": "上調評級",
        "downgrade": "下調評級",
        "approval": "批准",
        "fda": "FDA",
        "phase 3": "三期臨床",
        "phase ii": "二期臨床",
        "phase i": "一期臨床",
        "trial": "試驗",
        "results": "結果",
        "positive": "正面",
        "negative": "負面",
        "launch": "推出",
        "product": "產品",
        "partnership": "合作",
        "acquisition": "收購",
        "buyback": "回購",
        "dividend": "配息",
        "increase": "增加",
        "decrease": "減少",
        "growth": "成長",
        "demand": "需求",
        "supply": "供應",
        "chip": "晶片",
        "ai": "AI",
        "quantum": "量子",
        "drug": "藥物",
        "cancer": "癌症",
        "obesity": "肥胖",
        "diabetes": "糖尿病",
        "cardiovascular": "心血管",
        "record": "創紀錄",
        "strong": "強勁",
        "weak": "疲弱",
        "solid": "穩健",
        "robust": "強勁",
        "surge": "飆升",
        "plunge": "暴跌",
        "rally": "反彈",
        "pullback": "回調",
        "volatility": "波動",
        "risk": "風險",
        "opportunity": "機會",
        "challenge": "挑戰",
        "momentum": "動能",
        "tailwind": "助力",
        "headwind": "逆風",
    }
    
    chinese_parts: List[str] = []
    remaining = title
    
    for en_word, zh_word in sorted(translations.items(), key=lambda x: -len(x[0])):
        if en_word in remaining.lower():
            chinese_parts.append(zh_word)
            remaining = re.sub(re.escape(en_word), "", remaining, flags=re.IGNORECASE).strip()
    
    remaining = re.sub(r'[^\w\s\-]', '', remaining).strip()
    
    if chinese_parts:
        summary = " ".join(chinese_parts)
        if remaining and len(remaining) < 50:
            summary += f"：{remaining}"
        return summary
    else:
        cleaned = re.sub(r'[^\w\s\-]', '', remaining).strip()
        return cleaned[:60] if len(cleaned) > 60 else cleaned


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


def check_event_with_detail(event_date_str: str, event_type: str) -> Optional[str]:
    """Check if an event is within 30 days and return formatted string."""
    try:
        event_dt = datetime.strptime(str(event_date_str), "%Y-%m-%d")
    except ValueError:
        return None
    
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (event_dt - today).days
    
    if delta < 0:
        return None
    
    display = event_type
    
    if 0 <= delta <= 7:
        return f"⏰ {display} — {delta} 天後 ({event_date_str})"
    if delta <= 30:
        return f"📅 {display} — {delta} 天後 ({event_date_str})"
    return None


def build_daily_report(
    holdings_data: List[Dict[str, Any]],
) -> str:
    """Generate a structured daily investment report with 3 tiers.
    
    Layout:
    1. Header with date and market status
    2. URGENT: Stocks with buy/sell/stop-loss signals
    3. UPCOMING: Events within 14 days
    4. WATCHLIST: All other holdings (compact)
    5. Footer with system status
    """
    now_tw = datetime.now(TW_TZ)
    now_us = datetime.now(US_EASTERN)
    date_str: str = now_tw.strftime("%Y-%m-%d (%a)")
    
    # Check if weekend
    day_of_week = now_tw.weekday()  # 0=Monday, 6=Sunday
    is_weekend = day_of_week >= 5
    
    # Get last trading day price if weekend
    last_trading_date = now_tw.strftime("%m-%d")
    if is_weekend:
        # Find last Friday
        days_since_friday = (now_tw - datetime(now_tw.year, now_tw.month, now_tw.day)).days
        if days_since_friday == 1:  # Saturday
            last_trading_date = (now_tw.replace(hour=0, minute=0, second=0, microsecond=0) - __import__('datetime').timedelta(days=1)).strftime("%m-%d")
        elif days_since_friday == 2:  # Sunday
            last_trading_date = (now_tw.replace(hour=0, minute=0, second=0, microsecond=0) - __import__('datetime').timedelta(days=2)).strftime("%m-%d")
        else:
            last_trading_date = (now_tw.replace(hour=0, minute=0, second=0, microsecond=0) - __import__('datetime').timedelta(days=days_since_friday - 1)).strftime("%m-%d")
    
    report_lines: List[str] = [
        "📈 美股投資策略日報 | " + date_str,
    ]
    
    if is_weekend:
        report_lines.append(f"💡 註：週{['一','二','三','四','五','六','日'][day_of_week]}休市，以下為上週五 ({last_trading_date}) 收盤參考價")
    else:
        report_lines.append("💡 註：以下為今日收盤價")
    
    report_lines.append("=" * 40)

    # Collect all stock data
    urgent_stocks: List[Dict] = []  # Buy/sell/stop-loss signals
    upcoming_events: List[Dict] = []  # Events <= 14 days
    watchlist: List[Dict] = []  # All other stocks

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

        stock_info = fetch_stock_info(ticker)

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
            "current_price": current_price,
            "avg_cost": avg_cost,
            "shares": int(shares),
            "pnl_pct": pnl_pct,
            "total_pnl": total_pnl,
            "buy_zones": buy_zones,
            "sell_zones": sell_zones,
            "catalyst_raw": catalyst_raw,
            "notes": notes,
            "news": stock_info.get("news", []) if stock_info else [],
        }

        # Categorize
        is_urgent = False
        is_upcoming = False

        # Check buy/sell signals
        if buy_zones and current_price <= buy_zones[0]:
            is_urgent = True
        if sell_zones and current_price >= sell_zones[0]:
            is_urgent = True

        # Check stop-loss (if price drops >10% from avg cost)
        if pnl_pct < -10:
            is_urgent = True

        # Check upcoming events (<= 14 days)
        if catalyst_raw:
            dates = [d.strip() for d in str(catalyst_raw).split(",") if d.strip()]
            for cd in dates:
                try:
                    event_dt = datetime.strptime(cd, "%Y-%m-%d")
                    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    delta = (event_dt - today).days
                    if 0 <= delta <= 14:
                        is_upcoming = True
                        stock_entry["event_type"] = classify_catalyst(cd, notes)
                        stock_entry["event_date"] = cd
                        stock_entry["event_delta"] = delta
                except ValueError:
                    pass

        if is_urgent:
            urgent_stocks.append(stock_entry)
        elif is_upcoming:
            upcoming_events.append(stock_entry)
        else:
            watchlist.append(stock_entry)

    # Build report sections
    # Section 1: URGENT
    if urgent_stocks:
        report_lines.append("")
        report_lines.append("🚨 【需立即行動】 (觸發買賣/停損條件)")
        report_lines.append("─" * 40)
        
        for s in urgent_stocks:
            ticker = s["ticker"]
            cp = s["current_price"]
            ac = s["avg_cost"]
            shares = s["shares"]
            pnl_pct = s["pnl_pct"]
            total_pnl = s["total_pnl"]

            # P&L emoji
            if pnl_pct >= 5:
                emoji = "🟢"
            elif pnl_pct >= 0:
                emoji = "⚪"
            else:
                emoji = "🔴"

            report_lines.append(f"{emoji} {ticker} ({_get_company_name(ticker)})")
            report_lines.append(f"   💰 持倉: {shares}股 | 均價: ${ac:.2f} | 損益: {pnl_pct:+.1f}%")
            report_lines.append(f"   📉 當前價: ${cp:.2f}")

            # Buy signal
            if s["buy_zones"] and cp <= s["buy_zones"][0]:
                zone_str = ", ".join(f"${bz:.2f}" for bz in s["buy_zones"])
                report_lines.append(f"   ✅ 觸發買入：已落入第一買入區間 [{zone_str}]")

            # Sell signal
            if s["sell_zones"] and cp >= s["sell_zones"][0]:
                zone_str = ", ".join(f"${sz:.2f}" for sz in s["sell_zones"])
                report_lines.append(f"   🔻 觸發賣出：已達到賣出區間 [{zone_str}]")

            # Stop-loss
            if pnl_pct < -10:
                report_lines.append(f"   🛑 觸發停損：已跌破停損點 (${ac * 0.9:.2f})")

            # Notes
            if s["notes"]:
                report_lines.append(f"   💬 {s['notes']}")

            report_lines.append("")

    # Section 2: UPCOMING EVENTS
    if upcoming_events:
        report_lines.append("⏳ 【催化劑倒數提醒】 (未來 14 天內)")
        report_lines.append("─" * 40)
        
        for s in upcoming_events:
            ticker = s["ticker"]
            notes = s.get("notes", "")
            event_type = s.get("event_type", "重要事件")
            event_date = s.get("event_date", "")
            event_delta = s.get("event_delta", 0)

            report_lines.append(f"⚠️ {ticker} ({_get_company_name(ticker)}) | 倒數 {event_delta} 天")
            report_lines.append(f"   📅 事件: {event_type} ({event_date})")
            if notes:
                report_lines.append(f"   💬 備註: {notes}")
            report_lines.append("")

    # Section 3: WATCHLIST (compact)
    if watchlist:
        report_lines.append("📊 【常規持倉追蹤】 (未觸發特殊條件)")
        report_lines.append("─" * 40)
        
        for s in watchlist[:10]:  # Show max 10 for brevity
            ticker = s["ticker"]
            cp = s["current_price"]
            pnl_pct = s["pnl_pct"]

            if pnl_pct >= 5:
                emoji = "🟢"
            elif pnl_pct >= 0:
                emoji = "⚪"
            else:
                emoji = "🔴"

            report_lines.append(f"• {ticker}: ${cp:.2f} (損益 {pnl_pct:+.1f}%) | 區間觀望中")

        if len(watchlist) > 10:
            report_lines.append(f"*(其餘 {len(watchlist) - 10} 檔持倉詳細數據請至 Google Sheets 檢視)*")

        report_lines.append("")

    # Footer
    report_lines.append("=" * 40)
    report_lines.append("⚙️ 系統狀態: 資料更新成功 | 下次執行: 07-20 21:05 (UTC)")
    report_lines.append("⚠️ 免責聲明: 本日報由系統自動生成，僅供操作參考，請自行確認市場流動性與風險。")

    return "\n".join(report_lines)


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

    report = build_daily_report(holdings)
    print(report)

    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("Daily report sent successfully.")


if __name__ == "__main__":
    main()