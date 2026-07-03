"""Generate report for any date — bypass CLI arg bug.
Usage: python3 scripts/run_specific_date.py 2026-07-02
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Accept date arg (default 30/6 for backward compat)
TARGET_DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-06-30"
os.environ["DSA_REPORT_DATE_OVERRIDE"] = TARGET_DATE
os.environ["DSA_LLM_MAX_TOKENS"] = "8000"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analyzer import analyze, render_summary_md, render_report_md
from src.db import save_report, init_db
from src.data_fetcher import fetch_snapshot
from src.news_fetcher import fetch_news
from src.ticker_loader import load_hk_tickers, load_us_tickers
from src.config import get_config

MAX_WORKERS = int(os.environ.get("DSA_PARALLEL", "20"))


def process_one(code: str) -> tuple[str, str]:
    try:
        snap = fetch_snapshot(code)
        if not snap:
            return code, "no-snapshot"
        news = fetch_news(code=code, name_zh=snap.get("name_zh") or code, name_en=code, max_results=5, days=3)
        result = analyze(
            code=code,
            name=snap.get("name_zh") or snap.get("name_en") or code,
            snapshot=snap,
            news=news or [],
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
            news=news or [],
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


def main():
    init_db()
    hk = load_hk_tickers()
    us = load_us_tickers()
    all_codes = hk + us
    print(f"=== Target date: {TARGET_DATE} ===")
    print(f"Total: {len(hk)} HK + {len(us)} US = {len(all_codes)} tickers")
    t0 = time.time()
    ok = 0; fail = 0; fail_codes = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(process_one, c): c for c in all_codes}
        for i, fut in enumerate(as_completed(futs), 1):
            code, status = fut.result()
            if status.startswith("OK"):
                ok += 1
            else:
                fail += 1
                fail_codes.append((code, status))
            if i % 50 == 0:
                elapsed = time.time() - t0
                rate = i / elapsed
                eta = (len(all_codes) - i) / rate
                print(f"  {i}/{len(all_codes)} ok={ok} fail={fail} {rate:.1f}/s ETA {eta:.0f}s", flush=True)
    print(f"\nDone: {ok} ok / {fail} fail in {time.time()-t0:.0f}s")
    if fail_codes:
        print(f"Failures:")
        for c, s in fail_codes[:20]:
            print(f"  {c}: {s}")


if __name__ == "__main__":
    main()