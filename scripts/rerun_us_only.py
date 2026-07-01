"""Inline rerun US-only stocks to apply USD prompt fix."""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from src.analyzer import analyze, render_summary_md, render_report_md
from src.db import save_report

DB = "data/dsa_hk.db"
MAX_WORKERS = 15


def get_cached_snapshot(code: str, date: str):
    conn = sqlite3.connect(DB)
    r = conn.execute(
        "SELECT data_snapshot_json FROM daily_report WHERE code=? AND report_date=?",
        (code, date),
    ).fetchone()
    conn.close()
    if not r or not r[0]:
        return None
    try:
        return json.loads(r[0])
    except Exception:
        return None


def rerun_one(code: str, date: str) -> tuple[str, str]:
    snapshot = get_cached_snapshot(code, date)
    if not snapshot:
        return code, "no-snapshot"
    try:
        name = snapshot.get("name_zh") or snapshot.get("name_en") or ""
        result = analyze(
            code=code,
            name=name,
            snapshot=snapshot,
            news=None,
            language="zh-Hant",
        )
        if result is None:
            return code, "analyze-returned-none"
        summary_md = render_summary_md(result, language="zh-Hant")
        full_md = render_report_md(result, snapshot, language="zh-Hant")
        # Preserve op_advice from existing record (don't override nudges)
        conn = sqlite3.connect(DB)
        existing_op = conn.execute(
            "SELECT operation_advice FROM daily_report WHERE code=? AND report_date=?",
            (code, date),
        ).fetchone()
        conn.close()
        op_to_use = (existing_op[0] if existing_op and existing_op[0] else result.operation_advice)
        save_report(
            code=code,
            report_date=date,
            score=result.score,
            sentiment=result.sentiment,
            trend=result.trend,
            operation_advice=op_to_use,
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
        return code, f"OK score={result.score} op={op_to_use}"
    except Exception as e:
        return code, f"ERR {type(e).__name__}: {e}"


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-06-29"
    codes = json.load(open("/tmp/us-rerun-list.json"))
    print(f"Rerunning {len(codes)} US stocks for {date} (USD-aware prompt)")
    ok = 0; fail = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(rerun_one, c, date): c for c in codes}
        for i, fut in enumerate(as_completed(futures), 1):
            code, status = fut.result()
            if status.startswith("OK"):
                ok += 1
            else:
                fail += 1
                print(f"  [{code}] {status}", flush=True)
            if i % 25 == 0:
                print(f"  Progress: {i}/{len(codes)} ok={ok} fail={fail}", flush=True)
    print(f"Done: {ok} ok / {fail} fail")


if __name__ == "__main__":
    main()
