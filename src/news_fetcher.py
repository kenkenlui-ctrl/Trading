"""News fetcher for HK stocks. Tries: Tavily → Bocha → Brave → DuckDuckGo (no-key fallback)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import unquote

import httpx
import threading

from .config import get_config

logger = logging.getLogger(__name__)

# Semaphore to limit concurrent Tavily API calls (Tavily free plan: ~1000/month)
_tavily_sem = threading.Semaphore(3)  # conservative to avoid 432 rate limit


def _extract_keywords(code: str, name_zh: Optional[str], name_en: Optional[str]) -> list[str]:
    """Build query keywords from ticker."""
    keywords = []
    if name_zh:
        keywords.append(name_zh)
    if name_en and name_en != name_zh:
        keywords.append(name_en)
    keywords.append(code.split(".")[0])  # Just the number
    keywords.append("港股")
    return keywords


def _search_tavily(query: str, max_results: int = 5, days: int = 7) -> list[dict]:
    cfg = get_config()
    if not cfg.tavily_api_key:
        return []
    # Rate limit: max 3 concurrent Tavily calls, fail fast if can't acquire
    acquired = _tavily_sem.acquire(timeout=5)
    if not acquired:
        logger.warning("Tavily rate limit: semaphore busy, skipping")
        return []
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": cfg.tavily_api_key,
                    "query": query,
                    "max_results": max_results,
                    "days": days,
                    "include_answer": False,
                    "topic": "news",
                },
            )
            r.raise_for_status()
            data = r.json()
            results = []
            for item in data.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", "")[:500],
                    "published": item.get("published_date", ""),
                    "source": "tavily",
                })
            return results
    except Exception as e:
        logger.warning(f"Tavily failed: {e}")
        return []
    finally:
        _tavily_sem.release()


def _search_bocha(query: str, max_results: int = 5, days: int = 7) -> list[dict]:
    cfg = get_config()
    if not cfg.bocha_api_key:
        return []
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(
                "https://api.bochaai.com/v1/web-search",
                json={
                    "apiKey": cfg.bocha_api_key,
                    "query": query,
                    "summary": True,
                    "count": max_results,
                    "freshness": "oneWeek" if days <= 7 else "oneMonth",
                },
                headers={"Authorization": f"Bearer {cfg.bocha_api_key}"},
            )
            r.raise_for_status()
            data = r.json()
            results = []
            for item in data.get("data", {}).get("webPages", {}).get("value", []):
                results.append({
                    "title": item.get("name", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("snippet", "")[:500],
                    "published": item.get("datePublished", ""),
                    "source": "bocha",
                })
            return results
    except Exception as e:
        logger.warning(f"Bocha failed: {e}")
        return []


def _search_brave(query: str, max_results: int = 5, days: int = 7) -> list[dict]:
    cfg = get_config()
    if not cfg.brave_api_key:
        return []
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={
                    "q": query,
                    "count": max_results,
                    "freshness": "pw" if days <= 7 else "pm",
                },
                headers={
                    "X-Subscription-Token": cfg.brave_api_key,
                    "Accept": "application/json",
                },
            )
            r.raise_for_status()
            data = r.json()
            results = []
            for item in data.get("web", {}).get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("description", "")[:500],
                    "published": item.get("age", ""),
                    "source": "brave",
                })
            return results
    except Exception as e:
        logger.warning(f"Brave failed: {e}")
        return []


def _search_duckduckgo(query: str, max_results: int = 5, days: int = 7) -> list[dict]:
    """DuckDuckGo HTML search — no API key, no auth, free. Works as a fallback."""
    try:
        # DDG's lite HTML endpoint
        url = "https://html.duckduckgo.com/html/"
        params = {
            "q": query,
            "kl": "hk-zh",  # Hong Kong region, Chinese
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
        }
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            r = client.post(url, data=params, headers=headers)
            r.raise_for_status()
            html = r.text

        # Parse results — DDG HTML uses result__a class for titles, result__snippet for snippets
        results = []
        # Find result blocks
        for match in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        ):
            raw_url, title, snippet = match.groups()
            # DDG wraps URLs in a redirect — extract real URL
            real_url = raw_url
            uddg = re.search(r"uddg=([^&]+)", raw_url)
            if uddg:
                real_url = unquote(uddg.group(1))

            # Strip HTML tags from title/snippet
            title_clean = re.sub(r"<[^>]+>", "", title).strip()
            snippet_clean = re.sub(r"<[^>]+>", "", snippet).strip()

            if title_clean and real_url:
                results.append({
                    "title": title_clean,
                    "url": real_url,
                    "snippet": snippet_clean[:500],
                    "published": "",
                    "source": "duckduckgo",
                })
            if len(results) >= max_results:
                break

        return results
    except Exception as e:
        logger.warning(f"DuckDuckGo failed: {e}")
        return []


def fetch_news(
    code: str,
    name_zh: Optional[str] = None,
    name_en: Optional[str] = None,
    max_results: int = 5,
    days: int = 7,
) -> list[dict]:
    """
    Fetch recent news for a HK ticker. Tries: Tavily → Bocha → Brave → DuckDuckGo.
    DuckDuckGo is the no-key fallback that always works.
    Returns list of {title, url, snippet, published, source}.
    """
    cfg = get_config()

    keywords = _extract_keywords(code, name_zh, name_en)
    # Build a focused bilingual query
    primary_name = name_zh or name_en or code
    query = f"{primary_name} {code} 港股 最新"
    en_query = f"{name_en or code} HK stock news" if name_en else query

    # Try sources in order of preference
    sources_priority = []
    if cfg.bocha_api_key:
        sources_priority.append(("bocha", _search_bocha, query))
    if cfg.tavily_api_key:
        sources_priority.append(("tavily", _search_tavily, en_query))
    if cfg.brave_api_key:
        sources_priority.append(("brave", _search_brave, en_query))
    # DuckDuckGo is the always-on fallback (no key required)
    sources_priority.append(("duckduckgo", _search_duckduckgo, query))

    all_results = []
    for name, fn, q in sources_priority:
        try:
            results = fn(q, max_results=max_results, days=days)
            if results:
                logger.info(f"Got {len(results)} news items for {code} via {name}")
                all_results.extend(results)
                if len(all_results) >= max_results:
                    break
        except Exception as e:
            logger.warning(f"{name} threw for {code}: {e}")
            continue

    # De-dup by URL
    seen = set()
    deduped = []
    for r in all_results:
        url = r.get("url")
        if url and url not in seen:
            seen.add(url)
            deduped.append(r)

    return deduped[:max_results]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    news = fetch_news("0700.HK", "騰訊控股", "Tencent", max_results=3)
    print(f"Got {len(news)} news items for 0700.HK")
    for n in news:
        print(f"  - [{n['source']}] {n['title'][:80]}")
        print(f"    {n['url']}")
