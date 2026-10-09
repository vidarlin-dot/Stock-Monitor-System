# -*- coding: utf-8 -*-
"""
Analyst target-price scraper via Exa MCP search.

Purpose:
    Complement the cnyes QFII feed with real-time brokerage target-price
    announcements mined from financial news (SETN / UDN / FNN / 經濟日報 / FTNN /
    鉅亨網 etc.) so the daily monitor can auto-promote any ticker whose
    consensus target price shifted today.

Strategy:
    1. Query `exa.web_search_exa` for "{name} ({code}) 目標價 外資 調升"
       (and an English variant for US tickers).
    2. Extract every target-price mention from the top N articles:
         - target number (int, may contain commas/Chinese commas/unicode digits)
         - broker name (瑞銀 / 野村 / 摩根 / 高盛 / UBS / Nomura / ...)
         - publication date
    3. Merge with cnyes data (cnyes wins on conflict because it is structured
       data; exa is free-text and is a fallback / supplemental source).
    4. Persist latest snapshot to `src/data/analyst_targets.json` so the next
       run can diff against it and auto-promote changed tickers.

mcporter CLI usage (installed on host, no API key):
    mcporter call "exa.web_search_exa" query:"..." numResults:5 objective:"..."
Output is plain text lines; we parse Target/URL/Published/Highlights blocks.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# mcporter / exa plumbing
# ---------------------------------------------------------------------------

MCPORTER_BIN = "mcporter"
EXA_TOOL = "exa.web_search_exa"


def _run_exa_search(query: str, num_results: int = 5,
                    objective: str = "", timeout: int = 45) -> Optional[str]:
    """Invoke `mcporter call exa.web_search_exa` and return raw stdout.

    Returns None if mcporter is unavailable or the call fails.
    """
    # Try `mcporter call` first (preferred, no node_modules required).
    cmd = [
        MCPORTER_BIN, "call", EXA_TOOL,
        f"query:{query}",
        f"numResults:{num_results}",
    ]
    if objective:
        cmd.append(f"objective:{objective}")
    # Windows: mcporter is a .cmd shim in npm; shell=True handles it.
    is_windows = os.name == "nt"
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8",
            shell=is_windows,
        )
        if res.returncode != 0:
            logger.warning("mcporter call failed rc=%s stderr=%s",
                           res.returncode, (res.stderr or "")[:400])
            return None
        return res.stdout
    except FileNotFoundError:
        logger.warning("mcporter binary not found; skipping exa search")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("mcporter call timed out after %ds", timeout)
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("mcporter call error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Map common unicode/Chinese fullwidth digits to ASCII.
def _to_ascii_digits(s: str) -> str:
    conv = {"０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
            "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
            "，": ",", "。": ".", "．": "."}
    for k, v in conv.items():
        s = s.replace(k, v)
    return s


# Broker / 券商 synonyms we look for in a highlight line.
_BROKER_PATTERNS = {
    "瑞銀": "瑞銀", "UBS": "瑞銀(UBS)",
    "野村": "野村", "Nomura": "野村(Nomura)",
    "摩根": "摩根", "Morgan": "摩根(Morgan)",
    "高盛": "高盛", "Goldman": "高盛(Goldman)",
    "美系": "美系", "美系券商": "美系", "US broker": "美系",
    "美銀": "美銀", "BofA": "美銀(BofA)",
    "法興": "法興", "SocGen": "法興(SocGen)",
    "日商": "日商", "日本券商": "日商",
    "德商": "德商", "德意志": "德商",
    "英商": "英商",
    "台系": "台系", "本土": "本土", "台券商": "台系", "華南": "華南",
    "凱基": "凱基", "群益": "群益", "元大": "元大", "富邦": "富邦",
    "國泰": "國泰", "安碩": "安碩", "元大期貨": "元大",
}


def _detect_broker(text: str) -> str:
    for pat, label in _BROKER_PATTERNS.items():
        if pat in text:
            return label
    return ""


# Regex for "目標價 XXX 元" / "目標價調升至 XXXX 元" / "目標價至 X,XXX 元"
# We deliberately allow digits with commas and also a decimal (e.g., 5350.5).
_TARGET_RE = re.compile(
    r"目標價\s*(?:調升|調高|上調|調降|調降|下調|調整|至|到)?\s*"
    r"([0-9][0-9,\.]{0,12})\s*元"
)

# Also catch "目標價 ... 到 6,670 元" (space variant)
_TARGET_LOOSE_RE = re.compile(
    r"目標價[^\d]{0,15}([0-9][0-9,\.]{0,12})\s*元"
)


def _clean_target(raw: str) -> float:
    raw = _to_ascii_digits(raw).replace(",", "")
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.0


def _extract_from_articles(raw: str) -> List[Dict[str, Any]]:
    """Parse mcporter's plain-text output into a list of article records.

    Each record: {url, published, target, broker, title, raw_snippet}.
    """
    # Output from exa via mcporter is grouped blocks separated by "\n---\n".
    # Within each block, first line is "Title: ...", second is "URL: ...",
    # third is "Published: ...", fourth is "Author: ...", rest is
    # "Highlights:\n<lines>".
    articles: List[Dict[str, Any]] = []
    if not raw:
        return articles

    blocks = re.split(r"\n-{3,}\n", raw.strip())
    for block in blocks:
        if not block.strip():
            continue
        rec: Dict[str, Any] = {"url": "", "published": "", "title": "",
                               "target": 0.0, "broker": "", "snippet": ""}
        lines = [ln.rstrip() for ln in block.splitlines()]
        # Parse header lines.
        for ln in lines:
            m = re.match(r"^\s*Title:\s*(.*)$", ln)
            if m:
                rec["title"] = m.group(1).strip()
                continue
            m = re.match(r"^\s*URL:\s*(\S+)", ln)
            if m:
                rec["url"] = m.group(1).strip()
                continue
            m = re.match(r"^\s*Published:\s*(\S+)", ln)
            if m:
                rec["published"] = m.group(1).strip()
                continue
        # Find the Highlights section text.
        hl_start = block.find("Highlights:")
        snippet = block[hl_start:] if hl_start >= 0 else block
        rec["snippet"] = snippet[:3000]
        # Scan snippet lines for 目標價 mentions; broker + target + url per-line.
        target = 0.0
        broker = ""
        best_line_url = ""
        for line in snippet.splitlines():
            # The "highlights" text sometimes embeds URL in its own line;
            # also try to catch "Target Price" / "Price Target" lines (en).
            if ("目標價" in line or "Target Price" in line.upper()
                    or "Price Target" in line.upper()):
                m = (_TARGET_RE.search(line)
                     or _TARGET_LOOSE_RE.search(line)
                     or re.search(r"([0-9][0-9,\.]{0,12})\s*(?:USD|usd|元|NTD|TWD)", line))
                if m:
                    t = _clean_target(m.group(1))
                    if t > target:
                        target = t
                    broker = _detect_broker(line) or broker
        if target == 0:
            # Fallback: scan whole block for first target.
            m = _TARGET_RE.search(rec.get("snippet", ""))
            if m:
                target = _clean_target(m.group(1))
        rec["target"] = target
        rec["broker"] = broker
        if target > 0:
            articles.append(rec)
    return articles


def _parse_date(raw: str) -> str:
    """Return YYYYMMDD string; empty string if unparseable."""
    if not raw:
        return ""
    raw = raw.strip()
    # exa returns ISO-ish strings: 2026-09-18T00:00:00.000Z
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    # Some sites expose "2026-09-18"
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", raw)
    if m:
        return f"{m.group(1)}{m.group(2).zfill(2)}{m.group(3).zfill(2)}"
    return ""


# ---------------------------------------------------------------------------
# Query builders
# ---------------------------------------------------------------------------

def _tw_query(ticker: str, name: str) -> str:
    """Natural-language search for a Taiwan stock's latest target-price news."""
    return (f"{name} ({ticker}) 目標價 外資 調升 財測 法人 "
            f"券商 最新 2026 台股")


