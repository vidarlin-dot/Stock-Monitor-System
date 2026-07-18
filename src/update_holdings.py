"""Interactive holdings-update script.

Parses broker trade-confirm text (Chinese / English mixed) via regex,
computes updated share counts and average costs, then writes back to
the local JSON portfolio file.

Usage::

    python src/update_holdings.py

Then paste your broker confirmation text when prompted.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from config import PortfolioManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns – designed for high fault-tolerance with mixed CN/EN text
# ---------------------------------------------------------------------------

_TICKER_PATTERNS: List[str] = [
    r"(?:股票|標的|代碼|Ticker|Symbol)[:：\s]*([A-Z]{1,5}\d{0,4})",
    r"([A-Z]{2,5})\s+(?:股|share)",
    r"\b([A-Z]{2,5})\b",
]

_DIRECTION_PATTERNS: List[str] = [
    r"(?:買進|買|Buy|購入|入場)",
    r"(?:賣出|賣|Sell|沽出|出場|平倉)",
]

_SHARE_PATTERNS: List[str] = [
    r"(?:股數|數量|Shares|張數|口數)[:：\s]*(\d+)",
    r"(\d+)\s*(?:股|share|張|口)\b",
    r"(?:共|合計|total)[:：\s]*(\d+)",
]

_PRICE_PATTERNS: List[str] = [
    r"(?:均價|成交價|Price|價格|單價)[:：\s]*[\$NT\$ ]*([\d,]+\.?\d*)",
    r"([\d,]+\.?\d*)\s*(?:USD|美元|元|TWD|台幣)?(?:每股|per\s*share)?",
    r"(?:at|以)[:：\s]*[\$ ]*([\d,]+\.?\d*)",
]


def _clean_number(raw: str) -> float:
    """Strip commas and convert to float."""
    return float(raw.replace(",", ""))


def _upper_pat(pattern: str) -> str:
    """Return pattern uppercased for comparison."""
    return pattern.upper()


def parse_trade_text(text: str) -> Optional[Tuple[str, str, float, float]]:
    """Parse trade confirmation text and return (ticker, direction, shares, price).

    Args:
        text: Raw broker text (pasted by the user).

    Returns:
        A 4-tuple of (ticker, direction, shares, price), or ``None`` if
        parsing fails entirely.
    """
    upper_text: str = text.upper()

    # --- Ticker ---
    ticker: Optional[str] = None
    for pat in _TICKER_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            ticker = m.group(1).strip()
            break

    # --- Direction ---
    direction: Optional[str] = None
    for pat in _DIRECTION_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            upper_pat_str = _upper_pat(pat)
            direction = (
                "BUY" if "BUY" in upper_pat_str or "買" in text else "SELL"
            )
            break

    # --- Shares ---
    shares: Optional[float] = None
    for pat in _SHARE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            shares = _clean_number(m.group(1))
            break

    # --- Price ---
    price: Optional[float] = None
    for pat in _PRICE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            price = _clean_number(m.group(1))
            break

    if ticker and direction and shares and price:
        return (ticker, direction, shares, price)

    return None


def manual_fallback() -> Tuple[str, str, float, float]:
    """Prompt the user for trade details interactively.

    Returns:
        (ticker, direction, shares, price)
    """
    print("\n⚠️  無法自動解析交易明細，請手動輸入：\n")
    ticker = input("股票代碼 (Ticker): ").strip().upper()
    while not ticker:
        ticker = input("股票代碼 (Ticker): ").strip().upper()

    direction_raw = (
        input("方向 [B]買進 / [S]賣出 (Buy/Sell): ").strip().upper()
    )
    while direction_raw not in ("B", "S", "BUY", "SELL"):
        direction_raw = input("方向 [B]買進 / [S]賣出: ").strip().upper()
    direction = "BUY" if direction_raw.startswith("B") else "SELL"

    shares = float(input("股數 (Shares): "))
    price = float(input("成交均價 (Price): "))

    return (ticker, direction, shares, price)


def compute_new_avg(
    old_shares: float,
    old_avg: float,
    traded_shares: float,
    trade_price: float,
    direction: str,
) -> Tuple[float, float]:
    """Compute updated shares and average cost after a trade.

    Args:
        old_shares: Current total shares held.
        old_avg: Current average cost per share.
        traded_shares: Number of shares bought or sold.
        trade_price: Execution price per share.
        direction: ``"BUY"`` or ``"SELL"``.

    Returns:
        (new_shares, new_avg_cost)
    """
    if direction == "BUY":
        new_shares: float = old_shares + traded_shares
        if new_shares == 0:
            return (0.0, 0.0)
        new_avg = (old_shares * old_avg + traded_shares * trade_price) / new_shares
        return (new_shares, new_avg)

    # SELL
    new_shares = old_shares - traded_shares
    if new_shares <= 0:
        new_shares = 0.0
        new_avg: float = 0.0
    else:
        new_avg = old_avg  # average cost unchanged on sell
    return (new_shares, new_avg)


def main() -> None:
    """Entry point for the holdings-update script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    print("=" * 50)
    print("  Stock Monitor — Holdings Update Tool")
    print("=" * 50)

    # Load portfolio
    manager = PortfolioManager()
    holdings: List[Dict[str, Any]] = manager.load_holdings()

    # Build lookup: ticker → index
    holdings_map: Dict[str, int] = {}
    for idx, h in enumerate(holdings):
        tk = str(h.get("ticker", "")).strip().upper()
        if tk:
            holdings_map[tk] = idx

    # --- Parse trade text ---
    print("\n請貼上券商交易明細文字 (貼完後按 Enter 兩次，或 Ctrl+D / Ctrl+Z 結束)：")
    lines: list = []
    try:
        while True:
            line = input()
            if line.strip() == "" and lines and lines[-1] == "":
                break
            lines.append(line)
    except EOFError:
        pass

    trade_text = "\n".join(lines).strip()
    if not trade_text:
        print("⚠️  未收到任何輸入，跳過。")
        sys.exit(0)

    parsed = parse_trade_text(trade_text)
    if parsed is None:
        print("⚠️  Regex 解析失敗，進入手動模式…")
        ticker, direction, traded_shares, trade_price = manual_fallback()
    else:
        ticker, direction, traded_shares, trade_price = parsed
        print(f"\n✓ 解析結果 → {ticker} | {direction} | {traded_shares} 股 | ${trade_price}")

    # --- Compute ---
    if ticker not in holdings_map:
        print(f"\n⚠️  找不到 {ticker} 的持倉紀錄，請先在 portfolio.json 中新增。")
        sys.exit(1)

    old_row = holdings[holdings_map[ticker]]
    old_shares = float(old_row.get("shares", 0))
    old_avg = float(old_row.get("avg_cost", 0))

    new_shares, new_avg = compute_new_avg(
        old_shares, old_avg, traded_shares, trade_price, direction
    )

    print(f"\n📊 更新前 → {old_shares:.0f} 股 | 均價 ${old_avg:.4f}")
    print(f"📊 更新後 → {new_shares:.0f} 股 | 均價 ${new_avg:.4f}")

    confirm = input("\n確定要寫入 portfolio.json？[Y/n]: ").strip().lower()
    if confirm.startswith("n"):
        print("已取消。")
        sys.exit(0)

    manager.update_holding(ticker, new_shares, new_avg)
    print("\n✅ 持倉已成功更新！")


if __name__ == "__main__":
    main()
