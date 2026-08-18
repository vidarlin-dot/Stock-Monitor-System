# -*- coding: utf-8 -*-
"""Fetch QFII analyst ratings from cnyes.com.

Scrapes all pages of https://www.cnyes.com/archive/twstock/board/ratediff.aspx?gt=qfii&gp=rate
and returns a dict of {ticker: rating_info}.
"""

import json
import logging
import os
import re
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.cnyes.com/archive/twstock/board/ratediff.aspx?gt=qfii&gp=rate"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def fetch_all_qfii_ratings() -> dict:
    """Fetch all QFII ratings from all pages (1-11)."""
    all_ratings: dict = {}
    for page in range(1, 12):
        url = BASE_URL + ("&pg=" + str(page) if page > 1 else "")
        try:
            r = requests.get(url, timeout=20, headers=HEADERS)
            r.raise_for_status()
        except Exception as e:
            logger.warning("Failed to fetch page %d: %s", page, e)
            continue
        # Extract rows: <TR><TD>...</TD>...</TR>
        rows = re.findall(r"<TR[^>]*>(.*?)</TR>", r.text, re.DOTALL)
        count = 0
        for row in rows[1:]:  # skip header row
            cells = re.findall(r"<TD[^>]*>(.*?)</TD>", row, re.DOTALL)
            if len(cells) < 9:
                continue
            try:
                date = re.sub(r"<[^>]+>", "", cells[0]).strip()
                ticker_raw = re.sub(r"<[^>]+>", "", cells[1]).strip()
                m = re.search(r"(\d+)", ticker_raw)
                if not m:
                    continue
                ticker = m.group(1)
                name = ticker_raw.replace(ticker, "").replace("-", "").strip()
                broker = re.sub(r"<[^>]+>", "", cells[2]).strip()
                new_rat = re.sub(r"<[^>]+>", "", cells[5]).strip()
                new_tgt = re.sub(r"<[^>]+>", "", cells[7]).strip()
                curr = re.sub(r"<[^>]+>", "", cells[8]).strip()
                if ticker not in all_ratings or date > all_ratings[ticker].get("date", ""):
                    all_ratings[ticker] = {
                        "date": date,
                        "ticker": ticker,
                        "name": name,
                        "broker": broker,
                        "orig_rating": re.sub(r"<[^>]+>", "", cells[3]).strip(),
                        "change": re.sub(r"<[^>]+>", "", cells[4]).strip(),
                        "new_rating": new_rat,
                        "old_target": re.sub(r"<[^>]+>", "", cells[6]).strip(),
                        "new_target": new_tgt,
                        "current_price": curr,
                    }
                count += 1
            except Exception as e:
                logger.debug("Failed to parse row: %s", e)
        logger.info("Page %d: %d rows", page, count)
    return all_ratings


