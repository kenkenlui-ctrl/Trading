#!/usr/bin/env python3
"""
Fast backfill for retrospective dates — skip LLM, use only data fetcher.

Phase 1 (data fetch): for each (date, code), fetch closing data via yfinance (US)
or futu kline (HK retrospective). No LLM call.
Phase 2 (narrative): user runs snapshot_derive_narrative.py --date X to fill
LLM-narrative slot with snapshot-derived clean summary.

Speed: 1-2s/record (no LLM rate limit) vs 30-60s/record (LLM call).
~10-20x faster than LLM-backfill for retrospective dates.

User: skip LLM trades analysis depth for backfill speed.
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
US_UNIVERSE_FILE = PROJECT_ROOT / 'us_universe_200.json'

os.environ.setdefault('FT_LOG_LEVEL', 'ERROR')


def _r(x):
    if x is None:
        return None
    return round(float(x), 2)


def fetch_yfinance_close(code: str, target_date: str):
    """Fetch closing data from yfinance history (US path)."""
    try:
        import yfinance as yf
        end = (datetime.strptime(target_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        start = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=30)).strftime('%Y-%m-%d')
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
        }
    except Exception as e:
        return None


def fetch_futu_kline_close(code: str, target_date: str):
    """Fetch closing data from futu OpenD kline (HK path, shared connection)."""
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


def save_record(cur, code: str, target_date: str, snap: dict):
    """Save a record to daily_report (no LLM narrative)."""
    full_md = f"""# {code} {target_date} (snapshot-only, no LLM)

## 價格數據 (snapshot)
- 收市價: {snap['last_price']}
- 前收: {snap['prev_close']}
- 變化: {snap['change_pct']}%
- 開盤: {snap['open']}
- 最高: {snap['day_high']}
- 最低: {snap['day_low']}
- 成交量: {snap['volume']}

_此記錄係 fast_backfill.py 跳過 LLM 直接 save 嘅 retrospective record。LLM narrative 將由 snapshot_derive_narrative.py 補上。_
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
            ?, ?, '[]', ?, 'fast_backfill',
            '{}', 'long',
            NULL, NULL, '{}',
            NULL, NULL, NULL,
            NULL, 'fast_backfill: skipped LLM', NULL)
    """, (code, target_date, full_md, full_md, json.dumps(snap, ensure_ascii=False)))


def backfill_date(cur, target_date: str, max_workers: int = 8):
    """Backfill all US + HK codes for target_date (no LLM)."""
    with open(US_UNIVERSE_FILE) as f:
        us_codes = json.load(f)
    with open(HK_UNIVERSE_FILE) as f:
        hk_codes = json.load(f)
    print(f"  US: {len(us_codes)} codes, HK: {len(hk_codes)} codes")

    ok = fail = 0

    def fetch_us(code):
        snap = fetch_yfinance_close(code, target_date)
        if snap:
            return ('US', code, snap)
        return ('US', code, None)

    def fetch_hk(code):
        snap = fetch_futu_kline_close(code, target_date)
        if snap:
            return ('HK', code, snap)
        return ('HK', code, None)

    # Parallel fetch
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = []
        for c in us_codes:
            futs.append(ex.submit(fetch_us, c))
        for c in hk_codes:
            futs.append(ex.submit(fetch_hk, c))
        for i, fut in enumerate(as_completed(futs), 1):
            kind, code, snap = fut.result()
            if snap:
                save_record(cur, code, target_date, snap)
                ok += 1
            else:
                fail += 1
            if i % 50 == 0:
                conn.commit()
                print(f"  [{i}/{len(futs)}] ok={ok} fail={fail}")
    conn.commit()
    print(f"  Done: ok={ok} fail={fail}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', action='append', required=True, help='Target date (can repeat)')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    for d in args.date:
        print(f"\n=== Backfilling {d} ===")
        backfill_date(cur, d, max_workers=args.workers)
        conn.commit()
    conn.close()
    print(f"\nAll dates done")
