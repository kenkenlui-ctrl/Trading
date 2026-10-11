#!/usr/bin/env python3
"""hk_mom_cross_market.py — top 10% (D1) cross-market test.

Runs the same 12m-monthly top-decile rule on US and JP panels, full + 2H.
Compares to local EW B&H and main index for same-window decomposition.

If HK's "top 10% stable in 2H" is real alpha, US/JP should show similar
stable top-decile behaviour. If it's an HK-specific artefact (e.g.
point-in-time membership quirk in HK), US/JP will diverge.
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
SPLIT = pd.Timestamp("2022-01-01")

# HK, US, JP — separate bars dirs
BARS_DIRS = {
    "HK": (DATA / "bt10y2_hk", None),  # HK has separate dir already
    "US": (DATA / "bt10y2", lambda p: not (p.stem.endswith("_HK") or p.stem.endswith("_T"))),
    "JP": (DATA / "bt10y2", lambda p: p.stem.endswith("_T")),
}
INDEX_FILE = {
    "HK": "IDX_HSI",
    "US": "IDX_GSPC",
    "JP": "IDX_N225",
}


def load_panel_market(market: str) -> pd.DataFrame:
    bars_dir, filt = BARS_DIRS[market]
    mask = build_mask(bars_dir)
    if market == "HK":
        masked = {k for k in mask if k.endswith("_HK")}
    else:
        masked = mask
    series = []
    for p in sorted(bars_dir.glob("*.json")):
        if filt and not filt(p):
            continue
        if p.stem in masked:
            continue
        b = load(p)
        if b is None or b.empty:
            continue
        series.append(b.close.rename(p.stem))
    return pd.concat(series, axis=1).sort_index()


def load_index(market: str) -> pd.Series:
    p = DATA / "bt10y2" / f"{INDEX_FILE[market]}.json"
    if not p.exists():
        return None
    rows = json.loads(p.read_text())
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["close"].sort_index()


def ew_bh(panel: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Equal-weight buy-and-hold annualised between start and end."""
    p_window = panel.loc[start:end]
    if p_window.empty:
        return None
    # Find first valid bar
    p_window = p_window.dropna(how="all")
    if p_window.empty:
        return None
    t0_px = p_window.iloc[0]
    valid = t0_px[t0_px > 0]
    if len(valid) < 20:
        return None
    w = pd.Series(1.0 / len(valid), index=valid.index)
    norm = p_window[valid.index].div(t0_px[valid.index])
    v = (norm * w).sum(axis=1).dropna()
    if len(v) < 2:
        return None
    years = (v.index[-1] - v.index[0]).days / 365.25
    if years < 0.5:
        return None
    return float(((v.iloc[-1] / v.iloc[0]) ** (1 / years) - 1) * 100)


