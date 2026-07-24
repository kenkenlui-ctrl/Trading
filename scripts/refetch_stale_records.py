#!/usr/bin/env python3
"""
Re-fetch all daily_report records with stale 7/7 data_as_of (2026-07-25 07:25 HKT).

Bug: 7/24 daily run (and some 7/22/7/23 records) ended up with data_as_of=2026-07-07
instead of the correct report date. Root cause: the env var propagation or
data_fetcher override logic had a bug. Now we re-fetch with proper override.

This script:
1. Finds all records with data_as_of='2026-07-07 ...' (stale)
2. For each, sets DSA_REPORT_DATE_OVERRIDE=<report_date>
3. Re-fetches snapshot
4. Updates data_snapshot_json with fresh data
5. Re-runs MA update + narrative MA regex
"""
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'data' / 'dsa_hk.db'
sys.path.insert(0, str(PROJECT_ROOT))

import yfinance as yf


def fetch_fresh_snapshot(code, report_date):
    """Fetch snapshot with proper override env set."""
    os.environ['DSA_REPORT_DATE_OVERRIDE'] = report_date
    from src.data_fetcher import fetch_snapshot
    return fetch_snapshot(code)


def _hk_zfill(code: str) -> str:
    if code.endswith('.HK'):
        num = code.split('.')[0]
        return f'{num.zfill(4)}.HK'
    return code


def fetch_fresh_ma(code, report_date):
    """Fetch MA20/50/100/200 as-of report_date using yfinance history."""
    try:
        yf_sym = _hk_zfill(code)
        from datetime import datetime, timedelta
        target = datetime.strptime(report_date, '%Y-%m-%d')
        start = target - timedelta(days=365)
        end = target + timedelta(days=1)
        h = yf.Ticker(yf_sym).history(start=start.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d'))
        if h.empty:
            return {}
        h = h[h.index.strftime('%Y-%m-%d') <= report_date]
        if len(h) < 5:
            return {}
        closes = h['Close'].astype(float)
        closes = closes[closes > 0]
        def _r(x):
            v = float(x) if x is not None and not (isinstance(x, float) and (x != x)) else None
            return round(v, 2) if v is not None else None
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
    except Exception:
        return {}


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Find all stale records (data_as_of=2026-07-07)
    cur.execute("""
        SELECT id, report_date, code, data_snapshot_json
        FROM daily_report
        WHERE json_extract(data_snapshot_json, '$.data_as_of') LIKE '%2026-07-07%'
    """)
    rows = cur.fetchall()
    print(f'Found {len(rows)} stale records (data_as_of=2026-07-07)')

    updated = 0
    failed = 0
    start = time.time()
    for rid, report_date, code, snap_str in rows:
        try:
            # Re-fetch snapshot
            fresh = fetch_fresh_snapshot(code, report_date)
            if not fresh:
                failed += 1
                continue
            # Re-fetch MA
            fresh_ma = fetch_fresh_ma(code, report_date)
            if fresh_ma:
                fresh['ma20'] = fresh_ma.get('ma20')
                fresh['ma50'] = fresh_ma.get('ma50')
                fresh['ma100'] = fresh_ma.get('ma100')
                fresh['ma200'] = fresh_ma.get('ma200')
            # Update DB
            cur2 = conn.cursor()
            cur2.execute('UPDATE daily_report SET data_snapshot_json=? WHERE id=?',
                         (json.dumps(fresh, ensure_ascii=False), rid))
            updated += 1
            if updated % 20 == 0:
                conn.commit()
                print(f'  {updated}/{len(rows)} updated ({time.time()-start:.0f}s)')
        except Exception as e:
            print(f'  FAIL {report_date} {code}: {e}')
            failed += 1
    conn.commit()
    print(f'Done in {time.time()-start:.0f}s: updated={updated} failed={failed}')


if __name__ == '__main__':
    main()
