"""Backtest orchestrator.
For each (date, ticker), fetch historical snapshot → LLM signal → store.
Then compute hit rate at 1D/1W/1M horizons.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from statistics import mean, median
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analyzer import analyze, render_summary_md, render_report_md
from src.backtest_fetcher import fetch_historical_snapshot
from src.news_fetcher import fetch_news

DB = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backtest")
logger.setLevel(logging.INFO)


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            report_date TEXT NOT NULL,
            as_of_date TEXT NOT NULL,
            operation_advice TEXT,
            score INTEGER,
            sentiment TEXT,
            trend TEXT,
            score_breakdown_json TEXT,
            key_levels_json TEXT,
            last_price REAL,
            target_price REAL,
            stop_loss REAL,
            entry_zone TEXT,
            data_snapshot_json TEXT,
            summary_md TEXT,
            llm_model TEXT,
            UNIQUE(code, as_of_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS bt_date ON backtest_results(as_of_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS bt_code ON backtest_results(code)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            as_of_date TEXT NOT NULL,
            horizon TEXT NOT NULL,
            target_date TEXT NOT NULL,
            price_at_signal REAL,
            price_at_target REAL,
            pct_change REAL,
            signal_correct INTEGER,
            UNIQUE(code, as_of_date, horizon)
        )
    """)
    conn.commit()


def get_codes() -> list[str]:
    """Return all 400 codes in HK+US universe."""
    hk = json.load(open("/Users/kenken/Documents/dsa-hk/hk_universe_200.json"))
    us = json.load(open("/Users/kenken/Documents/dsa-hk/us_universe_200.json"))
    return sorted(set(hk) | set(us))


def get_already_done(date: str) -> set[str]:
    """Return set of codes already in backtest_results for this date."""
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT code FROM backtest_results WHERE as_of_date=?", (date,)).fetchall()
    conn.close()
    return {r[0] for r in rows}


def process_one(code: str, as_of_date: str) -> tuple[str, str]:
    """Process one (ticker, date). Returns (code, status)."""
    try:
        snap = fetch_historical_snapshot(code, as_of_date)
        if not snap:
            return code, "no-snapshot"
        # Skip news fetch for backtest — historical news not available + adds 5-10s latency
        result = analyze(
            code=code,
            name=snap.get("name_zh") or code,
            snapshot=snap,
            news=[],
            language="zh-Hant",
        )
        if result is None:
            return code, "analyze-none"
        # Render markdown
        summary_md = render_summary_md(result, language="zh-Hant")
        # Save to DB
        conn = sqlite3.connect(DB)
        ensure_table(conn)
        conn.execute("""
            INSERT OR REPLACE INTO backtest_results
            (code, report_date, as_of_date, operation_advice, score, sentiment, trend,
             score_breakdown_json, key_levels_json, last_price, target_price, stop_loss,
             entry_zone, data_snapshot_json, summary_md, llm_model)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            code, as_of_date, as_of_date, result.operation_advice, result.score,
            result.sentiment, result.trend,
            json.dumps(result.score_breakdown or {}, ensure_ascii=False),
            json.dumps(result.key_levels or {}, ensure_ascii=False),
            snap.get("last_price"),
            getattr(result, "target_price", None),
            getattr(result, "stop_loss", None),
            getattr(result, "entry_zone", None),
            json.dumps(snap, ensure_ascii=False, default=str),
            summary_md,
            result.llm_model,
        ))
        conn.commit()
        conn.close()
        return code, f"OK op={result.operation_advice} score={result.score}"
    except Exception as e:
        return code, f"ERR {type(e).__name__}: {e}"


def run_chunk(codes: list[str], dates: list[str], workers: int = 12) -> dict:
    """Run one chunk of (codes × dates). Returns stats."""
    tasks = [(c, d) for d in dates for c in codes]
    total = len(tasks)
    ok = 0; fail = 0; errors = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_one, c, d): (c, d) for c, d in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            code, status = fut.result()
            if status.startswith("OK"):
                ok += 1
            else:
                fail += 1
                if len(errors) < 5:
                    errors.append(f"{code}@{status.split(' ',1)[0]}: {status[:80]}")
            if i % 100 == 0:
                elapsed = time.time() - t0
                rate = i / elapsed
                eta = (total - i) / rate if rate > 0 else 0
                logger.info(f"  [{os.getpid()}] {i}/{total} ok={ok} fail={fail} {rate:.1f}/s ETA {eta:.0f}s")
    elapsed = time.time() - t0
    logger.info(f"[{os.getpid()}] DONE: {ok}/{total} ok in {elapsed:.0f}s ({ok/elapsed:.1f}/s)")
    return {"ok": ok, "fail": fail, "errors": errors, "elapsed": elapsed, "total": total}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", nargs="+", required=True, help="Dates YYYY-MM-DD")
    parser.add_argument("--codes", help="Optional: file with codes (one per line). Default: all 400.")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--chunk-id", default="main")
    parser.add_argument("--skip-done", action="store_true", help="Skip codes already in DB")
    args = parser.parse_args()

    if args.codes:
        # Accept either JSON list file or one-code-per-line file
        import json as _json
        try:
            with open(args.codes) as f:
                raw = f.read().strip()
            if raw.startswith("["):
                codes = _json.loads(raw)
            else:
                codes = [c.strip() for c in raw.splitlines() if c.strip()]
        except Exception as e:
            logger.error(f"Failed to read codes file {args.codes}: {e}")
            return 1
    else:
        codes = get_codes()

    if args.skip_done:
        # Filter out (code, date) combos already done
        new_tasks = []
        skipped = 0
        for d in args.dates:
            done = get_already_done(d)
            for c in codes:
                if c in done:
                    skipped += 1
                else:
                    new_tasks.append((c, d))
        logger.info(f"[{args.chunk_id}] {len(codes)} codes × {len(args.dates)} dates. Skipped {skipped} already-done. Remaining {len(new_tasks)} tasks.")
        if not new_tasks:
            logger.info("Nothing to do!")
            return 0
        # Re-organize: run per date
        for d in args.dates:
            done = get_already_done(d)
            remaining = [c for c in codes if c not in done]
            if remaining:
                logger.info(f"[{args.chunk_id}] {d}: {len(remaining)} remaining")
                run_chunk(remaining, [d], workers=args.workers)
        return 0

    logger.info(f"[{args.chunk_id}] {len(codes)} codes × {len(args.dates)} dates = {len(codes) * len(args.dates)} tasks")
    run_chunk(codes, args.dates, workers=args.workers)


if __name__ == "__main__":
    main()
