# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")

lines = []
ac = 100.0
total_pnl = 10.0
pnl_pct = 1.0
cp = 105.0

lines.append(
    f"   {chr(0x1F4B0)} \u5747\u50f9\uff1a${ac:.2f} | "
    f"\u640d\u76ca\uff1a${total_pnl:+,.2f} ({pnl_pct:+.1f}%)")
lines.append(f"   {chr(0x1F4C2)} \u7576\u524d\u50f9\uff1a${cp:.2f}")
buy_zone_str = "95.00, 98.00"
sell_zone_str = "110.00, 115.00"
lines.append(f"   {chr(0x2B06)} \u8cb7\u9032\u5340\u9593\uff1a{buy_zone_str}")
lines.append(f"   {chr(0x2B07)} \u8ce3\u51fa\u5340\u9593\uff1a{sell_zone_str}")

print("\n".join(lines))
