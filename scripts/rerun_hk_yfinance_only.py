"""Re-run HK 7/8 with yfinance ONLY (no Tencent — Tencent returns current data).

5-digit HK codes yfinance doesn't have will be SKIPPED (no historical source).
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ["DSA_REPORT_DATE_OVERRIDE"] = sys.argv[1] if len(sys.argv) > 1 else "2026-07-08"
os.environ["DSA_LLM_MAX_TOKENS"] = "8000"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import yfinance as yf

from src.analyzer import analyze, render_summary_md, render_report_md
from src.db import save_report, init_db
from src.news_fetcher import fetch_news
from src.ticker_loader import load_hk_tickers
from src.config import get_config

cfg = get_config()
MAX_WORKERS = int(os.environ.get("DSA_PARALLEL", "15"))
TARGET_DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-07-08"
TARGET_DT = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
NEXT_DT = TARGET_DT + timedelta(days=1)


def fetch_yfinance(code: str) -> dict | None:
    """Fetch target_date close + prev_close via yfinance. Returns None if no data."""
    try:
        t = yf.Ticker(code)
        # Use period instead of start/end — start/end args trigger different yfinance
        # endpoint that returns empty for 5-digit HK codes. period='30d' is safe.
        hist = t.history(period="30d", auto_adjust=False)
        if hist.empty:
            return None
        target_date_str = TARGET_DT.strftime("%Y-%m-%d")
        target_idx = None
        for i, (dt, _) in enumerate(hist.iterrows()):
            if dt.strftime("%Y-%m-%d") == target_date_str:
                target_idx = i
                break
        if target_idx is None:
            # Holiday fallback: use the LATEST row BEFORE today (not today)
            # We want TARGET_DATE data, not current
            cutoff = pd.Timestamp.now(tz=hist.index.tz).normalize() - pd.Timedelta(days=1)
            for i, (dt, _) in enumerate(hist.iterrows()):
                if dt.normalize() <= cutoff:
                    target_idx = i
            if target_idx is None:
                return None
        k = hist.iloc[target_idx]
        last = float(k["Close"])
        if target_idx > 0:
            prev_close = float(hist.iloc[target_idx - 1]["Close"])
        else:
            prev_close = last
        change_pct = (last - prev_close) / prev_close * 100 if prev_close else 0
        return {
            "code": code,
            "last_price": last,
            "prev_close": prev_close,
            "change_pct": round(change_pct, 2),
            "day_high": float(k["High"]),
            "day_low": float(k["Low"]),
            "open": float(k["Open"]),
            "volume": float(k["Volume"]),
            "source": f"yfinance-{hist.index[target_idx].strftime('%Y-%m-%d')}",
            "target_date": TARGET_DATE,
            "actual_data_date": hist.index[target_idx].strftime("%Y-%m-%d"),
        }
    except Exception:
        return None


def process_one(code: str) -> tuple[str, bool]:
    try:
        snap = fetch_yfinance(code)
        if not snap:
            return code, False  # yfinance has no data for this HK ticker
        news = fetch_news(code=code, max_results=5, days=7)
        result = analyze(code, snap.get("name_zh") or code, snap, news, language="zh-Hant")
        if not result:
            return code, False
        full_md = render_report_md(result, snap, language="zh-Hant")
        summary_md = render_summary_md(result, language="zh-Hant")
        save_report(
            code=code, report_date=TARGET_DATE, score=result.score,
            sentiment=result.sentiment, trend=result.trend,
            operation_advice=result.operation_advice,
            summary_md=summary_md, full_md=full_md, news=news,
            data_snapshot=snap, llm_model=result.llm_model,
            score_breakdown_json=json.dumps(result.score_breakdown or {}, ensure_ascii=False),
            trade_direction=result.trade_direction,
            support_zone=result.support_zone,
            resistance_zone=result.resistance_zone,
            key_levels_json=json.dumps(result.key_levels or {}, ensure_ascii=False),
        )
        return code, True
    except Exception as e:
        print(f"  [{code}] error: {e}", flush=True)
        return code, False


def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed

    init_db()

    import sqlite3
    con = sqlite3.connect("data/dsa_hk.db")
    n_del = con.execute("DELETE FROM daily_report WHERE report_date=? AND code LIKE '%.HK'", (TARGET_DATE,)).rowcount
    con.commit()
    con.close()
    print(f"=== Re-run HK {TARGET_DATE} — yfinance only (no Tencent) ===")
    print(f"  Deleted {n_del} existing {TARGET_DATE} HK records")

    hk = load_hk_tickers()
    print(f"HK tickers: {len(hk)}")

    ok = fail = 0
    start = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(process_one, code): code for code in hk}
        for i, fut in enumerate(as_completed(futures), 1):
            code, success = fut.result()
            if success: ok += 1
            else: fail += 1
            if i % 25 == 0 or i == len(hk):
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(hk) - i) / rate if rate > 0 else 0
                print(f"  HK {i}/{len(hk)} ok={ok} fail={fail} {rate:.1f}/s ETA {eta:.0f}s", flush=True)

    elapsed = time.time() - start
    print(f"\n=== HK done: {ok}/{len(hk)} ok, {fail} fail, {elapsed:.1f}s ===")


if __name__ == "__main__":
    main()