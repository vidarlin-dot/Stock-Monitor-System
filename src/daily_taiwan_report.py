# -*- coding: utf-8 -*-
"""Taiwan stock daily focus report — 四段式結構化日報.

FocusScore pipeline:
  1. Fetch market data for watchlist
  2. Compute FocusScore = 0.30*Heat + 0.25*Inst + 0.25*Cat
                         + 0.10*Trend + 0.10*WatchFit - Risk
  3. Filter: must be in watchlist AND score >= 65
  4. Select top 5 (max), mark category
  5. Build structured report with 4 sections
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz

from config import GoogleSheetsManager
from line_notifier import LineNotifier
from taiwan_market_data import (
    StockMarketData,
    compute_focus_score,
    fetch_all_stock_data,
    is_taiwan_trading_day,
)

logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone("Asia/Taipei")
FOCUS_THRESHOLD = 65
MAX_FOCUS_STOCKS = 5
MIN_FOCUS_STOCKS = 3


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def _fmt_price(val: float) -> str:
    if val <= 0:
        return "N/A"
    return f"{val:,.0f}"


def _fmt_pct(val: float) -> str:
    if val == 0:
        return "0.00%"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.2f}%"


def _vol_change_str(vol: int, avg: int) -> str:
    if avg <= 0 or vol <= 0:
        return "尚無足夠資料"
    ratio = vol / avg
    if ratio >= 2.0:
        return f"暴增至均量 {ratio:.1f} 倍"
    elif ratio >= 1.5:
        return f"放量至均量 {ratio:.1f} 倍"
    elif ratio >= 1.0:
        return f"較均量偏高 ({ratio:.1f} 倍)"
    elif ratio >= 0.7:
        return f"接近均量 ({ratio:.1f} 倍)"
    else:
        return f"缩量至均量 {ratio:.1f} 倍"


def _target_range_str(data: StockMarketData) -> str:
    if data.mean_target > 0:
        return f"{_fmt_price(data.mean_target)} 元"
    return "N/A"


def _target_upside_str(data: StockMarketData) -> str:
    if data.mean_target > 0 and data.current_price > 0:
        ups = (data.mean_target - data.current_price) / data.current_price * 100
        return f"{_fmt_pct(ups)} 上漲空間"
    return "N/A"


def _ma_position_str(data: StockMarketData) -> str:
    price = data.current_price
    if price <= 0:
        return "N/A"
    parts = []
    if data.close_5d > 0:
        pos = "上方" if price > data.close_5d else "下方"
        parts.append(f"MA5{pos}")
    if data.close_20d > 0:
        pos = "上方" if price > data.close_20d else "下方"
        parts.append(f"MA20{pos}")
    if data.close_60d > 0:
        pos = "上方" if price > data.close_60d else "下方"
        parts.append(f"MA60{pos}")
    return "、".join(parts) if parts else "N/A"


def _build_focus_detail(ticker: str, data: StockMarketData,
                        h: Dict[str, Any],
                        score_info: Dict[str, Any]) -> str:
    """Build the detailed section for one focus stock."""
    lines = []
    category = score_info["category"]
    score = score_info["focus_score"]

    lines.append("")
    lines.append(f"### {ticker} {data.short_name}｜焦點分數 {score:.0f}/100｜{category}")
    lines.append("")

    # 入選原因
    reasons = []
    if score_info["market_heat"] >= 60:
        reasons.append(f"市場熱度高 ({score_info['market_heat']:.0f} 分)")
    if score_info["catalyst"] >= 60:
        reasons.append(f"有明確催化事件 ({score_info['catalyst']:.0f} 分)")
    if score_info["institutional"] >= 60:
        reasons.append(f"分析師偏多 ({score_info['institutional']:.0f} 分)")
    if score_info["trend"] >= 60:
        reasons.append(f"技術面轉強 ({score_info['trend']:.0f} 分)")
    if score_info["watchlist_fit"] >= 70:
        reasons.append("核心追蹤股")
    reason_str = "、".join(reasons) if reasons else "綜合表現突出"
    lines.append(f"- 入選原因：{reason_str}，符合焦點股入選門檻（≥{FOCUS_THRESHOLD} 分）")

    # 收盤表現
    lines.append("")
    lines.append("- 收盤表現：收盤 {_fmt_price(data.current_price)} 元，")
    lines.append(f"  漲跌 {_fmt_pct(data.day_change_pct)}；")
    lines.append(f"  成交值 {_vol_change_str(data.volume, data.avg_volume_20d)}")

    # 籌碼訊號
    lines.append("")
    lines.append("- 籌碼訊號：")
    if data.rec_key:
        lines.append(f"  分析師評等：{data.rec_label}（{data.analysts} 位分析師）")
    else:
        lines.append("  分析師評等：尚無足夠資料")
    if data.mean_target > 0:
        lines.append(f"  目標價：{_target_range_str(data)}（{_target_upside_str(data)}）")
    lines.append("  ⚠️ 三大法人買賣超資料暫無法從 Yahoo Finance 取得，")
    lines.append("    請手動更新至 Google Sheet「Taiwan_Stock」工作表的法人欄位")

    # 主要催化
    lines.append("")
    lines.append("- 主要催化：")
    catalysts = _extract_catalysts(data, h)
    if catalysts:
        for c in catalysts[:3]:
            lines.append(f"  1. {c}")
    else:
        lines.append("  尚無明確催化事件")

    # 市場／法人論點摘要
    lines.append("")
    lines.append("- 市場／法人論點摘要：")
    news_points = _summarize_news(data.news_list, data.rec_label)
    if news_points:
        for np in news_points[:3]:
            lines.append(f"  · {np}")
    else:
        lines.append("  近期無顯著新聞或觀點")

    # 技術與量價位置
    lines.append("")
    lines.append("- 技術與量價位置：")
    lines.append(f"  均線位置：{_ma_position_str(data)}")
    if data.high_20d > 0 and data.current_price > 0:
        dist = (data.high_20d - data.current_price) / data.high_20d * 100
        if dist <= 2:
            lines.append(f"  距離 20 日高點僅 {dist:.1f}%，接近突破區")
        elif dist <= 10:
            lines.append(f"  距 20 日高點 {dist:.1f}%，整理區間內")
        else:
            lines.append(f"  距 20 日高點 {dist:.1f}%，尚未突破")

    # 風險與反證
    lines.append("")
    lines.append("- 風險與反證：")
    risks = _extract_risks(data, score_info)
    if risks:
        for r in risks:
            lines.append(f"  · {r}")
    else:
        lines.append("  暫無明顯風險訊號")

    # 今日觀察重點
    lines.append("")
    lines.append("- 今日觀察重點：")
    observations = _generate_observations(data, score_info)
    for o in observations[:3]:
        lines.append(f"  · {o}")

    # 判讀
    lines.append("")
    lines.append(f"- 判讀：{_generate_verdict(data, score_info)}")

    return "\n".join(lines)


def _extract_catalysts(data: StockMarketData, h: Dict[str, Any]) -> List[str]:
    """Extract catalyst events from sheet data and news."""
    catalysts = []
    catalyst_date = str(h.get("catalystdate", h.get("催化事件日期", ""))).strip()
    notes = str(h.get("notes", h.get("備註", ""))).strip()
    if catalyst_date:
        catalysts.append(f"[{catalyst_date}] {catalyst_date} 事件")
    if notes:
        catalysts.append(notes)
    # News catalysts
    for item in data.news_list[:3]:
        title = _extract_title(item)
        if title and len(title) > 5:
            catalysts.append(f"[新聞] {title[:50]}")
    return catalysts


def _summarize_news(news_list: List[Dict[str, Any]], rec_label: str) -> List[str]:
    """Summarize recent news headlines."""
    points = []
    for item in news_list[:4]:
        title = _extract_title(item)
        if title:
            points.append(title[:60])
    if rec_label and rec_label != "觀望":
        points.insert(0, f"分析師整體評等：{rec_label}")
    return points


def _extract_risks(data: StockMarketData, score_info: Dict[str, Any]) -> List[str]:
    """Extract risk signals."""
    risks = []
    if data.day_change_pct <= -3:
        risks.append(f"當日下跌 {data.day_change_pct:.1f}%，需觀察是否續跌")
    price = data.current_price
    if data.mean_target > 0 and price > data.mean_target * 1.15:
        risks.append(f"股價已超出目標價 {((price/data.mean_target-1)*100):.0f}%，估值偏高")
    if score_info["risk_penalty"] >= 5:
        risks.append(f"風險扣分 {score_info['risk_penalty']:.0f} 分，請留意潛在負面因素")
    return risks


def _generate_observations(data: StockMarketData,
                           score_info: Dict[str, Any]) -> List[str]:
    """Generate today's observation points."""
    obs = []
    if data.avg_volume_20d > 0:
        obs.append(f"成交量是否維持在 20 日均量 {_fmt_price(data.avg_volume_20d)} 以上")
    obs.append(f"收盤價是否站稳 MA20 ({_fmt_price(data.close_20d)} 元)")
    if data.high_20d > 0:
        obs.append(f"是否突破 20 日高點 {_fmt_price(data.high_20d)} 元")
    obs.append("分析師評等或目標價是否有調整")
    return obs


