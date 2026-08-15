"""Configuration and Google Sheets management module."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsManager:
    """Manage portfolio configuration and holdings stored in Google Sheets."""

    def __init__(self) -> None:
        service_account_json: Optional[str] = None
        sheet_name: Optional[str] = None

        service_account_json = self._get_env("GCP_SERVICE_ACCOUNT_JSON")
        sheet_name = self._get_env("SHEET_NAME", default="Portfolio")

        if not service_account_json:
            raise ValueError("GCP_SERVICE_ACCOUNT_JSON is not set.")

        service_account_json = service_account_json.lstrip("\ufeff")
        creds_dict: Dict[str, Any] = json.loads(service_account_json)
        credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        self.client: gspread.Client = gspread.authorize(credentials)
        self.sheet_name: str = sheet_name
        self.worksheet: Optional[gspread.Worksheet] = None

    def load_config(self) -> Dict[str, Any]:
        """Load holdings from Google Sheets.

        Supports both English and Chinese column headers.
        """
        self.worksheet = self.client.open(self.sheet_name).worksheet("Holdings")
        rows: List[List[Any]] = self.worksheet.get_all_values()

        if len(rows) < 2:
            raise ValueError("Sheet has fewer than 2 rows.")

        headers_raw: List[str] = [str(h).strip() for h in rows[0]]
        holdings: List[Dict[str, Any]] = []

        for row in rows[1:]:
            if not any(cell is not None and str(cell).strip() for cell in row):
                continue
            record: Dict[str, Any] = dict(zip(headers_raw, row))
            holdings.append(record)

        logger.info("Loaded %d holding(s) from Google Sheets.", len(holdings))
        return {"holdings": holdings, "config": {"sheet_name": self.sheet_name}}

    def update_holdings(
        self,
        ticker: str,
        new_shares: float,
        new_avg_cost: float,
    ) -> None:
        """Update shares and avg cost for a ticker."""
        if self.worksheet is None:
            raise RuntimeError("Call load_config() first.")

        rows: List[List[Any]] = self.worksheet.get_all_values()
        headers_raw: List[str] = [str(h).strip() for h in rows[0]]

        col_mapping = {
            "ticker": ["ticker", "代碼"],
            "shares": ["shares", "股數"],
            "avgcost": ["avgcost", "均價"],
            "updated": ["updated", "更新時間"],
        }

        idx_map = {}
        for key, possible_names in col_mapping.items():
            for name in possible_names:
                if name in headers_raw:
                    idx_map[key] = headers_raw.index(name)
                    break

        if "ticker" not in idx_map:
            raise ValueError("Ticker column not found.")

        for i, row in enumerate(rows[1:], start=2):
            if str(row[idx_map["ticker"]]).strip().upper() == ticker.upper():
                if "shares" in idx_map:
                    self.worksheet.update_cell(i, idx_map["shares"] + 1, int(new_shares))
                if "avgcost" in idx_map:
                    self.worksheet.update_cell(i, idx_map["avgcost"] + 1, round(new_avg_cost, 4))
                if "updated" in idx_map:
                    self.worksheet.update_cell(i, idx_map["updated"] + 1, datetime.now().isoformat())
                logger.info("Updated %s -> shares=%s, avg_cost=%.4f", ticker, int(new_shares), new_avg_cost)
                return

        raise ValueError(f"Ticker '{ticker}' not found.")


    def update_daily_info(
        self,
        ticker: str,
        earnings_date: str,
        eps_estimate: str,
        sentiment_label: str,
    ) -> None:
        """Update earnings date and sentiment for a single ticker.

        Args:
            ticker: Stock ticker symbol.
            earnings_date: Next earnings date string (e.g. '2026-08-11').
            eps_estimate: EPS estimate string (e.g. '.45').
            sentiment_label: Retail sentiment label (e.g. '偏多 🟢').
        """
        if self.worksheet is None:
            raise RuntimeError("Call load_config() first.")

        rows: List[List[Any]] = self.worksheet.get_all_values()
        headers_raw: List[str] = [str(h).strip() for h in rows[0]]

        # Find column indices dynamically
        col_map = {}
        possible_cols = {
            "ticker": ["ticker", "代碼"],
            "updated": ["updated", "更新時間"],
            "earnings": ["earnings_date", "next_earnings", "下次財報"],
            "eps_est": ["eps_estimate", "財報EPS預期"],
            "sentiment": ["sentiment", "散戶情緒"],
        }
        for key, names in possible_cols.items():
            for name in names:
                if name in headers_raw:
                    col_map[key] = headers_raw.index(name)
                    break

        if "ticker" not in col_map:
            raise ValueError("Ticker column not found.")

        for i, row in enumerate(rows[1:], start=2):
            if str(row[col_map["ticker"]]).strip().upper() == ticker.upper():
                updates = {}
                if "earnings" in col_map and earnings_date:
                    updates["earnings"] = earnings_date
                if "eps_est" in col_map and eps_estimate:
                    updates["eps_est"] = eps_estimate
                if "sentiment" in col_map and sentiment_label:
                    updates["sentiment"] = sentiment_label
                if "updated" in col_map:
                    updates["updated"] = datetime.now().isoformat()

                for col_key, value in updates.items():
                    if col_key in col_map:
                        self.worksheet.update_cell(i, col_map[col_key] + 1, value)

                logger.info("Updated %s -> earnings=%s, eps=%s, sentiment=%s",
                    ticker, earnings_date, eps_estimate, sentiment_label)
                return

        logger.warning("Ticker %s not found for daily info update.", ticker)



    def load_taiwan_stocks(self) -> List[Dict[str, Any]]:
        """Load Taiwan stock watchlist from Google Sheets Taiwan_Stock worksheet.

        Supports both English and Chinese column headers.
        """
        worksheet = self.client.open(self.sheet_name).worksheet("Taiwan_Stock")
        rows: List[List[Any]] = worksheet.get_all_values()

        if len(rows) < 2:
            raise ValueError("Taiwan_Stock sheet has fewer than 2 rows.")

        headers_raw: List[str] = [str(h).strip() for h in rows[0]]
        stocks: List[Dict[str, Any]] = []

        for row in rows[1:]:
            if not any(cell is not None and str(cell).strip() for cell in row):
                continue
            record: Dict[str, Any] = dict(zip(headers_raw, row))
            stocks.append(record)

        logger.info("Loaded %d Taiwan stock(s) from Google Sheets.", len(stocks))
        return stocks

    @staticmethod
    def _get_env(name: str, default: str = "") -> Optional[str]:
        import os
        val: Optional[str] = os.environ.get(name)
        if val is None or val.strip() == "":
            return default if default else None
        return val.strip()