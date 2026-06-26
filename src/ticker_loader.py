"""Load HK/US ticker universe from universe JSON files or curated-radar.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .config import get_config

UNIVERSE_DIR = Path(__file__).resolve().parent.parent


def _load_universe(filename: str) -> list[str]:
    path = UNIVERSE_DIR / filename
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def load_hk_tickers() -> list[str]:
    """
    Return list of HK tickers to analyze.

    Priority:
    1. HK_TICKERS_OVERRIDE (comma-separated env var)
    2. hk_universe_200.json in project root (200-ticker expanded universe)
    3. HK tickers from RADAR_PATH (curated-radar.json, legacy)
    4. Empty list
    """
    cfg = get_config()

    if cfg.hk_tickers_override:
        return [
            t.strip() for t in cfg.hk_tickers_override.split(",") if t.strip()
        ]

    # Try expanded 200-ticker HK universe first
    tickers = _load_universe("hk_universe_200.json")
    if tickers:
        if cfg.max_tickers > 0:
            tickers = tickers[: cfg.max_tickers]
        return tickers

    # Legacy: parse curated-radar.json
    radar_path = Path(cfg.radar_path)
    if not radar_path.exists():
        return []

    try:
        data = json.loads(radar_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    hk_tickers = [
        t["tickerId"]
        for t in data.get("tickers", [])
        if t.get("market") == "HK"
    ]
    hk_tickers = sorted(set(hk_tickers))

    if cfg.max_tickers > 0:
        hk_tickers = hk_tickers[: cfg.max_tickers]

    return hk_tickers


def load_us_tickers() -> list[str]:
    """
    Return list of US tickers to analyze.

    Priority:
    1. US_TICKERS_OVERRIDE (comma-separated env var)
    2. us_universe_200.json in project root (200-ticker US universe)
    3. US tickers from RADAR_PATH (curated-radar.json, legacy)
    4. Empty list
    """
    cfg = get_config()

    if cfg.us_tickers_override:
        return [
            t.strip() for t in cfg.us_tickers_override.split(",") if t.strip()
        ]

    tickers = _load_universe("us_universe_200.json")
    if tickers:
        if cfg.max_tickers > 0:
            tickers = tickers[: cfg.max_tickers]
        return tickers

    # Legacy: parse from curated-radar.json
    radar_path = Path(cfg.radar_path)
    if not radar_path.exists():
        return []

    try:
        data = json.loads(radar_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    us_tickers = [
        t["tickerId"]
        for t in data.get("tickers", [])
        if t.get("market") == "US"
    ]
    us_tickers = sorted(set(us_tickers))

    if cfg.max_tickers > 0:
        us_tickers = us_tickers[: cfg.max_tickers]

    return us_tickers


def is_hk_trading_day() -> bool:
    """
    Basic check: weekday + (skip HK public holidays if list provided).

    For v1, just returns True if Mon-Fri.
    Future: integrate HKEX holiday calendar.
    """
    from datetime import datetime
    return datetime.now().weekday() < 5  # Mon=0, Fri=4, Sat=5, Sun=6


if __name__ == "__main__":
    hk = load_hk_tickers()
    us = load_us_tickers()
    print(f"Loaded {len(hk)} HK + {len(us)} US = {len(hk)+len(us)} tickers")
    print(f"HK: {', '.join(hk[:5])}...")
    print(f"US: {', '.join(us[:5])}...")
