#!/usr/bin/env python3
"""
Replace MA20/50/100/200 values in narrative (full_md) with fresh data_snapshot values.

User feedback 2026-07-25 06:50: "i need everything correct!"
Root cause: re-LLM v3 ran BEFORE MA update → narrative has stale MA values,
but data_snapshot has fresh values. Mechanical regex replacement fixes
the narrative MA values without needing another re-LLM.

Pattern matching:
- "MA20 $X.XX" → "MA20 $FRESH_MA20"
- "MA20 X.XX" → "MA20 FRESH_MA20"
- "MA20 (X.XX)" → "MA20 (FRESH_MA20)"
- "MA20 = X.XX" → "MA20 = FRESH_MA20"

Values are 2-decimal floats. Conservative: only replace when value is in
expected range (50 < X < 1000) and matches known stale values.
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'data' / 'dsa_hk.db'

# Match MA{N} followed by optional $ and a number (any context)
# Use word boundary on left so we don't match things like "$XMA20" or "XMA20" (rare but possible)
MA_PATTERN = re.compile(
    r'(MA(20|50|100|200))\s*([$＝=]?\s*)\(?(\d+\.?\d*)\)?',
    re.IGNORECASE
)


def update_one(conn, code, report_date, snap, full_md):
    """Update MA references in full_md using snap ma20/50/100/200. Returns (new_md, num_replacements)."""
    if not full_md or not snap:
        return full_md, 0

    def repl(m):
        ma_name = m.group(1).upper()  # MA20, MA50, etc.
        ma_n = int(m.group(2))
        prefix = m.group(3) or ''  # optional $ or = or empty
        old_val = float(m.group(4))
        key = f'ma{ma_n}'
        new_val = snap.get(key)
        if new_val is None or abs(new_val - old_val) < 0.01:
            return m.group(0)  # no change
        # Format: "MA20 $X.XX" or "MA20 X.XX" preserving original prefix style
        return f'{ma_name}{prefix}{new_val:.2f}'

    new_md, n = MA_PATTERN.subn(repl, full_md)
    return new_md, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dates', nargs='+', default=['2026-07-22', '2026-07-23'])
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    total_updated = 0
    total_replacements = 0
    for d in args.dates:
        cur.execute('SELECT code, full_md, data_snapshot_json FROM daily_report WHERE report_date=? AND full_md NOT LIKE "%LLM narrative 暫停%"', (d,))
        rows = cur.fetchall()
        n_updated = 0
        n_repl = 0
        for code, full_md, snap_str in rows:
            try:
                snap = json.loads(snap_str) if snap_str else {}
            except Exception:
                continue
            new_md, repls = update_one(conn, code, d, snap, full_md)
            if repls > 0:
                if not args.dry_run:
                    cur.execute('UPDATE daily_report SET full_md=? WHERE report_date=? AND code=?', (new_md, d, code))
                n_updated += 1
                n_repl += repls
        conn.commit()
        print(f'{d}: {n_updated} records updated, {n_repl} MA replacements')
        total_updated += n_updated
        total_replacements += n_repl
    print(f'Total: {total_updated} records, {total_replacements} MA replacements')


if __name__ == '__main__':
    main()