def _us_query(ticker: str, name: str) -> str:
    return (f"{name} {ticker} analyst target price upgrade downgrade "
            f"brokerage rating Wall Street latest 2026")


def _tw_objective(ticker: str) -> str:
    return (f"Find the most recent brokerage target-price announcement for "
            f"Taiwan stock {ticker}. Return 1-2 target price figures per "
            f"article, the broker name (瑞銀/野村/摩根/高盛/美系/本土), and "
            f"publication date. Exclude older-than-30-day items and exclude "
            f"price targets that are clearly historical (i.e., "
            f'"目標價 ... 元" referring to prior-year forecasts).')


def _us_objective(ticker: str) -> str:
    return (f"Find the most recent analyst target-price changes for US stock "
            f"{ticker}. Return broker name, old and new target price, and "
            f"publication date. Exclude pre-2024 items.")


# ---------------------------------------------------------------------------
# Snapshot persistence
# ---------------------------------------------------------------------------

def _snapshot_path() -> str:
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "data")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "analyst_targets.json")


def load_prev_snapshot() -> Dict[str, Dict[str, Any]]:
    p = _snapshot_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to load prev snapshot: %s", exc)
        return {}


def save_snapshot(targets: Dict[str, Dict[str, Any]]) -> None:
    p = _snapshot_path()
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(targets, f, ensure_ascii=False, indent=2)
        logger.info("Saved analyst target snapshot: %d entries -> %s",
                    len(targets), p)
    except Exception as exc:
        logger.warning("Failed to save snapshot: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_targets_for_watchlist(
    watchlist: List[Dict[str, Any]],
    kind: str = "tw",
    limit: int = 20,
) -> Dict[str, Dict[str, Any]]:
    """Fetch latest analyst targets for a watchlist via exa.

    Args:
        watchlist: list of dicts with keys 'ticker' (may include name)
                   and optionally '公司名稱'/'name'.
        kind: 'tw' for Taiwan stocks, 'us' for US.
        limit: max tickers to query in one run (protects API budget).

    Returns:
        {ticker: {target, broker, date, url, source, confidence}}
    """
    results: Dict[str, Dict[str, Any]] = {}
    q_fn = _tw_query if kind == "tw" else _us_query
    o_fn = _tw_objective if kind == "tw" else _us_objective

    for h in watchlist:
        ticker = str(h.get("ticker", h.get("代碼", ""))).strip()
        m = re.match(r"(\d+|[A-Za-z.\-]+)", ticker)
        if not m:
            continue
        ticker = m.group(1)
        name = (str(h.get("公司名稱", h.get("name", h.get("短名", "")))).strip()
                or ticker)
        if ticker in results:
            continue
        if len(results) >= limit:
            logger.info("Reached exa query limit (%d)", limit)
            break

        raw = _run_exa_search(
            query=q_fn(ticker, name),
            num_results=4,
            objective=o_fn(ticker),
        )
        if raw is None:
            continue
        articles = _extract_from_articles(raw)
        if not articles:
            continue
        # Keep only fresh items (last 30 days) when the date is parseable.
        today = datetime.now().strftime("%Y%m%d")
        cutoff = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                  - timedelta(days=30)).strftime("%Y%m%d")
        dated = [a for a in articles
                 if a.get("published") and _parse_date(a["published"]) >= cutoff]
        undated = [a for a in articles if not a.get("published")]
        pool = dated + undated
        pool.sort(key=lambda a: a.get("published", ""), reverse=True)
        if not pool:
            continue
        top = pool[0]
        published = _parse_date(top.get("published", ""))
        if not published:
            published = today
        conf = "high" if (top.get("broker") and published) else (
            "medium" if published else "low")
        results[ticker] = {
            "target": top["target"],
            "broker": top.get("broker", ""),
            "date": published,
            "url": top.get("url", ""),
            "source": "exa",
            "confidence": conf,
            "all": [
                {
                    "target": a["target"],
                    "broker": a.get("broker", ""),
                    "date": _parse_date(a.get("published", "")),
                    "url": a.get("url", ""),
                    "snippet": (a.get("snippet") or "")[:300],
                }
                for a in pool[:4]
            ],
        }
        logger.info(
            "Exa target: %s %s -> %.0f (%s @ %s)",
            ticker, name, results[ticker]["target"],
            results[ticker]["broker"], results[ticker]["date"],
        )
    return results


