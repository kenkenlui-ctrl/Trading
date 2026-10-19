#!/usr/bin/env python3
"""Self-tests for the close-fill engine (closefill.py) and Direction A.

The load-bearing test here is `test_prefix_consistency`. A no-look-ahead claim
is usually argued, not measured: the features are described as shifted, the
code looks shifted, and nobody checks. The check that settles it is mechanical
— run the screen on a series TRUNCATED at date t and on the full series, and
require the selections at every date up to t to be identical. Any use of a
bar after t changes one of them.

The rest pin the portfolio arithmetic by hand, because an engine that silently
mis-times an exit still produces a plausible equity curve.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "v2engine"))

import closefill as cf
import signal_search_s1 as s1

PASSED, FAILED = 0, 0


def record(name: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAILED: {name}  {detail}")


# ======================================================================================
# fixtures
# ======================================================================================
def one_symbol_bars(closes, start="2024-01-02") -> pd.DataFrame:
    """A single name with a given close series; lows/highs bracket the close."""
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": 1e6}, index=idx)


def simple_market(closes_by_sym: dict) -> tuple[dict, pd.DatetimeIndex]:
    px = {s: one_symbol_bars(v) for s, v in closes_by_sym.items()}
    cal = pd.DatetimeIndex(sorted(set().union(*[set(b.index) for b in px.values()])))
    return px, cal


# ======================================================================================
# no-look-ahead
# ======================================================================================
def test_prefix_consistency() -> None:
    """Truncating the data must not change any selection at or before the cut.

    This is the audit the contract asks for, run on real data rather than on a
    toy. It is the only way to know the screen is not quietly reaching forward.
    """
    px, idx_close, _ = cf.load_market("us")
    syms = sorted(px)[:12]
    mkt_up = s1.market_up_series(idx_close).dropna()
    cfg = cf.ScreenConfig()

    def run(subset, upto=None):
        feats = {}
        for sym in subset:
            b = px[sym]
            if upto is not None:
                b = b.loc[b.index <= upto]
            if len(b) < 250:
                continue
            feats[sym] = s1.base_features(b)
        usable = [s for s in feats]
        if not usable:
            return {}
        sub = {s: px[s].loc[px[s].index <= upto] for s in usable} if upto is not None \
            else {s: px[s] for s in usable}
        return cf.screen_daily(sub, feats, mkt_up, cfg)

    full = run(syms)
    if not full:
        record("prefix consistency audit ran on real data", False,
               "no selections produced — the test would be vacuous")
        return
    record("prefix consistency audit ran on real data", True)

    dates = sorted(full)
    # cut near the end, where the features have the most history behind them
    checks = [dates[int(len(dates) * f)] for f in (0.5, 0.75, 0.9)]
    for cut in checks:
        trunc = run(syms, upto=cut)
        # every date up to the cut must have the same selection
        diffs = []
        for d, names in trunc.items():
            if full.get(d, []) != names:
                diffs.append(d)
        record(f"truncating at {cut.date()} changes no selection at or before it",
               not diffs,
               f"{len(diffs)} dates differ, first {diffs[:3]}")


def test_features_are_shifted() -> None:
    """The S1 level used on day t must come from bars through t-1 only."""
    closes = list(np.linspace(100, 130, 40))
    b = one_symbol_bars(closes)
    s1v = s1.s1_series(b, 20)
    # s1_series is low.shift(1).rolling(20).min(): at row t it is the min of
    # lows over t-20..t-1, so it must NOT contain today's low.
    for i in range(25, len(b)):
        window = b.low.iloc[i - 20:i]
        if abs(s1v.iloc[i] - window.min()) > 1e-9:
            record("the S1 level excludes the current session", False,
                   f"at row {i} s1={s1v.iloc[i]} but window min={window.min()}")
            return
        if s1v.iloc[i] == b.low.iloc[i] and b.low.iloc[i] < window.min():
            record("the S1 level excludes the current session", False,
                   f"at row {i} the S1 level equals today's low")
            return
    record("the S1 level excludes the current session", True)


# ======================================================================================
# portfolio arithmetic, by hand
# ======================================================================================
def test_cohort_arithmetic() -> None:
    """One name, one cohort at a time, no costs: equity must be [1.0, 1.1, 1.2]."""
    px, cal = simple_market({"A": [100, 110, 120]})
    closes = cf.close_matrix(px, cal)
    trad = cf.tradable_matrix(px, cal)
    baskets = {cal[0]: ["A"], cal[1]: ["A"], cal[2]: ["A"]}
    cfg = cf.ExecConfig(hold=1, cost_rt=0.0)
    curve = cf.run_cohort(baskets, closes, trad, cal, cfg)
    got = curve["equity"].to_numpy()
    want = np.array([1.0, 1.1, 1.2])
    record("a single-name cohort compounds exactly as computed by hand",
           bool(np.allclose(got, want, atol=1e-9)),
           f"got {got.tolist()}, want {want.tolist()}")


def test_cost_accounting() -> None:
    """A rolling one-day cohort on a constant price must lose exactly the
    round-trip cost per day.

    Each day the old cohort is sold and a new one is bought at the same price,
    so the portfolio pays one full round trip every day: the drag is
    (1 - cost_rt) per day, and day 0 additionally carries the entry-side cost.
    """
    px, cal = simple_market({"A": [100, 100, 100, 100]})
    closes = cf.close_matrix(px, cal)
    trad = cf.tradable_matrix(px, cal)
    baskets = {d: ["A"] for d in cal}
    rt = 0.0025
    cfg = cf.ExecConfig(hold=1, cost_rt=rt)
    curve = cf.run_cohort(baskets, closes, trad, cal, cfg)
    side = rt / 2.0
    got = curve["equity"].to_numpy()
    want = [(1 - side) * (1 - rt) ** k for k in range(len(cal))]
    record("a rolling cohort pays exactly one round trip per day",
           bool(np.allclose(got, want, rtol=1e-9)),
           f"got {got.tolist()}, want {[round(w, 8) for w in want]}")


def test_exposure_is_measured_not_assumed() -> None:
    """Exposure must reach 100% once the cohorts have ramped up, and a name
    with no bar must not be tradable that day.

    The portfolio starts with one cohort worth 1/hold of the book and fills up
    over `hold` days, so the first days are legitimately under-invested. That
    ramp is a property of the construction, not a matching failure, and it has
    to be excluded from the exposure measurement rather than averaged in.
    """
    px, cal = simple_market({"A": [100, 101, 102, 103], "B": [50, 51, 52, 53]})
    closes = cf.close_matrix(px, cal)
    trad = cf.tradable_matrix(px, cal)
    cfg = cf.ExecConfig(hold=2, cost_rt=0.0)

    full = cf.run_cohort({d: ["A", "B"] for d in cal}, closes, trad, cal, cfg)
    ramped = float(full["exposure"].iloc[cfg.hold:].mean())
    record("a full basket is 100% invested once the cohorts have ramped up",
           abs(ramped - 1.0) < 0.005,
           f"mean exposure after the ramp {ramped:.6f}; the residual is the "
           f"weight drift inside a cohort, which is expected")
    record("the ramp-up period is visible, not silently averaged away",
           float(full["exposure"].iloc[0]) < 1.0,
           "the first day cannot be fully invested when hold > 1")

    # a name that stops having bars mid-sample must not be bought on those days
    px2 = {"A": one_symbol_bars([100, 101, 102, 103])}
    px2["B"] = one_symbol_bars([50, 51, 52, 53]).iloc[:2]
    cal2 = pd.DatetimeIndex(sorted(set().union(*[set(b.index) for b in px2.values()])))
    c2 = cf.close_matrix(px2, cal2)
    t2 = cf.tradable_matrix(px2, cal2)
    part = cf.run_cohort({d: ["A", "B"] for d in cal2}, closes=c2, tradable=t2,
                          cal=cal2, cfg=cfg)
    expect = [int(t2.loc[d].sum()) for d in cal2]
    record("a name with no bar is not tradable that day",
           part["filled"].tolist() == expect,
           f"filled per day {part['filled'].tolist()}, expected {expect} "
           f"(only the names with a bar that day)")


def test_fill_lag_shifts_the_fill_not_the_signal() -> None:
    """With fill_lag=1 the basket decided at t is bought at t+1's close."""
    px, cal = simple_market({"A": [100, 200, 300]})
    closes = cf.close_matrix(px, cal)
    trad = cf.tradable_matrix(px, cal)
    baskets = {cal[0]: ["A"]}

    c0 = cf.ExecConfig(hold=1, cost_rt=0.0, fill_lag=0)
    c1 = cf.ExecConfig(hold=1, cost_rt=0.0, fill_lag=1)
    e0 = cf.run_cohort(baskets, closes, trad, cal, c0)["equity"].to_numpy()
    e1 = cf.run_cohort(baskets, closes, trad, cal, c1)["equity"].to_numpy()
    # lag 0 buys at 100 on day 0 and is marked at 200 on day 1 -> 2.0
    # lag 1 buys at 200 on day 1 and is marked at 300 on day 2 -> 1.5
    record("fill_lag shifts the fill to the next close",
           abs(e0[1] - 2.0) < 1e-9 and abs(e1[2] - 1.5) < 1e-9,
           f"lag0 equity {e0.tolist()}, lag1 equity {e1.tolist()}")


