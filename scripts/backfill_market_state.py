#!/usr/bin/env python3
"""Backfill market_state (HSI chg) for missing recent dates.

Uses futu kline for HK.800000 (HSI) + yfinance ^HSI as fallback.
Populates market_state for: 7/24, 7/25 (weekend, skip), 7/27, 7/28, 7/29, 7/30, 7/31.
"""
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "dsa_hk.db"
MISSING_DATES = ["2026-07-24", "2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31"]

def main():
    from src.db import save_market_state, get_db

    # Use yfinance for HSI ^HSI
    import yfinance as yf
    target_dates = MISSING_DATES
    start = datetime.strptime(target_dates[0], "%Y-%m-%d").date() - timedelta(days=2)
    end = datetime.strptime(target_dates[-1], "%Y-%m-%d").date() + timedelta(days=2)

    print(f"Fetching ^HSI for {start} -> {end}", flush=True)
    ticker = yf.Ticker("^HSI")
    hist = ticker.history(start=start.isoformat(), end=end.isoformat(), auto_adjust=False)
    if hist is None or len(hist) == 0:
        print("No HSI data", flush=True)
        return

    for d in target_dates:
        target_dt = datetime.strptime(d, "%Y-%m-%d").date()
        mask = hist.index.date == target_dt
        if not mask.any():
            print(f"  {d}: no yfinance row", flush=True)
            continue
        row = hist[mask].iloc[0]
        hsi_close = float(row["Close"])
        prev_dt = target_dt - timedelta(days=1)
        # Skip weekends
        if prev_dt.weekday() >= 5:
            prev_dt = prev_dt - timedelta(days=prev_dt.weekday() - 4)
        prev_mask = hist.index.date == prev_dt
        if prev_mask.any():
            hsi_prev = float(hist[prev_mask].iloc[0]["Close"])
        else:
            # Try 2 days back
            prev_dt2 = prev_dt - timedelta(days=1)
            prev_mask2 = hist.index.date == prev_dt2
            if prev_mask2.any():
                hsi_prev = float(hist[prev_mask2].iloc[0]["Close"])
            else:
                hsi_prev = hsi_close
        hsi_chg = (hsi_close - hsi_prev) / hsi_prev * 100
        # Determine regime
        if hsi_chg > 1.0:
            regime = "BULL"
        elif hsi_chg < -1.0:
            regime = "BEAR"
        else:
            regime = "NEUTRAL"
        save_market_state(d, hsi_chg, hsi_close, None, regime)
        print(f"  {d}: HSI close={hsi_close:.2f} chg={hsi_chg:+.2f}% regime={regime}", flush=True)

if __name__ == "__main__":
    main()
