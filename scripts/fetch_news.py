"""fetch_news.py — Cache Futu news per ticker to data/news/<TICKER>.json.

Called from build_dashboard.py at build time. For each HK ticker that has an
actionable signal (BUY/SELL/WAIT with plan), fetch the latest 5 news items from
Futu's ai-news-search API. Cached for 6 hours — re-fetched on next build if
stale.

Output format per ticker:
  data/news/0700_HK.json
  {
    "fetched_at": "2026-08-30T01:00:00",
    "items": [
      {"title": "...", "publish_time": "2026-08-29 10:30:00", "url": "..."},
      ...
    ]
  }

Rate limit: 200ms between requests.
"""
from __future__ import annotations
import json
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

REPO = Path("/Users/kenken/dev/dsa-hk")
NEWS_DIR = REPO / "data" / "news"
NEWS_DIR.mkdir(parents=True, exist_ok=True)
STALE_HOURS = 6

API_BASE = "https://ai-news-search.futunn.com"
ENDPOINT = f"{API_BASE}/news_search"

# 2026-08-30: cache TTL
CACHE_TTL = timedelta(hours=STALE_HOURS)


def _is_fresh(p: Path) -> bool:
    if not p.exists():
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(d.get("fetched_at", "2000-01-01T00:00:00"))
        return datetime.now() - ts < CACHE_TTL
    except Exception:
        return False


def fetch_one(keyword: str, ticker: str, lang: str = "zh-HK", size: int = 5) -> dict | None:
    """Call futu-news-search API. Returns dict {fetched_at, items} or None on failure."""
    cache_path = NEWS_DIR / f"{ticker.replace('.', '_')}.json"
    if _is_fresh(cache_path):
        return json.loads(cache_path.read_text(encoding="utf-8"))

    params = {
        "keyword": keyword,
        "size": str(size),
        "news_type": "1",  # news only
        "lang": lang,
        "sort_type": "2",  # by time
    }
    url = f"{ENDPOINT}?{urlencode(params)}"
    try:
        result = subprocess.run(
            ["curl", "-sG", "--max-time", "10", "-A", "leeks-terminal/1.0 (Build)",
             url],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            print(f"  curl error for {ticker}: {result.stderr[:100]}")
            return None
        body = result.stdout.strip()
        if not body:
            return None
        d = json.loads(body)
        if d.get("code") != 0:
            print(f"  API error for {ticker}: {d.get('message', '?')}")
            return None
        items_raw = d.get("data") or []
        if isinstance(items_raw, dict):
            items_raw = items_raw.get("list") or items_raw.get("items") or []
        items = []
        for it in items_raw[:size]:
            items.append({
                "title": it.get("title", "").strip(),
                "publish_time": _fmt_time(it.get("publish_time")),
                "url": it.get("url", "").strip(),
            })
        out = {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "items": items,
        }
        cache_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
    except Exception as e:
        print(f"  Exception for {ticker}: {e}")
        return None


def _fmt_time(ts) -> str:
    """Convert API timestamp to YYYY-MM-DD HH:MM (HKT) — Futu returns seconds."""
    if not ts:
        return ""
    try:
        n = int(ts)
        if n > 1e12:  # ms
            n //= 1000
        dt = datetime.fromtimestamp(n)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)[:16]


def fetch_for_tickers(tickers: list[dict], delay: float = 0.25) -> dict[str, dict]:
    """Fetch news for a list of {ticker, keyword, lang} dicts. Returns {ticker: data}.

    Sleeps `delay` seconds between requests to respect rate limits.
    Skips cached entries within CACHE_TTL.
    """
    results = {}
    for i, t in enumerate(tickers):
        ticker = t["ticker"]
        keyword = t.get("keyword", ticker)
        lang = t.get("lang", "zh-HK")
        cache_path = NEWS_DIR / f"{ticker.replace('.', '_')}.json"
        if _is_fresh(cache_path):
            results[ticker] = json.loads(cache_path.read_text(encoding="utf-8"))
            print(f"  [{i+1}/{len(tickers)}] {ticker}: cached")
        else:
            print(f"  [{i+1}/{len(tickers)}] {ticker}: fetching '{keyword}'...")
            d = fetch_one(keyword, ticker, lang=lang)
            if d is not None:
                results[ticker] = d
            time.sleep(delay)
    return results


def load_cached(ticker: str) -> dict | None:
    """Read cached news for a ticker (used at build time)."""
    p = NEWS_DIR / f"{ticker.replace('.', '_')}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    # Test with a few tickers
    test = [
        {"ticker": "00700.HK", "keyword": "Tencent", "lang": "zh-HK"},
        {"ticker": "09988.HK", "keyword": "Alibaba", "lang": "zh-HK"},
        {"ticker": "02359.HK", "keyword": "WuXi AppTec", "lang": "zh-HK"},
    ]
    res = fetch_for_tickers(test)
    for t, d in res.items():
        print(f"\n=== {t} ===")
        for it in d.get("items", [])[:3]:
            print(f"  - {it.get('title')[:80]}")
            print(f"    {it.get('publish_time')} | {it.get('url', '')[:60]}")
