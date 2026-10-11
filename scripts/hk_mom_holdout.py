#!/usr/bin/env python3
"""hk_mom_holdout.py — time-series holdout (2022-2026) for the pre-registered rule.

Splits the panel at 2022-01-01 and re-runs:
  1. The pre-registered rule alone (12m, monthly) on 2H
  2. All 12 combinations on 2H, ranked — tests winner's curse over time

If 12m monthly is still top-4 on 2H, no time-selection-bias.
If 2H annualised < 1%, edge has decayed (McLean-Pontiff haircut confirmed).
If 2H < 0, edge is gone.
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
SPLIT = pd.Timestamp("2022-01-01")

DESCRIPTORS = {
    "12m": 252,
    "12m-1m": 231,
    "6m": 126,
    "3m": 63,
}
REBALANCES = {
    "monthly": 21,
    "biweekly": 10,
    "quarterly": 63,
}


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


def run_combo(P: pd.DataFrame, lookback: int, step: int, period_filter=None) -> dict:
    """Run a combo. period_filter: callable(date) -> bool, or None for full sample."""
    dates = list(range(lookback, len(P) - step, step))
    if period_filter is not None:
        dates = [d for d in dates if period_filter(P.index[d])]
    rets, prev = [], None
    for i in dates:
        s = (P.iloc[i] / P.iloc[i - lookback] - 1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(s) < 40:
            continue
        k = max(1, int(round(len(s) * TOP_FRAC)))
        top = s.sort_values(ascending=False).index[:k]
        fut = P.iloc[i + 1:i + 1 + step]
        px = P.iloc[i]
        one_way = len(top) if prev is None else len(set(top) ^ prev)
        ret = float(fut[list(top)].div(px[list(top)], axis=1).mean(axis=1).mean()) - 1
        cost = COST_RT * one_way / max(len(top), 1)
        rets.append(ret - cost)
        prev = set(top)
    if len(rets) < 6:
        return {"n_periods": len(rets)}
    r = np.array(rets)
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
    P = load_panel()
    print(f"Panel: {P.shape[0]} bars × {P.shape[1]} names  ({P.index.min().date()} → {P.index.max().date()})")
    print(f"Split: {SPLIT.date()}")
    print()

    full = lambda d: True
    second_half = lambda d: d >= SPLIT
    first_half = lambda d: d < SPLIT

    # ---- 1. Pre-registered rule on 2H ----
    print("=== 1. Pre-registered rule (12m, monthly) — full vs 2H holdout ===\n")
    full_res = run_combo(P, 252, 21, full)
    h2_res = run_combo(P, 252, 21, second_half)
    h1_res = run_combo(P, 252, 21, first_half)
    print(f"  Full sample (2017→2026):  ann={full_res['annualised_pct']:>6.2f}%  n={full_res['n_periods']}  "
          f"sharpe={full_res['sharpe_like']:>5.2f}  hit={full_res['hit_rate_pct']:>5.1f}%  "
          f"maxDD={full_res['max_dd_pct']:>6.2f}%")
    print(f"  1H (2017→2021-12):        ann={h1_res['annualised_pct']:>6.2f}%  n={h1_res['n_periods']}  "
          f"sharpe={h1_res['sharpe_like']:>5.2f}  hit={h1_res['hit_rate_pct']:>5.1f}%  "
          f"maxDD={h1_res['max_dd_pct']:>6.2f}%")
    print(f"  2H (2022→2026):           ann={h2_res['annualised_pct']:>6.2f}%  n={h2_res['n_periods']}  "
          f"sharpe={h2_res['sharpe_like']:>5.2f}  hit={h2_res['hit_rate_pct']:>5.1f}%  "
          f"maxDD={h2_res['max_dd_pct']:>6.2f}%")
    decay = h2_res['annualised_pct'] - h1_res['annualised_pct']
    print(f"\n  2H − 1H = {decay:+.2f}pp  (positive = no decay; negative = decay)")

    # Decision band
    h2_ann = h2_res['annualised_pct']
    if h2_ann >= 3.0:
        decision = "TIME-STABLE — proceed to paper trade"
    elif h2_ann >= 1.0:
        decision = "DECAYED-BUT-REAL — haircut confirmed; consider with caution"
    elif h2_ann >= 0.0:
        decision = "EDGE GONE in 2H — abandon, not tradeable"
    else:
        decision = "NEGATIVE in 2H — strategy destroyed post-2021"
    print(f"\n  Decision: {decision}")
    print()

    # ---- 2. 12-rule re-rank on 2H ----
    print("=== 2. All 12 combinations re-ranked on 2H (2022-2026) ===\n")
    rows = []
    for d_name, d_lb in DESCRIPTORS.items():
        for r_name, r_step in REBALANCES.items():
            r_full = run_combo(P, d_lb, r_step, full)
            r_h2 = run_combo(P, d_lb, r_step, second_half)
            rows.append({
                "descriptor": d_name,
                "rebalance": r_name,
                "full_ann_pct": r_full.get("annualised_pct"),
                "h2_ann_pct": r_h2.get("annualised_pct"),
                "h2_n": r_h2.get("n_periods"),
                "h2_sharpe": r_h2.get("sharpe_like"),
                "h2_hit_pct": r_h2.get("hit_rate_pct"),
            })
    df = pd.DataFrame(rows).sort_values("h2_ann_pct", ascending=False, na_position="last")
    df.insert(0, "h2_rank", range(1, len(df) + 1))
    df["full_rank"] = df["full_ann_pct"].rank(ascending=False, method="min").astype(int)
    print(df.to_string(index=False))

    # Where is 12m monthly on 2H?
    rule_row = df[(df["descriptor"] == "12m") & (df["rebalance"] == "monthly")].iloc[0]
    print(f"\n  12m monthly on 2H: rank {int(rule_row['h2_rank'])} / 12  "
          f"(ann {rule_row['h2_ann_pct']:>6.2f}%, full-sample was rank {int(rule_row['full_rank'])} / 12)")

    # Save
    out = {
        "split_date": str(SPLIT.date()),
        "pre_registered_rule": {
            "descriptor": "12m",
            "rebalance": "monthly",
            "full": full_res,
            "first_half": h1_res,
            "second_half": h2_res,
            "decay_2h_minus_1h_pp": round(decay, 2),
            "decision": decision,
        },
        "twelve_combo_rerank_2h": rows,
        "rule_2h_rank": int(rule_row["h2_rank"]),
    }
    out_p = DATA / "hk_mom_holdout.json"
    json.dump(out, open(out_p, "w"), indent=2)
    print(f"\nWrote {out_p}")


if __name__ == "__main__":
    main()
