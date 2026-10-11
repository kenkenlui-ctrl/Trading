#!/usr/bin/env python3
"""hk_mom_screen_12.py — recreate the 4×3 = 12 momentum screening grid.

Standard momentum literature:
  4 descriptors: 12m (252d), 12m-skip-1m (231d, Jegadeesh-Titman), 6m (126d), 3m (63d)
  3 rebalances:   monthly (21d), quarterly (63d), biweekly (10d)
  = 12 combinations

Run on same panel as hk_momentum_engine.py.
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
TOP_FRAC = 0.20

DESCRIPTORS = {
    "12m": 252,
    "12m-1m": 231,   # skip 1 month (Jegadeesh-Titman 1993)
    "6m": 126,
    "3m": 63,
}
REBALANCES = {
    "monthly": 21,
    "biweekly": 10,
    "quarterly": 63,
}

def load_panel() -> tuple[pd.DataFrame, list]:
    bars_dir = DATA / "bt10y2_hk"
    mask = build_mask(bars_dir)
    masked = {k for k in mask if k.endswith("_HK")}
    series, first_dates = [], []
    for p in sorted(bars_dir.glob("*.json")):
        b = load(p)
        if b is None or b.empty or p.stem in masked:
            continue
        series.append(b.close.rename(p.stem))
        first_dates.append((p.stem, str(b.index.min().date())))
    P = pd.concat(series, axis=1).sort_index()
    return P, first_dates


def run_one(P: pd.DataFrame, lookback: int, step: int) -> dict:
    dates = list(range(lookback, len(P) - step, step))
    rows = []
    prev = None
    for i in dates:
        s = (P.iloc[i] / P.iloc[i - lookback] - 1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(s) < 40:
            continue
        k = max(1, int(round(len(s) * TOP_FRAC)))
        top = s.sort_values(ascending=False).index[:k]
        fut = P.iloc[i + 1:i + 1 + step]
        px = P.iloc[i]
        one_way = len(top) if prev is None else len(set(top) ^ set(prev))
        ret = float(fut[list(top)].div(px[list(top)], axis=1).mean(axis=1).mean()) - 1
        cost = COST_RT * one_way / max(len(top), 1)
        rows.append(ret - cost)
        prev = set(top)
    r = np.array(rows)
    if len(r) < 12:
        return {"n": len(r)}
    eq = np.cumprod(1 + r)
    dd = eq / np.maximum.accumulate(eq) - 1
    periods_per_year = 252 / step
    return {
        "n_periods": int(len(r)),
        "annualised_pct": round(float(r.mean() * periods_per_year * 100), 2),
        "vol_pct": round(float(r.std(ddof=1) * np.sqrt(periods_per_year) * 100), 2),
        "sharpe_like": round(float(r.mean() / r.std(ddof=1) * np.sqrt(periods_per_year)), 3),
        "hit_rate_pct": round(float((r > 0).mean() * 100), 1),
        "max_dd_pct": round(float(dd.min() * 100), 2),
    }


def main():
    print("Loading HK panel …")
    P, first_dates = load_panel()
    print(f"  panel: {P.shape[0]} bars × {P.shape[1]} names  (window {P.index.min().date()} → {P.index.max().date()})")

    # ---- 12-combination table ----
    rows = []
    for d_name, d_lb in DESCRIPTORS.items():
        for r_name, r_step in REBALANCES.items():
            res = run_one(P, d_lb, r_step)
            res["descriptor"] = d_name
            res["rebalance"] = r_name
            res["lookback_bars"] = d_lb
            res["rebalance_bars"] = r_step
            rows.append(res)
    df = pd.DataFrame(rows)
    df = df.sort_values("annualised_pct", ascending=False)
    print("\n=== 12-combination screening table (sorted by annualised %) ===\n")
    print(df.to_string(index=False))

    out = DATA / "hk_momentum_screen_12.json"
    df.to_json(out, orient="records", indent=2)
    print(f"\nWrote {out}")

    # ---- Universe attrition ----
    cutoff = pd.Timestamp("2016-09-01")
    n_total = P.shape[1]
    n_late_ipo = sum(1 for _, fd in first_dates if pd.Timestamp(fd) >= cutoff)
    n_pre_ipo = n_total - n_late_ipo
    print(f"\n=== Universe composition (cutoff {cutoff.date()}) ===")
    print(f"  Total names in current panel:           {n_total}")
    print(f"  Names with first bar BEFORE cutoff:     {n_pre_ipo}  (could have been in index in 2016)")
    print(f"  Names with first bar AT/AFTER cutoff:   {n_late_ipo}  (post-cutoff IPOs)")
    print(f"  Estimated attrition upper bound:        unknown (no point-in-time membership; "
          f"names delisted pre-2016 absent from panel)")


if __name__ == "__main__":
    main()