def test_placebo_matches_the_daily_count() -> None:
    """The placebo must hold the same NUMBER of names on every day."""
    px, cal = simple_market({"A": [100, 101], "B": [50, 51], "C": [20, 21], "D": [5, 6]})
    trad = cf.tradable_matrix(px, cal)
    uni = cf.universe_basket(trad)
    real = {cal[0]: ["A", "B"], cal[1]: ["C"]}
    rng = np.random.default_rng(7)
    plac = cf.random_basket(real, uni, rng)
    ok = all(len(plac[d]) == len(real[d]) for d in real)
    record("the placebo holds the same number of names per day", ok,
           f"real {[len(real[d]) for d in real]} vs placebo "
           f"{[len(plac[d]) for d in real]}")
    ok2 = all(set(plac[d]) <= set(uni[d]) for d in real)
    record("the placebo only draws names that could be traded", ok2,
           "the placebo must not invent names")


def test_universe_fallback_keeps_exposure_at_100() -> None:
    """An empty selection day must fall back to the universe, or the arm is
    not fully invested and the two arms are not exposure-matched."""
    px, cal = simple_market({"A": [100, 101, 102, 103], "B": [50, 51, 52, 53]})
    closes = cf.close_matrix(px, cal)
    trad = cf.tradable_matrix(px, cal)
    uni = cf.universe_basket(trad)
    real = {cal[0]: ["A"], cal[1]: [], cal[2]: ["B"]}
    fallback = {d: (real.get(d) or uni[d]) for d in cal}
    cfg = cf.ExecConfig(hold=2, cost_rt=0.0)
    curve = cf.run_cohort(fallback, closes, trad, cal, cfg)
    ramped = float(curve["exposure"].iloc[cfg.hold:].mean())
    record("the universe fallback keeps the arm fully invested",
           abs(ramped - 1.0) < 0.005,
           f"mean exposure after the ramp {ramped:.6f}")


