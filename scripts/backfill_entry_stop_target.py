#!/usr/bin/env python3
"""Backfill entry_zone / stop_loss / target_price from full_md.

2026-07-13: User noticed detail table shows "—" for these 3 columns.
Root cause: save_report() never extracted them from full_md into DB columns.
This script parses full_md with the same regex used in render_report_page()
and updates the DB. Safe to re-run (idempotent, skips already-filled rows).
"""
import sqlite3
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "dsa_hk.db"

# Match the same patterns as scripts/build_static.py report_page_html
ENTRY_RE = re.compile(r"\*?\*?入場區間\*?\*?[：:]\s*([^\n]+)")
STOP_RE = re.compile(r"\*?\*?止[損蝕]位\*?\*?[：:]\s*([^\n]+)")
TARGET_RE = re.compile(r"\*?\*?目標價\*?\*?[：:]\s*([^\n]+)")

# Optional stop-loss variants the LLM might emit
STOP_FALLBACK_RE = re.compile(r"\*?\*?(?:止[損蝕]位|止[損蝕])[^\n]*?\*?\*?[：:]\s*([^\n]+)")


def extract(md: str) -> tuple[str | None, str | None, str | None]:
    """Pull entry/stop/target text from full_md. Returns (None, None, None) if not found."""
    if not md:
        return None, None, None
    entry_m = ENTRY_RE.search(md)
    stop_m = STOP_RE.search(md)
    target_m = TARGET_RE.search(md)
    entry = entry_m.group(1).strip() if entry_m else None
    stop = stop_m.group(1).strip() if stop_m else None
    target = target_m.group(1).strip() if target_m else None
    # If stop didn't match primary, try fallback
    if not stop:
        fb = STOP_FALLBACK_RE.search(md)
        if fb:
            stop = fb.group(1).strip()
    return entry, stop, target


def main() -> int:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # All rows where entry_zone is empty
    rows = cur.execute(
        """
        SELECT id, report_date, code, full_md, entry_zone, stop_loss, target_price
        FROM daily_report
        WHERE (entry_zone IS NULL OR entry_zone = '')
           OR (stop_loss IS NULL OR stop_loss = '')
           OR (target_price IS NULL OR target_price = '')
        ORDER BY report_date DESC, code
        """
    ).fetchall()

    print(f"Candidates to backfill: {len(rows)}")

    filled = 0
    skipped = 0
    no_match = 0
    for r in rows:
        e, s, t = extract(r["full_md"] or "")
        if not e and not s and not t:
            no_match += 1
            continue
        # Only write fields that are currently empty AND we have a value
        sets = []
        params: list = []
        if e and (not r["entry_zone"]):
            sets.append("entry_zone = ?")
            params.append(e)
        if s and (not r["stop_loss"]):
            sets.append("stop_loss = ?")
            params.append(s)
        if t and (not r["target_price"]):
            sets.append("target_price = ?")
            params.append(t)
        if not sets:
            skipped += 1
            continue
        params.append(r["id"])
        cur.execute(
            f"UPDATE daily_report SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        filled += 1

    con.commit()
    con.close()
    print(f"  Updated: {filled}")
    print(f"  Skipped (already filled): {skipped}")
    print(f"  No regex match: {no_match}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