def _generate_verdict(data: StockMarketData,
                      score_info: Dict[str, Any]) -> str:
    """Generate a verdict statement."""
    cat = score_info["category"]
    score = score_info["focus_score"]
    change = data.day_change_pct

    if cat == "風險焦點":
        return "有明確風險訊號，列為風險觀察，不宜追價，等待籌碼轉向確認"
    elif score >= 80:
        if change > 3:
            return "偏多焦點但當日大漲，注意追價風險，建議等待回測均線後再評估"
        return "綜合表現突出，籌碼與事件面皆轉正向，可作為重點觀察標的"
    elif score >= 70:
        return "偏多但需待量價確認，觀察是否能突破近期整理區間"
    elif score >= 65:
        return "訊號初步出現，建議持續追蹤籌碼與新聞面變化再決定操作"
    else:
        return "中性觀察，目前缺乏明確催化或籌碼支持，暫不列為重點焦點"


def _extract_title(item: Dict[str, Any]) -> str:
    if isinstance(item.get("content"), dict):
        return item["content"].get("title", "") or ""
    return item.get("title", "") or ""


def _build_watchlist_summary(all_scores: Dict[str, Dict[str, Any]],
                              threshold: int = FOCUS_THRESHOLD) -> str:
    """Build the 'not selected but worth watching' table."""
    lines = []
    below = [(t, s) for t, s in all_scores.items() if s["focus_score"] < threshold]
    if not below:
        lines.append("  所有追蹤股均已達焦點門檻。")
        return "\n".join(lines)
    lines.append("  | 股票代號 | 焦點分數 | 未入選原因 |")
    lines.append("  |---|---|---|")
    for ticker, s in sorted(below, key=lambda x: x[1]["focus_score"], reverse=True)[:10]:
        reasons = []
        if s["market_heat"] < 50:
            reasons.append("熱度不足")
        if s["catalyst"] < 50:
            reasons.append("無催化")
        if s["institutional"] < 50:
            reasons.append("籌碼偏中性")
        if s["trend"] < 50:
            reasons.append("技術面弱勢")
        reason_str = "、".join(reasons) if reasons else "綜合分數未達門檻"
        lines.append(f"  | {ticker} | {s['focus_score']:.0f} | {reason_str} |")
    return "\n".join(lines)


