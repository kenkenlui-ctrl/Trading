"""portfolio_test.py — v2 vs control at the portfolio level, against the only
benchmark that can actually answer the question.

REVISION HISTORY (do not read the old numbers as current)
    v1 of this file printed us200 v2 +1811.8%, CAGR +34.0%, Sharpe 2.97. Three
    separate defects, all of which inflated the result:
      1. it computed a `net` column with costs and then never used it — P&L was
         booked gross
      2. MAX_NOTIONAL was capped PER TRADE, not per NAME, so up to 5 lots of the
         same stock could sit in a 10-slot book (verified: peak 5 lots of one name)
      3. Sharpe was differenced across event dates and annualised as if every gap
         were one trading day, which inflates it whenever the curve is sparse
    On top of that the bar store itself had isolated bars printed at the wrong
    price scale. See bt_clean.py.

WHY THE BENCHMARK CHANGED
    The 201-name universe is today's large caps applied retroactively. Equal
    weighted, that universe beat its own index in 11 of 11 years in the US. So
    "v2 beat SPY" is not evidence of an edge — it is evidence of a universe that
    was chosen with hindsight. The question that means something is:
        v2  vs  equal-weight monthly rebalance of the SAME names
    If v2 lands on that benchmark, the strategy is the selection bias.

MACHINERY (identical for both arms, so entry quality stays the only difference)
    1% of equity risked per trade, 10 slots, 10% notional per NAME,
    gross exposure <= 100% of equity, most-oversold candidate wins a free slot.

Usage:
    python3 scripts/portfolio_test.py
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
from bt_clean import bars_dir, load as load_clean, _load_mask_cache, filter_candidates  # noqa: E402

OUT = REPO / "data" / "portfolio_test.csv"
HOLD, MAX_SLOTS, RISK, MAX_NOTIONAL = 10, 10, 0.01, 0.10
COST = "realistic"
# Index, not ETF, and not 1306_T: the ETF's own series has an unadjusted 10:1
# glitch (two sessions at 1/10 scale in March 2026) which shows up as a -91%
# benchmark drawdown that never happened.
MARKETS = {"us200": ("IDX_GSPC", "IDX_GSPC"), "jp200": ("IDX_N225", "IDX_N225")}


def load(path: Path) -> pd.DataFrame | None:
    return load_clean(path)


def market_up_series(mkt: str) -> pd.Series | None:
    for sym in MARKETS[mkt]:
        p = bars_dir() / f"{sym}.json"
        if p.exists():
            b = load(p)
            if b is not None:
                f = features(b)
                return f.prev_close > f.ma200
    return None


def candidates_v2(b: pd.DataFrame) -> list[dict]:
    f = features(b)
    O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a, s1 = f.atr_pct.to_numpy(), f.S1.to_numpy()
    ma200, slope = f.ma200.to_numpy(), f.ma200_slope.to_numpy()
    boxw, r5 = f.box_w.to_numpy(), f.ret_5d.to_numpy()
    ok = (f.prev_close.to_numpy() > ma200) & (slope > 0) & (a < 0.06) & (boxw < 0.35)
    out = []
    for i in np.where(ok)[0]:
        if np.isnan(s1[i]) or np.isnan(a[i]) or a[i] <= 0:
            continue
        if not (O[i] > s1[i] and L[i] <= s1[i]):
            continue
        if C[i] < s1[i]:
            continue                                    # close-hold
        stop = s1[i] * (1 - 3 * a[i])
        if not (0 < stop < s1[i]):
            continue
        r = _simulate(1, i, s1[i], stop, 1e12, O, H, L, C, HOLD, True)
        if r is None:
            continue
        j, xp, why = r
        out.append({"entry_date": b.index[i], "exit_date": b.index[j], "entry": s1[i],
                    "stop": stop, "exit": xp, "gross": xp / s1[i] - 1,
                    "why": why, "ret5d": r5[i]})
    return out


def candidates_control(b: pd.DataFrame, up: pd.Series) -> list[dict]:
    f = features(b)
    O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a, ma200, slope = f.atr_pct.to_numpy(), f.ma200.to_numpy(), f.ma200_slope.to_numpy()
    boxw, r5 = f.box_w.to_numpy(), f.ret_5d.to_numpy()
    ok = (f.prev_close.to_numpy() > ma200) & (slope > 0) & (a < 0.06) & (boxw < 0.35)
    upa = up.reindex(b.index).fillna(False).to_numpy(bool)
    out = []
    for i in np.where(ok)[0]:
        if np.isnan(a[i]) or a[i] <= 0 or not upa[i]:
            continue
        stop = O[i] * (1 - 3 * a[i])
        if stop <= 0:
            continue
        r = _simulate(1, i, O[i], stop, 1e12, O, H, L, C, HOLD, False)
        if r is None:
            continue
        j, xp, why = r
        out.append({"entry_date": b.index[i], "exit_date": b.index[j], "entry": O[i],
                    "stop": stop, "exit": xp, "gross": xp / O[i] - 1,
                    "why": why, "ret5d": r5[i]})
    return out


def walk(cands: list[dict], mkt: str) -> tuple[pd.DataFrame | None, int]:
    """Slot-constrained walk. Costs ARE applied. Notional is capped per NAME."""
    if not cands:
        return None, 0
    d = pd.DataFrame(cands).sort_values(["entry_date", "ret5d"])
    d["cost"] = [cost_rt(mkt, COST, w == "stop") for w in d.why]
    dates = sorted(set(d.entry_date) | set(d.exit_date))
    equity, pos, curve, peak_one_name = 100_000.0, [], [], 0
    for dt in dates:
        keep = []
        for p in pos:
            if p["exit_date"] <= dt:
                equity += p["shares"] * (p["exit"] * (1 - p["cost"]) - p["entry"])
            else:
                keep.append(p)
        pos = keep
        for _, t in d[d.entry_date == dt].iterrows():
            if len(pos) >= MAX_SLOTS:
                break
            rps = t.entry - t.stop
            if rps <= 0:
                continue
            room = MAX_NOTIONAL * equity - sum(
                p["shares"] * p["entry"] for p in pos if p["sym"] == t.sym)
            if room <= 0:
                continue
            sh = min(RISK * equity / rps, room / t.entry)
            if sh <= 0:
                continue
            if sum(p["shares"] * p["entry"] for p in pos) + sh * t.entry > equity:
                continue
            pos.append({"sym": t.sym, "entry": t.entry, "exit": t.exit,
                        "exit_date": t.exit_date, "shares": sh, "cost": t.cost})
        seen: dict[str, int] = {}
        for p in pos:
            seen[p["sym"]] = seen.get(p["sym"], 0) + 1
        if seen:
            peak_one_name = max(peak_one_name, max(seen.values()))
        curve.append({"date": dt, "equity": equity, "open": len(pos)})
    return pd.DataFrame(curve), peak_one_name


def stats(eq: pd.Series) -> dict:
    e = eq.to_numpy(float)
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return {"total%": (e[-1] / e[0] - 1) * 100,
            "cagr%": ((e[-1] / e[0]) ** (1 / yrs) - 1) * 100,
            "maxDD%": (e / np.maximum.accumulate(e) - 1).min() * 100}


def sharpe_on_calendar(curve: pd.DataFrame, cal: pd.DatetimeIndex) -> float:
    """Sharpe must be measured on a fixed trading-day grid. Differencing a step
    function across event dates and annualising with sqrt(252) treats a 3-week
    gap as a 1-day return."""
    e = curve.set_index("date").equity.reindex(cal).ffill().bfill().to_numpy(float)
    r = np.diff(np.log(e))
    return r.mean() / r.std(ddof=1) * np.sqrt(252) if r.std(ddof=1) > 0 else 0.0


def main() -> int:
    src = bars_dir()
    mask = _load_mask_cache()
    print(f"bar source: {src}")
    print(f"glitch-masked symbols: {len(mask)}  masked bars: {sum(len(v) for v in mask.values())}\n")
    rows = []
    for mkt in MARKETS:
        up = market_up_series(mkt)
        if up is None:
            print(f"  {mkt}: no index bars, skipping")
            continue
        acc: dict[str, list[dict]] = {"v2": [], "control": []}
        raw_n = {"v2": 0, "control": 0}
        px: dict[str, pd.Series] = {}
        for p in sorted(src.glob("*.json")):
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
            px[s] = b.close
            for lab, cs in (("v2", candidates_v2(b)),
                            ("control", candidates_control(b, up.reindex(b.index)))):
                tagged = [dict(c, sym=s) for c in cs]
                raw_n[lab] += len(tagged)
                kept, _ = filter_candidates(tagged, mask)
                acc[lab].extend(kept)

        ib = load(src / f"{MARKETS[mkt][0]}.json")
        cal = ib.index
        # B0 index, B1 same-universe equal weight (the benchmark that matters).
        # .mean(axis=1) is load-bearing: transform("mean") on a DataFrame returns a
        # per-TICKER monthly mean, so prod() without it multiplies 201 tickers per
        # session and "grows" the benchmark to 10^70%.
        b1src = pd.DataFrame(px).reindex(cal)
        b1src = b1src.loc[b1src.notna().any(axis=1)]
        cal = b1src.index
        mm = b1src.pct_change().groupby(
            [cal.year, cal.month]).transform("mean").mean(axis=1)
        b1 = (100_000 * (1 + mm).cumprod()).ffill()
        # the benchmark index gets the same glitch treatment as everything else
        b0 = ib.close.copy()
        for d in mask.get(MARKETS[mkt][0], set()):
            b0 = b0[b0.index != d]
        b0 = b0.reindex(cal).ffill().bfill()
        rows.append({"market": mkt, "arm": "B0 index B&H", **stats(b0), "sharpe": 0.0,
                     "candidates": 0, "avg_slots": 0})
        rows.append({"market": mkt, "arm": "B1 universe EW", **stats(b1), "sharpe": 0.0,
                     "candidates": 0, "avg_slots": 0})

        for lab in ("v2", "control"):
            curve, peak = walk(acc[lab], mkt)
            if curve is None:
                print(f"  {mkt} {lab}: no candidates")
                continue
            m = stats(curve.set_index("date").equity)
            rows.append({"market": mkt, "arm": lab, **m,
                         "sharpe": round(sharpe_on_calendar(curve, cal), 2),
                         "candidates": len(acc[lab]), "avg_slots": round(curve.open.mean(), 1),
                         "peak_lots_one_name": peak})
            eq = curve.set_index("date").equity
            byyear, prev = {}, 100_000.0
            for y in sorted(set(eq.index.year)):
                ye = eq[eq.index.year == y].iloc[-1]
                byyear[y] = (ye / prev - 1) * 100
                prev = ye
            print(f"  {mkt} {lab} by year: " +
                  "  ".join(f"{y}:{v:+.0f}%" for y, v in byyear.items()))
            print(f"{mkt:6} {lab:8} n={len(acc[lab]):6,} (raw {raw_n[lab]:,})  "
                  f"total {m['total%']:+8.1f}%  CAGR {m['cagr%']:+6.1f}%  "
                  f"maxDD {m['maxDD%']:+6.1f}%  Sharpe {sharpe_on_calendar(curve, cal):+.2f}  "
                  f"slots {curve.open.mean():4.1f}  peak lots/name {peak}")

    if rows:
        df = pd.DataFrame(rows)
        pd.set_option("display.width", 220)
        print("\n" + "=" * 100)
        print(df.to_string(index=False, float_format=lambda x: f"{x:+.1f}"))
        print("\n" + "=" * 100)
        print("THE ONLY QUESTION THAT MATTERS: does v2 beat the SAME universe held passively?")
        for mkt in MARKETS:
            g = df[df.market == mkt].set_index("arm")
            try:
                v2, b1, b0 = g.loc["v2"], g.loc["B1 universe EW"], g.loc["B0 index B&H"]
            except KeyError:
                continue
            print(f"  {mkt:6} v2 CAGR {v2['cagr%']:+.1f}%   universe EW {b1['cagr%']:+.1f}%"
                  f"   index {b0['cagr%']:+.1f}%")
            print(f"         v2 - universeEW = {v2['cagr%'] - b1['cagr%']:+.1f} pp/yr"
                  f"     universeEW - index = {b1['cagr%'] - b0['cagr%']:+.1f} pp/yr")
            print(f"         v2 - universeEW = {v2['total%'] - b1['total%']:+.1f} pp total"
                  f"     v2 maxDD {v2['maxDD%']:+.1f}% vs universeEW {b1['maxDD%']:+.1f}%")
        df.to_csv(OUT, index=False)
        print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
