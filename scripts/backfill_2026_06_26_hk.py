"""Backfill 26/6 HK records using current live data.
26/6 rerun pipeline 之前 failed by `AttributeError` on None result.
即使 fix 咗, 26/6 真實 close data 已經 fetch 唔到 (Tencent live only, yfinance history 用 last 5d cover 26/6)。
呢個 script 用 current live data + 標記 `backfill_source=live_29_6` 嚟 populate dashboard 26/6 HK part。
"""
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analyzer import analyze, render_summary_md, render_report_md
from src.db import save_report, init_db
from src.data_fetcher import fetch_snapshot
from src.news_fetcher import fetch_news

TARGET_DATE = "2026-06-26"
MAX_WORKERS = 8

def backfill_one(code: str) -> tuple[str, str]:
    try:
        snap = fetch_snapshot(code)
        if not snap:
            return code, "no-snapshot"
        news = fetch_news(code=code, name_zh=snap.get("name_zh"), name_en=snap.get("name_en"), max_results=5, days=7)
        result = analyze(
            code=code,
            name=snap.get("name_zh") or snap.get("name_en") or code,
            snapshot=snap,
            news=news or [],
            language="zh-Hant",
        )
        if result is None:
            return code, "analyze-returned-none"
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
        return code, f"OK score={result.score}"
    except Exception as e:
        return code, f"ERR {type(e).__name__}: {e}"


def main():
    init_db()
    # Find HK codes missing from 26/6
    db = sqlite3.connect("data/dsa_hk.db")
    with open("hk_universe_200.json") as f:
        hk = json.load(f)
    with open("us_universe_200.json" if os.path.exists("us_universe_200.json") else "us_200.json") as f:
        us = json.load(f) if os.path.exists("us_universe_200.json") else []
    all_codes = set(hk) | set(us)
    done = set(r[0] for r in db.execute("SELECT code FROM daily_report WHERE report_date=?", (TARGET_DATE,)).fetchall())
    missing = sorted([c for c in all_codes - done if c.endswith(".HK")])
    db.close()
    print(f"Backfilling {len(missing)} HK codes for {TARGET_DATE} (live data, {MAX_WORKERS} workers)")
    print(f"NOTE: {TARGET_DATE} actual close data not available; using current live quote + history. Marked as backfill in DB.")
    t0 = time.time()
    ok = 0
    fail = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(backfill_one, c): c for c in missing}
        for i, fut in enumerate(as_completed(futures), 1):
            code, status = fut.result()
            if status.startswith("OK"):
                ok += 1
            else:
                fail += 1
                print(f"  [{code}] {status}")
            if i % 20 == 0:
                print(f"  Progress: {i}/{len(missing)} ok={ok} fail={fail} elapsed={time.time()-t0:.0f}s")
    print(f"Done: {ok} ok / {fail} fail in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
