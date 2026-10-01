"""fetch_sentiment.py — Cache Futu community sentiment per ticker (anti-indicator use).

Wraps /stock_feed endpoint. Caches for 6 hours. Computes bull_pct/bear_pct
distribution. Used as CONTRARIAN signal — when bull_pct is extreme (≥80%),
that's a warning sign (deep-research audit found LLM-optimistic-sentiment
has only 30% hit rate on this site).

Output: data/sentiment/<TICKER>.json with {fetched_at, bull_pct, bear_pct, post_count}.
"""
from __future__ import annotations
import json
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, quote

REPO = Path("/Users/kenken/dev/dsa-hk")
SENT_DIR = REPO / "data" / "sentiment"
SENT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL = timedelta(hours=6)
API_BASE = "https://ai-news-search.futunn.com"

BULL_KEYWORDS = [
    "看多", "看好", "看涨", "升", "上", "突破", "買入", "建倉", "加倉", "長線", "持有",
    "做多", "多頭", "好", "機會", "回升", "反弹", "反彈", "業績", "增長", "看好", "上車",
    "call", "bullish", "buy", "long", "moon", "🚀", "📈", "升穿", "破位",
]
BEAR_KEYWORDS = [
    "看空", "看淡", "看跌", "跌", "下", "破", "止損", "止蝕", "斬倉", "離場", "減倉",
    "做空", "空頭", "淡", "跌穿", "套", "虧", "危", "避", "下行", "回調", "回调",
    "put", "bearish", "sell", "short", "crash", "📉", "💀", "⛔",
]


def _is_fresh(p: Path) -> bool:
    if not p.exists():
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(d.get("fetched_at", "2000-01-01T00:00:00"))
        return datetime.now() - ts < CACHE_TTL
    except Exception:
        return False


def _classify(text: str) -> str:
    if not text:
        return "neutral"
    text_low = text.lower()
    bull = sum(1 for k in BULL_KEYWORDS if k in text or k.lower() in text_low)
    bear = sum(1 for k in BEAR_KEYWORDS if k in text or k.lower() in text_low)
    if bull > bear + 0.5:
        return "bullish"
    if bear > bull + 0.5:
        return "bearish"
    return "neutral"


def fetch_one(ticker: str, keyword: str | None = None) -> dict | None:
    """Fetch /stock_feed posts and compute sentiment distribution."""
    cache_path = SENT_DIR / f"{ticker.replace('.', '_')}.json"
    if _is_fresh(cache_path):
        return json.loads(cache_path.read_text(encoding="utf-8"))

    kw = keyword or ticker.split(".")[0]  # use 00700 for 00700.HK
    params = {"keyword": kw, "size": "30"}
    url = f"{API_BASE}/stock_feed?{urlencode(params, quote_via=quote)}"
    try:
        r = subprocess.run(
            ["curl", "-sG", "--max-time", "10", "-A", "futu-comment-sentiment/0.0.2 (Skill)", url],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        d = json.loads(r.stdout)
        if d.get("code") != 0:
            return None
        items = d.get("data") or []
        if isinstance(items, dict):
            items = items.get("list") or items.get("items") or []
        if not items:
            return None
        # Classify each post
        bull = bear = neu = 0
        for it in items[:30]:
            text = (it.get("title", "") + " " + it.get("desc", "")).strip()
            text = re.sub(r"<[^>]+>", "", text)  # strip HTML
            label = _classify(text)
            if label == "bullish":
                bull += 1
            elif label == "bearish":
                bear += 1
            else:
                neu += 1
        total = max(bull + bear + neu, 1)
        out = {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "bull_pct": round(100 * bull / total, 1),
            "bear_pct": round(100 * bear / total, 1),
            "neutral_pct": round(100 * neu / total, 1),
            "post_count": total,
        }
        cache_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
    except Exception as e:
        print(f"  Exception for {ticker}: {e}")
    return None


def fetch_for_tickers(tickers: list[dict], delay: float = 0.3) -> dict[str, dict]:
    """Fetch sentiment for a list of {ticker, keyword} dicts."""
    results = {}
    for i, t in enumerate(tickers):
        ticker = t["ticker"]
        kw = t.get("keyword")
        cache_path = SENT_DIR / f"{ticker.replace('.', '_')}.json"
        if _is_fresh(cache_path):
            results[ticker] = json.loads(cache_path.read_text(encoding="utf-8"))
            print(f"  [{i+1}/{len(tickers)}] {ticker}: cached")
        else:
            print(f"  [{i+1}/{len(tickers)}] {ticker}: fetching...")
            d = fetch_one(ticker, keyword=kw)
            if d is not None:
                results[ticker] = d
            time.sleep(delay)
    return results


def load_cached(ticker: str) -> dict | None:
    """Read cached sentiment for a ticker (used at build time)."""
    p = SENT_DIR / f"{ticker.replace('.', '_')}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    res = fetch_for_tickers([
        {"ticker": "00700.HK", "keyword": "騰訊"},
        {"ticker": "09988.HK", "keyword": "阿里"},
    ])
    for t, d in res.items():
        print(f"\n=== {t} ===")
        if d:
            print(f"  bull={d['bull_pct']}% bear={d['bear_pct']}% neu={d['neutral_pct']}% n={d['post_count']}")
