#!/usr/bin/env python3
"""Retry HK records for dates where HK phase failed in fast_backfill.

Uses futu OpenD kline (single shared connection per code) to fill missing HK records.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / 'data' / 'dsa_hk.db'
HK_UNIVERSE_FILE = PROJECT_ROOT / 'hk_universe_200.json'

os.environ.setdefault('FT_LOG_LEVEL', 'ERROR')


def _r(x):
    if x is None:
        return None
    return round(float(x), 2)


def fetch_futu_kline_close(code: str, target_date: str):
    """Fetch closing data from futu OpenD kline."""
    try:
        from futu import OpenQuoteContext, KLType, RET_OK
        import pandas as pd
        digits = code.split('.')[0].zfill(5)
        futu_code = f'HK.{digits}'
        ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        try:
            end = (datetime.strptime(target_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            start = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=10)).strftime('%Y-%m-%d')
            ret, klines, *_ = ctx.request_history_kline(futu_code, start=start, end=end, ktype=KLType.K_DAY)
            if ret != RET_OK or not isinstance(klines, pd.DataFrame) or klines.empty:
                return None
            target_row = None
            target_pos = None
            for pos, (_, row) in enumerate(klines.iterrows()):
                tk = row.get('time_key', '')
                if isinstance(tk, str) and tk.startswith(target_date):
                    target_row = row
                    target_pos = pos
                    break
            if target_row is None:
                return None
            prev_close = None
            if target_pos is not None and target_pos > 0:
                prev_close = _r(klines.iloc[target_pos - 1]['close'])
            last_price = _r(target_row['close'])
            if last_price is None:
                return None
            change_pct = round(
                (last_price - prev_close) / prev_close * 100, 2
            ) if prev_close else None
            return {
                'open': _r(target_row['open']),
                'day_high': _r(target_row['high']),
                'day_high': _r(target_row['high']),
                'day_low': _r(target_row['low']),
                'last_price': last_price,
                'prev_close': prev_close,
                'change_pct': change_pct,
                'volume': int(target_row['volume']) if target_row['volume'] else 0,
            }
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    except Exception:
        return None


def fetch_yfinance_hk_close(code: str, target_date: str):
    """Fetch HK closing data from yfinance history (4-digit format, fallback for futu miss)."""
    try:
        import yfinance as yf
        # yfinance HK format: 07709.HK (already correct)
        end = (datetime.strptime(target_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        start = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=10)).strftime('%Y-%m-%d')
        t = yf.Ticker(code)
        hist = t.history(start=start, end=end, auto_adjust=False)
        if hist is None or hist.empty:
            return None
        target_row = None
        target_pos = None
        for pos, (idx, row) in enumerate(hist.iterrows()):
            idx_str = idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)[:10]
            if idx_str == target_date:
                target_row = row
                target_pos = pos
                break
        if target_row is None:
            return None
        prev_close = None
        if target_pos is not None and target_pos > 0:
            prev_close = _r(hist.iloc[target_pos - 1]['Close'])
        last_price = _r(target_row['Close'])
        if last_price is None:
            return None
        change_pct = round(
            (last_price - prev_close) / prev_close * 100, 2
        ) if prev_close else None
        return {
            'open': _r(target_row['Open']),
            'day_high': _r(target_row['High']),
            'day_low': _r(target_row['Low']),
            'last_price': last_price,
            'prev_close': prev_close,
            'change_pct': change_pct,
            'volume': int(target_row['Volume']) if target_row['Volume'] else 0,
            'source': 'yfinance-history-fallback',
        }
    except Exception:
        return None


def fetch_hk_with_fallback(code: str, target_date: str):
    """Try futu first, then yfinance HK history as fallback."""
    snap = fetch_futu_kline_close(code, target_date)
    if snap:
        snap['source'] = 'futu-history'
        return snap
    snap = fetch_yfinance_hk_close(code, target_date)
    if snap:
        return snap
    return None


def save_record(cur, code: str, target_date: str, snap: dict):
    full_md = f"""# {code} {target_date} (snapshot-only, no LLM)

## 價格數據
- 收市價: {snap['last_price']}
- 前收: {snap['prev_close']}
- 變化: {snap['change_pct']}%
- 開盤: {snap['open']}
- 最高: {snap['day_high']}
- 最低: {snap['day_low']}
- 成交量: {snap['volume']}

_此記錄係 retry_hk_backfill.py 用 futu kline 拎 retrospective closing data. LLM narrative 將由 snapshot_derive_narrative.py 補上._
"""
    cur.execute("""
        INSERT OR REPLACE INTO daily_report (
            code, report_date, score, sentiment, trend, operation_advice,
            summary_md, full_md, news_json, data_snapshot_json, llm_model,
            score_breakdown_json, trade_direction,
            support_zone, resistance_zone, key_levels_json,
            entry_zone, stop_loss, target_price,
            llm_original_op, decision_reason, signal_score
        ) VALUES (?, ?, NULL, NULL, NULL, NULL,
            ?, ?, '[]', ?, 'retry_hk_backfill',
            '{}', 'long',
            NULL, NULL, '{}',
            NULL, NULL, NULL,
            NULL, 'retry_hk_backfill: HK snapshot only', NULL)
    """, (code, target_date, full_md, full_md, json.dumps(snap, ensure_ascii=False)))


def retry_hk_for_date(cur, target_date: str, max_workers: int = 4):
    """Retry HK codes for target_date (skip already-saved)."""
    with open(HK_UNIVERSE_FILE) as f:
        hk_codes = json.load(f)
    # Filter out codes already saved
    cur.execute("SELECT code FROM daily_report WHERE report_date=? AND code LIKE '%.HK'", (target_date,))
    existing = {r[0] for r in cur.fetchall()}
    missing = [c for c in hk_codes if c not in existing]
    print(f"  {target_date}: {len(existing)} existing, {len(missing)} missing")
    if not missing:
        return 0, 0

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_hk_with_fallback, c, target_date): c for c in missing}
        for fut in as_completed(futs):
            snap = fut.result()
            if snap:
                save_record(cur, futs[fut], target_date, snap)
                ok += 1
            else:
                fail += 1
    return ok, fail


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', action='append', required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    total_ok = 0
    for d in args.date:
        print(f"\n=== Retry HK {d} ===")
        ok, fail = retry_hk_for_date(cur, d, max_workers=args.workers)
        print(f"  ok={ok} fail={fail}")
        total_ok += ok
        conn.commit()
    conn.close()
    print(f"\nTotal HK added: {total_ok}")
