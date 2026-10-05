"""portfolio_diag.py — why did portfolio_test print +1811% / Sharpe 2.97?

Read-only diagnostics. Answers five questions with numbers, not opinions:
  1. how survivorship-biased is the 201-name universe itself
  2. does the edge live in the train window or the test window
  3. is the return concentrated in a handful of names
  4. can you hold 10 lots of the same stock at once (per-NAME cap bypassed?)
  5. is the Sharpe number even a Sharpe (irregular step -> sqrt(252) inflation)
Also re-runs the walk with costs actually applied, since portfolio_test
computes `net` and then never uses it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
from core import features, _simulate, cost_rt  # noqa: E402

BARS = REPO / "data" / "bt10y"
HOLD, MAX_SLOTS, RISK, MAX_NOTIONAL = 10, 10, 0.01, 0.10
MARKETS = {"us200": ("SPY", "IDX_GSPC"), "jp200": ("1306_T", "IDX_N225")}


def load(p: Path):
    try:
        rows = json.loads(p.read_text())
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) < 300:
        return None
    df = pd.DataFrame(rows)
    if not {"date", "open", "high", "low", "close"} <= set(df.columns):
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.dropna(subset=["open", "high", "low", "close"]).sort_values("date").set_index("date")


def up_series(mkt):
    for sym in MARKETS[mkt]:
        p = BARS / f"{sym}.json"
        if p.exists():
            b = load(p)
            if b is not None:
                return (features(b).prev_close > features(b).ma200)


def cands_v2(b, mkt, through=0.0):
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
        if not (O[i] > s1[i] and L[i] <= s1[i] * (1 - through)):
            continue
        if C[i] < s1[i]:
            continue
        stop = s1[i] * (1 - 3 * a[i])
        if not (0 < stop < s1[i]):
            continue
        r = _simulate(1, i, s1[i], stop, 1e12, O, H, L, C, HOLD, True)
        if r is None:
            continue
        j, xp, why = r
        out.append({"sym": mkt, "entry_date": b.index[i], "exit_date": b.index[j],
                    "entry": s1[i], "stop": stop, "exit": xp,
                    "gross": xp / s1[i] - 1, "why": why, "ret5d": r5[i]})
    return out


def cands_control(b, mkt, up):
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
        out.append({"sym": mkt, "entry_date": b.index[i], "exit_date": b.index[j],
                    "entry": O[i], "stop": stop, "exit": xp,
                    "gross": xp / O[i] - 1, "why": why, "ret5d": r5[i]})
    return out


def walk(cands, mkt, net=True, per_name_cap=True):
    """Re-implementation that actually applies costs, and optionally enforces a
    PER-NAME notional cap (portfolio_test only capped PER TRADE)."""
    if not cands:
        return None, 0, 0
    d = pd.DataFrame(cands)
    d["cost"] = [cost_rt(mkt, "realistic", w == "stop") for w in d.why]
    d["net"] = d.gross - d.cost
    d = d.sort_values(["entry_date", "ret5d"])
    dates = sorted(set(d.entry_date) | set(d.exit_date))
    equity, pos, curve, peak_same = 100_000.0, [], [], 0
    for dt in dates:
        keep = []
        for p in pos:
            if p["exit_date"] <= dt:
                px = p["exit"] * (1 - p["cost"]) if net else p["exit"]
                equity += p["shares"] * (px - p["entry"])
            else:
                keep.append(p)
        pos = keep
        for _, t in d[d.entry_date == dt].iterrows():
            if len(pos) >= MAX_SLOTS:
                break
            rps = t.entry - t.stop
            if rps <= 0:
                continue
            sh = min(RISK * equity / rps, MAX_NOTIONAL * equity / t.entry)
            if per_name_cap:
                same = sum(p["shares"] * p["entry"] for p in pos if p["sym"] == t.sym)
                room = MAX_NOTIONAL * equity - same
                if room <= 0:
                    continue
                sh = min(sh, room / t.entry)
            notional = sum(p["shares"] * p["entry"] for p in pos) + sh * t.entry
            if notional > equity:
                continue
            pos.append({"sym": t.sym, "entry": t.entry, "exit": t.exit,
                        "entry_date": dt, "exit_date": t.exit_date, "shares": sh,
                        "cost": cost_rt(mkt, "realistic", t.why == "stop")})
        c = {}
        for p in pos:
            c[p["sym"]] = c.get(p["sym"], 0) + 1
        if c:
            peak_same = max(peak_same, max(c.values()))
        curve.append({"date": dt, "equity": equity, "open": len(pos)})
    return pd.DataFrame(curve), peak_same, len(d)


def sharpe_daily(curve, index_dates):
    """Rebuild the equity curve on a CALENDAR of trading days, then Sharpe.
    portfolio_test differenced the step function across event dates only and
    annualised as if each gap were one day."""
    if curve is None or len(curve) < 2:
        return 0.0
    e = curve.set_index("date").equity.reindex(index_dates).ffill().bfill()
    r = np.diff(np.log(e.to_numpy(float)))
    return r.mean() / r.std(ddof=1) * np.sqrt(252) if r.std(ddof=1) > 0 else 0.0


def main() -> int:
    for mkt in MARKETS:
        print("=" * 78)
        print(f"### {mkt}")
        up = up_series(mkt)
        acc = {"v2": [], "control": []}

        # --- Q1: universe bias -------------------------------------------------
        syms = []
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

        idx_sym = MARKETS[mkt][0]
        ib = load(BARS / f"{idx_sym}.json")
        cal = ib.index
        print(f"  index {idx_sym}: {cal[0].date()} -> {cal[-1].date()}  n={len(cal)}")

        # equal-weight universe vs index, by calendar year
        print("\n  [Q1] equal-weight MONTHLY rebalance of the universe vs index, by year")
        px = pd.DataFrame({s: b.close for s, b in syms}).reindex(cal).ffill()
        r = px.pct_change()
        idxr = ib.close.pct_change()
        for y in sorted(set(cal.year)):
            m = r.index.year == y
            ew = (1 + r[m].mean(axis=1)).prod() - 1
            ix = (1 + idxr[m]).prod() - 1
            print(f"       {y}  universe EW {ew:+7.1%}   index {ix:+7.1%}   gap {ew-ix:+7.1%}")

        # --- Q2/Q3/Q4/Q5 -------------------------------------------------------
        for lab in ("v2", "control"):
            df = pd.DataFrame(acc[lab])
            raw, peak_same, n = walk(acc[lab], mkt, net=False, per_name_cap=False)
            fixed, _, _ = walk(acc[lab], mkt, net=True, per_name_cap=True)
            if raw is None or fixed is None:
                continue
            y0, y1 = cal[0], cal[-1]
            yrs = (y1 - y0).days / 365.25
            e_raw, e_fix = raw.equity.iloc[-1], fixed.equity.iloc[-1]
            print(f"\n  [{lab}]  candidates={n:,}  distinct names={df.sym.nunique()}"
                  f"   max simultaneous lots of ONE name={peak_same}")
            print(f"       as published (gross, no cost, no per-name cap): {e_raw/1e5-1:+.1%}"
                  f"   CAGR {(e_raw/1e5)**(1/yrs)-1:+.1%}")
            print(f"       costs + per-name cap applied:                {e_fix/1e5-1:+.1%}"
                  f"   CAGR {(e_fix/1e5)**(1/yrs)-1:+.1%}")
            sh_pub = np.diff(np.log(raw.equity.to_numpy(float)))
            sh_pub = sh_pub.mean() / sh_pub.std(ddof=1) * np.sqrt(252)
            print(f"       Sharpe as published (irregular steps): {sh_pub:+.2f}"
                  f"   |  on trading-day calendar: {sharpe_daily(raw, cal):+.2f}"
                  f" / {sharpe_daily(fixed, cal):+.2f} (fixed)")
            # per calendar year on the FIXED curve
            fy = fixed.set_index("date").equity
            print("       per-year (fixed):", end=" ")
            prev = 1e5
            for y in sorted(set(fy.index.year)):
                ye = fy[fy.index.year == y].iloc[-1]
                print(f"{y}:{ye/prev-1:+.0%}", end="  ")
                prev = ye
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