def top_decile(panel: pd.DataFrame, top_frac: float, period_filter=None) -> dict:
    """Run top-decile momentum strategy. Returns {gross_ann, net_ann, sharpe, hit, n, turnover, ...}."""
    dates = list(range(LB, len(panel) - STEP, STEP))
    if period_filter is not None:
        dates = [d for d in dates if period_filter(panel.index[d])]
    rets, traded_recs, prev = [], [], None
    for i in dates:
        s = (panel.iloc[i] / panel.iloc[i - LB] - 1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(s) < 50:
            continue
        k = max(2, int(round(len(s) * top_frac)))
        top = s.sort_values(ascending=False).index[:k]
        if prev is not None:
            traded_recs.append({"traded": int(len(set(top) ^ prev)), "held": int(len(top))})
        fut = panel.iloc[i + 1:i + 1 + STEP]
        px = panel.iloc[i]
        r = float(fut[list(top)].div(px[list(top)], axis=1).mean(axis=1).mean()) - 1
        rets.append(r)
        prev = set(top)
    r = np.array(rets)
    if len(r) < 6:
        return None
    spy = 252 / STEP
    eq = np.cumprod(1 + r)
    dd = eq / np.maximum.accumulate(eq) - 1
    if traded_recs:
        df = pd.DataFrame(traded_recs)
        avg_turn = float(df["traded"].mean() / df["held"].mean() * 100)
        real_cost_pa = 0.0025 * (avg_turn / 100) * spy * 100
    else:
        avg_turn = real_cost_pa = 0
    gross_ann = float(r.mean() * spy * 100)
    return {
        "n_periods": int(len(r)),
        "gross_ann_pct": round(gross_ann, 2),
        "net_ann_pct": round(gross_ann - real_cost_pa, 2),
        "sharpe": round(float(r.mean() / r.std(ddof=1) * np.sqrt(spy)), 3),
        "hit_pct": round(float((r > 0).mean() * 100), 1),
        "max_dd_pct": round(float(dd.min() * 100), 2),
        "avg_turnover_pct": round(avg_turn, 1),
        "real_cost_pa_pct": round(real_cost_pa, 2),
    }


def main():
    results = {}
    for market in ["HK", "US", "JP"]:
        print(f"\n========== {market} ==========")
        P = load_panel_market(market)
        # Drop IDX_* columns if any
        P = P[[c for c in P.columns if not c.startswith("IDX_")]]
        print(f"Panel: {P.shape[0]} bars × {P.shape[1]} names  "
              f"({P.index.min().date()} → {P.index.max().date()})")

        # Find common start for fair comparison
        idx = load_index(market)
        if idx is not None:
            idx = idx.reindex(P.index).ffill()
            # Use the index start as comparison anchor
            idx_ann_full = ew_bh(pd.DataFrame(idx), P.index.min(), P.index.max())
            idx_ann_2h = ew_bh(pd.DataFrame(idx), SPLIT, P.index.max())
        else:
            idx_ann_full = idx_ann_2h = None

        ew_full = ew_bh(P, P.index.min(), P.index.max())
        ew_2h = ew_bh(P, SPLIT, P.index.max())

        if idx_ann_full is not None:
            print(f"  Index full ann%: {idx_ann_full:.2f}  |  2H ann%: {idx_ann_2h:.2f}")
        if ew_full is not None:
            print(f"  EW B&H  full ann%: {ew_full:.2f}  |  2H ann%: {ew_2h:.2f}" if ew_2h else f"  EW B&H  full ann%: {ew_full:.2f}")

        # Top 10% and top 20% in 2H
        for frac, lab in [(0.10, "Top 10%"), (0.20, "Top 20%")]:
            full = top_decile(P, frac, None)
            h2 = top_decile(P, frac, lambda d: d >= SPLIT)
            if full:
                print(f"  {lab} full: gross={full['gross_ann_pct']:>6.2f}%  net={full['net_ann_pct']:>6.2f}%  "
                      f"sharpe={full['sharpe']:>5.2f}  hit={full['hit_pct']:>5.1f}%  turn={full['avg_turnover_pct']:.1f}%  cost={full['real_cost_pa_pct']:.2f}%")
            if h2:
                print(f"  {lab} 2H:   gross={h2['gross_ann_pct']:>6.2f}%  net={h2['net_ann_pct']:>6.2f}%  "
                      f"sharpe={h2['sharpe']:>5.2f}  hit={h2['hit_pct']:>5.1f}%  turn={h2['avg_turnover_pct']:.1f}%  cost={h2['real_cost_pa_pct']:.2f}%")

            results[f"{market}_{lab.replace(' ', '_').replace('%', 'pct')}"] = {
                "full": full, "2H": h2,
                "ew_full_pct": round(ew_full, 2) if ew_full is not None else None,
                "ew_2h_pct": round(ew_2h, 2) if ew_2h is not None else None,
                "index_full_pct": round(idx_ann_full, 2) if idx_ann_full is not None else None,
                "index_2h_pct": round(idx_ann_2h, 2) if idx_ann_2h is not None else None,
            }
            # Same-window selection premium
            if h2 and ew_2h is not None:
                sel = round(h2["net_ann_pct"] - ew_2h, 2)
                print(f"  {lab} 2H selection premium over EW (same window): {sel:+}pp")
                results[f"{market}_{lab.replace(' ', '_').replace('%', 'pct')}"]["selection_premium_2h_pp"] = sel

    out_p = DATA / "hk_mom_cross_market.json"
    json.dump(results, open(out_p, "w"), indent=2)
    print(f"\nWrote {out_p}")

    # Cross-market summary
    print("\n\n========== Cross-market summary (2H = 2022-2026) ==========")
    print(f"  {'market':<6} {'basket':<10} {'net_ann%':>9} {'sharpe':>7} {'hit%':>6} {'sel_prem_pp':>13}")
    for k, v in results.items():
        if v.get("2H"):
            sel = v.get("selection_premium_2h_pp", "")
            print(f"  {k.split('_')[0]:<6} {k.split('_', 1)[1]:<10} "
                  f"{v['2H']['net_ann_pct']:>8.2f} {v['2H']['sharpe']:>6.2f} {v['2H']['hit_pct']:>5.1f}  {sel if sel != '' else 'n/a':>12}")


if __name__ == "__main__":
    main()
