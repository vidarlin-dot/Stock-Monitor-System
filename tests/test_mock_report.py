# -*- coding: utf-8 -*-
"""Test mock data for Stock Monitor System."""

import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def get_mock_holdings() -> list:
    """Return mock holdings data for testing."""
    return [
        {
            "ticker": "AAPL",
            "shares": 100,
            "avgcost": 150.00,
            "buyzone": "140.00,133.00",
            "sellzone": "195.00,190.00",
            "catalystdate": "2026-09-15",
            "notes": "Q3 earnings expected",
            "analyst_comment": "Buy (12 analysts) | bullish",
        },
        {
            "ticker": "NVDA",
            "shares": 50,
            "avgcost": 800.00,
            "buyzone": "750.00,712.50",
            "sellzone": "1200.00,1150.00",
            "catalystdate": "2026-10-01",
            "notes": "AI chip demand strong",
            "analyst_comment": "Strong Buy (15 analysts) | record revenue",
        },
        {
            "ticker": "TSLA",
            "shares": 30,
            "avgcost": 250.00,
            "buyzone": "200.00,190.00",
            "sellzone": "350.00,340.00",
            "catalystdate": "2026-08-20",
            "notes": "Cybertruck production ramp",
            "analyst_comment": "Hold (8 analysts) | delivery concern",
        },
    ]


def print_report_preview():
    """Print a preview of what the report would look like."""
    holdings = get_mock_holdings()
    
    print("=" * 60)
    print("[TEST] US Stock Investment Strategy Daily Report | 2026-08-08 (Sat)")
    print("=" * 60)
    print()
    print("[INFO] Note: Weekend market closed, below is Friday (08-07) closing reference")
    print()
    
    for h in holdings:
        ticker = h["ticker"]
        buy_zone = h["buyzone"]
        sell_zone = h["sellzone"]
        analyst = h["analyst_comment"]
        catalyst = h.get("catalystdate", "")
        notes = h.get("notes", "")
        
        print(f"{'='*20} {ticker} {'='*20}")
        print(f"BUY Zone: ${buy_zone}")
        print(f"SELL Zone: ${sell_zone}")
        print(f"Analyst: {analyst}")
        if catalyst:
            print(f"Next Catalyst: {catalyst}")
        if notes:
            print(f"Notes: {notes}")
        print()
    
    print("=" * 60)
    print("[OK] Test report generated successfully!")
    print("=" * 60)


if __name__ == "__main__":
    print_report_preview()
