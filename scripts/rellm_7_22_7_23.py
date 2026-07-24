#!/usr/bin/env python3
"""
Re-LLM 7/22 + 7/23 narrative (background job, 2026-07-24).

User feedback: rate limit recovered, wants narrative depth restored.
Run: nohup python3 scripts/rellm_7_22_7_23.py --workers 2 > logs/rellm.log 2>&1 &

Affects all daily_report records for 7/22 + 7/23 (569 total).
Saves to DB columns: summary_md, full_md, operation_advice, score, signal_score,
score_breakdown_json, trade_direction, support_zone, resistance_zone, key_levels_json,
entry_zone, stop_loss, target_price, sentiment, trend, confidence, llm_model, reasoning.
Does NOT touch data_snapshot_json (rounding already done 2026-07-24).
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'data' / 'dsa_hk.db'
LOGS_DIR = PROJECT_ROOT / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.FileHandler(LOGS_DIR / 'rellm_7_22_7_23.log'), logging.StreamHandler()],
)
log = logging.getLogger('rellm')

# Set up env BEFORE importing analyzer
os.environ.setdefault('DSA_LLM_MAX_TOKENS', '8000')

from src.config import get_config
from src.analyzer import analyze, _extract_json
import litellm

cfg = get_config()
if cfg.minimax_api_key:
    os.environ['MiniMax_API_KEY'] = cfg.minimax_api_key


def load_records(target_dates):
    """Load all daily_report records for given dates. Returns list of (code, name, snap, existing_score, existing_op)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    records = []
    for date in target_dates:
        cur.execute('''
            SELECT t.code, t.name_zh, d.data_snapshot_json, d.score, d.operation_advice
            FROM daily_report d JOIN ticker t ON d.code = t.code
            WHERE d.report_date = ?
            ORDER BY d.signal_score DESC NULLS LAST, d.score DESC NULLS LAST
        ''', (date,))
        for code, name, snap_str, ex_score, ex_op in cur.fetchall():
            try:
                snap = json.loads(snap_str) if snap_str else {}
            except Exception:
                snap = {}
            records.append((date, code, name, snap, ex_score, ex_op))
    conn.close()
    return records


def rellm_one(date, code, name, snap, ex_score, ex_op):
    """Call LLM for one record. Returns (code, date, result_dict, elapsed_s) or (code, date, None, err)."""
    t0 = time.time()
    try:
        res = analyze(code=code, name=name, snapshot=snap, news=[])
        elapsed = time.time() - t0
        if res is None:
            return code, date, None, elapsed, 'analyze returned None'
        # Convert AnalysisResult to dict for DB
        rd = res.to_dict() if hasattr(res, 'to_dict') else res.__dict__
        return code, date, rd, elapsed, None
    except Exception as e:
        return code, date, None, time.time() - t0, f'{type(e).__name__}: {str(e)[:200]}'


