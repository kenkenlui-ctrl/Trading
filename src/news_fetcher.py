"""News fetcher for HK stocks. Uses Futu cloud news API (free, no key)."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Optional

import httpx

from .config import get_config

logger = logging.getLogger(__name__)

# Semaphore + delay to respect Futu API rate limits (~10 req/sec, very strict)
_futu_sem = threading.Semaphore(3)  # max 3 concurrent
_min_interval_seconds = 0.15  # 150ms between calls
_last_call_lock = threading.Lock()
_last_call_time = [0.0]


def _throttle():
    """Block until safe to make another Futu API call."""
    with _last_call_lock:
        now = time.time()
        wait = _min_interval_seconds - (now - _last_call_time[0])
        if wait > 0:
            time.sleep(wait)
        _last_call_time[0] = time.time()


def _search_futu_news(query: str, max_results: int = 5, days: int = 7) -> list[dict]:
    """Futu cloud news API — no key, no auth, free. Best for HK stocks."""
    if not query:
        return []
    if not _futu_sem.acquire(timeout=10):
        logger.warning(f"Futu news semaphore timeout for {query}")
        return []
    try:
        _throttle()
        with httpx.Client(timeout=15) as client:
            r = client.get(
                "https://ai-news-search.futunn.com/news_search",
                params={
                    "keyword": query,
                    "size": max_results,
                    "news_type": 1,
                    "lang": "zh-HK",
                    "sort_type": 2,
                },
                headers={"User-Agent": "dsa-hk-news/1.0"},
            )
            if r.status_code == 429:
                logger.debug(f"Futu 429 for {query}, backing off")
                time.sleep(1.0)
                return []
            r.raise_for_status()
            data = r.json()
        if data.get("code") != 0:
            logger.warning(f"Futu news API error: {data.get('message')}")
            return []
        results = []
        for item in data.get("data", []) or []:
            ts = item.get("publish_time")
            published = ""
            try:
                if isinstance(ts, (int, float)):
                    if ts > 1e12:  # ms
                        ts = ts / 1000
                    published = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                elif isinstance(ts, str) and ts.isdigit():
                    ts = int(ts)
                    if ts > 1e12:
                        ts = ts / 1000
                    published = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                elif isinstance(ts, str):
                    published = ts
            except (ValueError, OSError):
                published = str(ts) if ts else ""
            # Strip HTML tags from title
            import re as _re
            title = _re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
            snippet = _re.sub(r"<[^>]+>", "", item.get("summary", "") or "").strip()[:500]
            results.append({
                "title": title,
                "url": item.get("url", ""),
                "snippet": snippet,
                "published": published,
                "source": "futu",
            })
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        logger.warning(f"Futu news failed: {e}")
        return []
    finally:
        _futu_sem.release()


def fetch_news(
    code: str,
    name_zh: Optional[str] = None,
    name_en: Optional[str] = None,
    max_results: int = 5,
    days: int = 7,
) -> list[dict]:
    """
    Fetch recent news for a HK ticker from Futu cloud news API.
    Returns list of {title, url, snippet, published, source}.
    """
    primary_name = name_zh or name_en or code
    return _search_futu_news(primary_name, max_results=max_results, days=days)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    news = fetch_news("0700.HK", "騰訊控股", "Tencent", max_results=3)
    print(f"Got {len(news)} news items for 0700.HK")
    for n in news:
        print(f"  - [{n['source']}] {n['published']} | {n['title'][:70]}")
        print(f"    {n['url']}")
