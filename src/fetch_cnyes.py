# -*- coding: utf-8 -*-
"""Fetch QFII analyst ratings from cnyes.com - scrape all 11 pages."""
import json
import logging
import os
import re
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.cnyes.com/archive/twstock/board/ratediff.aspx?gt=qfii&gp=rate"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def fetch_all_qfii_ratings() -> dict:
    """Fetch all QFII ratings from all 11 pages."""
    all_ratings: dict = {}
    session = requests.Session()
    session.headers.update(HEADERS)

    for page in range(1, 12):
        url = BASE_URL + ("&pg=" + str(page) if page > 1 else "")
        try:
            r = session.get(url, timeout=20)
            r.raise_for_status()
        except Exception as e:
            logger.warning("Failed to fetch page %d: %s", page, e)
            continue

        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.DOTALL | re.IGNORECASE)
        count = 0
        for row in rows[1:]:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if len(cells) < 9 or not cells[0] or cells[0] == "評等日期":
                continue
            try:
                date = cells[0]
                ticker_raw = cells[1]
                m = re.search(r"(\d+)", ticker_raw)
                if not m:
                    continue
                ticker = m.group(1)
                name = ticker_raw.replace(ticker, "").replace("-", "").strip()
                broker = cells[2]
                new_rat = cells[5]
                new_tgt = cells[7]
                curr = cells[8]
                if ticker not in all_ratings or date > all_ratings[ticker].get("date", ""):
                    all_ratings[ticker] = {
                        "date": date, "ticker": ticker, "name": name,
                        "broker": broker, "new_rating": new_rat,
                        "new_target": new_tgt, "current_price": curr,
                    }
                count += 1
            except Exception as e:
                logger.debug("Parse error: %s", e)
        logger.info("Page %d: %d rows", page, count)

    return all_ratings


def load_cnyes_ratings() -> dict:
    """Load QFII ratings - scrape fresh data each time."""
    ratings = fetch_all_qfii_ratings()
    logger.info("Auto-scraped %d ratings from cnyes", len(ratings))

    # Manual fallback for tickers not in web scrape
    manual_qfii = {
        "3443": {"date": "20260708", "ticker": "3443", "name": "創意", "broker": "Factset", "new_rating": "", "new_target": "5535", "current_price": ""},
        "2330": {"date": "20260707", "ticker": "2330", "name": "台積電", "broker": "Factset", "new_rating": "強力買進", "new_target": "2888", "current_price": "2380"},
        "2327": {"date": "20260702", "ticker": "2327", "name": "國巨", "broker": "Factset", "new_rating": "買進", "new_target": "1040", "current_price": "576"},
        "2454": {"date": "20260703", "ticker": "2454", "name": "聯發科", "broker": "Factset", "new_rating": "", "new_target": "5350", "current_price": "3885"},
        "1303": {"date": "20260703", "ticker": "1303", "name": "南亞", "broker": "Factset", "new_rating": "觀望", "new_target": "156", "current_price": "200"},
        "2408": {"date": "20260707", "ticker": "2408", "name": "南亞科", "broker": "Factset", "new_rating": "買進", "new_target": "548", "current_price": "515"},
        "2059": {"date": "20260707", "ticker": "2059", "name": "川湖", "broker": "Factset", "new_rating": "", "new_target": "14370", "current_price": ""},
        "3189": {"date": "20260701", "ticker": "3189", "name": "景碩", "broker": "Factset", "new_rating": "強力買進", "new_target": "680", "current_price": "865"},
        "9910": {"date": "20260702", "ticker": "9910", "name": "豐泰", "broker": "Factset", "new_rating": "", "new_target": "80.2", "current_price": "69.1"},
        "2881": {"date": "20260702", "ticker": "2881", "name": "富邦金", "broker": "Factset", "new_rating": "", "new_target": "129.5", "current_price": ""},
        "2882": {"date": "20260702", "ticker": "2882", "name": "國泰金", "broker": "Factset", "new_rating": "超越市場", "new_target": "105", "current_price": "99.2"},
        "3105": {"date": "20260708", "ticker": "3105", "name": "穩懋", "broker": "Factset", "new_rating": "觀望", "new_target": "562.5", "current_price": "374.5"},
        "1795": {"date": "20260708", "ticker": "1795", "name": "美時", "broker": "Factset", "new_rating": "中立", "new_target": "212.5", "current_price": "184"},
        "3034": {"date": "20260708", "ticker": "3034", "name": "聯詠", "broker": "Factset", "new_rating": "", "new_target": "505", "current_price": ""},
        "2383": {"date": "20260707", "ticker": "2383", "name": "台光電", "broker": "Factset", "new_rating": "", "new_target": "6205", "current_price": ""},
        "8210": {"date": "20260707", "ticker": "8210", "name": "勤誠", "broker": "Factset", "new_rating": "", "new_target": "1025", "current_price": ""},
        "1476": {"date": "20260707", "ticker": "1476", "name": "儒鴻", "broker": "Factset", "new_rating": "", "new_target": "310.5", "current_price": ""},
        "6488": {"date": "20260713", "ticker": "6488", "name": "", "broker": "Factset", "new_rating": "符合市場", "new_target": "775", "current_price": "1060"},
        "6446": {"date": "20260703", "ticker": "6446", "name": "", "broker": "Factset", "new_rating": "無", "new_target": "1125", "current_price": "1400"},
        "2610": {"date": "20260703", "ticker": "2610", "name": "華航", "broker": "Factset", "new_rating": "無", "new_target": "21", "current_price": "20.1"},
        "8299": {"date": "20260615", "ticker": "8299", "name": "群聯", "broker": "Factset", "new_rating": "", "old_target": "3000", "new_target": "2000", "current_price": ""},
    }
    for ticker, data in manual_qfii.items():
        if ticker not in ratings:
            ratings[ticker] = data
    logger.info("Total QFII ratings: %d (auto: %d, manual: %d)", len(ratings), len(ratings) - len(manual_qfii), len(manual_qfii))
    return ratings

