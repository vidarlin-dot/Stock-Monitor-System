"""Fetch QFII analyst ratings from cnyes.com and save to cache."""
import requests, re, json, os, logging
from datetime import datetime
import pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
TW_TZ = pytz.timezone("Asia/Taipei")

URL = "https://www.cnyes.com/twstock/board/ratediff.aspx"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache", "cnyes")
os.makedirs(CACHE_DIR, exist_ok=True)


def fetch_cnyes_ratings() -> list:
    resp = requests.get(URL, headers=HEADERS, timeout=20, verify=False)
    content = resp.content.decode("utf-8", errors="replace")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.DOTALL | re.IGNORECASE)
    ratings = []
    for row in rows[1:]:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if len(cells) >= 9 and cells[0] and cells[0] != "評等日期":
            code_name = cells[1]
            m = re.match(r"(\d+)-?(.*)", code_name.strip())
            if m:
                ticker = m.group(1)
                name = m.group(2).strip()
            else:
                ticker = code_name.strip()
                name = ""
            ratings.append({
                "date": cells[0],
                "ticker": ticker,
                "name": name,
                "broker": cells[2],
                "orig_rating": cells[3],
                "change": cells[4],
                "new_rating": cells[5],
                "old_target": cells[6],
                "new_target": cells[7],
                "current_price": cells[8],
            })
    return ratings


def main():
    logger.info("Fetching QFII ratings from cnyes.com...")
    ratings = fetch_cnyes_ratings()
    logger.info("Fetched %d ratings", len(ratings))

    # Deduplicate: keep latest per ticker
    latest = {}
    for r in ratings:
        t = r["ticker"]
        if t not in latest or r["date"] > latest[t]["date"]:
            latest[t] = r

    out_path = os.path.join(CACHE_DIR, "latest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    logger.info("Saved %d unique ticker ratings to %s", len(latest), out_path)

    # Also save full history
    hist_path = os.path.join(CACHE_DIR, "history.json")
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(ratings, f, ensure_ascii=False, indent=2)
    logger.info("Saved full history (%d rows) to %s", len(ratings), hist_path)

    # Print summary of watchlist matches
    watchlist_tickers = ["6669", "3017", "3037", "2317", "2344", "3443", "3661", "6515", "2449", "3231", "4979", "6282"]
    logger.info("Watchlist matches:")
    for t in watchlist_tickers:
        if t in latest:
            r = latest[t]
            tgt = r["new_target"]
            price = r["current_price"]
            ups = ""
            if tgt and price:
                try:
                    ups_pct = (float(tgt) - float(price)) / float(price) * 100
                    ups = f"  (+{ups_pct:.1f}%)"
                except:
                    pass
            logger.info("  %s %s: rating=%s target=%s cur=%s%s", t, r["name"], r["new_rating"], tgt, price, ups)
        else:
            logger.info("  %s: NO RATING", t)


if __name__ == "__main__":
    main()
