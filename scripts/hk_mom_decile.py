#!/usr/bin/env python3
"""hk_mom_decile.py — decile spread: smooth or cliff?

For each rebalance (12m lookback, monthly):
  - score every name on 12m return
  - sort into 10 deciles
  - compute each decile's next-21-day return (equal-weight within decile)
  - report decile-by-decile annualised

Same decile analysis on:
  - Full sample (2017-2026)
  - 1H (2017-2021)
  - 2H (2022-2026)

If smooth: dispersion is uniform, top 20% likely capture most of it.
If cliff: top 10% is uniquely good, smaller basket might be more efficient.
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts"))
from bt_clean import build_mask, load

DATA = REPO / "data"
COST_RT = 0.0025
LB = 252
STEP = 21
N_DECILES = 10
SPLIT = pd.Timestamp("2022-01-01")


def load_panel() -> pd.DataFrame:
    bars_dir = DATA / "bt10y2_hk"
    mask = build_mask(bars_dir)
    masked = {k for k in mask if k.endswith("_HK")}
    series = []
    for p in sorted(bars_dir.glob("*.json")):
        b = load(p)
        if b is None or b.empty or p.stem in masked:
            continue
        series.append(b.close.rename(p.stem))
    return pd.concat(series, axis=1).sort_index()


def decile_returns(P: pd.DataFrame, period_filter=None) -> pd.DataFrame:
    """Return a DataFrame with columns D1=best, D10=worst, indexed by rebalance date.
    Each cell = net return over next 21 trading days, equal-weight within decile.
    """
    dates = list(range(LB, len(P) - STEP, STEP))
    if period_filter is not None:
        dates = [d for d in dates if period_filter(P.index[d])]
    rows = []
    for i in dates:
        s = (P.iloc[i] / P.iloc[i - LB] - 1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(s) < 80:  # need at least 8 per decile
            continue
        # Sort desc, split into 10 equal-count buckets
        sorted_idx = s.sort_values(ascending=False).index
        k = len(sorted_idx) // N_DECILES
        # Apply pro-rata cost: traded = full rebalance each period
        # (conservative — actual turn is partial)
        cost = COST_RT  # full rebalance = 100% turn
        decile_rets = []
        for d in range(N_DECILES):
            bucket = sorted_idx[d * k:(d + 1) * k]
            fut = P.iloc[i + 1:i + 1 + STEP]
            px = P.iloc[i]
            r = float(fut[list(bucket)].div(px[list(bucket)], axis=1).mean(axis=1).mean()) - 1
            decile_rets.append(r - cost)
        rows.append({"date": P.index[i], **{f"D{d+1}": decile_rets[d] for d in range(N_DECILES)}})
    return pd.DataFrame(rows).set_index("date")


def annualised(returns: pd.Series, periods_per_year: float) -> float:
    r = returns.dropna().to_numpy()
    return float(r.mean() * periods_per_year * 100)


def stats(returns: pd.Series, periods_per_year: float) -> dict:
    r = returns.dropna().to_numpy()
    eq = np.cumprod(1 + r)
    dd = eq / np.maximum.accumulate(eq) - 1
    return {
        "n": int(len(r)),
        "annualised_pct": round(float(r.mean() * periods_per_year * 100), 2),
        "vol_pct": round(float(r.std(ddof=1) * np.sqrt(periods_per_year) * 100), 2),
        "sharpe_like": round(float(r.mean() / r.std(ddof=1) * np.sqrt(periods_per_year)), 3),
        "hit_rate_pct": round(float((r > 0).mean() * 100), 1),
        "max_dd_pct": round(float(dd.min() * 100), 2),
    }


def main():
    P = load_panel()
    print(f"Panel: {P.shape[0]} bars × {P.shape[1]} names")
    print(f"Decile count: {N_DECILES}, lookback: {LB}, step: {STEP}")
    print()

    full = decile_returns(P)
    h1 = decile_returns(P, lambda d: d < SPLIT)
    h2 = decile_returns(P, lambda d: d >= SPLIT)
    spy = 252 / STEP

    # Per-decile annualised
    def report(df, label):
        print(f"=== Decile returns — {label} (n={len(df)}) ===")
        print(f"  {'Decile':<10} {'Ann%':>8} {'Sharpe':>7} {'Hit%':>6} {'MaxDD%':>8}")
        for d in range(1, N_DECILES + 1):
            col = f"D{d}"
            s = stats(df[col], spy)
            print(f"  D{d:<9} {s['annualised_pct']:>7.2f} {s['sharpe_like']:>6.2f} {s['hit_rate_pct']:>5.1f} {s['max_dd_pct']:>7.2f}")
        # Dispersion D1 - D10
        d1 = stats(df["D1"], spy)
        d10 = stats(df["D10"], spy)
        ls = stats(df["D1"] - df["D10"], spy)
        print(f"  ----  D1 - D10 long-short (gross on D1 leg, no short cost):  {ls['annualised_pct']:>6.2f}%  sharpe={ls['sharpe_like']:.2f}")
        print(f"  Decile spread (D1 ann − D10 ann): {(d1['annualised_pct']-d10['annualised_pct']):+.2f}pp")
        # Adjacent decile gaps to check smooth vs cliff
        gaps = []
        for d in range(1, N_DECILES):
            ann_d = stats(df[f"D{d}"], spy)["annualised_pct"]
            ann_d1 = stats(df[f"D{d+1}"], spy)["annualised_pct"]
            gaps.append(ann_d - ann_d1)
        print(f"  Adjacent decile gaps (D1-D2, D2-D3, ..., D9-D10): {[round(g,2) for g in gaps]}")
        max_gap = max(gaps); min_gap = min(gaps)
        print(f"  Max gap: {max_gap:.2f}pp  |  Min gap: {min_gap:.2f}pp  |  Range: {max_gap-min_gap:.2f}pp")
        # Cliff heuristic: if max gap > 2x mean, it's cliff-like
        mean_gap = sum(gaps)/len(gaps)
        if max_gap > 2 * mean_gap:
            print(f"  → CLIFF: max gap {max_gap:.2f}pp > 2× mean {mean_gap:.2f}pp (top decile uniquely good)")
        else:
            print(f"  → SMOOTH: max gap {max_gap:.2f}pp ≤ 2× mean {mean_gap:.2f}pp (dispersion uniform)")
        print()
        return d1, d10, ls

    f1, f10, fls = report(full, "FULL (2017→2026)")
    h1d1, h1d10, h1ls = report(h1, "1H (2017→2021)")
    h2d1, h2d10, h2ls = report(h2, "2H (2022→2026)")

    # Cross-period comparison
    print("=== Cross-period: 1H vs 2H by decile ===")
    print(f"  {'Decile':<8} {'1H ann%':>9} {'2H ann%':>9} {'delta':>8}")
    for d in range(1, N_DECILES + 1):
        a1 = stats(h1[f"D{d}"], spy)["annualised_pct"]
        a2 = stats(h2[f"D{d}"], spy)["annualised_pct"]
        print(f"  D{d:<7} {a1:>8.2f} {a2:>8.2f} {a2-a1:>+7.2f}")
    print()
    print("=== 2H D1 alone ===")
    print(f"  D1 2H ann% = {h2d1['annualised_pct']:.2f}, sharpe {h2d1['sharpe_like']:.2f}, hit {h2d1['hit_rate_pct']:.1f}%")
    print(f"  D10 2H ann% = {h2d10['annualised_pct']:.2f}")
    print(f"  D1-D10 2H gross: {h2ls['annualised_pct']:.2f}%  (sharpe {h2ls['sharpe_like']:.2f})")

    out = {
        "panel": {"n_bars": int(P.shape[0]), "n_names": int(P.shape[1])},
        "params": {"lookback": LB, "step": STEP, "n_deciles": N_DECILES, "cost_rt": COST_RT},
        "full": {f"D{d}": stats(full[f"D{d}"], spy) for d in range(1, N_DECILES + 1)},
        "first_half": {f"D{d}": stats(h1[f"D{d}"], spy) for d in range(1, N_DECILES + 1)},
        "second_half": {f"D{d}": stats(h2[f"D{d}"], spy) for d in range(1, N_DECILES + 1)},
        "long_short_full": stats(full["D1"] - full["D10"], spy),
        "long_short_2h": stats(h2["D1"] - h2["D10"], spy),
    }
    out_p = DATA / "hk_mom_decile.json"
    json.dump(out, open(out_p, "w"), indent=2)
    print(f"\nWrote {out_p}")


if __name__ == "__main__":
    main()
