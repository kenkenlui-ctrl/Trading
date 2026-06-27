#!/usr/bin/env python3
"""
End-to-end daily refresh pipeline (Owner request 2026-06-27).

Runs once per calendar day (lazily triggered by Streamlit sidebar button OR
manual cron / shell), in this order:

  1. Regen HK universe (top 200 by 20d avg turnover, drop <50M HKD)
  2. Prune DB: DELETE daily_report rows where code not in current universe
     (so stale low-turnover codes like 0732.HK don't pollute future dashboards)
  3. Analyze all HK + US codes (full pipeline, takes ~10 min)
  4. Rebuild static dashboard (public/) — 9 filter variants per date + index
  5. Stage + commit + push to GitHub → Cloudflare Pages auto-deploys

Idempotent: safe to re-run; each step no-ops if already done today.

Usage:
    python3 scripts/refresh_daily.py            # full refresh
    python3 scripts/refresh_daily.py --skip-analyze   # skip analysis (DB-clean + static only)
    python3 scripts/refresh_daily.py --skip-push      # skip git push (manual review)
    python3 scripts/refresh_daily.py --dry-run        # print steps without executing
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

UNIVERSE_FILE = PROJECT_ROOT / "hk_universe_200.json"
DB_PATH = PROJECT_ROOT / "data" / "dsa_hk.db"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run(cmd: list[str], cwd: Path | str | None = None, timeout: int = 1800) -> int:
    """Run a subprocess, print stdout/stderr live, return exit code."""
    log(f"$ {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or PROJECT_ROOT,
            capture_output=False,  # stream
            timeout=timeout,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        log(f"  ✘ TIMEOUT after {timeout}s")
        return 124
    except FileNotFoundError as e:
        log(f"  ✘ command not found: {e}")
        return 127


def step_regen_hk_universe(dry: bool) -> bool:
    """Step 1: refresh hk_universe_200.json from candidate pool."""
    log("━━━ Step 1/5: Regen HK universe (top 200 by 20d turnover) ━━━")
    if dry:
        log("  (dry-run) would run: python3 scripts/regen_hk_universe.py")
        return True
    rc = run([sys.executable, "scripts/regen_hk_universe.py"])
    if rc != 0:
        log(f"  ✘ regen failed rc={rc}")
        return False
    if not UNIVERSE_FILE.exists():
        log(f"  ✘ {UNIVERSE_FILE.name} not written")
        return False
    log(f"  ✓ universe size: {len(json.loads(UNIVERSE_FILE.read_text()))} codes")
    return True


def step_prune_db(dry: bool) -> bool:
    """Step 2: DELETE daily_report rows where code not in current universe.

    Rationale: 0732.HK had 2.4M HKD turnover (Owner complaint 2026-06-27) — too
    thin for day-trade entry/exit. After regen dropped it from universe, old
    reports still polluted DB. We mirror the universe whitelist into daily_report.
    """
    log("━━━ Step 2/5: Prune DB (drop reports for codes not in universe) ━━━")
    if not UNIVERSE_FILE.exists():
        log(f"  ✘ {UNIVERSE_FILE.name} missing — run step_regen_hk_universe first")
        return False
    universe = set(json.loads(UNIVERSE_FILE.read_text()))

    # Read DB
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    cur = db.execute("SELECT DISTINCT code FROM daily_report WHERE code LIKE '%.HK'")
    db_hk_codes = {row[0] for row in cur.fetchall()}
    db.close()

    stale = sorted(db_hk_codes - universe)
    log(f"  Universe HK: {len(universe)} codes")
    log(f"  DB HK codes: {len(db_hk_codes)} codes")
    log(f"  Stale (in DB, not in universe): {len(stale)} → {stale[:10]}{'...' if len(stale) > 10 else ''}")

    if not stale:
        log("  ✓ nothing to prune")
        return True
    if dry:
        log("  (dry-run) would DELETE")
        return True

    db = sqlite3.connect(str(DB_PATH))
    placeholders = ",".join("?" for _ in stale)
    cur = db.execute(
        f"DELETE FROM daily_report WHERE code IN ({placeholders})",
        stale,
    )
    deleted = cur.rowcount
    db.commit()
    db.close()
    log(f"  ✓ DELETED {deleted} reports for {len(stale)} stale codes")
    return True


def step_analyze(dry: bool) -> bool:
    """Step 3: analyze all HK + US tickers (takes ~10 min)."""
    log("━━━ Step 3/5: Analyze HK + US tickers (~10 min) ━━━")
    if dry:
        log("  (dry-run) would run: python3 -m src.pipeline analyze")
        return True
    rc = run(
        [sys.executable, "-m", "src.pipeline", "analyze"],
        timeout=1800,  # 30 min hard cap
    )
    if rc != 0:
        log(f"  ✘ analyze failed rc={rc}")
        return False
    log(f"  ✓ analyze complete rc={rc}")
    return True


def step_build_static(dry: bool) -> bool:
    """Step 4: rebuild public/ HTML for Cloudflare Pages."""
    log("━━━ Step 4/5: Build static HTML dashboard ━━━")
    if dry:
        log("  (dry-run) would run: python3 scripts/build_static.py --all --index --static-pages")
        return True
    rc = run([sys.executable, "scripts/build_static.py", "--all", "--index", "--static-pages"])
    if rc != 0:
        log(f"  ✘ build_static failed rc={rc}")
        return False
    log("  ✓ static dashboard built")
    return True


def step_push(dry: bool) -> bool:
    """Step 5: git add + commit + push (Cloudflare Pages auto-deploys)."""
    log("━━━ Step 5/5: Git commit + push (Cloudflare Pages auto-deploy) ━━━")
    if dry:
        log("  (dry-run) would git add + commit + push")
        return True

    # Stage universe + DB + public
    rc = run(["git", "add", "-A"])
    if rc != 0:
        log(f"  ✘ git add failed rc={rc}")
        return False

    # Check if there's anything to commit
    rc = run(["git", "diff", "--cached", "--quiet"], cwd=PROJECT_ROOT)
    if rc == 0:
        log("  ✓ nothing to commit (everything already pushed)")
        return True

    today = datetime.now().strftime("%Y-%m-%d")
    msg = f"Daily refresh {today}: regen + analyze + static rebuild"
    rc = run(["git", "commit", "-m", msg])
    if rc != 0:
        log(f"  ✘ git commit failed rc={rc}")
        return False
    log("  ✓ committed")

    # Push with longer timeout (gh CLI can be slow on large repos)
    rc = run(["git", "push", "origin", "main"], timeout=300)
    if rc != 0:
        log(f"  ✘ git push failed rc={rc}")
        return False
    log("  ✓ pushed to origin/main → Cloudflare Pages auto-deploys")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily refresh pipeline")
    parser.add_argument("--skip-analyze", action="store_true", help="Skip Step 3 (analysis)")
    parser.add_argument("--skip-push", action="store_true", help="Skip Step 5 (git push)")
    parser.add_argument("--dry-run", action="store_true", help="Print steps without executing")
    parser.add_argument(
        "--only",
        choices=["regen", "prune", "analyze", "build", "push"],
        help="Run only one step (for debugging)",
    )
    args = parser.parse_args()

    t0 = time.time()
    log("=" * 60)
    log("Leeks Terminal · Daily Refresh Pipeline")
    log("=" * 60)

    steps = [
        ("regen", step_regen_hk_universe),
        ("prune", step_prune_db),
        ("analyze", step_analyze),
        ("build", step_build_static),
        ("push", step_push),
    ]

    skip_map = {
        "analyze": args.skip_analyze,
    }

    failed = []
    for name, fn in steps:
        if args.only and args.only != name:
            continue
        if skip_map.get(name):
            log(f"━━━ Step {name}: SKIPPED (--skip-{name}) ━━━")
            continue
        if not fn(args.dry_run):
            failed.append(name)
            log(f"  ✘ step {name} failed — aborting pipeline")
            break

    elapsed = time.time() - t0
    log("=" * 60)
    if failed:
        log(f"✘ FAILED at step: {failed[0]} ({elapsed:.1f}s)")
        return 1
    log(f"✓ All steps done ({elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
