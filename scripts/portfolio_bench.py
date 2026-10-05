"""portfolio_bench.py — the honest alpha question.

portfolio_diag already showed the universe itself beats the index every single
year. That means the only fair benchmark for v2 is NOT the index, it is:
    B1 = equal-weight monthly rebalance of the same 201 names
If v2 lands on top of B1, then v2 is not an edge, it is the selection bias
with extra steps.

Also stress-tests the bias: strip the 20 best-performing names out of the
benchmark universe and see whether the "universe premium" survives.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
sys.path.insert(0, str(REPO / "scripts"))
from core import features, _simulate, cost_rt  # noqa: E402
from portfolio_diag import load, up_series, cands_v2, cands_control, walk  # noqa: E402

BARS = REPO / "data" / "bt10y"
MARKETS = {"us200": ("SPY", "IDX_GSPC"), "jp200": ("1306_T", "IDX_N225")}


def stats(eq: pd.Series, label: str) -> dict:
    e = eq.to_numpy(float)
    r = e[-1] / e[0] - 1
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return {"label": label, "total%": r * 100, "cagr%": ((e[-1] / e[0]) ** (1 / yrs) - 1) * 100,
            "maxDD%": (e / np.maximum.accumulate(e) - 1).min() * 100}


def main() -> int:
    rows = []
    for mkt in MARKETS:
        up = up_series(mkt)
        syms = []
        acc = {"v2": [], "control": []}
        for p in sorted(BARS.glob("*.json")):
            s = p.stem
            is_jp = s.endswith("_T")
            is_us = (not is_jp) and ("_" not in s) and not s.startswith(("HK_", "JP_"))
            if mkt == "jp200" and not is_jp:
                continue
            if mkt == "us200" and not is_us:
                continue
            b = load(p)
            if b is None:
                continue
            syms.append((s, b))
            for lab, cs in (("v2", cands_v2(b, mkt)), ("control", cands_control(b, mkt, up.reindex(b.index)))):
                for c in cs:
                    c = dict(c); c["sym"] = s
                    acc[lab].append(c)

        ib = load(BARS / f"{MARKETS[mkt][0]}.json")
        cal = ib.index
        px = pd.DataFrame({s: b.close for s, b in syms}).reindex(cal).ffill()
        r = px.pct_change()
        yrs = (cal[-1] - cal[0]).days / 365.25

        # B0 index buy & hold
        rows.append(stats(ib.close, f"{mkt} B0 index B&H"))

        # B1 universe equal-weight MONTHLY rebalance
        # axis=1 is load-bearing: .prod() on a DataFrame collapses ACROSS columns
        # (one number per ticker) instead of down the time axis.
        eq = (1 + r.groupby([cal.year, cal.month]).transform("mean")).prod(axis=1)
        b1 = 100_000 * eq.cumprod()
        rows.append(stats(b1, f"{mkt} B1 universe EW"))

        # B2 same, minus the 20 best 10y performers -> is the premium just survivors?
        tot = px.iloc[-1] / px.iloc[0] - 1
        drop = tot.sort_values(ascending=False).head(20).index.tolist()
        r2 = px.drop(columns=drop).pct_change()
        eq2 = (1 + r2.groupby([cal.year, cal.month]).transform("mean")).prod(axis=1)
        b2 = 100_000 * eq2.cumprod()
        rows.append(stats(b2, f"{mkt} B2 universe EW -top20"))

        # B3 daily equal-weight rebalance (no monthly batching)
        rows.append(stats(100_000 * (1 + r.mean(axis=1)).cumprod(), f"{mkt} B3 universe daily EW"))

        # strategy arms, all fixes
        for lab in ("v2", "control"):
            curve, _, _ = walk(acc[lab], mkt, net=True, per_name_cap=True)
            if curve is None:
                continue
            rows.append(stats(curve.set_index("date").equity, f"{mkt} {lab} (fixed)"))

    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(df.to_string(index=False, float_format=lambda x: f"{x:+.1f}"))

    print("\n" + "=" * 74)
    print("HONEST ALPHA  (v2 minus the SAME-universe equal-weight benchmark)")
    for mkt in MARKETS:
        g = df[df.label.str.startswith(mkt)].set_index("label")
        try:
            v2 = g.loc[f"{mkt} v2 (fixed)"].cagr
            b1 = g.loc[f"{mkt} B1 universe EW"].cagr
            b0 = g.loc[f"{mkt} B0 index B&H"].cagr
            b2 = g.loc[f"{mkt} B2 universe EW -top20"].cagr
        except KeyError:
            continue
        print(f"  {mkt:6} v2 CAGR {v2:+6.1f}%  |  universe EW {b1:+6.1f}%  |  "
              f"index {b0:+6.1f}%  |  EW-top20 {b2:+6.1f}%")
        print(f"         v2 - universeEW = {v2 - b1:+.1f} pp/yr     "
              f"universeEW - index = {b1 - b0:+.1f} pp/yr")
    df.to_csv(REPO / "data" / "portfolio_bench.csv", index=False)
    print(f"\nwrote {REPO / 'data' / 'portfolio_bench.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
