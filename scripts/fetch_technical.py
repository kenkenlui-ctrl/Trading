"""fetch_technical.py — Cache Futu technical anomalies per ticker.

Wraps futu-technical-anomaly skill script. Caches for 6 hours.
Output: data/technical/<TICKER>.json with {fetched_at, items, raw}.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
TECH_DIR = REPO / "data" / "technical"
TECH_DIR.mkdir(parents=True, exist_ok=True)
SKILL_SCRIPT = Path("/Users/kenken/.minimax/skills/futu-technical-anomaly/scripts/handle_technical_anomaly.py")
CACHE_TTL = timedelta(hours=6)


def _is_fresh(p: Path) -> bool:
    if not p.exists():
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(d.get("fetched_at", "2000-01-01T00:00:00"))
        return datetime.now() - ts < CACHE_TTL
    except Exception:
        return False


def _parse_content(raw: str) -> list[dict]:
    """Parse the content field into list of {ts, text} dicts.

    Format: `[timestamp: 1787846400]\\ntext\\n[timestamp: ...]\\ntext`
    """
    items = []
    if not raw:
        return items
    cur_ts = 0
    cur_ts_str = ""
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("[timestamp:"):
            # Flush previous pending item
            if cur_ts or cur_ts_str:
                pass  # previous text already appended
            try:
                ts_part = line.split("]")[0].split(":")[1].strip()
                cur_ts = int(ts_part)
                cur_ts_str = datetime.fromtimestamp(cur_ts).strftime("%Y-%m-%d")
            except Exception:
                cur_ts = 0
                cur_ts_str = ""
        else:
            items.append({
                "ts": cur_ts,
                "ts_str": cur_ts_str,
                "text": line,
            })
            cur_ts = 0
            cur_ts_str = ""
    return items


def fetch_one(ticker: str, market: str = "HK", time_range: int = 7) -> dict | None:
    """Call futu-technical-anomaly. Returns dict {fetched_at, items, raw} or None."""
    cache_path = TECH_DIR / f"{ticker.replace('.', '_')}.json"
    if _is_fresh(cache_path):
        return json.loads(cache_path.read_text(encoding="utf-8"))

    if market == "HK":
        code = ticker.split(".")[0]  # "00700" from "00700.HK"
        symbol = f"HK.{int(code):05d}"  # pad to 5 digits, drop leading zeros
    else:
        symbol = f"US.{ticker.split('.')[0]}"

    if not SKILL_SCRIPT.exists():
        print(f"  SKILL_SCRIPT missing: {SKILL_SCRIPT}")
        return None

    try:
        py = shutil.which("python3") or shutil.which("python") or "python3"
        result = subprocess.run(
            [py, str(SKILL_SCRIPT), symbol, "--time-range", str(time_range), "--json"],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode != 0:
            return None
        # The skill script prints JSON + log lines. Find first '{' and last '}'
        # to extract the JSON object (content string can contain '}' characters
        # that would fool a line-based scanner).
        text = result.stdout
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0 or end <= start:
            return None
        blob = text[start : end + 1]
        try:
            d = json.loads(blob)
        except Exception:
            return None
        data_field = d.get("data") or {}
        content = data_field.get("content", "")
        if data_field.get("err_code") != 0 or not content:
            return None
        items = _parse_content(content)
        out = {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "raw": content,
            "items": items,
        }
        cache_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
    except Exception as e:
        print(f"  Exception for {ticker}: {e}")
    return None


def fetch_for_tickers(tickers: list[dict], delay: float = 0.5) -> dict[str, dict]:
    """Fetch technical anomalies for a list of {ticker, market} dicts."""
    results = {}
    for i, t in enumerate(tickers):
        ticker = t["ticker"]
        market = t.get("market", "HK")
        cache_path = TECH_DIR / f"{ticker.replace('.', '_')}.json"
        if _is_fresh(cache_path):
            results[ticker] = json.loads(cache_path.read_text(encoding="utf-8"))
            print(f"  [{i+1}/{len(tickers)}] {ticker}: cached")
        else:
            print(f"  [{i+1}/{len(tickers)}] {ticker}: fetching...")
            d = fetch_one(ticker, market=market)
            if d is not None:
                results[ticker] = d
            time.sleep(delay)
    return results


def load_cached(ticker: str) -> dict | None:
    """Read cached technical anomaly for a ticker (used at build time)."""
    p = TECH_DIR / f"{ticker.replace('.', '_')}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    res = fetch_for_tickers([
        {"ticker": "00700.HK", "market": "HK"},
        {"ticker": "02359.HK", "market": "HK"},
    ])
    for t, d in res.items():
        print(f"\n=== {t} ===")
        for it in d.get("items", [])[:5]:
            print(f"  {it['ts_str']}: {it['text'][:80]}")
