"""Swing-tradeable universe filter.

Loads swing_universe.json (regular codes) and swing_blacklist.json (excluded
codes) produced by build_swing_v4.py, and exposes helpers to filter BUY
signals for multi-day swing paper trading.

Rule: a code is swing-tradeable iff it's in swing_universe (not in blacklist).

For day-trade (intraday), NO filter is applied — leveraged ETPs are
tradable intraday but suffer daily-reset decay so are excluded for swing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional

# Resolve paths relative to repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
SWING_UNIVERSE_PATH = REPO_ROOT / "data" / "swing_universe.json"
SWING_BLACKLIST_PATH = REPO_ROOT / "data" / "swing_blacklist.json"

_universe_cache: Optional[set[str]] = None
_blacklist_cache: Optional[set[str]] = None
_meta_cache: Optional[dict] = None


def _load_universe() -> set[str]:
    global _universe_cache
    if _universe_cache is not None:
        return _universe_cache
    if not SWING_UNIVERSE_PATH.exists():
        _universe_cache = set()
        return _universe_cache
    try:
        data = json.loads(SWING_UNIVERSE_PATH.read_text(encoding="utf-8"))
        _universe_cache = set(data.get("swing_universe", []))
    except Exception:
        _universe_cache = set()
    return _universe_cache


def _load_blacklist() -> set[str]:
    global _blacklist_cache
    if _blacklist_cache is not None:
        return _blacklist_cache
    if not SWING_BLACKLIST_PATH.exists():
        _blacklist_cache = set()
        return _blacklist_cache
    try:
        data = json.loads(SWING_BLACKLIST_PATH.read_text(encoding="utf-8"))
        _blacklist_cache = set(data.get("excluded_codes", []))
    except Exception:
        _blacklist_cache = set()
    return _blacklist_cache


def _meta() -> dict:
    global _meta_cache
    if _meta_cache is not None:
        return _meta_cache
    if SWING_UNIVERSE_PATH.exists():
        try:
            _meta_cache = json.loads(SWING_UNIVERSE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _meta_cache = {}
    else:
        _meta_cache = {}
    return _meta_cache


def is_swing_tradeable(code: str) -> bool:
    """True iff code is in swing_universe (not in blacklist)."""
    if not code:
        return False
    universe = _load_universe()
    if not universe:
        # Fallback: if universe file missing, allow all (don't break)
        return True
    return code in universe


def filter_swing_tradeable(codes: Iterable[str]) -> list[str]:
    """Return only swing-tradeable codes from input."""
    universe = _load_universe()
    if not universe:
        return list(codes)
    return [c for c in codes if c in universe]


def get_blacklist() -> set[str]:
    """Return set of excluded codes (for audit / logging)."""
    return _load_blacklist()


def get_universe_size() -> int:
    return len(_load_universe())


def summary() -> dict:
    """Return summary of current universe config."""
    return {
        "universe_size": get_universe_size(),
        "blacklist_size": len(_load_blacklist()),
        "generated_at": _meta().get("generated_at", "unknown"),
        "method": _meta().get("method", "unknown"),
        "swing_universe_path": str(SWING_UNIVERSE_PATH),
        "swing_blacklist_path": str(SWING_BLACKLIST_PATH),
    }


def reload() -> None:
    """Force reload from disk (after blacklist update)."""
    global _universe_cache, _blacklist_cache, _meta_cache
    _universe_cache = None
    _blacklist_cache = None
    _meta_cache = None


if __name__ == "__main__":
    import sys
    s = summary()
    print(json.dumps(s, indent=2, ensure_ascii=False))
    print(f"\nSample excluded: {sorted(_load_blacklist())[:10]}")
    print(f"Sample included: {sorted(_load_universe())[:10]}")
