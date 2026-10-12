# -*- coding: utf-8 -*-
"""Fallback ticker->name map for TW report rendering.

Used when the Google Sheet's short-name column is blank for a ticker that
appeared in a scraped/changed list. Values verified against the 2026-10-12
correction table; extend this map only with names confirmed in reports.
"""

TW_NAME_MAP = {
    "6147": "曜越",
    "3211": "順達",
    "6669": "緯穎",
    "3017": "奇鋐",
    "3443": "創意",
    "3661": "世芯-KY",
    "5289": "宜鼎",
    "6515": "穎崴",
    "8299": "群聯",
    "3481": "群創",
    "3231": "緯創",
    "3711": "日月光投控",
    "2886": "兆豐金",
    "4979": "新網",
    "5536": "達邁",
}


def tw_display_name(ticker: str, fallback: str = "") -> str:
    """Return the display name for a TW ticker, preferring `fallback`."""
    t = str(ticker).strip()
    if fallback and fallback not in ("None", "N/A") and fallback != t:
        return fallback
    return TW_NAME_MAP.get(t, fallback or t)
