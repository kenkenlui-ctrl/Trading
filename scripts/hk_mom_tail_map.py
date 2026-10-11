#!/usr/bin/env python3
"""hk_mom_tail_map.py — map the left tail of the top-10% momentum basket.

No circuit breaker. Just run the basket and report the distribution of
period returns, so the question "is the basket itself untradeable, or is the
breaker design wrong?" gets an answer from data instead of opinion.

Also runs Barroso & Santa-Clara (2015 JFE) vol-scaling as the principled
alternative to a binary breaker: scale exposure by the basket's own trailing
6-month realized vol, targeting constant risk. A binary breaker flattens you
in the middle of a crash; vol-scaling shrinks exposure BEFORE the crash.

Outputs:
  - tail count per threshold per window (no-breaker)
  - worst day per window (no-breaker)
  - vol-scaled return distribution per window
  - hit rate and maxDD for both variants
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
TOP_FRAC = 0.10
SPLIT = pd.Timestamp("2022-01-01")
VOL_WINDOW = 126          # 6 months of 21-bar returns
VOL_TARGET = 0.15         # target annualised vol for the basket


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


def score(panel: pd.DataFrame, i: int) -> pd.Series:
    return (panel.iloc[i] / panel.iloc[i - LB] - 1).replace([np.inf, -np.inf], np.nan).dropna()


def run(panel: pd.DataFrame, vol_scale: bool) -> pd.DataFrame:
    """Run the basket. vol_scale=False → fixed exposure. True → Barroso-Santa-Clara."""
    dates = list(range(LB, len(panel) - STEP, STEP))
    rows = []
    prev = None
    realized_vol_hist = []   # trailing vol of basket returns
    for n, i in enumerate(dates):
        s = score(panel, i)
        if len(s) < 50:
            continue
        k = max(2, int(round(len(s) * TOP_FRAC)))
        top = sorted(s.sort_values(ascending=False).index[:k])
        fut = panel.iloc[i + 1:i + 1 + STEP]
        px = panel.iloc[i]
        basket_ret = float(fut[list(top)].div(px[list(top)], axis=1).mean(axis=1).mean()) - 1
        one_way = len(top) if prev is None else len(set(top) ^ prev)
        cost = COST_RT * one_way / len(top)
        net = basket_ret - cost

        # Vol scaling: exposure = target_vol / trailing_realised_vol, capped at 1.0
        exposure = 1.0
        if vol_scale and len(realized_vol_hist) >= 6:
            trailing = np.array(realized_vol_hist[-VOL_WINDOW:])
            trailing_ann = float(trailing.std(ddof=1) * np.sqrt(252 / STEP))
            if trailing_ann > 1e-6:
                exposure = min(1.0, VOL_TARGET / trailing_ann)
        realized_vol_hist.append(net)

        rows.append({
            "signal_date": str(panel.index[i].date()),
            "effective_date": str(panel.index[i + 1].date()),
            "n_held": len(top),
            "turnover_pct": round(one_way / len(top) * 100, 1),
            "basket_ret_pct": round(basket_ret * 100, 4),
            "cost_pct": round(cost * 100, 4),
            "net_ret_pct": round(net * 100, 4),
            "exposure": round(exposure, 4),
            "scaled_net_pct": round(net * exposure * 100, 4),
        })
        prev = set(top)
    return pd.DataFrame(rows)


def tail_report(df: pd.DataFrame, col: str, label: str):
    # df[col] is already in PERCENT units (e.g. -5.5 means -5.5%).
    # Convert once to fraction for math, and back to percent for display.
    r = df[col].to_numpy(float) / 100
    n = len(r)
    counts = {f"<=-{t}%": int((r <= -t / 100).sum()) for t in (2, 3, 5, 8)}
    worst_pct = float(r.min()) * 100
    worst_date = df.loc[df[col].idxmin(), "effective_date"]
    eq = np.cumprod(1 + r)
    dd = eq / np.maximum.accumulate(eq) - 1
    ann = float(r.mean() * (252 / STEP) * 100)
    maxdd_pct = float(dd.min()) * 100
    print(f"\n--- {label} ---")
    print(f"  periods:            {n}")
    print(f"  annualised:         {ann:+.2f}%")
    print(f"  max drawdown:       {maxdd_pct:+.2f}%")
    print(f"  hit rate:           {(r > 0).mean() * 100:.1f}%")
    print(f"  worst MONTH:        {worst_pct:+.2f}%  (period ending {worst_date})")
    print(f"  tail counts:        " + "  ".join(f"{k}: {v}" for k, v in counts.items()))
    tail = r <= -0.03
    streak = best = 0
    for t in tail:
        streak = streak + 1 if t else 0
        best = max(best, streak)
    print(f"  worst <-3% streak:  {best} consecutive months")
    return {
        "n_periods": n,
        "annualised_pct": round(ann, 2),
        "max_dd_pct": round(maxdd_pct, 2),
        "hit_rate_pct": round(float((r > 0).mean() * 100), 1),
        "worst_month_pct": round(worst_pct, 2),
        "worst_month_end": worst_date,
        "tail_counts": counts,
        "worst_streak_le_3pct": int(best),
    }


def main():
    panel = load_panel()
    print(f"Panel: {panel.shape[0]} bars × {panel.shape[1]} names  ({panel.index.min().date()} → {panel.index.max().date()})")

    results = {}
    for vol_scale, tag in [(False, "fixed"), (True, "vol_scaled")]:
        df = run(panel, vol_scale)
        col = "net_ret_pct" if not vol_scale else "scaled_net_pct"
        label = "Fixed exposure (no breaker)" if not vol_scale else "Barroso-Santa-Clara vol-scaled"
        print(f"\n{'=' * 64}\n{label}\n{'=' * 64}")
        print(f"Average exposure: {df['exposure'].mean():.3f}  min: {df['exposure'].min():.3f}")
        for wname, wfilter in [("1H 2017-2021", lambda d: pd.Timestamp(d) < SPLIT),
                               ("2H 2022-2026", lambda d: pd.Timestamp(d) >= SPLIT),
                               ("FULL 9.1y", lambda d: True)]:
            sub = df[df["effective_date"].map(wfilter)]
            if sub.empty:
                continue
            results[f"{tag}_{wname}"] = tail_report(sub, col, f"{label} · {wname}")

    out_p = DATA / "hk_mom_tail_map.json"
    json.dump(results, open(out_p, "w"), indent=2)
    print(f"\nWrote {out_p}")


if __name__ == "__main__":
    main()