# ---------------------------------------------------------------------------
# Diff / promotion helper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Analyst coverage count tracking
# ---------------------------------------------------------------------------

def _analyst_snapshot_path() -> str:
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "data")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "analyst_counts.json")


def load_analyst_snapshot() -> Dict[str, int]:
    """Return {ticker: analyst_count} from the previous run's snapshot."""
    p = _analyst_snapshot_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return {k: int(v) for k, v in data.items()
                if isinstance(v, (int, float))}
    except Exception:
        return {}


def save_analyst_snapshot(counts: Dict[str, int]) -> None:
    p = _analyst_snapshot_path()
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(counts, f, ensure_ascii=False, indent=2)
        logger.info("Saved analyst count snapshot: %d entries", len(counts))
    except Exception as exc:
        logger.warning("Failed to save analyst count snapshot: %s", exc)


def diff_analyst_counts(prev: Dict[str, int],
                       curr: Dict[str, int],
                       min_analysts: int = 3) -> Dict[str, Dict[str, Any]]:
    """Return tickers where the number of tracking analysts increased.

    A ticker qualifies when:
      - prev count < curr count
      - curr count >= min_analysts   (avoid noise on 1-2 analyst tickers)

    Returns {ticker: {"prev": int, "curr": int, "delta": int}}
    """
    added: Dict[str, Dict[str, Any]] = {}
    for ticker, n_curr in curr.items():
        n_prev = prev.get(ticker, 0)
        if n_curr > n_prev and n_curr >= min_analysts:
            added[ticker] = {
                "prev": n_prev,
                "curr": n_curr,
                "delta": n_curr - n_prev,
            }
    return added


