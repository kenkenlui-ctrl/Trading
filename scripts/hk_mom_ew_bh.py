#!/usr/bin/env python3
"""hk_mom_ew_bh.py — EW buy-and-hold baseline, turnover measurement, delisted count.

Three things in one pass:
  1. Equal-weight buy-and-hold of the entire panel universe, same window.
     This is the universe-effect baseline: what HK equal-weight survivors earned,
     no momentum, no selection. If it matches the 15% headline, the strategy is
     'buy HK survivors', and the momentum rule adds only the difference.
  2. Actual turnover of the top-20% momentum basket, measured from holdings.
     Used to tighten the cost model: real round-trip = 0.25% * 2 * turnover_fraction.
  3. Names whose last bar is BEFORE the panel end (2026-10-05). This is a
     survivorship lower bound: a name that stopped trading inside the window is
     either suspended, delisted, or fell out of the universe. The histogram of
     last-bar dates is observable attrition.
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

PANEL_END = pd.Timestamp("2026-10-05")


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


def annualised(returns: np.ndarray, periods_per_year: float) -> float:
    return float(returns.mean() * periods_per_year * 100)


def stats(returns: np.ndarray, periods_per_year: float) -> dict:
    eq = np.cumprod(1 + returns)
    dd = eq / np.maximum.accumulate(eq) - 1
    return {
        "n": int(len(returns)),
        "annualised_pct": round(annualised(returns, periods_per_year), 2),
        "vol_pct": round(float(returns.std(ddof=1) * np.sqrt(periods_per_year) * 100), 2),
        "sharpe_like": round(float(returns.mean() / returns.std(ddof=1) * np.sqrt(periods_per_year)), 3),
        "max_dd_pct": round(float(dd.min() * 100), 2),
    }


def main():
    P = load_panel()
    H = load_hsi().reindex(P.index).ffill()
    print(f"Panel: {P.shape[0]} bars × {P.shape[1]} names  (window {P.index.min().date()} → {P.index.max().date()})")
    print()

    # ---- 1. EW buy-and-hold ----
    # Equal-weight across all names, rebalanced to equal-weight only at the start
    # (i.e. once at t=0). Standard "buy-and-hold" definition. No transactions after.
    valid_t0 = P.iloc[LB].dropna()
    valid_t0 = valid_t0[valid_t0 > 0]
    n_ew = len(valid_t0)
    weights = pd.Series(1.0 / n_ew, index=valid_t0.index)
    # Portfolio value at t = sum(weight_i * price_i(t) / price_i(t0))
    # Returns per period: (V_t / V_{t-1}) - 1
    P_aligned = P[valid_t0.index]
    norm = P_aligned.div(P_aligned.iloc[0])
    portfolio_value = (norm * weights).sum(axis=1)
    periods = portfolio_value.iloc[LB:]
    rets_ew = periods.pct_change().dropna().to_numpy()
    spy = 252 / STEP
    # For an annualised figure over 10y, just use total period: 10.16 years
    years = (periods.index[-1] - periods.index[0]).days / 365.25
    total_ret = float(portfolio_value.iloc[-1] / portfolio_value.iloc[LB] - 1)
    ann_ew = ((1 + total_ret) ** (1 / years) - 1) * 100
    print("=== 1. EW buy-and-hold (no momentum, no rebalance after t0) ===")
    print(f"  n names at t0:               {n_ew}")
    print(f"  Total return {years:.2f}y:    {total_ret*100:.2f}%")
    print(f"  Annualised:                  {ann_ew:.2f}%")
    print(f"  Vol (period):                {rets_ew.std(ddof=1)*100:.2f}%/period, {rets_ew.std(ddof=1)*np.sqrt(spy)*100:.2f}% annualised")
    # Monthly bucket for period-level comparison
    monthly_rets = periods.resample("ME").last().pct_change().dropna().to_numpy()
    print(f"  Annualised (monthly bucket): {annualised(monthly_rets, 12):.2f}%")
    print()

    # ---- 2. Actual turnover of top-20% momentum basket ----
    print("=== 2. Top-20% momentum basket — actual turnover ===")
    dates = list(range(LB, len(P) - STEP, STEP))
    held_records, traded_records, gross_rets = [], [], []
    prev = None
    for i in dates:
        s = (P.iloc[i] / P.iloc[i - LB] - 1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(s) < 40:
            continue
        k = max(1, int(round(len(s) * TOP_FRAC)))
        top = s.sort_values(ascending=False).index[:k]
        held_records.append({"date": str(P.index[i].date()), "n": int(len(top))})
        if prev is not None:
            traded = len(set(top) ^ prev)
            traded_records.append({"date": str(P.index[i].date()), "traded": int(traded), "held": int(len(top)),
                                   "turnover_pct": round(traded / len(top) * 100, 1)})
        fut = P.iloc[i + 1:i + 1 + STEP]
        px = P.iloc[i]
        gross_rets.append(float(fut[list(top)].div(px[list(top)], axis=1).mean(axis=1).mean()) - 1)
        prev = set(top)
    if traded_records:
        df_tr = pd.DataFrame(traded_records)
        avg_turn = float(df_tr["turnover_pct"].mean())
        med_turn = float(df_tr["turnover_pct"].median())
        max_turn = float(df_tr["turnover_pct"].max())
        print(f"  Rebalances measured:         {len(df_tr)}")
        print(f"  Avg turnover per rebalance:  {avg_turn:.1f}%")
        print(f"  Median turnover:             {med_turn:.1f}%")
        print(f"  Max turnover:                {max_turn:.1f}%")
        # Cost: 0.25% RT = 0.125% one-way. Charged pro-rata on traded fraction.
        # Real annual cost = 0.25% * avg_turnover/100 * periods_per_year
        real_cost_pa = 0.0025 * (avg_turn / 100) * spy * 100
        print(f"  Real RT cost pa (using 0.25% RT × turn/100 × 12): {real_cost_pa:.2f}%/year")
        # If turnover is ~85%, real cost is ~2.55%/year, more than doubles the engine's assumption
    else:
        avg_turn = 0
        print("  No turnover records.")

    # Long-only net return at real cost
    gross_arr = np.array(gross_rets[1:])  # align with traded_records
    net_arr = gross_arr - (0.0025 * (avg_turn / 100))
    ann_gross = annualised(gross_arr, spy)
    ann_net_real = annualised(net_arr, spy)
    ann_net_engine = annualised(gross_arr - 0.0025 * (13.0 / 30.0), spy)  # engine's model
    print(f"  Top-20% gross annualised:    {ann_gross:.2f}%")
    print(f"  Top-20% net (real turnover): {ann_net_real:.2f}%  ← uses measured turnover")
    print(f"  Top-20% net (engine model):  {ann_net_engine:.2f}%  ← engine assumes 13/30=43% turn")
    print()

    # ---- 3. Survivorship: last-bar histogram ----
    print("=== 3. Survivorship lower bound (names whose last bar is before panel end) ===")
    last_bar = P.apply(lambda c: c.last_valid_index())
    last_bar_dates = last_bar.dropna()
    n_total = len(last_bar_dates)
    n_active = (last_bar_dates >= PANEL_END - pd.Timedelta(days=30)).sum()
    n_silent = n_total - n_active
    print(f"  Total names in panel:        {n_total}")
    print(f"  Active (last bar ≥ 2026-09-05): {n_active}")
    print(f"  Silent (last bar earlier):   {n_silent}  ← lower bound on attrition")
    print()
    print("  Last-bar year distribution:")
    yr = last_bar_dates.dt.year.value_counts().sort_index()
    for y, c in yr.items():
        bar = "█" * min(int(c), 50)
        print(f"    {y}: {c:>4}  {bar}")
    # Names silent ≥ 1 year before panel end
    cutoff_silent = PANEL_END - pd.Timedelta(days=365)
    n_silent_year = (last_bar_dates < cutoff_silent).sum()
    print(f"\n  Silent ≥ 1y before panel end: {n_silent_year}  ← attrition lower bound")
    # These are the names that disappeared in the run. Re-running the strategy
    # WITHOUT these names would give an upper-bound-clean figure.

    # ---- Save outputs ----
    out = {
        "ew_buy_and_hold": {
            "n_names": int(n_ew),
            "years": round(years, 2),
            "total_return_pct": round(total_ret * 100, 2),
            "annualised_pct": round(ann_ew, 2),
            "monthly_bucket_annualised_pct": round(annualised(monthly_rets, 12), 2),
        },
        "top20_turnover": {
            "n_rebalances": int(len(traded_records)) if traded_records else 0,
            "avg_turnover_pct": round(avg_turn, 1),
            "median_turnover_pct": round(med_turn, 1) if traded_records else None,
            "max_turnover_pct": round(max_turn, 1) if traded_records else None,
            "real_cost_pa_pct": round(real_cost_pa, 2) if traded_records else None,
            "top20_gross_annualised_pct": round(ann_gross, 2),
            "top20_net_real_annualised_pct": round(ann_net_real, 2),
            "top20_net_engine_annualised_pct": round(ann_net_engine, 2),
        },
        "survivorship_lower_bound": {
            "panel_end": str(PANEL_END.date()),
            "n_total": int(n_total),
            "n_active": int(n_active),
            "n_silent": int(n_silent),
            "n_silent_1y": int(n_silent_year),
        },
    }
    out_p = DATA / "hk_mom_ew_bh.json"
    json.dump(out, open(out_p, "w"), indent=2)
    print(f"\nWrote {out_p}")


if __name__ == "__main__":
    main()
