"""Optimized daily refresh — fast path with lessons applied.

Speedups vs v1:
- 12 parallel HK workers (cached+Tencent doesn't trigger any rate limit)
- 20 parallel US workers
- Skip news fetch (LLM doesn't need it for top-line signal)
- Auto-detects source date for HK cache

Usage:
    python3 scripts/run_daily.py --date 2026-07-15
    python3 scripts/run_daily.py --date 2026-07-15 --us-only
    python3 scripts/run_daily.py --date 2026-07-15 --hk-only --hk-workers 15
"""
import os
import sys
import json
import time
import ssl
import argparse
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

ssl._create_default_https_context = ssl._create_unverified_context

PROJECT_ROOT = "/Users/kenken/Documents/dsa-hk"
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, f"{PROJECT_ROOT}/scripts")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Report date YYYY-MM-DD")
    parser.add_argument("--source", default=None, help="HK cache source date (auto-detect if None)")
    parser.add_argument("--us-workers", type=int, default=20)
    parser.add_argument("--hk-workers", type=int, default=12)
    parser.add_argument("--no-news", action="store_true", help="Skip news fetch for speed")
    parser.add_argument("--us-only", action="store_true")
    parser.add_argument("--hk-only", action="store_true")
    args = parser.parse_args()

    os.environ["DSA_REPORT_DATE_OVERRIDE"] = args.date
    os.environ["DSA_LLM_MAX_TOKENS"] = "8000"
    os.environ["DSA_PARALLEL"] = str(args.us_workers)

    from src.analyzer import analyze, render_summary_md, render_report_md
    from src.db import save_report, init_db
    from src.data_fetcher import fetch_snapshot
    from src.news_fetcher import fetch_news
    from src.ticker_loader import load_hk_tickers, load_us_tickers
    from rerun_hk_sina_tencent import (
        fetch_tencent_hk, fetch_sina_hk,
        build_today_snapshot, get_cached_snapshot,
    )

    init_db()

    # Auto-detect source date for HK cache
    if not args.source:
        conn = sqlite3.connect(f"{PROJECT_ROOT}/data/dsa_hk.db")
        row = conn.execute(
            "SELECT report_date FROM daily_report WHERE report_date < ? "
            "GROUP BY report_date ORDER BY report_date DESC LIMIT 1",
            (args.date,),
        ).fetchone()
        conn.close()
        args.source = row[0] if row else args.date

    print(f"=== Daily refresh {args.date} (HK cache: {args.source}) ===", flush=True)

    def process_us(code):
        try:
            snap = fetch_snapshot(code)
            if not snap:
                return code, "no-snapshot"
            news = []
            if not args.no_news:
                news = fetch_news(code=code, name_zh=snap.get("name_zh") or code,
                                  name_en=code, max_results=3, days=3) or []
            result = analyze(code=code, name=snap.get("name_zh") or code,
                              snapshot=snap, news=news, language="zh-Hant")
            if result is None:
                return code, "analyze-none"
            save_report(
                code=code, report_date=args.date,
                score=result.score, sentiment=result.sentiment, trend=result.trend,
                operation_advice=result.operation_advice,
                summary_md=render_summary_md(result, language="zh-Hant"),
                full_md=render_report_md(result, snap, language="zh-Hant"),
                news=news, data_snapshot=snap, llm_model=result.llm_model,
                score_breakdown_json=json.dumps(result.score_breakdown or {}, ensure_ascii=False),
                trade_direction=result.trade_direction,
                support_zone=result.support_zone, resistance_zone=result.resistance_zone,
                key_levels_json=json.dumps(result.key_levels or {}, ensure_ascii=False),
            )
            return code, f"OK score={result.score}"
        except Exception as e:
            return code, f"ERR {type(e).__name__}: {str(e)[:60]}"

    def process_hk(code):
        try:
            cached = get_cached_snapshot(code, args.source)
            if not cached:
                return code, "no-cached"
            live = fetch_tencent_hk(code) or fetch_sina_hk(code)
            if not live or not live.get("current"):
                return code, "no-live"
            snap = build_today_snapshot(code, cached, live)
            result = analyze(code=code, name=snap.get("name_zh") or code,
                              snapshot=snap, news=None, language="zh-Hant")
            if result is None:
                return code, "analyze-none"
            save_report(
                code=code, report_date=args.date,
                score=result.score, sentiment=result.sentiment, trend=result.trend,
                operation_advice=result.operation_advice,
                summary_md=render_summary_md(result, language="zh-Hant"),
                full_md=render_report_md(result, snap, language="zh-Hant"),
                news=[], data_snapshot=snap, llm_model=result.llm_model,
                score_breakdown_json=json.dumps(result.score_breakdown or {}, ensure_ascii=False),
                trade_direction=result.trade_direction,
                support_zone=result.support_zone, resistance_zone=result.resistance_zone,
                key_levels_json=json.dumps(result.key_levels or {}, ensure_ascii=False),
            )
            return code, f"OK score={result.score}"
        except Exception as e:
            return code, f"ERR {type(e).__name__}: {str(e)[:60]}"

    if not args.hk_only:
        us_codes = load_us_tickers()
        print(f"\nPhase 1: US {len(us_codes)} ({args.us_workers} workers)", flush=True)
        t0 = time.time()
        ok = fail = 0
        with ThreadPoolExecutor(max_workers=args.us_workers) as ex:
            futs = {ex.submit(process_us, c): c for c in us_codes}
            for i, fut in enumerate(as_completed(futs), 1):
                c, status = fut.result()
                if status.startswith("OK"): ok += 1
                else: fail += 1
                if i % 30 == 0 or status.startswith("ERR"):
                    print(f"  US [{i}/{len(us_codes)}] ok={ok} fail={fail} {time.time()-t0:.0f}s", flush=True)
        print(f"US done: {ok}/{len(us_codes)} in {time.time()-t0:.0f}s", flush=True)

    if not args.us_only:
        hk_codes = load_hk_tickers()
        print(f"\nPhase 2: HK {len(hk_codes)} ({args.hk_workers} workers)", flush=True)
        t0 = time.time()
        ok = fail = 0
        with ThreadPoolExecutor(max_workers=args.hk_workers) as ex:
            futs = {ex.submit(process_hk, c): c for c in hk_codes}
            for i, fut in enumerate(as_completed(futs), 1):
                c, status = fut.result()
                if status.startswith("OK"): ok += 1
                else: fail += 1
                if i % 20 == 0 or status.startswith("ERR"):
                    print(f"  HK [{i}/{len(hk_codes)}] ok={ok} fail={fail} {time.time()-t0:.0f}s", flush=True)
        print(f"HK done: {ok}/{len(hk_codes)} in {time.time()-t0:.0f}s", flush=True)

    conn = sqlite3.connect(f"{PROJECT_ROOT}/data/dsa_hk.db")
    n = conn.execute("SELECT COUNT(*) FROM daily_report WHERE report_date=?", (args.date,)).fetchone()[0]
    conn.close()
    print(f"\n>>> {args.date} in DB: {n}/400", flush=True)


if __name__ == "__main__":
    main()