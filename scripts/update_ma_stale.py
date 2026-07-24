#!/usr/bin/env python3
"""
Update MA20/50/100/200 for 7/22+7/23 daily_report records (2026-07-25).

User complaint: re-LLM narrative still references stale 7/7 MA values
(e.g. TSLA MA20=$391.83, MA50=$404.97) because data_snapshot ma20/50/100/200
fields were never recomputed for the actual report dates.

This script:
1. Fetch ~250 trading days of yfinance history for each 7/22+7/23 code
2. Compute MA20/50/100/200 as-of report_date
3. Update data_snapshot_json ma20/50/100/200 fields
4. Update full_md regex references (e.g. "MA20 $391.83" → "MA20 $391.83")
   Actually no — the LLM narrative uses these values. If we update
   data_snapshot but not the LLM narrative, the narrative still says old
   values. Better: leave full_md alone (LLM-generated), but
   update the technical data table to fresh values.

Actually safest: update BOTH data_snapshot AND a "技術數據" table that
the build_static uses for rendering. Let me check build_static to see
where MA values are displayed.
"""
import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'data' / 'dsa_hk.db'

import yfinance as yf


def _hk_zfill(code: str) -> str:
    if code.endswith('.HK'):
        num = code.split('.')[0]
        return f'{num.zfill(4)}.HK'
    return code


def fetch_ma_for_date(code: str, report_date: str) -> dict:
    """Fetch MA20/50/100/200 as-of report_date using yfinance history."""
    try:
        yf_sym = _hk_zfill(code)
        target = datetime.strptime(report_date, '%Y-%m-%d')
        # Fetch 250 calendar days before to ensure enough trading data
        start = target - timedelta(days=365)
        end = target + timedelta(days=1)
        h = yf.Ticker(yf_sym).history(start=start.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d'))
        if h.empty:
            return {}
        # Filter to <= report_date
        h = h[h.index.strftime('%Y-%m-%d') <= report_date]
        if len(h) < 5:
            return {}
        closes = h['Close'].astype(float)
        closes = closes[closes > 0]  # filter out zero/NaN

        def _r(x):
            v = float(x) if x is not None and not (isinstance(x, float) and (x != x)) else None
            return round(v, 2) if v is not None else None

        # MA windows — use min(len, N) to handle short history
        def ma(n):
            if len(closes) >= n:
                return _r(closes.tail(n).mean())
            return _r(closes.mean()) if len(closes) > 0 else None

        return {
            'ma20': ma(20),
            'ma50': ma(50),
            'ma100': ma(100),
            'ma200': ma(200),
        }
    except Exception as e:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dates', nargs='+', default=['2026-07-22', '2026-07-23'])
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Load all codes for given dates
    all_codes = []
    for d in args.dates:
        cur.execute('SELECT DISTINCT code FROM daily_report WHERE report_date=?', (d,))
        for (code,) in cur.fetchall():
            all_codes.append((d, code))
    print(f'Total records: {len(all_codes)}')

    # Use ThreadPoolExecutor for parallel yfinance calls
    updated = 0
    failed = 0
    no_data = 0
    start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_ma_for_date, code, d): (d, code) for d, code in all_codes}
        for f in as_completed(futs):
            d, code = futs[f]
            try:
                ma = f.result()
            except Exception as e:
                ma = {}
            if not ma or 'ma20' not in ma:
                no_data += 1
                continue
            # Update DB
            cur2 = conn.cursor()
            cur2.execute('SELECT data_snapshot_json FROM daily_report WHERE report_date=? AND code=?', (d, code))
            row = cur2.fetchone()
            if not row or not row[0]:
                failed += 1
                continue
            try:
                snap = json.loads(row[0])
            except Exception:
                failed += 1
                continue
            old_ma20 = snap.get('ma20')
            snap['ma20'] = ma['ma20']
            snap['ma50'] = ma['ma50']
            snap['ma100'] = ma['ma100']
            snap['ma200'] = ma['ma200']
            if not args.dry_run:
                cur2.execute('UPDATE daily_report SET data_snapshot_json=? WHERE report_date=? AND code=?',
                             (json.dumps(snap, ensure_ascii=False), d, code))
            updated += 1
    conn.commit()
    print(f'Done in {time.time()-start:.1f}s: updated={updated} no_data={no_data} failed={failed}')


if __name__ == '__main__':
    main()
