"""Backfill missing entry_zone / stop_loss / target_price from full_md.

Bug: rerun_date_historical.py save_report() was missing these 3 fields.
This script reads existing full_md and extracts them via regex, then
updates the DB without re-running the LLM.
"""
import re
import sqlite3
import sys
from datetime import datetime

DB_PATH = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"

DATES = sys.argv[1:] or ["2026-07-03", "2026-07-06", "2026-07-07"]


def parse_entry_stop_target(full_md: str) -> tuple[str | None, str | None, str | None]:
    """Parse from rendered full_md format: '- **入場區間**: $X-$Y ...'"""
    entry = stop = target = None
    # Markdown bold prefix optional, supports full-width or half-width colon
    m = re.search(r"\*?\*?入場區間\*?\*?[：:]\s*([^\n]+)", full_md or "")
    if m:
        entry = m.group(1).strip()
    m = re.search(r"\*?\*?止[損蝕]位\*?\*?[：:]\s*([^\n]+)", full_md or "")
    if m:
        stop = m.group(1).strip()
    m = re.search(r"\*?\*?目標價\*?\*?[：:]\s*([^\n]+)", full_md or "")
    if m:
        target = m.group(1).strip()
    return entry, stop, target


con = sqlite3.connect(DB_PATH)
cur = con.cursor()

# Find records missing entry_zone
total_updated = 0
for date in DATES:
    rows = cur.execute(
        """SELECT id, code, full_md, entry_zone, stop_loss, target_price
           FROM daily_report
           WHERE report_date=?""",
        (date,),
    ).fetchall()
    print(f"\n=== {date}: {len(rows)} records ===")
    updated = 0
    skipped = 0
    for id_, code, full_md, e, s, t in rows:
        # Skip if all 3 already populated
        if e and s and t:
            skipped += 1
            continue
        ne, ns, nt = parse_entry_stop_target(full_md or "")
        if ne or ns or nt:
            cur.execute(
                """UPDATE daily_report
                   SET entry_zone=COALESCE(?, entry_zone),
                       stop_loss=COALESCE(?, stop_loss),
                       target_price=COALESCE(?, target_price)
                   WHERE id=?""",
                (ne, ns, nt, id_),
            )
            updated += 1
    con.commit()
    print(f"  Updated: {updated}, Skipped (already populated): {skipped}")
    total_updated += updated

con.close()
print(f"\n=== Total updated: {total_updated} ===")