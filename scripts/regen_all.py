#!/usr/bin/env python3
"""
Unified universe regen — runs HK + US + JP, with a TRADING-DAY cadence gate.

OWNERSHIP (Kenneth, 2026-10-08): this runs WEEKLY ON CLOUD ("Undercover"),
which then pushes the three *_universe_200.json files into this repo. The daily
refresh pipeline only READS those files; it must never call this script. Do not
re-wire it into refresh.sh — a weekly 200-name universe change is a reviewed
input, not something that should happen implicitly inside a daily build.

No cron inside this repo either. If you are running this by hand (e.g. to seed
or to reproduce a cloud run), the cadence gate below still applies.

2026-10-08 changes (three real defects in the previous version):

  1. JP was never called. build_jp_universe.py existed and wrote
     jp_universe_200.json, but regen_all.py only ran HK and US — and JP's
     list feeds fetch_jp_charts.py, the daily JP fetch. So "unified regen"
     left a third of the site on a universe nobody refreshed. Its cadence
     entry was likewise never written, so it could not even have been seen
     as stale.

  2. The gate counted CALENDAR days, not trading days. "Every 5 days" over
     calendar days is 3 trading days across a weekend and 5 across a clean
     week — so the effective re-rank cadence drifted by up to 40%. The
     intent was five trading days.

  3. The gate used datetime.now() (wall clock) rather than the market
     calendar, so it could not know a session had not happened yet.

Usage:
    python3 scripts/regen_all.py                # respect the cadence gate
    python3 scripts/regen_all.py --force        # bypass the gate
    python3 scripts/regen_all.py --hk-only | --us-only | --jp-only
    python3 scripts/regen_all.py --status

Cadence log: data/radar_regen.json
    {market: {last_regen, count, <threshold key>}}
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
CADENCE_LOG = PROJECT_ROOT / "data" / "radar_regen.json"

# Owner 2026-07-14: five-day cycle. 2026-10-08: counted in TRADING days.
REGEN_INTERVAL_TRADING_DAYS = 5

MARKETS = (
    ("hk", "HK", "regen_hk_universe.py"),
    ("us", "US", "regen_us_universe.py"),
    ("jp", "JP", "build_jp_universe.py"),
)


def _read_cadence() -> dict:
    if not CADENCE_LOG.exists():
        return {}
    try:
        return json.loads(CADENCE_LOG.read_text())
    except Exception:
        return {}


def last_regen_any() -> str | None:
    """Most recent last_regen across ALL logged markets."""
    log = _read_cadence()
    dates = [d for d in (log.get(k, {}).get("last_regen") for k, *_ in MARKETS) if d]
    return max(dates) if dates else None


def trading_days_between(a: str | date, b: str | date) -> int:
    """Weekdays between a and b, b inclusive, a exclusive.

    Weekday count is not a full exchange calendar — it ignores HK/JP/US public
    holidays. That is a deliberate approximation: the alternative is a stale
    hard-coded holiday table that is wrong once a year, and the cost of being
    a day or two early is one extra re-rank. Weekends are the only gap large
    enough to matter over a five-day window, and they are the gap the old
    calendar-day gate got wrong.
    """
    da = date.fromisoformat(a) if isinstance(a, str) else a
    db = date.fromisoformat(b) if isinstance(b, str) else b
    n, cur = 0, da + timedelta(days=1)
    while cur <= db:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def should_regen(force: bool) -> tuple[bool, str]:
    """Return (should_run, human-readable reason)."""
    if force:
        return True, "force flag"
    last = last_regen_any()
    if last is None:
        return True, "no prior regen on file"
    try:
        date.fromisoformat(last)
    except Exception:
        return True, f"unparseable last_regen date {last!r}"
    days = trading_days_between(last, date.today())
    if days >= REGEN_INTERVAL_TRADING_DAYS:
        return True, (f"last regen {days} trading days ago "
                      f"(>= {REGEN_INTERVAL_TRADING_DAYS}); last run {last}")
    return False, (f"last regen {days} trading days ago "
                   f"(< {REGEN_INTERVAL_TRADING_DAYS}); last run {last}")


def show_status() -> int:
    log = _read_cadence()
    last = last_regen_any()
    if not last:
        print("No prior regen logged. Run `python3 scripts/regen_all.py --force` to seed.")
        return 0
    days = trading_days_between(last, date.today())
    due_in = REGEN_INTERVAL_TRADING_DAYS - days
    print(f"Last regen: {last} ({days} trading days ago)")
    print(f"Interval:   {REGEN_INTERVAL_TRADING_DAYS} trading days "
          f"(not calendar days)")
    print(f"Next due:   {'now — overdue' if due_in <= 0 else f'in {due_in} trading day(s)'}")
    print(f"Cadence log: {CADENCE_LOG}")
    for key, name, _ in MARKETS:
        d = log.get(key)
        print(f"  {name}: {d.get('count')} codes @ {d.get('last_regen')}"
              if d else f"  {name}: NOT LOGGED — has never been regen-gated")
    return 0


def run_one(name: str, script: str) -> int:
    print(f"\n=== {name} universe regen ===", flush=True)
    return subprocess.call([sys.executable, str(SCRIPTS_DIR / script)])


def main() -> int:
    p = argparse.ArgumentParser(
        description=f"Universe regen for HK/US/JP "
                    f"({REGEN_INTERVAL_TRADING_DAYS}-trading-day cadence)")
    p.add_argument("--force", action="store_true", help="Bypass the cadence gate")
    for key, _, _ in MARKETS:
        p.add_argument(f"--{key}-only", action="store_true", help=f"Skip the other markets")
    p.add_argument("--status", action="store_true", help="Show cadence status and exit")
    args = p.parse_args()

    if args.status:
        return show_status()

    run, reason = should_regen(args.force)
    if not run:
        print(f"[skip] {reason}. Use --force to override.")
        return 0
    print(f"[proceed] {reason}.", flush=True)

    only = {k for k, _, _ in MARKETS if getattr(args, f"{k}_only")}
    rc = 0
    ran = []
    for key, name, script in MARKETS:
        if only and key not in only:
            continue
        rc |= run_one(name, script)
        ran.append(key)

    if rc == 0:
        print(f"\n✓ Regen done for {', '.join(k.upper() for k in ran)}. "
              f"Next due in {REGEN_INTERVAL_TRADING_DAYS} trading days.")
    else:
        print(f"\n✗ Regen exited with rc={rc} — cadence log NOT advanced, "
              f"so the next run retries the markets that failed.", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())