def load_cnyes_ratings() -> dict:
    """Load QFII ratings - scrape fresh data each time."""
    ratings = fetch_all_qfii_ratings()
    # Merge with manual data for tickers not in web scrape
    manual_qfii = {
        "3443": {"date": "20260708", "ticker": "3443", "name": "創意", "broker": "Factset", "orig_rating": "", "change": "", "new_rating": "", "old_target": "5699", "new_target": "5535", "current_price": ""},
        "2330": {"date": "20260707", "ticker": "2330", "name": "台積電", "broker": "Factset", "orig_rating": "", "change": "", "new_rating": "強力買進", "old_target": "2800", "new_target": "2888", "current_price": "2380"},
        "2327": {"date": "20260702", "ticker": "2327", "name": "國巨", "broker": "Factset", "orig_rating": "", "change": "買進", "new_rating": "買進", "old_target": "975", "new_target": "1040", "current_price": "576"},
        "2454": {"date": "20260703", "ticker": "2454", "name": "聯發科", "broker": "Factset", "orig_rating": "", "change": "", "new_rating": "", "old_target": "", "new_target": "5350", "current_price": "3885"},
        "1303": {"date": "20260703", "ticker": "1303", "name": "南亞", "broker": "Factset", "orig_rating": "", "change": "觀望", "new_rating": "觀望", "old_target": "112", "new_target": "156", "current_price": "200"},
        "2408": {"date": "20260707", "ticker": "2408", "name": "南亞科", "broker": "Factset", "orig_rating": "", "change": "買進", "new_rating": "買進", "old_target": "520", "new_target": "548", "current_price": "515"},
        "2059": {"date": "20260707", "ticker": "2059", "name": "川湖", "broker": "Factset", "orig_rating": "", "change": "", "new_rating": "", "old_target": "7115", "new_target": "14370", "current_price": ""},
        "3189": {"date": "20260701", "ticker": "3189", "name": "景碩", "broker": "Factset", "orig_rating": "", "change": "強力買進", "new_rating": "強力買進", "old_target": "650", "new_target": "680", "current_price": "865"},
        "9910": {"date": "20260702", "ticker": "9910", "name": "豐泰", "broker": "Factset", "orig_rating": "", "change": "", "new_rating": "", "old_target": "", "new_target": "80.2", "current_price": "69.1"},
        "2881": {"date": "20260702", "ticker": "2881", "name": "富邦金", "broker": "Factset", "orig_rating": "", "change": "", "new_rating": "", "old_target": "107.5", "new_target": "129.5", "current_price": ""},
        "2882": {"date": "20260702", "ticker": "2882", "name": "國泰金", "broker": "Factset", "orig_rating": "", "change": "超越市場", "new_rating": "超越市場", "old_target": "97.2", "new_target": "105", "current_price": "99.2"},
        "3105": {"date": "20260708", "ticker": "3105", "name": "穩懋", "broker": "Factset", "orig_rating": "", "change": "觀望", "new_rating": "觀望", "old_target": "590", "new_target": "562.5", "current_price": "374.5"},
        "1795": {"date": "20260708", "ticker": "1795", "name": "美時", "broker": "Factset", "orig_rating": "", "change": "中立", "new_rating": "中立", "old_target": "225", "new_target": "212.5", "current_price": "184"},
        "3034": {"date": "20260708", "ticker": "3034", "name": "聯詠", "broker": "Factset", "orig_rating": "", "change": "", "new_rating": "", "old_target": "475", "new_target": "505", "current_price": ""},
        "2383": {"date": "20260707", "ticker": "2383", "name": "台光電", "broker": "Factset", "orig_rating": "", "change": "", "new_rating": "", "old_target": "6600", "new_target": "6205", "current_price": ""},
        "8210": {"date": "20260707", "ticker": "8210", "name": "勤誠", "broker": "Factset", "orig_rating": "", "change": "", "new_rating": "", "old_target": "1790", "new_target": "1025", "current_price": ""},
        "1476": {"date": "20260707", "ticker": "1476", "name": "儒鴻", "broker": "Factset", "orig_rating": "", "change": "", "new_rating": "", "old_target": "455", "new_target": "310.5", "current_price": ""},
        "6488": {"date": "20260713", "ticker": "6488", "name": "", "broker": "Factset", "orig_rating": "", "change": "", "new_rating": "符合市場", "old_target": "750", "new_target": "775", "current_price": "1060"},
        "6446": {"date": "20260703", "ticker": "6446", "name": "", "broker": "Factset", "orig_rating": "", "change": "無", "new_rating": "無", "old_target": "", "new_target": "1125", "current_price": "1400"},
        "2610": {"date": "20260703", "ticker": "2610", "name": "華航", "broker": "Factset", "orig_rating": "", "change": "無", "new_rating": "無", "old_target": "", "new_target": "21", "current_price": "20.1"},
    }
    for ticker, data in manual_qfii.items():
        if ticker not in ratings:
            ratings[ticker] = data
    logger.info("Loaded %d QFII ratings (auto: %d, manual: %d)", len(ratings), len(ratings) - len(manual_qfii), len(manual_qfii))
    return ratings
