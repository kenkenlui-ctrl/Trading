#!/usr/bin/env python3
"""
Fix 7/27 HK records: re-fetch 7/27 close from Futu OpenD (truth source).

Uses single shared Futu connection (avoids per-code connect overhead).
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / 'data' / 'dsa_hk.db'

REPORT_DATE = '2026-07-27'
PREV_DATE = '2026-07-24'

os.environ.setdefault('FT_LOG_LEVEL', 'ERROR')


def _r(x):
    if x is None:
        return None
    return round(float(x), 2)


def main():
    from futu import OpenQuoteContext, KLType, RET_OK
    import pandas as pd

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "SELECT code FROM daily_report WHERE report_date=? AND code LIKE '%.HK' "
        "AND json_extract(data_snapshot_json, '$.data_as_of') LIKE '%2026/07/28%' ORDER BY code",
        (REPORT_DATE,),
    )
    codes = [r[0] for r in cur.fetchall()]
    print(f"Stale HK records: {len(codes)}")

    if not codes:
        print("Nothing to fix")
        return

    ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    try:
        ok = fail = 0
        for i, code in enumerate(codes, 1):
            digits = code.split('.')[0].zfill(5)
            futu_code = f'HK.{digits}'
            try:
                ret, klines, *_ = ctx.request_history_kline(
                    futu_code, start='2026-07-15', end='2026-07-28', ktype=KLType.K_DAY
                )
                if ret != RET_OK or not isinstance(klines, pd.DataFrame) or klines.empty:
                    print(f"  [{i}/{len(codes)}] [SKIP] {code}: ret={ret}")
                    fail += 1
                    continue

                # Find 7/27 row
                target_row = None
                for _, row in klines.iterrows():
                    tk = row.get('time_key', '')
                    if isinstance(tk, str) and tk.startswith(REPORT_DATE):
                        target_row = row
                        break
                if target_row is None:
                    print(f"  [{i}/{len(codes)}] [SKIP] {code}: 7/27 not in kline")
                    fail += 1
                    continue

                # Find 7/24 row
                prev_close = None
                for _, row in klines.iterrows():
                    tk = row.get('time_key', '')
                    if isinstance(tk, str) and tk.startswith(PREV_DATE):
                        prev_close = _r(row.get('close'))
                        break

                last_price = _r(target_row.get('close'))
                if last_price is None:
                    print(f"  [{i}/{len(codes)}] [SKIP] {code}: 7/27 close None")
                    fail += 1
                    continue

                change_pct = round(
                    (last_price - prev_close) / prev_close * 100, 2
                ) if prev_close else None

                # Read current snapshot
                cur.execute("SELECT data_snapshot_json FROM daily_report WHERE report_date=? AND code=?", (REPORT_DATE, code))
                row = cur.fetchone()
                if not row:
                    fail += 1
                    continue
                snap = json.loads(row[0])
                new_snap = dict(snap)
                new_snap['last_price'] = last_price
                new_snap['prev_close'] = prev_close
                new_snap['change_pct'] = change_pct
                new_snap['day_high'] = _r(target_row.get('high'))
                new_snap['day_low'] = _r(target_row.get('low'))
                new_snap['open'] = _r(target_row.get('open'))
                new_snap['volume'] = int(target_row.get('volume', 0)) if target_row.get('volume') else 0
                new_snap['data_as_of'] = f'{REPORT_DATE} 16:00 HKT (closing)'
                new_snap['source'] = 'futu-history-7-27'

                cur.execute("""
                    UPDATE daily_report
                    SET data_snapshot_json=?,
                        entry_zone=NULL,
                        stop_loss=NULL,
                        target_price=NULL,
                        support_zone=NULL,
                        resistance_zone=NULL
                    WHERE report_date=? AND code=?
                """, (json.dumps(new_snap, ensure_ascii=False), REPORT_DATE, code))
                ok += 1
                if i % 5 == 0:
                    print(f"  [{i}/{len(codes)}] ok={ok} fail={fail}")
            except Exception as e:
                print(f"  [{i}/{len(codes)}] [ERR] {code}: {type(e).__name__}: {e}")
                fail += 1
        conn.commit()
        print(f"\nDone: ok={ok} fail={fail}")
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        conn.close()


if __name__ == "__main__":
    main()
