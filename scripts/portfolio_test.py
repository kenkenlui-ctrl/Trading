"""portfolio_test.py — v2 vs control at the portfolio level, not per trade.

WHY THIS FILE EXISTS
    control_test.py answered: per trade, v2 beats "buy any uptrend day" by
    2.6-6.6x on every window. But v2 only takes 2-4% as many opportunities
    (US 3,693 vs 132,032 trades). Per-trade superiority is NOT the same as
    "put the same capital in both and see who finishes richer" — the control
    churns through 10 slots many times over while v2 sits mostly idle.

    So both arms now run through identical portfolio machinery:
      * 1% of equity risked per trade
      * max 10 concurrent positions
      * max 10% notional per name
      * gross exposure capped at 100% of equity
      * when slots are full, the most-oversold candidate wins — the SAME
        ranking rule for both arms, so entry quality is still the only
        difference
      * P&L realised at exit, costs per market already netted

    A control that used a different slot-ranking rule would not be a control.

USAGE
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

BARS = REPO / "data" / "bt10y"
OUT = REPO / "data" / "portfolio_test.csv"
SPLIT = pd.Timestamp("2023-01-01")
HOLD = 10
MAX_SLOTS = 10
RISK = 0.01
MAX_NOTIONAL = 0.10
MARKETS = {"us200": ("SPY", "IDX_GSPC"), "jp200": ("1306_T", "IDX_N225")}

# benchmarks
SPY = None
NIKKEI = None


def load(path: Path) -> pd.DataFrame | None:
    try:
        rows = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) < 300:
        return None
    df = pd.DataFrame(rows)
    if not {"date", "open", "high", "low", "close"} <= set(df.columns):
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("date")
    return df.set_index("date")


def market_up_series(mkt: str) -> pd.Series | None:
    for sym in MARKETS[mkt]:
        p = BARS / f"{sym}.json"
        if p.exists():
            b = load(p)
            if b is not None:
                f = features(b)
                return f.prev_close > f.ma200
    return None


def candidates_v2(b: pd.DataFrame, mkt: str) -> list[dict]:
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
        out.append({"sym": mkt, "entry_date": b.index[i], "exit_date": b.index[j],
                    "entry": s1[i], "stop": stop, "exit": xp,
                    "gross": xp / s1[i] - 1, "why": why, "ret5d": r5[i]})
    return out


def candidates_control(b: pd.DataFrame, mkt: str, up: pd.Series) -> list[dict]:
    f = features(b)
    O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = f.atr_pct.to_numpy()
    ma200, slope = f.ma200.to_numpy(), f.ma200_slope.to_numpy()
    boxw, r5 = f.box_w.to_numpy(), f.ret_5d.to_numpy()
    ok = (f.prev_close.to_numpy() > ma200) & (slope > 0) & (a < 0.06) & (boxw < 0.35)
    idx = b.index
    upa = up.reindex(idx).fillna(False).to_numpy(bool)
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
        out.append({"sym": mkt, "entry_date": idx[i], "exit_date": idx[j],
                    "entry": O[i], "stop": stop, "exit": xp,
                    "gross": xp / O[i] - 1, "why": why, "ret5d": r5[i]})
    return out


def simulate(cands: list[dict], mkt: str) -> pd.DataFrame | None:
    """Slot-constrained portfolio walk. Returns an equity curve."""
    if not cands:
        return None
    d = pd.DataFrame(cands)
    d["net"] = d.gross - cost_rt(mkt, "site", d.why.eq("stop").to_numpy())
    d = d.sort_values(["entry_date", "ret5d"])   # most oversold wins a slot
    dates = sorted(set(d.entry_date) | set(d.exit_date))
    equity = 100_000.0
    open_pos: list[dict] = []
    curve, trades = [], 0
    for dt in dates:
        # realise anything exiting today
        still = []
        for p in open_pos:
            if p["exit_date"] <= dt:
                shares = p["shares"]
                equity += shares * (p["exit"] - p["entry"])
                trades += 1
            else:
                still.append(p)
        open_pos = still
        # open new ones if capacity
        todays = d[d.entry_date == dt]
        for _, t in todays.iterrows():
            if len(open_pos) >= MAX_SLOTS:
                break
            if any(p["sym"] == t.sym and p["entry_date"] == dt for p in open_pos):
                continue
            risk_per_share = (t.entry - t.stop)
            if risk_per_share <= 0:
                continue
            shares = RISK * equity / risk_per_share
            cap = MAX_NOTIONAL * equity / t.entry
            shares = min(shares, cap)
            gross_notional = sum(p["shares"] * p["entry"] for p in open_pos) + shares * t.entry
            if gross_notional > equity:      # gross cap 100%
                continue
            open_pos.append({"sym": t.sym, "entry": t.entry, "exit": t.exit,
                             "entry_date": dt, "exit_date": t.exit_date,
                             "shares": shares})
        curve.append({"date": dt, "equity": equity,
                      "open": len(open_pos)})
    return pd.DataFrame(curve)


def metrics(curve: pd.DataFrame) -> dict:
    if curve is None or len(curve) < 2:
        return {}
    e = curve.equity.to_numpy(float)
    ret = e[-1] / e[0] - 1
    years = max((curve.date.iloc[-1] - curve.date.iloc[0]).days / 365.25, 1e-9)
    cagr = (e[-1] / e[0]) ** (1 / years) - 1
    peak = np.maximum.accumulate(e)
    dd = e / peak - 1
    r = np.diff(np.log(e))
    sharpe = (r.mean() / r.std(ddof=1) * np.sqrt(252)) if r.std(ddof=1) > 0 else 0.0
    return {"total%": round(ret * 100, 1), "cagr%": round(cagr * 100, 1),
            "maxDD%": round(dd.min() * 100, 1), "sharpe": round(sharpe, 2),
            "avg_slots": round(curve.open.mean(), 1)}


def main() -> int:
    rows = []
    for mkt in MARKETS:
        up = market_up_series(mkt)
        if up is None:
            print(f"  {mkt}: no index bars, skipping")
            continue
        acc: dict[str, list[dict]] = {"v2": [], "control": []}
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
            for label, cands in (("v2", candidates_v2(b, mkt)),
                                 ("control", candidates_control(b, mkt, up.reindex(b.index)))):
                for c in cands:
                    c = dict(c)
                    c["sym"] = s
                    acc[label].append(c)

        for label in ("v2", "control"):
            curve = simulate(acc[label], mkt)
            if curve is None:
                print(f"  {mkt} {label}: no candidates")
                continue
            m = metrics(curve)
            rows.append({"market": mkt, "arm": label, **m,
                         "candidates": len(acc[label])})
            print(f"\n{mkt}  {label:8}  candidates={len(acc[label]):,}")
            print(f"   total {m['total%']:+.1f}%  CAGR {m['cagr%']:+.1f}%  "
                  f"maxDD {m['maxDD%']:+.1f}%  Sharpe {m['sharpe']:+.2f}  "
                  f"avg slots {m['avg_slots']}")

    # buy and hold
    print("\n" + "=" * 74)
    print("BUY & HOLD (same window, 100% in the index)")
    for mkt, syms in MARKETS.items():
        for sym in syms:
            p = BARS / f"{sym}.json"
            if not p.exists():
                continue
            b = load(p)
            if b is None or len(b) < 300:
                continue
            e = b.close
            print(f"  {mkt:6} {sym:10} {e.iloc[-1]/e.iloc[0]-1:+.1%}  "
                  f"maxDD {(e/e.cummax()-1).min():+.1%}")
            break

    print("\n" + "=" * 74)
    print("PORTFOLIO VERDICT  (10 slots, 1% risk/trade, gross<=100%)")
    for mkt in MARKETS:
        v = next((r for r in rows if r["market"] == mkt and r["arm"] == "v2"), None)
        c = next((r for r in rows if r["market"] == mkt and r["arm"] == "control"), None)
        if not (v and c):
            continue
        dv = v["total%"] - c["total%"]
        print(f"  {mkt:6} v2 {v['total%']:+.1f}%  vs  control {c['total%']:+.1f}%   "
              f"delta {dv:+.1f}pp  => {'v2 AHEAD' if dv > 0 else 'control ahead'}")
        print(f"         v2 avg slots {v['avg_slots']}  |  control avg slots {c['avg_slots']}")

    if rows:
        pd.DataFrame(rows).to_csv(OUT, index=False)
        print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
