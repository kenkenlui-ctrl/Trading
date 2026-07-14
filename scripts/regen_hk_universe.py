#!/usr/bin/env python3
"""
HK universe regeneration (2026-06-27, cadence revised 2026-07-14 to 5-day).
Fetches 20d avg turnover for a wide HK candidate pool (full HKEX ~5,500 stocks),
filters to >= 50M HKD turnover, takes top 200, writes to hk_universe_200.json,
and logs cadence to data/radar_regen.json.

Cadence: every 5 trading days (Mon) — Owner 2026-07-14 decision. NO cron.
Run via `python3 scripts/regen_all.py`. Legacy `com.dsa-hk.regen-universe.plist`
launchd entry was never installed; cadence is enforced by regen_all.py gate
+ Owner memory note.
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
CADENCE_LOG = PROJECT_ROOT / "data" / "radar_regen.json"
MIN_TURNOVER_M_HKD = 50.0
TOP_N = 200

# Hardcoded deny-list for codes with low / unreliable turnover that should
# NEVER be on the day-trade radar (Owner complaint 2026-06-27: 0732.HK 信利國際
# has 2.4M HKD daily turnover — too thin for entry/exit; not even worth scoring).
DENY_LIST: set[str] = {
    "0732.HK",  # 信利國際 — 2.4M HKD/day (近月 avg)，day-trade 入唔到場
}


def normalize_hk_code(code: str) -> str:
    """Normalize HK ticker to canonical 5-digit .HK form (Tencent format).
    Accepts: 0700.HK, 00700.HK, 700.HK → returns 00700.HK"""
    if not code:
        return code
    base = code.split(".")[0].strip().zfill(5)
    return f"{base}.HK"


def build_candidate_pool() -> list[str]:
    """Build HK candidate pool from full HKEX universe (Tencent gtimg) + DB-known codes.
    Owner request 2026-06-28: stop using HSI/HSCEI/midcaps curated subset — use whole
    HKEX (~5,500 stocks) so the top-200 ranking reflects actual market-wide turnover.

    All codes normalized to 5-digit .HK (00700.HK form, Tencent canonical).
    """
    pool = set()
    full_file = Path(__file__).resolve().parent / "hk_full_universe.json"
    if full_file.exists():
        try:
            data = json.loads(full_file.read_text())
            for entry in data:
                if isinstance(entry, dict) and entry.get("code", "").endswith(".HK"):
                    pool.add(normalize_hk_code(entry["code"]))
        except Exception as e:
            print(f"[warn] {full_file.name} read failed: {e}", file=sys.stderr)
    else:
        print(f"[warn] {full_file.name} missing — run scripts/fetch_hk_universe.py first",
              file=sys.stderr)
    # Existing universe (for backward compat — keep around in case full file is stale)
    if UNIVERSE_FILE.exists():
        try:
            for c in json.loads(UNIVERSE_FILE.read_text()):
                if isinstance(c, str) and c.endswith(".HK"):
                    pool.add(normalize_hk_code(c))
        except Exception:
            pass
    # DB-known HK codes (last 7 days) — protects codes that were in old runs but
    # today's turnover < MIN_TURNOVER (e.g. illiquid days). We don't promote them
    # to top-200, but we still probe in case today's a normal day.
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
                    pool.add(normalize_hk_code(r[0]))
            db.close()
        except Exception as e:
            print(f"[warn] DB lookup failed: {e}", file=sys.stderr)
    return sorted(pool)


def fetch_turnover(codes: list[str]) -> list[tuple[str, float]]:
    """Fetch today's turnover (HKD millions) per code via Tencent gtimg batch API.

    Reads from scripts/hk_full_universe.json (prefetched by scripts/fetch_hk_universe.py
    which scans all 9999 5-digit HK codes in ~3s). Faster + more reliable than yfinance.
    """
    full_file = Path(__file__).resolve().parent / "hk_full_universe.json"
    if not full_file.exists():
        print(f"[warn] {full_file.name} missing — run fetch_hk_universe.py first",
              file=sys.stderr)
        return [(c, 0.0) for c in codes]

    # Build lookup map: code → turnover_m_hkd
    by_code = {}
    try:
        for entry in json.loads(full_file.read_text()):
            if isinstance(entry, dict):
                by_code[entry["code"]] = entry.get("turnover_m_hkd", 0.0)
    except Exception as e:
        print(f"[warn] {full_file.name} parse failed: {e}", file=sys.stderr)
        return [(c, 0.0) for c in codes]

    return [(c, by_code.get(c, 0.0)) for c in codes]


def main() -> int:
    t0 = time.time()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Building HK candidate pool (full HKEX universe)...")
    pool = build_candidate_pool()
    print(f"  Pool size: {len(pool)} codes")

    CANDIDATE_FILE.write_text("\n".join(pool))

    print(f"  Loading turnover from hk_full_universe.json...")
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
    # Log cadence so regen_all.py can decide whether to skip (5-day cycle)
    log: dict = {}
    if CADENCE_LOG.exists():
        try:
            log = json.loads(CADENCE_LOG.read_text())
        except Exception:
            log = {}
    log["hk"] = {
        "last_regen": time.strftime("%Y-%m-%d"),
        "count": len(top),
        "threshold_m_hkd": MIN_TURNOVER_M_HKD,
    }
    CADENCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    CADENCE_LOG.write_text(json.dumps(log, indent=2, ensure_ascii=False))

    elapsed = time.time() - t0
    print(f"  Wrote {len(top)} codes to {UNIVERSE_FILE.name}")
    print(f"  Dropped {len(results) - len(filtered)} codes below {MIN_TURNOVER_M_HKD}M HKD")
    if dropped_deny:
        print(f"  Deny-list dropped: {dropped_deny}")
    # 0921.HK specific check — owner complaint 2026-06-28
    if "00921.HK" not in top:
        print(f"  ✓ 0921.HK dropped (low turnover today)")
    else:
        print(f"  ⚠ 0921.HK still in top 200 — investigate")
    print(f"  Top 10 by turnover:")
    for c, t in filtered[:10]:
        print(f"    {c}: {t:.0f}M HKD")
    print(f"  Bottom 5 in top {TOP_N}:")
    for c, t in filtered[TOP_N-5:TOP_N]:
        print(f"    {c}: {t:.0f}M HKD")
    print(f"  Elapsed: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())