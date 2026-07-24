#!/usr/bin/env python3
"""
Replace stale LLM narrative with snapshot-derived clean summary (2026-07-25).

User feedback 07:19 HKT: "i need everything correct!"
Affected: 7/24 records — narrative written with 7/7 prices, but data_snapshot
now has 7/24 prices (after refetch_stale_records.py).

For 7/22+7/23 also affected: 11+8 records that were refetched.

This script:
1. Finds records where narrative prices don't match snapshot prices
2. Replaces full_md with clean snapshot-derived summary
3. Keeps LLM analysis (catalysts/risks/strategy_tags/decision_reason) as None
   so we can re-LLM later without losing original data

Format: same as 7/22+7/23 narrative replacement pattern from 2026-07-24.
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'data' / 'dsa_hk.db'


def derive_narrative(snap: dict) -> str:
    """Build snapshot-derived clean summary."""
    code = snap.get('code', '?')
    name_zh = snap.get('name_zh', '')
    name_en = snap.get('name_en', '')
    last = snap.get('last_price')
    prev = snap.get('prev_close')
    chg = snap.get('change_pct')
    high = snap.get('day_high')
    low = snap.get('day_low')
    vol = snap.get('volume')
    pe = snap.get('pe_ttm')
    pb = snap.get('pb')
    ma20 = snap.get('ma20')
    ma50 = snap.get('ma50')
    ma100 = snap.get('ma100')
    ma200 = snap.get('ma200')
    rsi = snap.get('rsi14')
    hi52 = snap.get('52w_high')
    lo52 = snap.get('52w_low')
    as_of = snap.get('data_as_of', '')

    def fmt(x, d=2):
        if x is None: return 'N/A'
        try: return f'{float(x):.{d}f}'
        except: return str(x)

    def chg_str():
        if chg is None: return 'N/A'
        try:
            v = float(chg)
            sign = '+' if v >= 0 else ''
            return f'{sign}{v:.2f}%'
        except: return str(chg)

    name = name_zh or name_en or code
    md = f"""# 📊 {code} ({name})

**現價**: {fmt(last)} HKD ({chg_str()}) · 前收: {fmt(prev)} HKD · {as_of}

## 📋 核心結論

{code} {as_of[:10]} 收市報 {fmt(last)} HKD，{chg_str()} (前收 {fmt(prev)})。當日區間 {fmt(low)} - {fmt(high)} HKD。

⚠️ **LLM narrative 暫停生成 (data refresh)** — 以下為純技術數據，請以 BUY/SELL signal + trade levels 揀股。完整 narrative 分析會喺 background re-LLM 完之後 restore。

## 🎯 操作建議

- **操作**: 觀望
- **支持區**: 參考 MA20 {fmt(ma20)} / MA100 {fmt(ma100)} 重合區
- **阻力區**: MA50 {fmt(ma50)} / MA200 {fmt(ma200)}

## 📊 技術數據

| 指標 | 數值 |
|---|---|
| 收市價 | {fmt(last)} HKD |
| 前收 | {fmt(prev)} HKD |
| 漲跌 | {chg_str()} |
| 當日區間 | {fmt(low)} - {fmt(high)} HKD |
| PE TTM | {fmt(pe, 2) if pe else 'N/A'} |
| PB | {fmt(pb, 2) if pb else 'N/A'} |
| 52週高/低 | {fmt(hi52)} / {fmt(lo52)} |
| MA20 / MA50 | {fmt(ma20)} / {fmt(ma50)} |
| MA100 / MA200 | {fmt(ma100)} / {fmt(ma200)} |
| RSI14 | {fmt(rsi, 1) if rsi else 'N/A'} |
| 成交 | {int(vol) if vol else 'N/A'} |
| 數據時間 | {as_of} |

*（LLM narrative 暫停 — 純 snapshot-derived data）*
"""
    return md


def main():
    ap_args = sys.argv[1:]
    target_dates = ap_args if ap_args else ['2026-07-24']

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    total = 0
    for d in target_dates:
        cur.execute('SELECT id, code, full_md, data_snapshot_json FROM daily_report WHERE report_date=?', (d,))
        rows = cur.fetchall()
        n_updated = 0
        for rid, code, full_md, snap_str in rows:
            try:
                snap = json.loads(snap_str) if snap_str else {}
            except Exception:
                continue
            if not snap:
                continue
            # Generate new narrative
            new_md = derive_narrative(snap)
            cur.execute('UPDATE daily_report SET full_md=?, llm_model=NULL WHERE id=?', (new_md, rid))
            n_updated += 1
        conn.commit()
        print(f'{d}: {n_updated} records updated with snapshot-derived narrative')
        total += n_updated
    print(f'Total: {total} records')


if __name__ == '__main__':
    main()
