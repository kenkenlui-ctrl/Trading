#!/usr/bin/env python3
"""
Daily HK universe regeneration (2026-06-27).
Fetches 20d avg turnover for a wide HK candidate pool (HSI + HSCEI + mid-caps + DB-known),
filters to >= 50M HKD turnover, takes top 200, writes to hk_universe_200.json.

Run daily before the 18:00 HKT scheduler via launchd (com.dsa-hk.regen-universe.plist).
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = PROJECT_ROOT / "hk_universe_200.json"
CANDIDATE_FILE = Path("/tmp/hk_candidate_pool.txt")
MIN_TURNOVER_M_HKD = 50.0
TOP_N = 200

# Hardcoded deny-list for codes with low / unreliable turnover that should
# NEVER be on the day-trade radar (Owner complaint 2026-06-27: 0732.HK 信利國際
# has 2.4M HKD daily turnover — too thin for entry/exit; not even worth scoring).
DENY_LIST: set[str] = {
    "0732.HK",  # 信利國際 — 2.4M HKD/day (近月 avg)，day-trade 入唔到場
}


def build_candidate_pool() -> list[str]:
    """Build HK candidate pool from HSI/HSCEI constituents + current universe + DB."""
    # Constituent lists live alongside this script (in scripts/)
    here = Path(__file__).resolve().parent
    sources = [
        str(here / "hsi_constituents.txt"),
        str(here / "hscei_extra.txt"),
        str(here / "hk_midcaps.txt"),
    ]
    pool = set()
    for src in sources:
        p = Path(src)
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and line.endswith(".HK"):
                    pool.add(line)
    # Existing universe (for backward compat)
    if UNIVERSE_FILE.exists():
        try:
            for c in json.loads(UNIVERSE_FILE.read_text()):
                if isinstance(c, str) and c.endswith(".HK"):
                    pool.add(c)
        except Exception:
            pass
    # DB-known HK codes (last 7 days)
    db_path = PROJECT_ROOT / "data" / "dsa_hk.db"
    if db_path.exists():
        try:
            import sqlite3
            db = sqlite3.connect(str(db_path))
            rows = db.execute(
                "SELECT DISTINCT code FROM daily_report "
                "WHERE report_date >= date('now','-7 days') AND code LIKE '%.HK'"
            ).fetchall()
            for r in rows:
                if r[0].endswith(".HK"):
                    pool.add(r[0])
            db.close()
        except Exception as e:
            print(f"[warn] DB lookup failed: {e}", file=sys.stderr)
    return sorted(pool)


def fetch_turnover(codes: list[str]) -> list[tuple[str, float]]:
    """Fetch 20d avg turnover (HKD millions) for each code via yfinance."""
    import yfinance as yf

    def _fetch(code: str) -> tuple[str, float]:
        try:
            t = yf.Ticker(code)
            hist = t.history(period="1mo")
            if len(hist) >= 10:
                avg = (hist["Close"] * hist["Volume"]).mean() / 1e6
                return (code, float(avg))
        except Exception:
            pass
        return (code, 0.0)

    results = []
    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(_fetch, c): c for c in codes}
        for f in as_completed(futures):
            results.append(f.result())
    return results


def main() -> int:
    t0 = time.time()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Building HK candidate pool...")
    pool = build_candidate_pool()
    print(f"  Pool size: {len(pool)} codes")

    # Cache pool to /tmp for debugging
    CANDIDATE_FILE.write_text("\n".join(pool))

    print(f"  Fetching 20d turnover via yfinance...")
    results = fetch_turnover(pool)
    filtered = [(c, t) for c, t in results if t >= MIN_TURNOVER_M_HKD and c not in DENY_LIST]
    dropped_deny = sorted(DENY_LIST & set(c for c, _ in results))
    filtered.sort(key=lambda x: -x[1])
    top = [c for c, _ in filtered[:TOP_N]]

    if len(top) < 100:
        print(f"[error] Only {len(top)} candidates above threshold {MIN_TURNOVER_M_HKD}M — abort",
              file=sys.stderr)
        return 1

    UNIVERSE_FILE.write_text(json.dumps(top, ensure_ascii=False, indent=2))
    elapsed = time.time() - t0
    print(f"  Wrote {len(top)} codes to {UNIVERSE_FILE.name}")
    print(f"  Dropped {len(results) - len(filtered)} codes below threshold")
    if dropped_deny:
        print(f"  Deny-list dropped: {dropped_deny}")
    print(f"  Top 5 by turnover: {[(c, f'{t:.0f}M') for c, t in filtered[:5]]}")
    print(f"  Bottom 5 in top 200: {[(c, f'{t:.0f}M') for c, t in filtered[195:200]]}")
    print(f"  Elapsed: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())