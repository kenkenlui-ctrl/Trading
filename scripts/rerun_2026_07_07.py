"""Rerun analysis with separate HK/US concurrency — HK to YFinance is rate-limited,
so HK gets workers=4 with sleeps, US gets workers=20.

Usage: python3 scripts/rerun_2026_07_07.py
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

TARGET_DATE = "2026-07-07"
os.environ["DSA_REPORT_DATE_OVERRIDE"] = TARGET_DATE
os.environ["DSA_LLM_MAX_TOKENS"] = "8000"

sys.path.insert(0, "/Users/kenken/Documents/dsa-hk")

from src.analyzer import analyze, render_summary_md, render_report_md
from src.db import save_report, init_db
from src.data_fetcher import fetch_snapshot
from src.news_fetcher import fetch_news
from src.ticker_loader import load_hk_tickers, load_us_tickers

# Lower parallelism to dodge YFinance HK rate limit
HK_WORKERS = 4
US_WORKERS = 20
HK_SLEEP_BETWEEN = 0.5  # seconds between HK snapshot fetches


def process_one(code: str, market: str) -> tuple[str, str]:
    try:
        snap = fetch_snapshot(code)
        if not snap:
            return code, "no-snapshot"
        # Skip HK news if rate-limited too — use empty news list
        news = fetch_news(
            code=code,
            name_zh=snap.get("name_zh") or code,
            name_en=code,
            max_results=3,
            days=3,
        ) or []
        result = analyze(
            code=code,
            name=snap.get("name_zh") or snap.get("name_en") or code,
            snapshot=snap,
            news=news,
            language="zh-Hant",
        )
        if result is None:
            return code, "analyze-none"
        summary_md = render_summary_md(result, language="zh-Hant")
        full_md = render_report_md(result, snap, language="zh-Hant")
        save_report(
            code=code,
            report_date=TARGET_DATE,
            score=result.score,
            sentiment=result.sentiment,
            trend=result.trend,
            operation_advice=result.operation_advice,
            summary_md=summary_md,
            full_md=full_md,
            news=news,
            data_snapshot=snap,
            llm_model=result.llm_model,
            score_breakdown_json=json.dumps(result.score_breakdown or {}, ensure_ascii=False),
            trade_direction=result.trade_direction,
            support_zone=result.support_zone,
            resistance_zone=result.resistance_zone,
            key_levels_json=json.dumps(result.key_levels or {}, ensure_ascii=False),
        )
        return code, f"OK op={result.operation_advice} score={result.score}"
    except Exception as e:
        return code, f"ERR {type(e).__name__}: {e}"


def run_batch(codes: list[str], market: str, workers: int, sleep_between: float = 0):
    """Run a batch with optional intra-batch sleep."""
    ok = fail = 0
    fail_codes = []
    t0 = time.time()

    if sleep_between > 0:
        # Sequential mode (use single thread but add sleeps)
        for i, code in enumerate(codes, 1):
            code, status = process_one(code, market)
            if status.startswith("OK"):
                ok += 1
            else:
                fail += 1
                fail_codes.append((code, status))
            if i % 20 == 0:
                elapsed = time.time() - t0
                eta = (len(codes) - i) * (elapsed / i)
                print(
                    f"  {market}: {i}/{len(codes)} ok={ok} fail={fail} "
                    f"{elapsed:.0f}s elapsed ETA {eta:.0f}s",
                    flush=True,
                )
            time.sleep(sleep_between)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(process_one, c, market): c for c in codes}
            for i, fut in enumerate(as_completed(futs), 1):
                code, status = fut.result()
                if status.startswith("OK"):
                    ok += 1
                else:
                    fail += 1
                    fail_codes.append((code, status))
                if i % 50 == 0:
                    elapsed = time.time() - t0
                    eta = (len(codes) - i) * (elapsed / i)
                    print(
                        f"  {market}: {i}/{len(codes)} ok={ok} fail={fail} "
                        f"{elapsed:.0f}s elapsed ETA {eta:.0f}s",
                        flush=True,
                    )

    return ok, fail, fail_codes


def main():
    init_db()
    hk = load_hk_tickers()
    us = load_us_tickers()
    print(f"Target date: {TARGET_DATE}")
    print(f"HK: {len(hk)} codes, US: {len(us)} codes")

    # Run US first (faster, no rate limit)
    print("\n━━━ US BATCH ━━━")
    us_ok, us_fail, us_fails = run_batch(us, "US", US_WORKERS)
    print(f"US done: {us_ok} ok / {us_fail} fail")

    # Then HK with sleeps to avoid rate limit
    print("\n━━━ HK BATCH (with 0.5s sleeps to dodge YFinance rate limit) ━━━")
    hk_ok, hk_fail, hk_fails = run_batch(hk, "HK", HK_WORKERS, HK_SLEEP_BETWEEN)
    print(f"HK done: {hk_ok} ok / {hk_fail} fail")

    print(f"\n=== TOTAL: {us_ok+hk_ok} ok / {us_fail+hk_fail} fail ===")
    if us_fails:
        print(f"US failures (sample): {us_fails[:5]}")
    if hk_fails:
        print(f"HK failures (sample): {hk_fails[:10]}")


if __name__ == "__main__":
    main()