def save_result(date, code, result):
    """Save LLM result to DB. Returns True if saved."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        # Map result to DB columns
        updates = {
            'summary_md': result.get('summary', ''),
            'full_md': result.get('full_md', '') or result.get('summary', ''),  # full_md sometimes not in to_dict
            'operation_advice': result.get('operation_advice', ''),
            'score': result.get('score'),
            'signal_score': result.get('score'),  # alias
            'sentiment': result.get('sentiment', ''),
            'trend': result.get('trend', ''),
            'confidence': result.get('confidence', ''),
            'score_breakdown_json': json.dumps(result.get('score_breakdown') or {}, ensure_ascii=False),
            'trade_direction': result.get('trade_direction', 'both'),
            'support_zone': result.get('support_zone'),
            'resistance_zone': result.get('resistance_zone'),
            'key_levels_json': json.dumps(result.get('key_levels') or {}, ensure_ascii=False),
            'entry_zone': result.get('entry_zone'),
            'stop_loss': result.get('stop_loss'),
            'target_price': result.get('target_price'),
            'llm_model': result.get('llm_model', 'MiniMax-M3'),
            'llm_original_op': result.get('operation_advice', ''),
            'decision_reason': result.get('reasoning', ''),
            'features_json': None,  # leave as-is
        }
        # If full_md is empty but result has reasoning, use reasoning
        if not updates['full_md']:
            updates['full_md'] = result.get('reasoning') or updates['summary_md']
        # Re-render full_md via render_report_md if not present
        if not updates['full_md']:
            try:
                from src.analyzer import render_report_md
                from dataclasses import asdict
                # Build a fresh AnalysisResult-like object
                from src.analyzer import AnalysisResult
                ar = AnalysisResult(
                    code=code,
                    score=updates['score'] or 0,
                    sentiment=updates['sentiment'],
                    trend=updates['trend'],
                    operation_advice=updates['operation_advice'],
                    confidence=updates['confidence'],
                    summary=updates['summary_md'],
                    score_breakdown=result.get('score_breakdown') or {},
                    trade_direction=updates['trade_direction'],
                    entry_zone=updates['entry_zone'],
                    stop_loss=updates['stop_loss'],
                    target_price=updates['target_price'],
                    support_zone=updates['support_zone'],
                    resistance_zone=updates['resistance_zone'],
                    key_levels=result.get('key_levels') or {},
                    catalysts=result.get('catalysts') or [],
                    risks=result.get('risks') or [],
                    strategy_tags=result.get('strategy_tags') or [],
                    reasoning=result.get('reasoning', ''),
                    llm_model=updates['llm_model'],
                )
                updates['full_md'] = render_report_md(ar, snapshot=None, language='zh-Hant')
            except Exception as e:
                log.warning(f'render_report_md failed for {code}: {e}')

        set_clause = ', '.join(f'{k}=?' for k in updates)
        vals = list(updates.values()) + [date, code]
        cur.execute(f'UPDATE daily_report SET {set_clause} WHERE report_date=? AND code=?', vals)
        conn.commit()
        return True
    except Exception as e:
        log.error(f'save_result {date} {code} fail: {e}')
        return False
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=2, help='Concurrent LLM calls (1-2 recommended)')
    ap.add_argument('--dates', nargs='+', default=['2026-07-22', '2026-07-23'])
    ap.add_argument('--limit', type=int, default=0, help='Limit records per date (0=all)')
    args = ap.parse_args()

    log.info(f'=== Re-LLM job start: dates={args.dates} workers={args.workers} ===')
    records = load_records(args.dates)
    if args.limit:
        # Take top N per date (already sorted by signal_score DESC)
        per_date = {}
        filtered = []
        for r in records:
            d = r[0]
            per_date.setdefault(d, 0)
            if per_date[d] < args.limit:
                filtered.append(r)
                per_date[d] += 1
        records = filtered
    log.info(f'Loaded {len(records)} records')

    start = time.time()
    done = 0
    failed = 0
    saved = 0
    t_save_total = 0

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(rellm_one, *r): r for r in records}
        for f in as_completed(futs):
            date, code, name, _, _, _ = futs[f]
            try:
                c, d, result, elapsed, err = f.result()
            except Exception as e:
                c, d = code, date
                result, elapsed, err = None, 0, f'{type(e).__name__}: {str(e)[:100]}'
            done += 1
            if err or result is None:
                failed += 1
                log.warning(f'[{done}/{len(records)}] FAIL {d} {c}: {err}')
            else:
                t0 = time.time()
                ok = save_result(d, c, result)
                t_save_total += time.time() - t0
                if ok:
                    saved += 1
                    log.info(f'[{done}/{len(records)}] OK   {d} {c} score={result.get("score")} op={result.get("operation_advice")} llm={elapsed:.1f}s save={time.time()-t0:.1f}s')
                else:
                    failed += 1
            # Progress every 10
            if done % 10 == 0:
                elapsed_total = time.time() - start
                rate = done / elapsed_total if elapsed_total > 0 else 0
                eta = (len(records) - done) / rate if rate > 0 else 0
                log.info(f'PROGRESS: {done}/{len(records)} done, {failed} failed, {saved} saved | {rate:.2f}/s | ETA {eta/60:.1f}min')

    total = time.time() - start
    log.info(f'=== Re-LLM job done: {done} total, {saved} saved, {failed} failed in {total/60:.1f}min ===')
    log.info(f'    LLM time: {(total - t_save_total)/60:.1f}min, save time: {t_save_total:.1f}s')


if __name__ == '__main__':
    main()
