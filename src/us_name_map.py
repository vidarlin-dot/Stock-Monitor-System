# -*- coding: utf-8 -*-
"""Fallback ticker->name map for US report rendering.

Used when the Google Sheet company-name column is blank. Extend with names
confirmed in reports only.
"""

US_NAME_MAP = {
    "NVDA": "NVIDIA",
    "AMD": "AMD",
    "AVGO": "Broadcom",
    "MU": "Micron",
    "MRVL": "Marvell",
    "LITE": "Lumentum",
    "GLW": "Corning",
    "APP": "Applovin",
    "GOOG": "Alphabet",
    "BEAM": "Beam Therapeutics",
    "IONQ": "IonQ",
    "EWY": "iShares MSCI China",
}


def us_display_name(ticker: str, fallback: str = "") -> str:
    t = str(ticker).strip().upper()
    if fallback and fallback not in ("None", "N/A") and fallback != t:
        return fallback
    return US_NAME_MAP.get(t, fallback or t)
