#!/usr/bin/env python3
"""
US universe regeneration (2026-07-14).

Filters the existing US candidate pool (us_universe_200.json) by 20-day avg
$ volume (close * volume) >= $20M USD, takes top 200 by 20d avg $ volume,
writes to us_universe_200.json, and logs cadence to data/radar_regen.json.

Candidate pool = current us_universe_200.json. New tickers (e.g. recent IPOs
like SPCX/SpaceX 2026-06-12) must be added to the file manually first, then
the next regen will rank them in by 20d $ volume. (Owner instruction 2026-07-14:
keep manual add for IPOs; no auto-discovery yet.)

Cadence: every 5 trading days (Mon) as part of the 5-day report generation
cycle. Run via `python3 scripts/regen_all.py` — NOT standalone.
"""
from __future__ import annotations

import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = PROJECT_ROOT / "us_universe_200.json"
CADENCE_LOG = PROJECT_ROOT / "data" / "radar_regen.json"
MIN_DOLLAR_VOL_M_USD = 20.0  # $20M USD/day  (Owner decision 2026-07-14)
TOP_N = 200
FETCH_PERIOD = "1mo"
PARALLEL_WORKERS = 8

# Hardcoded deny-list — codes that should NEVER be on the day-trade radar
# (delisted / halted / SPAC pre-merger with no float). Empty for now; populate
# as complaints come in.
DENY_LIST: set[str] = set()


def build_candidate_pool() -> list[str]:
    """Read current us_universe_200.json as candidate pool.

    New tickers are added manually (e.g. SPCX on 2026-07-14) and ranked in
    by the regen based on 20d $ volume.
    """
    if not UNIVERSE_FILE.exists():
        print(f"[error] {UNIVERSE_FILE.name} missing", file=sys.stderr)
        return []
    try:
        data = json.loads(UNIVERSE_FILE.read_text())
        # Defensive: keep only string tickers, dedup, preserve order
        return list(dict.fromkeys(c for c in data if isinstance(c, str)))
    except Exception as e:
        print(f"[error] {UNIVERSE_FILE.name} parse failed: {e}", file=sys.stderr)
        return []


def fetch_avg_dollar_vol(code: str) -> tuple[str, float | None]:
    """Fetch 20d avg $ volume (USD millions) for a US ticker via yfinance.

    Returns (code, avg_m_usd), or (code, None) when the measurement itself
    could not be taken.

    2026-10-08: this used to return 0.0 on every failure, and the filter read
    0.0 as "below the $20M threshold" — so a throttled or empty Yahoo response
    was indistinguishable from a genuinely illiquid ticker. WBD came back with
    a single row that day (the same run that produced "every bar was null" in
    update_ohlc.py) and was therefore REMOVED from the universe. Because the
    universe is the candidate pool for batch_us200.py, that one bad response
    made a name permanently unfetchable: its page would have stayed published
    while silently freezing. A missing measurement is not evidence of low
    volume, and it must never be acted on as if it were.
    """
    try:
        t = yf.Ticker(code)
        hist = t.history(period=FETCH_PERIOD, auto_adjust=True)
        if hist is None or hist.empty or "Close" not in hist.columns or "Volume" not in hist.columns:
            return code, None
        # A truncated response cannot support a 20-day average.
        if len(hist) < 20:
            return code, None
        # $ volume per day = Close * Volume
        dv = (hist["Close"] * hist["Volume"]).tail(20)
        if dv.empty or not dv.notna().any():
            return code, None
        avg = float(dv.mean()) / 1_000_000  # → millions
        if not math.isfinite(avg):
            return code, None
        return code, avg
    except Exception:
        return code, None


def update_cadence_log(market: str, count: int) -> None:
    """Write last-regen timestamp to data/radar_regen.json.

    Preserves the other market's entry (so HK regen doesn't clobber US log
    and vice versa).
    """
    log: dict = {}
    if CADENCE_LOG.exists():
        try:
            log = json.loads(CADENCE_LOG.read_text())
        except Exception:
            log = {}
    log[market] = {
        "last_regen": time.strftime("%Y-%m-%d"),
        "count": count,
        "threshold_m_usd": MIN_DOLLAR_VOL_M_USD,
    }
    CADENCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    CADENCE_LOG.write_text(json.dumps(log, indent=2, ensure_ascii=False))


def main() -> int:
    t0 = time.time()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Building US candidate pool...")
    pool = build_candidate_pool()
    if not pool:
        return 1
    print(f"  Pool size: {len(pool)} codes")

    print(f"  Fetching 20d avg $ volume via yfinance ({PARALLEL_WORKERS} workers)...")
    results: list[tuple[str, float | None]] = []
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
        futures = {ex.submit(fetch_avg_dollar_vol, c): c for c in pool}
        done = 0
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if done % 25 == 0:
                print(f"    ...{done}/{len(pool)}")

    # Three outcomes, and only ONE of them may remove a name:
    #   measured and above threshold → keep, ranked
    #   measured and below threshold → drop (a real liquidity decision)
    #   could not measure          → KEEP (see fetch_avg_dollar_vol)
    unmeasured = sorted(c for c, v in results if v is None)
    measured = [(c, v) for c, v in results if v is not None]
    filtered = [(c, v) for c, v in measured
                if v >= MIN_DOLLAR_VOL_M_USD and c not in DENY_LIST]
    dropped_deny = sorted(DENY_LIST & set(c for c, _ in measured))
    # Unmeasured names are kept but cannot be ranked, so they sit behind every
    # measured name rather than competing on a fabricated turnover figure.
    candidates = filtered + [(c, 0.0) for c in unmeasured]
    filtered.sort(key=lambda x: -x[1])
    candidates.sort(key=lambda x: -x[1])
    top = [c for c, _ in candidates[:TOP_N]]
    demoted = [c for c, _ in candidates[TOP_N:]]
    if unmeasured:
        print(f"  ⚠ {len(unmeasured)} ticker(s) could not be measured — KEPT in the "
              f"universe because a failed fetch is not evidence of low volume: "
              f"{unmeasured[:10]}{' …' if len(unmeasured) > 10 else ''}")

    if len(top) < 100:
        print(
            f"[error] Only {len(top)} candidates above ${MIN_DOLLAR_VOL_M_USD:.0f}M USD — abort",
            file=sys.stderr,
        )
        return 1

    UNIVERSE_FILE.write_text(json.dumps(top, ensure_ascii=False, indent=2))
    update_cadence_log("us", len(top))

    elapsed = time.time() - t0
    print(f"  Wrote {len(top)} codes to {UNIVERSE_FILE.name}")
    print(f"  Dropped {len(measured) - len(filtered)} codes below "
          f"${MIN_DOLLAR_VOL_M_USD:.0f}M USD (measured, genuinely illiquid)")
    if dropped_deny:
        print(f"  Deny-list dropped: {dropped_deny}")
    print(f"  Top 10 by 20d avg $ volume:")
    for c, v in filtered[:10]:
        print(f"    {c}: ${v:.1f}M")
    print(f"  Bottom 5 in top {TOP_N}:")
    for c, v in filtered[TOP_N - 5:TOP_N]:
        print(f"    {c}: ${v:.1f}M")
    print(f"  Elapsed: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
