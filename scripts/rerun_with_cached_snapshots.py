"""Re-run LLM analysis for existing reports by REUSING stored data_snapshot_json.

Why: yfinance is rate-limited / unreliable for fresh fetches, but we already
have snapshots from the previous 2026-06-27 run. We just need the LLM to
re-emit the new 4-dim breakdown (with order_flow_score) using the updated
prompt.

Usage: python scripts/rerun_with_cached_snapshots.py 2026-06-27
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "dsa_hk.db"

# Same env vars as the original analyze run
os.environ.setdefault("DSA_LLM_MAX_TOKENS", "8000")


def get_cached_snapshot(code: str, date: str) -> dict | None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT data_snapshot_json FROM daily_report WHERE code=? AND report_date=?",
        (code, date),
    ).fetchone()
    if not row or not row["data_snapshot_json"]:
        return None
    try:
        return json.loads(row["data_snapshot_json"])
    except Exception:
        return None


def rerun_with_cached(date: str):
    from src.analyzer import analyze
    from src.db import save_report
    from concurrent.futures import ThreadPoolExecutor, as_completed

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    codes = [r["code"] for r in conn.execute(
        "SELECT DISTINCT code FROM daily_report WHERE report_date=? ORDER BY code", (date,)
    ).fetchall()]
    print(f"Re-running {len(codes)} codes for {date} with cached snapshots + new 4-dim prompt")
    conn.close()

    def _analyze_one(code: str) -> tuple[str, str]:
        snapshot = get_cached_snapshot(code, date)
        if not snapshot:
            return code, "no-cached-snapshot"
        try:
            # Reuse existing snapshot — pass directly into analyze()
            name = snapshot.get("name_zh") or snapshot.get("name_en") or ""
            result = analyze(
                code=code,
                name=name,
                snapshot=snapshot,
                news=None,
                language="zh",
            )
            if result is None:
                return code, "analyze-returned-none"
            # Persist into DB (mimic pipeline's analyze_ticker storage call)
            from src.analyzer import render_summary_md, render_report_md
            summary_md = render_summary_md(result, language="zh")
            full_md = render_report_md(result, snapshot, language="zh")
            save_report(
                code=code,
                report_date=date,
                score=result.score,
                sentiment=result.sentiment,
                trend=result.trend,
                operation_advice=result.operation_advice,
                summary_md=summary_md,
                full_md=full_md,
                news=[],
                data_snapshot=snapshot,
                llm_model=result.llm_model,
                score_breakdown_json=json.dumps(result.score_breakdown or {}, ensure_ascii=False),
                trade_direction=result.trade_direction,
                support_zone=result.support_zone,
                resistance_zone=result.resistance_zone,
                key_levels_json=json.dumps(result.key_levels or {}, ensure_ascii=False),
            )
            return code, f"OK score={result.score}"
        except Exception as e:
            return code, f"ERR {type(e).__name__}: {e}"

    workers = int(os.environ.get("DSA_PARALLEL", "5"))
    print(f"Workers: {workers}")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_analyze_one, c): c for c in codes}
        for fut in as_completed(futures):
            code, status = fut.result()
            if status.startswith("OK"):
                ok += 1
            else:
                fail += 1
            print(f"  {futures[fut]} {status}  ({ok} ok / {fail} fail)")
    print(f"\nDone: {ok} ok / {fail} fail / {len(codes)} total")


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-06-27"
    rerun_with_cached(date)