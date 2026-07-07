"""Sequential HK analysis for 2026-07-07 — uses cached US reports + new HK LLM passes.

US already done (200/200) with concurrent. HK needs sequential because YFinance
HK is fragile to bursts. Runs ~3-5 min sequentially.
"""
import json
import os
import sys
import time

TARGET_DATE = "2026-07-07"
os.environ["DSA_REPORT_DATE_OVERRIDE"] = TARGET_DATE
os.environ["DSA_LLM_MAX_TOKENS"] = "8000"

sys.path.insert(0, "/Users/kenken/Documents/dsa-hk")

from src.analyzer import analyze, render_summary_md, render_report_md
from src.db import save_report, init_db
from src.data_fetcher import fetch_snapshot
from src.news_fetcher import fetch_news
from src.ticker_loader import load_hk_tickers


def process(code: str) -> tuple[str, str]:
    try:
        snap = fetch_snapshot(code)
        if not snap:
            return code, "no-snapshot"
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


def main():
    init_db()
    hk = load_hk_tickers()
    print(f"=== Sequential HK pass for {TARGET_DATE} ({len(hk)} codes) ===\n")

    ok = fail = 0
    fails = []
    t0 = time.time()

    for i, code in enumerate(hk, 1):
        c, status = process(code)
        if status.startswith("OK"):
            ok += 1
        else:
            fail += 1
            fails.append((c, status))

        if i % 10 == 0 or status.startswith("ERR"):
            elapsed = time.time() - t0
            eta = (len(hk) - i) * (elapsed / i)
            print(
                f"  [{i:3d}/{len(hk)}] {c}: {status}  ok={ok} fail={fail}  "
                f"elapsed {elapsed:.0f}s ETA {eta:.0f}s",
                flush=True,
            )

        # Slow down to avoid YFinance HK rate limit
        time.sleep(1.5)

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"DONE: {ok} ok / {fail} fail in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    if fails:
        print(f"\nFailures ({len(fails)}):")
        for c, s in fails[:20]:
            print(f"  {c}: {s}")


if __name__ == "__main__":
    main()