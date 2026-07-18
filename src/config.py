"""Configuration and Google Sheets management module.

Provides GoogleSheetsManager for loading portfolio configuration
and updating holdings from environment-variable-backed service account auth.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)


class GoogleSheetsManager:
    """Manage portfolio configuration and holdings stored in Google Sheets.

    All sensitive credentials are read from environment variables; nothing
    is hard-coded.
    """

    def __init__(self) -> None:  # noqa: D401
        service_account_json: Optional[str] = None
        sheet_name: Optional[str] = None

        service_account_json = self._get_env("GCP_SERVICE_ACCOUNT_JSON")
        sheet_name = self._get_env("SHEET_NAME", default="Portfolio")

        if not service_account_json:
            raise ValueError(
                "Environment variable GCP_SERVICE_ACCOUNT_JSON is not set. "
                "Provide the GCP service-account JSON key as a string."
            )

        creds_dict: Dict[str, Any] = json.loads(service_account_json)
        credentials = Credentials.from_service_account_info(creds_dict)
        self.client: gspread.Client = gspread.authorize(credentials)
        self.sheet_name: str = sheet_name
        self.worksheet: Optional[gspread.Worksheet] = None

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def load_config(self) -> Dict[str, Any]:
        """Load portfolio configuration and current holdings from Google Sheets.

        Expected sheet layout::

            Row 1: Header (Ticker, Shares, AvgCost, BuyZone, SellZone, CatalystDate, Notes)
            Row 2+: Data rows

        Returns:
            A dictionary with keys ``holdings`` (list of dicts) and
            ``config`` (dict of metadata).
        """
        self.worksheet = self.client.open(self.sheet_name).worksheet("Holdings")
        rows: List[List[Any]] = self.worksheet.get_all_values()

        if len(rows) < 2:
            raise ValueError(
                f"Sheet '{self.sheet_name}' has fewer than 2 rows. "
                "Expected a header row plus at least one data row."
            )

        headers: List[str] = [str(h).strip().lower() for h in rows[0]]
        holdings: List[Dict[str, Any]] = []

        for row in rows[1:]:
            if not any(cell is not None and str(cell).strip() for cell in row):
                continue  # skip empty rows
            record: Dict[str, Any] = dict(zip(headers, row))
            holdings.append(record)

        logger.info("Loaded %d holding(s) from Google Sheets.", len(holdings))
        return {"holdings": holdings, "config": {"sheet_name": self.sheet_name}}

    def update_holdings(
        self,
        ticker: str,
        new_shares: float,
        new_avg_cost: float,
    ) -> None:
        """Update the share count and average cost for a single ticker.

        Args:
            ticker: Stock ticker symbol (case-insensitive).
            new_shares: New total number of shares held.
            new_avg_cost: New average cost per share.

        Raises:
            ValueError: If the ticker is not found in the sheet.
        """
        if self.worksheet is None:
            raise RuntimeError(
                "No worksheet loaded. Call load_config() first."
            )

        rows: List[List[Any]] = self.worksheet.get_all_values()
        headers: List[str] = [str(h).strip().lower() for h in rows[0]]

        if "ticker" not in headers:
            raise ValueError("Expected 'Ticker' column not found in the sheet.")

        ticker_idx: int = headers.index("ticker")
        shares_idx: int = headers.index("shares") if "shares" in headers else -1
        avgcost_idx: int = (
            headers.index("avgcost") if "avgcost" in headers else -1
        )

        found: bool = False
        for i, row in enumerate(rows[1:], start=2):  # 1-based row index
            if str(row[ticker_idx]).strip().upper() == ticker.upper():
                if shares_idx >= 0:
                    self.worksheet.update_cell(i, shares_idx + 1, int(new_shares))
                if avgcost_idx >= 0:
                    self.worksheet.update_cell(i, avgcost_idx + 1, round(new_avg_cost, 4))
                # Also update timestamp
                ts_idx = (
                    headers.index("updated")
                    if "updated" in headers
                    else -1
                )
                if ts_idx >= 0:
                    self.worksheet.update_cell(
                        i, ts_idx + 1, datetime.now().isoformat()
                    )
                found = True
                logger.info(
                    "Updated %s → shares=%s, avg_cost=%.4f",
                    ticker,
                    int(new_shares),
                    new_avg_cost,
                )
                break

        if not found:
            raise ValueError(f"Ticker '{ticker}' not found in the sheet.")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _get_env(name: str, default: str = "") -> Optional[str]:
        """Read an environment variable, falling back to *default*."""
        import os

        val: Optional[str] = os.environ.get(name)
        if val is None or val.strip() == "":
            return default if default else None
        return val.strip()