def build_taiwan_focus_report(stocks_data: Dict[str, StockMarketData],
                               watchlist: List[Dict[str, Any]]) -> str:
    """Build the full 4-section Taiwan focus report."""
    now_tw = datetime.now(TW_TZ)
    date_str = now_tw.strftime("%Y-%m-%d")
    prev_tw = now_tw - timedelta(days=1)
    prev_date_str = prev_tw.strftime("%Y-%m-%d")

    lines = []
    lines.append("")
    lines.append(f"# 台股追蹤焦點股日報｜{date_str}")
    lines.append(f"資料基準日：{prev_date_str}")
    lines.append("")

    # --- Section 1: Market Summary ---
    lines.append("## 一、市場摘要")
    lines.append("")
    lines.append(f"- 報告日期：{date_str}")
    lines.append(f"- 資料基準：前一交易日 {prev_date_str}")
    lines.append(f"- 追蹤股票總數：{len(watchlist)} 檔")

    # Compute scores for all stocks
    all_scores: Dict[str, Dict[str, Any]] = {}
    for h in watchlist:
        ticker = str(h.get("ticker", h.get("代碼", ""))).strip()
        if not ticker:
            continue
        data = stocks_data.get(ticker)
        if data is None or data.current_price <= 0:
            continue
        score_info = compute_focus_score(data, h)
        all_scores[ticker] = score_info

    lines.append(f"- 符合焦點條件股票（≥{FOCUS_THRESHOLD} 分）：")
    qualified = [(t, s) for t, s in all_scores.items() if s["focus_score"] >= FOCUS_THRESHOLD]
    lines.append(f"  {len(qualified)} 檔")
    lines.append("")

    # --- Section 2: Focus Stocks ---
    lines.append("## 二、我的追蹤清單命中結果")
    lines.append("")
    lines.append(f"- 追蹤股票總數：{len(watchlist)}")
    lines.append(f"- 符合焦點條件股票：{len(qualified)}")
    lines.append(f"- 入選焦點股票（最多 {MAX_FOCUS_STOCKS} 檔）：")

    if not qualified:
        lines.append("  本日無符合焦點門檻的追蹤股，請留意市場變化。")
    else:
        qualified.sort(key=lambda x: x[1]["focus_score"], reverse=True)
        for idx, (ticker, s) in enumerate(qualified[:MAX_FOCUS_STOCKS], 1):
            data = stocks_data[ticker]
            lines.append(
                f"  {idx}. {ticker} {data.short_name}｜"
                f"{s['focus_score']:.0f} 分｜{s['category']}"
            )
        if len(qualified) > MAX_FOCUS_STOCKS:
            lines.append(f"  ... 等共 {len(qualified)} 檔符合條件（僅顯示前{MAX_FOCUS_STOCKS}檔）")
    lines.append("")

    # --- Section 3: Focus Stock Details ---
    lines.append("## 三、焦點股明細")
    lines.append("")

    if qualified:
        qualified.sort(key=lambda x: x[1]["focus_score"], reverse=True)
        for ticker, s in qualified[:MAX_FOCUS_STOCKS]:
            data = stocks_data[ticker]
            h = next((hw for hw in watchlist
                      if str(hw.get("ticker", hw.get("代碼", ""))).strip() == ticker), {})
            lines.append(_build_focus_detail(ticker, data, h, s))
    else:
        lines.append("本日報暫無符合焦點門檻的個股。")
        lines.append("可能原因：市場波動不足、無新催化事件、或分析師資料暫缺。")
    lines.append("")

    # --- Section 4: Not Selected but Worth Watching ---
    lines.append("## 四、未列入主要焦點但值得留意的追蹤股")
    lines.append("")
    lines.append(_build_watchlist_summary(all_scores))
    lines.append("")
    lines.append("---")
    lines.append("⚠️ 本報告僅供研究參考，不構成任何投資建議。")
    lines.append("「熱門」不等於「值得買進」，請自行判斷風險。")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("Taiwan Focus Report starting...")

    if not is_taiwan_trading_day():
        now_tw = datetime.now(TW_TZ)
        date_str = now_tw.strftime("%Y-%m-%d (%a)")
        msg = (f"# 台股追蹤焦點股日報｜{date_str}\n\n"
               f"⚠️ 註：今日處於休市日，今日不派發。\n")
        print(msg)
        logger.info("Taiwan market is closed today, skipping report.")
        sys.exit(0)

    manager = GoogleSheetsManager()
    watchlist = manager.load_taiwan_stocks()
    if not watchlist:
        logger.warning("No Taiwan stock data, aborting.")
        sys.exit(0)

    logger.info("Processing %d Taiwan stock(s)...", len(watchlist))

    # Fetch market data
    stocks_data = fetch_all_stock_data(
        [str(h.get("ticker", h.get("代碼", ""))).strip()
         for h in watchlist]
    )

    if not stocks_data:
        # Try cache
        from taiwan_market_data import _load_cache
        cached = _load_cache("__ALL__")
        if cached:
            logger.info("Using cached data from previous trading day.")
            # Reconstruct StockMarketData objects
            from taiwan_market_data import StockMarketData
            stocks_data = {
                t: StockMarketData(**d) for t, d in cached.items()
                if isinstance(d, dict)
            }
        else:
            logger.error("No stock data available, aborting.")
            sys.exit(1)

    # Build and send report
    report = build_taiwan_focus_report(stocks_data, watchlist)
    print(report)

    notifier = LineNotifier()
    notifier.send_push_message(report)
    logger.info("Taiwan focus report pushed successfully.")


if __name__ == "__main__":
    main()