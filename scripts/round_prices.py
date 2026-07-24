#!/usr/bin/env python3
"""
Round all price fields in data_snapshot_json to 2 decimals (2026-07-24 fix).

User complaint: TSLA 7/23 page rendered "319.69000244140625 HKD" instead of
"319.69 HKD". Root cause: yfinance returns IEEE 754 doubles, and
fetch_snapshot() stored them directly into data_snapshot_json without rounding.

This script:
1. Round all price fields (last_price, prev_close, day_high, day_low, open,
   ma20/50/100/200, rsi14, 52w_high/low, pe_ttm, pb, dividend_yield) in
   data_snapshot_json to 2 decimals (price) or 4 decimals (ratios).
2. Re-render `full_md` markdown: replace any raw float like
   "319.69000244140625" with rounded 2-decimal version.

Affects all daily_report records — typically 7/15 to 7/23.
Run: python3 scripts/round_prices.py [--date 2026-07-23]
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'data' / 'dsa_hk.db'

# Price fields: round to 2 decimals
PRICE_FIELDS_2D = {
    'last_price', 'prev_close', 'day_high', 'day_low', 'open',
    'ma20', 'ma50', 'ma100', 'ma200',
    '52w_high', '52w_low',
    'ytd_change_pct',  # display as +X.XX%
    'change_pct', 'day_range_pct',
}

# Ratio fields: round to 4 decimals (PE, PB more precision)
RATIO_FIELDS_4D = {
    'pe_ttm', 'pb', 'dividend_yield', 'vol_ratio', 'rsi14',
}

# Volumetric fields: keep as int
INT_FIELDS = {'volume', 'turnover_hkd', 'market_cap_hkd'}


def _round(x, decimals):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return round(float(x), decimals)
    return x


def round_snapshot(snap: dict) -> tuple[dict, bool]:
    """Round all numeric fields in data_snapshot_json. Returns (new_snap, changed)."""
    if not snap:
        return snap, False
    changed = False
    new = dict(snap)
    for k in list(new.keys()):
        v = new[k]
        if k in PRICE_FIELDS_2D:
            r = _round(v, 2)
        elif k in RATIO_FIELDS_4D:
            r = _round(v, 4)
        elif k in INT_FIELDS:
            if isinstance(v, float) and v.is_integer():
                r = int(v)
            else:
                r = v
        else:
            r = v
        if r != v:
            new[k] = r
            changed = True
    return new, changed


# Match floats with > 2 decimals (e.g. 319.69000244140625, 552.3300170898438)
# but NOT integers (no decimal point) and NOT already-rounded (e.g. 12.34)
FLOAT_RAW_RE = re.compile(r'(?<![.\d])(\d+\.\d{3,})(?![\d])')


def _fmt_2d(m: re.Match) -> str:
    """Round matched float to 2 decimals, preserve integer part."""
    v = float(m.group(1))
    return f'{v:.2f}'


def clean_full_md(full_md: str) -> tuple[str, int]:
    """Replace any > 2-decimal float in full_md with rounded 2-decimal version.
    Returns (new_md, num_replacements)."""
    if not full_md:
        return full_md, 0
    new, n = FLOAT_RAW_RE.subn(_fmt_2d, full_md)
    return new, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='Only patch this report_date (e.g. 2026-07-23)')
    ap.add_argument('--dry-run', action='store_true', help='Print what would change without writing')
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if args.date:
        cur.execute('SELECT id, code, report_date, data_snapshot_json, full_md FROM daily_report WHERE report_date=?', (args.date,))
    else:
        cur.execute('SELECT id, code, report_date, data_snapshot_json, full_md FROM daily_report WHERE data_snapshot_json IS NOT NULL')
    rows = cur.fetchall()

    snap_fixed = 0
    md_fixed = 0
    total_md_repl = 0
    for rid, code, rdate, snap_str, full_md in rows:
        updates = []
        if snap_str:
            try:
                snap = json.loads(snap_str)
                new_snap, snap_changed = round_snapshot(snap)
                if snap_changed:
                    updates.append(('data_snapshot_json', json.dumps(new_snap, ensure_ascii=False)))
                    snap_fixed += 1
            except Exception as e:
                print(f'WARN {rdate} {code} parse snap fail: {e}', file=sys.stderr)
        if full_md:
            new_md, n_repl = clean_full_md(full_md)
            if n_repl > 0:
                updates.append(('full_md', new_md))
                md_fixed += 1
                total_md_repl += n_repl
        if updates:
            if args.dry_run:
                print(f'  {rdate} {code}: would update {[k for k,_ in updates]} (md_repl={total_md_repl if updates and "full_md" in dict(updates) else 0})')
            else:
                set_clause = ', '.join(f'{k}=?' for k, _ in updates)
                vals = [v for _, v in updates] + [rid]
                cur.execute(f'UPDATE daily_report SET {set_clause} WHERE id=?', vals)
    conn.commit()

    print(f'\n=== Round prices summary ===')
    print(f'  Snapshots rounded: {snap_fixed}/{len(rows)} records')
    print(f'  full_md cleanups: {md_fixed}/{len(rows)} records ({total_md_repl} raw floats replaced)')
    if args.dry_run:
        print('  (DRY RUN — no DB writes)')


if __name__ == '__main__':
    main()
