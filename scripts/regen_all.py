#!/usr/bin/env python3
"""
Unified radar regen — runs HK + US regen and logs cadence (2026-07-14).

Owner decision 2026-07-14: 5-day cadence (every Monday). NO cron — invoked
manually as part of the 5-day report generation cycle.

Usage:
    python3 scripts/regen_all.py                # respect 5-day cadence gate
    python3 scripts/regen_all.py --force        # bypass cadence check
    python3 scripts/regen_all.py --hk-only      # skip US
    python3 scripts/regen_all.py --us-only      # skip HK
    python3 scripts/regen_all.py --status       # show cadence + next due

Cadence log: data/radar_regen.json
    { "hk": {last_regen, count, threshold_m_hkd},
      "us": {last_regen, count, threshold_m_usd} }
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
CADENCE_LOG = PROJECT_ROOT / "data" / "radar_regen.json"
REGEN_INTERVAL_DAYS = 5  # Owner 2026-07-14: Mon-aligned 5-day cycle


def _read_cadence() -> dict:
    if not CADENCE_LOG.exists():
        return {}
    try:
        return json.loads(CADENCE_LOG.read_text())
    except Exception:
        return {}


def last_regen_any() -> str | None:
    """Most recent of (hk.last_regen, us.last_regen), or None if no log."""
    log = _read_cadence()
    candidates = []
    for mkt in ("hk", "us"):
        d = log.get(mkt, {}).get("last_regen")
        if d:
            candidates.append(d)
    return max(candidates) if candidates else None


def should_regen(force: bool) -> tuple[bool, str]:
    """Return (should_run, reason). reason is human-readable."""
    if force:
        return True, "force flag"
    last = last_regen_any()
    if last is None:
        return True, "no prior regen on file"
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d")
    except Exception:
        return True, f"unparseable last_regen date '{last}'"
    days = (datetime.now() - last_dt).days
    if days >= REGEN_INTERVAL_DAYS:
        return True, f"last regen {days} days ago (>={REGEN_INTERVAL_DAYS})"
    return False, f"last regen {days} days ago (<{REGEN_INTERVAL_DAYS})"


def show_status() -> int:
    log = _read_cadence()
    last = last_regen_any()
    if not last:
        print("No prior regen logged. Run `python3 scripts/regen_all.py --force` to seed.")
        return 0
    last_dt = datetime.strptime(last, "%Y-%m-%d")
    days = (datetime.now() - last_dt).days
    next_due = last_dt + timedelta(days=REGEN_INTERVAL_DAYS)
    print(f"Last regen: {last} ({days} days ago)")
    print(f"Next due:   {next_due.strftime('%Y-%m-%d')} "
          f"({'overdue' if datetime.now() >= next_due else 'in ' + str((next_due - datetime.now()).days) + ' days'})")
    print(f"Cadence log: {CADENCE_LOG}")
    for mkt in ("hk", "us"):
        d = log.get(mkt)
        if d:
            print(f"  {mkt.upper()}: {d.get('count')} codes @ {d.get('last_regen')}")
        else:
            print(f"  {mkt.upper()}: not yet logged")
    return 0


def run_one(name: str, script: str) -> int:
    print(f"\n=== {name} regen ===")
    rc = subprocess.call([sys.executable, str(SCRIPTS_DIR / script)])
    return rc


def main() -> int:
    p = argparse.ArgumentParser(description="Unified HK + US radar regen (5-day cadence)")
    p.add_argument("--force", action="store_true", help="Bypass 5-day cadence check")
    p.add_argument("--hk-only", action="store_true", help="Skip US regen")
    p.add_argument("--us-only", action="store_true", help="Skip HK regen")
    p.add_argument("--status", action="store_true", help="Show cadence status and exit")
    args = p.parse_args()

    if args.status:
        return show_status()

    run, reason = should_regen(args.force)
    if not run:
        print(f"[skip] {reason}. Use --force to override.")
        return 0
    print(f"[proceed] {reason}.")

    rc = 0
    if not args.us_only:
        rc |= run_one("HK", "regen_hk_universe.py")
    if not args.hk_only:
        rc |= run_one("US", "regen_us_universe.py")

    if rc == 0:
        print(f"\n✓ Radar regen done. Next due: "
              f"{(datetime.now() + timedelta(days=REGEN_INTERVAL_DAYS)).strftime('%Y-%m-%d')}.")
    else:
        print(f"\n✗ Radar regen exited with rc={rc}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
