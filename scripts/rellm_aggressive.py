#!/usr/bin/env python3
"""
Aggressive parallel re-LLM 7/22 + 7/23 (2026-07-24).

Strategy: 5 workers + 3 retries on fail. Accept 40-60% fail rate at peak,
retry-while-rest pattern for higher effective throughput. No data loss — every
record must succeed or be flagged.
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'data' / 'dsa_hk.db'
LOGS_DIR = PROJECT_ROOT / 'logs'
LOGS_DIR.mkdir(exist_ok=True)
# Ensure 'src' package importable when run as background script
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.FileHandler(LOGS_DIR / 'rellm_aggressive.log'), logging.StreamHandler()],
)
log = logging.getLogger('rellm')

os.environ.setdefault('DSA_LLM_MAX_TOKENS', '8000')

from src.config import get_config
from src.analyzer import analyze
import litellm

cfg = get_config()
if cfg.minimax_api_key:
    os.environ['MiniMax_API_KEY'] = cfg.minimax_api_key


def load_records(target_dates, limit_per_date=0):
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
        cnt = 0
        for code, name, snap_str, ex_score, ex_op in cur.fetchall():
            if limit_per_date and cnt >= limit_per_date:
                break
            try:
                snap = json.loads(snap_str) if snap_str else {}
            except Exception:
                snap = {}
            records.append((date, code, name, snap, ex_score, ex_op))
            cnt += 1
    conn.close()
    return records


def rellm_one(date, code, name, snap, ex_score=None, ex_op=None, retries=3):
    """Call LLM with retry. Returns (code, date, result, elapsed_total, error)."""
    t0 = time.time()
    last_err = None
    for attempt in range(retries):
        try:
            res = analyze(code=code, name=name, snapshot=snap, news=[])
            elapsed = time.time() - t0
            if res is None:
                last_err = 'analyze returned None'
                time.sleep(2 + attempt * 3)  # backoff
                continue
            return code, date, res, elapsed, None
        except Exception as e:
            last_err = f'{type(e).__name__}: {str(e)[:200]}'
            time.sleep(2 + attempt * 5)  # backoff
    return code, date, None, time.time() - t0, last_err


def save_result(date, code, result):
    """Save LLM result. Returns True on success."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        rd = result.to_dict() if hasattr(result, 'to_dict') else result.__dict__
        # Render full_md via render_report_md if not present
        full_md = rd.get('full_md')
        if not full_md:
            try:
                from src.analyzer import render_report_md, AnalysisResult
                ar = AnalysisResult(
                    code=code,
                    score=rd.get('score') or 0,
                    sentiment=rd.get('sentiment', ''),
                    trend=rd.get('trend', ''),
                    operation_advice=rd.get('operation_advice', ''),
                    confidence=rd.get('confidence', ''),
                    summary=rd.get('summary', ''),
                    score_breakdown=rd.get('score_breakdown') or {},
                    trade_direction=rd.get('trade_direction', 'both'),
                    entry_zone=rd.get('entry_zone'),
                    stop_loss=rd.get('stop_loss'),
                    target_price=rd.get('target_price'),
                    support_zone=rd.get('support_zone'),
                    resistance_zone=rd.get('resistance_zone'),
                    key_levels=rd.get('key_levels') or {},
                    catalysts=rd.get('catalysts') or [],
                    risks=rd.get('risks') or [],
                    strategy_tags=rd.get('strategy_tags') or [],
                    reasoning=rd.get('reasoning', ''),
                    llm_model=rd.get('llm_model', 'MiniMax-M3'),
                )
                full_md = render_report_md(ar, snapshot=None, language='zh-Hant')
            except Exception as e:
                full_md = rd.get('reasoning') or rd.get('summary', '')

        updates = {
            'summary_md': rd.get('summary', ''),
            'full_md': full_md,
            'operation_advice': rd.get('operation_advice', ''),
            'score': rd.get('score'),
            'signal_score': rd.get('score'),
            'sentiment': rd.get('sentiment', ''),
            'trend': rd.get('trend', ''),
            'score_breakdown_json': json.dumps(rd.get('score_breakdown') or {}, ensure_ascii=False),
            'trade_direction': rd.get('trade_direction', 'both'),
            'support_zone': rd.get('support_zone'),
            'resistance_zone': rd.get('resistance_zone'),
            'key_levels_json': json.dumps(rd.get('key_levels') or {}, ensure_ascii=False),
            'entry_zone': rd.get('entry_zone'),
            'stop_loss': rd.get('stop_loss'),
            'target_price': rd.get('target_price'),
            'llm_model': rd.get('llm_model', 'MiniMax-M3'),
            'llm_original_op': rd.get('operation_advice', ''),
            'decision_reason': rd.get('reasoning', ''),
        }
        set_clause = ', '.join(f'{k}=?' for k in updates)
        vals = list(updates.values()) + [date, code]
        cur.execute(f'UPDATE daily_report SET {set_clause} WHERE report_date=? AND code=?', vals)
        conn.commit()
        return True
    except Exception as e:
        log.error(f'save {date} {code} fail: {e}')
        return False
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--dates', nargs='+', default=['2026-07-22', '2026-07-23'])
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--retries', type=int, default=3)
    args = ap.parse_args()

    log.info(f'=== AGGRESSIVE re-LLM start: dates={args.dates} workers={args.workers} retries={args.retries} ===')
    records = load_records(args.dates, args.limit)
    log.info(f'Loaded {len(records)} records')

    start = time.time()
    done = 0
    failed_final = []
    saved = 0
    t_llm_total = 0
    t_save_total = 0

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(rellm_one, *r, retries=args.retries): r for r in records}
        for f in as_completed(futs):
            date, code, name, _, _, _ = futs[f]
            done += 1
            t_save_0 = time.time()
            try:
                c, d, result, elapsed, err = f.result()
            except Exception as e:
                c, d, result, elapsed, err = code, date, None, 0, f'{type(e).__name__}: {str(e)[:100]}'
            t_llm_total += elapsed
            if result is None:
                failed_final.append((d, c, err or 'unknown'))
                log.warning(f'[{done}/{len(records)}] FAIL {d} {c} ({elapsed:.1f}s) {err}')
            else:
                if save_result(d, c, result):
                    saved += 1
                    op = result.operation_advice if hasattr(result, 'operation_advice') else '?'
                    score = result.score if hasattr(result, 'score') else '?'
                    log.info(f'[{done}/{len(records)}] OK   {d} {c} score={score} op={op} ({elapsed:.1f}s)')
                else:
                    failed_final.append((d, c, 'save fail'))
            t_save_total += time.time() - t_save_0
            if done % 10 == 0:
                tot = time.time() - start
                rate = done / tot if tot > 0 else 0
                eta = (len(records) - done) / rate if rate > 0 else 0
                avg = t_llm_total / done if done > 0 else 0
                log.info(f'PROGRESS: {done}/{len(records)} saved={saved} failed={len(failed_final)} | {rate:.2f}/s avg_llm={avg:.1f}s | ETA {eta/60:.1f}min')

    tot = time.time() - start
    log.info(f'=== DONE: {done} records, {saved} saved, {len(failed_final)} failed in {tot/60:.1f}min ===')
    if failed_final:
        log.info(f'Failed: {failed_final[:20]}{"..." if len(failed_final) > 20 else ""}')


if __name__ == '__main__':
    main()
