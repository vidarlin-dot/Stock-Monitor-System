'''Financial data sources integration.

Integrates 3 free sources for earnings and financial summaries:
1. Earnings Whispers - Most accurate earnings dates & whisper numbers
2. Finviz - Visual fundamental data cards
3. TradingView - Financial dashboard with EPS surprise history
'''

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class FinancialDataFetcher:
    '''Fetch financial data from multiple free sources.'''

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    def fetch_all(self, ticker: str) -> Optional[Dict[str, Any]]:
        '''Fetch financial data from all available sources.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            Dictionary containing consolidated financial data, or None on failure.
        '''
        result: Dict[str, Any] = {
            "ticker": ticker.upper(),
            "source": {},
            "summary": "",
        }

        ew_data = self._fetch_earnings_whispers(ticker)
        if ew_data:
            result["source"]["earnings_whispers"] = ew_data

        finviz_data = self._fetch_finviz(ticker)
        if finviz_data:
            result["source"]["finviz"] = finviz_data

        tv_data = self._fetch_tradingview(ticker)
        if tv_data:
            result["source"]["tradingview"] = tv_data

        if result["source"]:
            result["summary"] = self._build_summary(result["source"])

        return result if result["source"] else None

    def _fetch_earnings_whispers(self, ticker: str) -> Optional[Dict[str, Any]]:
        '''Fetch data from Earnings Whispers.'''
        try:
            url = f"https://earningswhispers.com/study/{ticker}"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            data: Dict[str, Any] = {}

            next_row = soup.find("table", class_="ew-table")
            if next_row:
                rows = next_row.find_all("tr")
                if len(rows) > 1:
                    cells = rows[1].find_all(["td", "th"])
                    if len(cells) >= 4:
                        data["next_earnings_date"] = cells[0].get_text(strip=True)
                        data["next_earnings_time"] = cells[1].get_text(strip=True)
                        data["consensus_eps"] = cells[2].get_text(strip=True)
                        data["whisper_number"] = cells[3].get_text(strip=True)

            surprises: List[Dict[str, str]] = []
            recent_table = soup.find("table", id="recent-earnings-surprises")
            if recent_table:
                for row in recent_table.find_all("tr")[1:6]:
                    cells = row.find_all("td")
                    if len(cells) >= 5:
                        surprises.append({
                            "date": cells[0].get_text(strip=True),
                            "eps_actual": cells[1].get_text(strip=True),
                            "eps_estimate": cells[2].get_text(strip=True),
                            "surprise_pct": cells[3].get_text(strip=True),
                            "revenue_actual": cells[4].get_text(strip=True),
                        })
                data["recent_surprises"] = surprises

            return data if data else None

        except Exception as exc:
            logger.debug("Earnings Whispers fetch failed for %s: %s", ticker, exc)
            return None

    def _fetch_finviz(self, ticker: str) -> Optional[Dict[str, Any]]:
        '''Fetch data from Finviz.'''
        try:
            url = f"https://finviz.com/quote.ashx?t={ticker}"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            data: Dict[str, Any] = {}

            tables = soup.find_all("table", class_="snapshot-table2")
            if tables:
                for row in tables[0].find_all("tr"):
                    cells = row.find_all(["td", "th"])
                    if len(cells) == 2:
                        key = cells[0].get_text(strip=True).lower()
                        value = cells[1].get_text(strip=True)

                        if "pe ratio" in key:
                            data["pe_ratio"] = value
                        elif "forward pe" in key:
                            data["forward_pe"] = value
                        elif "eps growth qoq" in key:
                            data["eps_growth_qoq"] = value
                        elif "revenue growth yoy" in key:
                            data["revenue_growth_yoy"] = value
                        elif "next earnings" in key:
                            data["next_earnings_date"] = value
                        elif "analyst rating" in key:
                            data["analyst_rating"] = value
                        elif "target price" in key:
                            data["target_price"] = value

            return data if data else None

        except Exception as exc:
            logger.debug("Finviz fetch failed for %s: %s", ticker, exc)
            return None

    def _fetch_tradingview(self, ticker: str) -> Optional[Dict[str, Any]]:
        '''Fetch data from TradingView.'''
        try:
            url = f"https://www.tradingview.com/symbols/{ticker}/financials-earnings/"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            data: Dict[str, Any] = {}

            surprise_rows = soup.find_all("div", class_=re.compile(r"surprise|earnings", re.IGNORECASE))
            if surprise_rows:
                surprises: List[Dict[str, str]] = []
                for row in surprise_rows[:5]:
                    text = row.get_text(strip=True)
                    if text:
                        surprises.append({"data": text})
                data["earnings_surprises"] = surprises

            rating_divs = soup.find_all("div", class_=re.compile(r"rating|recommendation", re.IGNORECASE))
            if rating_divs:
                data["analyst_ratings"] = [d.get_text(strip=True) for d in rating_divs[:3]]

            return data if data else None

        except Exception as exc:
            logger.debug("TradingView fetch failed for %s: %s", ticker, exc)
            return None

    def _build_summary(self, sources: Dict[str, Dict]) -> str:
        '''Build a concise Chinese summary from all sources.'''
        parts: List[str] = []

        if "earnings_whispers" in sources:
            ew = sources["earnings_whispers"]
            if "next_earnings_date" in ew:
                parts.append("\u8ca1\u5831\u65e5\u671f: " + ew["next_earnings_date"])
            if "whisper_number" in ew:
                parts.append("\u5e02\u5831\u9810\u671fEPS: " + ew["whisper_number"])
            if "recent_surprises" in ew and ew["recent_surprises"]:
                latest = ew["recent_surprises"][0]
                if "surprise_pct" in latest:
                    parts.append("\u6700\u65b0EPS\u8da5\u9810\u671f: " + latest["surprise_pct"])

        if "finviz" in sources:
            fv = sources["finviz"]
            if "pe_ratio" in fv:
                parts.append("P/E: " + fv["pe_ratio"])
            if "eps_growth_qoq" in fv:
                parts.append("EPS\u5b63\u6210\u9577: " + fv["eps_growth_qoq"])
            if "revenue_growth_yoy" in fv:
                parts.append("\u55ae\u865f\u589e\u9577: " + fv["revenue_growth_yoy"])
            if "target_price" in fv:
                parts.append("\u5206\u6790\u5e2b\u76ee\u6a19\u50f9: " + fv["target_price"])
            if "analyst_rating" in fv:
                parts.append("\u5206\u6790\u5e2b\u8a55\u7d1a: " + fv["analyst_rating"])

        if "tradingview" in sources:
            tv = sources["tradingview"]
            if "earnings_surprises" in tv and tv["earnings_surprises"]:
                parts.append("\u8ca1\u5831\u60CA\u559c: " + tv["earnings_surprises"][0].get("data", ""))

        return " | ".join(parts[:4])