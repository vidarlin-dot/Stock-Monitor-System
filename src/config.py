"""Configuration and portfolio management module.

Provides PortfolioManager for loading/saving holdings from a local JSON file.
No cloud dependencies required.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"


class PortfolioManager:
    """Manage portfolio holdings stored in a local JSON file.

    All paths are resolved relative to this module, so no environment
    variables are required for credentials.
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        """Initialize PortfolioManager.

        Args:
            data_dir: Directory containing ``portfolio.json``.
                Defaults to ``data/`` next to this module.
        """
        self.data_dir: Path = data_dir or DEFAULT_DATA_DIR
        self.portfolio_path: Path = self.data_dir / "portfolio.json"
        self._ensure_file()

    def _ensure_file(self) -> None:
        """Create portfolio.json with sample data if it does not exist."""
        if not self.portfolio_path.exists():
            logger.info(
                "Portfolio file not found at %s. Creating with sample data.",
                self.portfolio_path,
            )
            sample: List[Dict[str, Any]] = [
                {
                    "ticker": "AAPL",
                    "shares": 100,
                    "avg_cost": 150.00,
                    "buy_zone": "140.00,135.00",
                    "sell_zone": "170.00,180.00",
                    "catalyst_date": "2026-09-15,2026-10-20",
                    "notes": "Q3 earnings",
                },
            ]
            self.portfolio_path.write_bytes(
                json.dumps(sample, indent=2, ensure_ascii=False).encode("utf-8")
            )

    def load_holdings(self) -> List[Dict[str, Any]]:
        """Load all holdings from the JSON file.

        Uses ``utf-8-sig`` encoding to handle optional BOM.

        Returns:
            A list of holding dictionaries.
        """
        raw: str = self.portfolio_path.read_text(encoding="utf-8-sig")
        raw = raw.lstrip("\ufeff")
        holdings: List[Dict[str, Any]] = json.loads(raw)
        logger.info("Loaded %d holding(s) from %s.", len(holdings), self.portfolio_path)
        return holdings

    def save_holdings(self, holdings: List[Dict[str, Any]]) -> None:
        """Overwrite the portfolio file with updated holdings."""
        self.portfolio_path.write_bytes(
            json.dumps(holdings, indent=2, ensure_ascii=False).encode("utf-8")
        )
        logger.info("Saved %d holding(s) to %s.", len(holdings), self.portfolio_path)

    def update_holding(
        self,
        ticker: str,
        new_shares: float,
        new_avg_cost: float,
    ) -> None:
        """Update shares and average cost for a single ticker."""
        holdings: List[Dict[str, Any]] = self.load_holdings()

        for h in holdings:
            if str(h.get("ticker", "")).strip().upper() == ticker.upper():
                h["shares"] = int(new_shares)
                h["avg_cost"] = round(new_avg_cost, 4)
                self.save_holdings(holdings)
                logger.info(
                    "Updated %s -> shares=%s, avg_cost=%.4f",
                    ticker,
                    int(new_shares),
                    new_avg_cost,
                )
                return

        raise ValueError(f"Ticker '{ticker}' not found in portfolio.")