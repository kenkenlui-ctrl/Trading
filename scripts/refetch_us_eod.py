"""Re-fetch US 7/8 with yfinance (7/8 EOD is now available)."""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ["DSA_REPORT_DATE_OVERRIDE"] = sys.argv[1] if len(sys.argv) > 1 else "2026-07-08"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import yfinance as yf

from src.analyzer import analyze, render_summary_md, render_report_md
from src.db import save_report
from src.news_fetcher import fetch_news
from src.ticker_loader import load_us_tickers

TARGET_DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-07-08"
TARGET_DT = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
NEXT_DT = TARGET_DT + timedelta(days=1)


def fetch_yfinance_us(code: str) -> dict | None:
    """Fetch US ticker actual TARGET_DATE close + prev_close via yfinance."""
    try:
        t = yf.Ticker(code)
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
            # Holiday/weekend fallback
            target_idx = len(hist) - 1
        k = hist.iloc[target_idx]
        actual_date = hist.index[target_idx].strftime("%Y-%m-%d")
        if actual_date != target_date_str:
            print(f"  [{code}] yfinance only has {actual_date}, NOT {target_date_str} — skipping", flush=True)
            return None
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
            "source": f"yfinance-{actual_date}",
            "target_date": TARGET_DATE,
            "actual_data_date": actual_date,
        }
    except Exception as e:
        return None


def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed

    us = load_us_tickers()
    print(f"=== Re-fetch US {TARGET_DATE} (now that EOD is settled) ===")
    print(f"US tickers: {len(us)}")

    ok = fail = 0
    start = time.time()
    with ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(fetch_yfinance_us, code): code for code in us}
        for i, fut in enumerate(as_completed(futures), 1):
            code = futures[fut]
            snap = fut.result()
            if snap:
                # Update DB with new snapshot
                import sqlite3
                con = sqlite3.connect("data/dsa_hk.db")
                con.execute(
                    "UPDATE daily_report SET data_snapshot_json=? WHERE report_date=? AND code=?",
                    (json.dumps(snap, ensure_ascii=False), TARGET_DATE, code),
                )
                con.commit()
                con.close()
                ok += 1
            else:
                fail += 1
            if i % 25 == 0 or i == len(us):
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(us) - i) / rate if rate > 0 else 0
                print(f"  US {i}/{len(us)} ok={ok} fail={fail} {rate:.1f}/s ETA {eta:.0f}s", flush=True)

    elapsed = time.time() - start
    print(f"\n=== US done: {ok}/{len(us)} ok, {fail} fail, {elapsed:.1f}s ===")


if __name__ == "__main__":
    main()