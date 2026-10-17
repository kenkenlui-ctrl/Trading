#!/usr/bin/env python3
"""Measure the equal-weight benchmark bias in ew_bh()/portfolio_test B1.

READ-ONLY diagnostic. Writes nothing. Answers one question:
how much of the published equal-weight CAGR is an artefact of

    mm = b1src.pct_change().groupby([cal.year, cal.month]).transform("mean").mean(axis=1)

`transform("mean")` on a per-ticker daily pct_change returns, for each calendar
month, that ticker's MEAN DAILY return, broadcast onto every session in the
month. The daily series therefore holds the month's average daily return
constant for all ~21 sessions, and compounding it daily yields (1+rbar)^21
rather than the realised path prod(1+r_d). Compounding the arithmetic mean
instead of the geometric one adds roughly n*sigma^2/2 per month, which for a
typical stock is a couple of pp/yr of pure artefact.

Run:  python3 scripts/measure_ew_bias.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hk_retest as HR  # noqa: E402


def ew_as_implemented(px: dict[str, pd.DataFrame], cal: pd.DatetimeIndex) -> pd.Series:
    """Byte-for-byte the current production line (hk_retest.py:334 / portfolio_test.py:280)."""
    b1src = pd.DataFrame({s: p.close for s, p in px.items()}).reindex(cal)
    b1src = b1src.loc[b1src.notna().any(axis=1)]
    cal = b1src.index
    mm = b1src.pct_change().groupby([cal.year, cal.month]).transform("mean").mean(axis=1)
    return (100_000 * (1 + mm).cumprod()).ffill()


def ew_daily_rebalanced(px: dict[str, pd.DataFrame], cal: pd.DatetimeIndex) -> pd.Series:
    """Textbook equal weight: cross-sectional mean of daily returns, compounded daily."""
    b1src = pd.DataFrame({s: p.close for s, p in px.items()}).reindex(cal)
    b1src = b1src.loc[b1src.notna().any(axis=1)]
    ret = b1src.pct_change()
    # Require a real cross-section, else a single survivor defines "the universe".
    breadth = ret.notna().sum(axis=1)
    mm = ret.where(breadth >= 5).mean(axis=1).dropna()
    return (100_000 * (1 + mm).cumprod()).ffill()


def _win_stats(curve: pd.Series, lo, hi) -> dict | None:
    w = curve[(curve.index >= lo) & (curve.index <= hi)].dropna()
    if len(w) < 2:
        return None
    e = w.to_numpy(float)
    yrs = (w.index[-1] - w.index[0]).days / 365.25
    if yrs <= 0:
        return None
    return {
        "cagr%": (e[-1] / e[0]) ** (1 / yrs) * 100 - 100,
        "maxDD%": (e / np.maximum.accumulate(e) - 1).min() * 100,
        "sessions": len(w),
    }


def main() -> int:
    print("=" * 78)
    print("EW benchmark bias: as-implemented (monthly broadcast) vs true daily EW")
    print("=" * 78)

    specs = [
        ("hk200", HR.load_hk_bars, HR.IDX_HSI),
        ("us200", lambda: HR.load_market_bars("us200"), HR.IDX_GSPC),
        ("jp200", lambda: HR.load_market_bars("jp200"), HR.IDX_N225),
    ]

    for mkt, loader, idx_path in specs:
        px = loader()
        idx = HR._load_index(idx_path)
        if idx is None:
            print(f"\n{mkt}: index missing, skipped")
            continue
        cal = idx.index
        buggy = ew_as_implemented(px, cal)
        monthly = HR.ew_bh(px, cal)          # the FIXED production function
        daily = ew_daily_rebalanced(px, cal)
        print(f"\n--- {mkt}: {len(px)} tickers, {cal[0].date()} to {cal[-1].date()} ---")
        print(f"{'window':<12} {'buggy':>9} {'MONTHLY':>9} {'daily':>9} "
              f"{'artefact':>10} {'bug DD':>8} {'fix DD':>8}")
        for win_name, lo, hi in HR.WINDOWS:
            b = _win_stats(buggy, lo, hi)
            m = _win_stats(monthly, lo, hi)
            d = _win_stats(daily, lo, hi)
            if not b or not m:
                print(f"{win_name:<12} {'n/a':>9}")
                continue
            ds = f"{d['cagr%']:>8.2f}%" if d else f"{'n/a':>9}"
            print(f"{win_name:<12} {b['cagr%']:>8.2f}% {m['cagr%']:>8.2f}% {ds} "
                  f"{b['cagr%'] - m['cagr%']:>+9.2f}pp {b['maxDD%']:>7.1f}% {m['maxDD%']:>7.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())