def test_metrics_are_sane() -> None:
    """A flat equity curve must not report a positive Sharpe."""
    px, cal = simple_market({"A": [100] * 10})
    closes = cf.close_matrix(px, cal)
    trad = cf.tradable_matrix(px, cal)
    cfg = cf.ExecConfig(hold=2, cost_rt=0.0)
    curve = cf.run_cohort({d: ["A"] for d in cal}, closes, trad, cal, cfg)
    m = cf.metrics(curve)
    record("a flat curve has zero volatility and no Sharpe",
           m["2016-2026"]["sharpe"] == 0.0,
           f"got sharpe {m['2016-2026']['sharpe']}")
    record("a flat curve reports 0% CAGR",
           abs(m["2016-2026"]["cagr%"]) < 1e-9,
           f"got cagr {m['2016-2026']['cagr%']}")


def main() -> int:
    print("Edge → Alpha self-tests (close-fill engine)")
    test_features_are_shifted()
    test_cohort_arithmetic()
    test_cost_accounting()
    test_exposure_is_measured_not_assumed()
    test_fill_lag_shifts_the_fill_not_the_signal()
    test_placebo_matches_the_daily_count()
    test_universe_fallback_keeps_exposure_at_100()
    test_metrics_are_sane()
    test_prefix_consistency()
    total = PASSED + FAILED
    print("=" * 78)
    print(f"{PASSED}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
