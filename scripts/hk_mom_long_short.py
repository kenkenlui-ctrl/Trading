#!/usr/bin/env python3
"""hk_mom_long_short.py — separate edge from HK beta.

For each monthly rebalance (12m lookback, top 20%):
  - long basket  = top 20% by 12m return
  - short basket = bottom 20% by 12m return
  - benchmark    = HSI close-to-close return over same period

Reports annualised % for: long, short, long-short, long − HSI, HSI.
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
TOP_FRAC = 0.20
BOTTOM_FRAC = 0.20


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


def load_hsi() -> pd.Series:
    p = DATA / "bt10y2" / "IDX_HSI.json"
    rows = json.loads(p.read_text())
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["close"].sort_index()


def stats(returns: np.ndarray, periods_per_year: float) -> dict:
    eq = np.cumprod(1 + returns)
    dd = eq / np.maximum.accumulate(eq) - 1
    return {
        "n": int(len(returns)),
        "annualised_pct": round(float(returns.mean() * periods_per_year * 100), 2),
        "vol_pct": round(float(returns.std(ddof=1) * np.sqrt(periods_per_year) * 100), 2),
        "sharpe_like": round(float(returns.mean() / returns.std(ddof=1) * np.sqrt(periods_per_year)), 3),
        "hit_rate_pct": round(float((returns > 0).mean() * 100), 1),
        "max_dd_pct": round(float(dd.min() * 100), 2),
    }


def run() -> dict:
    P = load_panel()
    H = load_hsi().reindex(P.index).ffill()

    dates = list(range(LB, len(P) - STEP, STEP))
    long_rets, short_rets, hsi_rets, cost_l, cost_s = [], [], [], [], []
    for i in dates:
        s = (P.iloc[i] / P.iloc[i - LB] - 1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(s) < 40:
            continue
        k_top = max(1, int(round(len(s) * TOP_FRAC)))
        k_bot = max(1, int(round(len(s) * BOTTOM_FRAC)))
        top = s.sort_values(ascending=False).index[:k_top]
        bot = s.sort_values(ascending=True).index[:k_bot]

        fut = P.iloc[i + 1:i + 1 + STEP]
        px = P.iloc[i]
        long_ret = float(fut[list(top)].div(px[list(top)], axis=1).mean(axis=1).mean()) - 1
        short_ret = float(fut[list(bot)].div(px[list(bot)], axis=1).mean(axis=1).mean()) - 1

        h_fut = H.iloc[i + 1:i + 1 + STEP]
        hsi_ret = float(h_fut.iloc[-1] / H.iloc[i] - 1) if len(h_fut) else np.nan

        long_rets.append(long_ret)
        short_rets.append(short_ret)
        hsi_rets.append(hsi_ret)
        cost_l.append(COST_RT)
        cost_s.append(COST_RT)

    long_arr = np.array(long_rets) - np.array(cost_l)
    short_arr = -np.array(short_rets) - np.array(cost_s)   # short leg
    hsi_arr = np.array(hsi_rets)
    ls_arr = long_arr + (-np.array(short_rets))   # gross long-short (no extra cost; both legs already costed)
    long_minus_hsi = long_arr - hsi_arr
    periods_per_year = 252 / STEP

    out = {
        "universe_n": int(P.shape[1]),
        "window": [str(P.index.min().date()), str(P.index.max().date())],
        "long_only": stats(long_arr, periods_per_year),
        "short_only": stats(-(-np.array(short_rets)) - np.array(cost_s), periods_per_year),  # short leg gross
        "long_short_ls": stats(ls_arr, periods_per_year),
        "long_minus_hsi": stats(long_minus_hsi, periods_per_year),
        "hsi_benchmark": stats(hsi_arr, periods_per_year),
        "cost_assumed": COST_RT,
        "note": "long_short_ls: equal-weight long top 20% + equal-weight short bottom 20%, both legs cost 0.25% RT. "
                "long_minus_hsi: long top 20% vs HSI close-to-close over same period.",
    }
    return out


if __name__ == "__main__":
    out = run()
    print("=== HK 12m momentum, long / short / long-short / vs HSI (monthly, 252d lookback) ===\n")
    for k in ["long_only", "long_short_ls", "long_minus_hsi", "hsi_benchmark", "short_only"]:
        v = out[k]
        print(f"{k:>18}: ann={v['annualised_pct']:>6.2f}%  vol={v['vol_pct']:>5.2f}%  "
              f"sharpe={v['sharpe_like']:>5.2f}  hit={v['hit_rate_pct']:>5.1f}%  "
              f"maxDD={v['max_dd_pct']:>6.2f}%  n={v['n']}")
    print()
    print(f"Universe: {out['universe_n']} names, window {out['window']}")
    print(f"Cost: {out['cost_assumed']*100:.2f}% RT per leg, applied pro-rata to traded fraction.")
    print(f"\nNote: {out['note']}")
    out_p = DATA / "hk_momentum_long_short.json"
    json.dump(out, open(out_p, "w"), indent=2)
    print(f"\nWrote {out_p}")