def diff_targets(prev: Dict[str, Dict[str, Any]],
                 curr: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Return tickers whose target price materially changed.

    A ticker is 'changed' if:
      - new ticker with target > 0
      - target diff >= 2% (relative) OR abs diff >= 1.0
      - broker label changed AND target same but new date
    """
    changed: Dict[str, Dict[str, Any]] = {}
    all_tickers = set(prev.keys()) | set(curr.keys())
    for t in all_tickers:
        p = prev.get(t)
        c = curr.get(t)
        if c is None or c.get("target", 0) <= 0:
            continue
        if p is None:
            changed[t] = {"prev": None, "curr": c, "reason": "new"}
            continue
        p_tgt = p.get("target", 0)
        c_tgt = c.get("target", 0)
        if p_tgt <= 0:
            continue
        rel = abs(c_tgt - p_tgt) / max(p_tgt, 1) * 100
        reason = ""
        if c.get("date") and p.get("date") and c["date"] > p.get("date"):
            reason = "newer_date"
        if rel >= 2.0 or abs(c_tgt - p_tgt) >= 1.0:
            reason = reason or "target_moved"
            changed[t] = {"prev": p, "curr": c, "reason": reason}
        elif c.get("broker") and p.get("broker") and c["broker"] != p["broker"]:
            changed[t] = {"prev": p, "curr": c, "reason": "broker_changed"}
    return changed


def merge_with_cnyes(cnyes: Dict[str, Dict[str, Any]],
                     exa: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Prefer cnyes (structured) over exa (free-text) when both present.

    If cnyes has no record for ticker T but exa does, exa fills the slot.
    If both have records, we keep cnyes' target but attach exa's URL as
    `cross_ref` for auditability.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for t, c in cnyes.items():
        tgt = float(c.get("new_target") or 0)
        rec: Dict[str, Any] = {
            "target": tgt,
            "broker": c.get("broker", ""),
            "date": c.get("date", ""),
            "rating": c.get("new_rating", ""),
            "url": c.get("url", ""),
            "source": "cnyes",
            "confidence": "high",
        }
        if t in exa:
            rec["cross_ref"] = exa[t].get("url", "")
            rec["cross_ref_target"] = exa[t].get("target", 0)
        merged[t] = rec
    for t, e in exa.items():
        if t not in merged:
            rec = {
                "target": e.get("target", 0),
                "broker": e.get("broker", ""),
                "date": e.get("date", ""),
                "url": e.get("url", ""),
                "source": "exa",
                "confidence": e.get("confidence", "medium"),
                "rating": "",
            }
            merged[t] = rec
    return merged


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    # CLI usage:
    #   python src/analyst_target_scraper.py 2330 2454 6669 3017
    # (or --tw 6669 / --us NVDA to auto-detect; default is TW)
    args = sys.argv[1:]
    kind = "tw"
    if args and args[0] == "--us":
        kind = "us"
        args = args[1:]
    tickers = [a for a in args if re.match(r"^[\dA-Za-z.\-]+$", a)]
    if not tickers:
        print("usage: analyst_target_scraper.py [--us] TICKER ...")
        sys.exit(1)
    watch = [{"ticker": t, "短名": t} for t in tickers]
    fresh = fetch_targets_for_watchlist(watch, kind=kind, limit=len(tickers))
    print(json.dumps(fresh, ensure_ascii=False, indent=2))
    prev = load_prev_snapshot()
    print("\n--- diff vs prev snapshot ---")
    print(json.dumps(diff_targets(prev, fresh), ensure_ascii=False, indent=2))
