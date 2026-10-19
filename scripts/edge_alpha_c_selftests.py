#!/usr/bin/env python3
"""Self-tests for the Direction C harness (edge_alpha_c.py).

The load-bearing test here is `test_prefix_consistency`, the same audit the
Direction A harness uses: run the whole pipeline on a series TRUNCATED at a
date and on the full series, and require that every selection, every sizing
weight and every capital scale at or before the cut is identical. A
no-look-ahead claim is usually argued, not measured; this is the measurement.

Direction C adds two adaptive pieces that Direction A did not have — the
Kelly estimate and the risk-parity scale — and both are things a portfolio
can quietly reach forward into. So the audit is run on them too, and the
Kelly's expansion window is pinned separately with a synthetic table where
the answer is known by hand.

The rest pin the test machinery: a bootstrap null with no spread must come
back VOID rather than as a p of zero, a null with real spread must give a
sensible p-value, and the BH adjustment must reproduce a hand-computed
example.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "v2engine"))

import closefill as cf
import edge_alpha_c as eac
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
# the grid
# ======================================================================================
def test_ofat_invariant() -> None:
    """The grid must be one-factor-at-a-time, and the invariant is asserted."""
    cells = eac.build_grid()
    try:
        ofat = eac.assert_ofat(cells)
        ok = True
        detail = ""
    except AssertionError as e:
        ok, ofat, detail = False, {}, str(e)
    record("the 16-cell grid satisfies the one-factor-at-a-time invariant", ok, detail)
    if not ok:
        return
    record("the grid has 16 entries", ofat["cells"] == 16, f"got {ofat['cells']}")
    record("every dimension offers every one of its levels",
           all(sorted(c.level for c in cells if c.dimension == d) == sorted(eac.LEVELS[d])
               for d in eac.DIMS), "a level is missing from a dimension")
    record("the baseline cell is present and is the baseline configuration",
           any(c.cell_id == "baseline" and c.config == eac.BASELINE for c in cells),
           "no baseline cell")
    # the cells equal to the baseline are its own level of each dimension, plus
    # the reference cell; everything else moves exactly one dimension
    record("the cells equal to the baseline are the baseline level of each "
           "dimension plus the reference",
           ofat["cells_equal_to_the_baseline_config"] == len(eac.DIMS) + 1,
           f"got {ofat['cells_equal_to_the_baseline_config']}")
    record("a cell that moves two dimensions is rejected by the invariant",
           _ofat_rejects([
               eac.Cell("bad", eac.DIM_SIZING, "equal",
                        {**eac.BASELINE, eac.DIM_SIZING: "equal", eac.DIM_HOLD: "t5"})]),
           "a two-dimension cell was accepted")


def _ofat_rejects(cells: list) -> bool:
    try:
        eac.assert_ofat(cells)
    except AssertionError:
        return True
    return False


# ======================================================================================
# no look-ahead
# ======================================================================================
def test_prefix_consistency() -> None:
    """Truncating the data must not change anything at or before the cut.

    Run on real data, on the real pipeline: the screen's selection, the
    sizing weights (including the Kelly estimate) and the risk-parity
    capital scale all have to be identical before the cut. Any use of a bar
    after the cut changes one of them.
    """
    px, idx_close, _ = cf.load_market("us")
    syms = sorted(px)[:12]
    full_px = {s: px[s] for s in syms}
    full_idx = idx_close

    mk_full = eac.build(full_px, full_idx)
    if not mk_full.selection:
        record("the prefix audit ran on real data", False,
               "no selections produced — the test would be vacuous")
        return
    record("the prefix audit ran on real data", True)

    dates = sorted(mk_full.selection)
    cuts = [dates[int(len(dates) * f)] for f in (0.5, 0.75, 0.9)]
    for cut in cuts:
        trunc_px = {s: b.loc[b.index <= cut] for s, b in full_px.items()}
        trunc_idx = full_idx.loc[full_idx.index <= cut]
        mk_cut = eac.build(trunc_px, trunc_idx)

        # 1) the selection
        sel_diffs = [d for d, names in mk_cut.selection.items()
                     if mk_full.selection.get(d, []) != names]
        record(f"truncating at {cut.date()} changes no selection at or before it",
               not sel_diffs, f"{len(sel_diffs)} dates differ, first {sel_diffs[:3]}")

        # 2) the sizing weights, including the Kelly estimate
        w_full = eac.risk1pct_weights(mk_full)
        w_cut = eac.risk1pct_weights(mk_cut)
        wd = [d for d in w_cut if w_full.get(d) != w_cut.get(d)]
        record(f"truncating at {cut.date()} changes no risk1pct weight at or before it",
               not wd, f"{len(wd)} dates differ, first {wd[:3]}")

        k_full, _ = eac.kelly_weights(mk_full, 10, 0.0025, 20)
        k_cut, _ = eac.kelly_weights(mk_cut, 10, 0.0025, 20)
        kd = [d for d in k_cut if k_full.get(d) != k_cut.get(d)]
        record(f"truncating at {cut.date()} changes no Kelly weight at or before it",
               not kd, f"{len(kd)} dates differ, first {kd[:3]}")

        # 3) the risk-parity capital scale
        base_full = eac.run_cell(next(c for c in eac.build_grid() if c.cell_id == "baseline"),
                                  mk_full, {"risk1pct": w_full}, cap_scale=0.6, cost_rt=0.0025)
        base_cut = eac.run_cell(next(c for c in eac.build_grid() if c.cell_id == "baseline"),
                                 mk_cut, {"risk1pct": w_cut}, cap_scale=0.6, cost_rt=0.0025)
        eq_diffs = [d for d in base_cut.index
                    if abs(float(base_full["equity"].reindex([d]).iloc[0])
                           - float(base_cut["equity"].reindex([d]).iloc[0])) > 1e-9]
        record(f"truncating at {cut.date()} changes no equity value at or before it",
               not eq_diffs, f"{len(eq_diffs)} dates differ, first {eq_diffs[:3]}")

        s_full, _ = eac.riskparity_scale(base_full, full_idx, mk_full.cal, 60, 0.5, 1.0)
        s_cut, _ = eac.riskparity_scale(base_cut, trunc_idx, mk_cut.cal, 60, 0.5, 1.0)
        sd = [d for d in s_cut if abs(s_full[d] - s_cut[d]) > 1e-12]
        record(f"truncating at {cut.date()} changes no risk-parity scale at or before it",
               not sd, f"{len(sd)} dates differ, first {sd[:3]}")


def test_kelly_expansion_window() -> None:
    """A name's Kelly estimate must not use outcomes it could not have seen.

    An outcome is dated by its EXIT session (the close at which the trade
    would have been closed). The estimate at date i may only use outcomes
    whose exit session is strictly before i.
    """
    hold = 10
    outs = [(0, 0.10), (10, -0.05), (20, 0.20), (30, 0.30)]
    f15, n15 = eac.kelly_fraction(outs, hold, 15)
    record("the Kelly estimate at i=15 uses only the outcomes that had exited",
           n15 == 1, f"n={n15}, expected 1 (the signal at 0 exits at 10, the one at 10 "
                     f"exits at 20, which is not before 15)")
    f25, n25 = eac.kelly_fraction(outs, hold, 25)
    record("the Kelly estimate at i=25 uses the two outcomes that had exited",
           n25 == 2, f"n={n25}, expected 2")
    f35, n35 = eac.kelly_fraction(outs, hold, 35)
    record("the Kelly estimate at i=35 uses the three outcomes that had exited",
           n35 == 3, f"n={n35}, expected 3")

    # a future outcome must not move the estimate
    outs_future = outs + [(14, 5.0)]
    f15b, n15b = eac.kelly_fraction(outs_future, hold, 15)
    record("an outcome that exits after the signal date does not move the estimate",
           abs(f15b - f15) < 1e-12 and n15b == n15,
           f"f {f15} -> {f15b}, n {n15} -> {n15b}")
    # ... but it does move it once it has exited
    f25b, n25b = eac.kelly_fraction(outs_future, hold, 25)
    record("the same outcome is used once it has exited",
           n25b == 3 and abs(f25b - f25) > 1e-12,
           f"n {n25} -> {n25b}, f {f25} -> {f25b}")

    # the fraction itself: one win and one loss of equal size -> p=0.5, b=1
    f, n = eac.kelly_fraction([(0, 0.10), (1, -0.10)], hold, 20)
    record("the Kelly fraction is p - (1 - p) / b, clipped to [0, 0.5]",
           abs(f - 0.0) < 1e-12 and n == 2, f"f={f}, n={n}")
    f, n = eac.kelly_fraction([(0, 0.10), (1, 0.10), (2, 0.10)], hold, 20)
    record("a name with only wins gets f = p, clipped to 0.5",
           abs(f - 0.5) < 1e-12 and n == 3, f"f={f}, n={n} (p=1.0, clipped to 0.5)")
    f, n = eac.kelly_fraction([(0, -0.10)], hold, 20)
    record("a name with only losses gets f = 0", abs(f) < 1e-12, f"f={f}")


# ======================================================================================
# the test machinery
# ======================================================================================
def test_block_bootstrap() -> None:
    """A null with no spread must come back VOID, not as a p of zero."""
    zeros = np.zeros(24)
    out = eac.block_bootstrap_p(zeros, block=12, resamples=500, seed=1, direction=1)
    record("a difference series with zero spread is void, not a pass",
           out["void"] and out["p_value"] == 1.0,
           f"void={out['void']}, p={out['p_value']}")

    rng = np.random.default_rng(11)
    null = rng.normal(0.0, 0.01, 120)
    out = eac.block_bootstrap_p(null, block=12, resamples=2000, seed=20261019, direction=1)
    record("a null with no real difference gives a p-value that is not small",
           (not out["void"]) and 0.05 < out["p_value"] < 0.95,
           f"p={out['p_value']}")

    shifted = rng.normal(0.0, 0.01, 120) + 0.05
    out_up = eac.block_bootstrap_p(shifted, block=12, resamples=2000, seed=20261019, direction=1)
    out_dn = eac.block_bootstrap_p(shifted, block=12, resamples=2000, seed=20261019, direction=-1)
    record("a genuine positive difference gives a small p-value",
           (not out_up["void"]) and out_up["p_value"] < 0.05,
           f"p={out_up['p_value']}")
    record("the reported p-value is two-sided, so it does not depend on the direction",
           abs(out_up["p_value"] - out_dn["p_value"]) < 1e-12,
           f"two-sided p {out_up['p_value']} vs {out_dn['p_value']}")
    record("the one-sided p-value is large when the difference is read the wrong way",
           (not out_dn["void"]) and out_dn["p_value_one_sided"] > 0.9,
           f"one-sided p={out_dn['p_value_one_sided']}")
    record("the one-sided p-value is small when the difference is read the right way",
           (not out_up["void"]) and out_up["p_value_one_sided"] < 0.05,
           f"one-sided p={out_up['p_value_one_sided']}")

    a = eac.block_bootstrap_p(shifted, block=12, resamples=2000, seed=20261019, direction=1)
    b = eac.block_bootstrap_p(shifted, block=12, resamples=2000, seed=20261019, direction=1)
    c = eac.block_bootstrap_p(shifted, block=12, resamples=2000, seed=20261020, direction=1)
    record("the bootstrap is reproducible under the same seed",
           a["p_value"] == b["p_value"], f"{a['p_value']} vs {b['p_value']}")
    record("a different seed gives a different draw (but the same ballpark)",
           a["p_value"] != c["p_value"] or a["null_mean"] != c["null_mean"],
           "the seed is not being used")


def test_benjamini_hochberg() -> None:
    """The BH adjustment must reproduce a hand-computed example."""
    q = eac.benjamini_hochberg([0.01, 0.02, 0.03, 0.04])
    record("BH on four evenly spaced p-values gives q = 0.04 for each",
           all(abs(x - 0.04) < 1e-12 for x in q), f"got {q}")
    q = eac.benjamini_hochberg([0.001, 0.5, 0.9])
    want = [0.003, 0.75, 0.9]
    record("BH step-up reproduces the hand-computed example",
           all(abs(x - w) < 1e-12 for x, w in zip(q, want)), f"got {q}, want {want}")
    q = eac.benjamini_hochberg([0.5, 0.6])
    record("BH never returns a q above 1", all(0.0 <= x <= 1.0 for x in q), f"got {q}")


def test_monthly_returns() -> None:
    """The monthly return series must be the month-over-month change."""
    idx = pd.bdate_range("2024-01-01", periods=90)
    eq = pd.Series(np.linspace(1.0, 1.09, 90), index=idx)
    curve = pd.DataFrame({"equity": eq}, index=idx)
    m = eac.monthly_returns(curve)
    n_months = len({str(p) for p in idx.to_period("M")})
    record("the monthly return series has one value per calendar month",
           len(m) == n_months, f"got {len(m)}, expected {n_months}")
    first_month = str(idx.to_period("M")[0])
    end_of_first = eq.loc[[p for p in eq.index if str(p.to_period("M")) == first_month][-1]]
    record("the first month's return is measured against the starting value of 1.0",
           abs(float(m.iloc[0]) - (float(end_of_first) - 1.0)) < 1e-12,
           f"first month return {float(m.iloc[0])}, want {float(end_of_first) - 1.0}")
    record("the monthly returns compound back to the equity curve",
           abs(float(np.prod(1.0 + m.to_numpy(float))) - float(eq.iloc[-1])) < 1e-9,
           f"compounded {float(np.prod(1.0 + m.to_numpy(float)))} vs equity "
           f"{float(eq.iloc[-1])}")


def test_windows() -> None:
    """The rolling windows must run forward from the start and backward from the end."""
    cal = pd.DatetimeIndex(pd.bdate_range("2016-09-01", periods=2534))
    w = eac.build_windows(cal, 24, 1)
    fwd = [x for x in w if x.direction == "forward"]
    bwd = [x for x in w if x.direction == "backward"]
    record("windows are built in both directions", len(fwd) > 0 and len(bwd) > 0,
           f"forward {len(fwd)}, backward {len(bwd)}")
    record("every window is 24 months long",
           all(len(x.months) == 24 for x in w), "a window is not 24 months")
    record("the forward chain starts at the beginning of the sample",
           fwd[0].start == str(cal[0].date()), f"got {fwd[0].start}")
    record("the backward chain ends at the end of the sample",
           bwd[0].end == str(cal[-1].date()), f"got {bwd[0].end}")
    record("every window is pinned to real sessions",
           all(pd.Timestamp(x.start) <= pd.Timestamp(x.end) for x in w),
           "a window's start is after its end")
    record("the window's sessions are inside the sample",
           all(pd.Timestamp(x.start) >= cal[0] and pd.Timestamp(x.end) <= cal[-1] for x in w),
           "a window falls outside the sample")
    record("the forward chain steps one month at a time",
           all(fwd[i + 1].start > fwd[i].start for i in range(len(fwd) - 1)),
           "the forward chain does not advance")
    union = set()
    for x in w:
        union.update(x.months)
    record("the two chains together cover the whole sample",
           len(union) == len({str(p) for p in cal.to_period("M")}),
           f"union covers {len(union)} months of "
           f"{len({str(p) for p in cal.to_period('M')})}")


# ======================================================================================
# the levels
# ======================================================================================
def test_sizing_levels() -> None:
    """The sizing rules must do what Amendment C says they do."""
    px, idx_close, _ = cf.load_market("us")
    mk = eac.build({s: px[s] for s in sorted(px)[:8]}, idx_close)
    d = mk.cal[100]
    names = [s for s in mk.basket.get(d, []) if s in mk.col_of][:3]
    if not names:
        record("the sizing rules were exercised on real data", False, "no basket to test")
        return
    record("the sizing rules were exercised on real data", True)

    w = eac.risk1pct_weights(mk)[d]
    ok = True
    detail = ""
    for s in names:
        a = float(mk.atr[mk.pos_of[d], mk.col_of[s]])
        want = 0.10 if (not np.isfinite(a) or a <= 0) else min(0.01 / (3 * a), 0.10)
        if abs(w[s] - want) > 1e-12:
            ok, detail = False, f"{s}: got {w[s]}, want {want}"
            break
    record("the risk1pct weight is min(0.01 / (3 x ATR%), 0.10)", ok, detail)

    v = eac.volscaled_weights(mk)[d]
    k = len(names)
    cap = 10.0 / k
    record("the volscaled weight never exceeds 10x the equal weight",
           all(v[s] <= cap + 1e-12 for s in names),
           f"cap {cap}, weights {[(s, round(v[s], 6)) for s in names]}")

    kw, diag = eac.kelly_weights(mk, 10, 0.0025, 20)
    ok = True
    detail = ""
    for s in names:
        outs = eac.kelly_outcomes(mk, 10, 0.0025).get(s, [])
        _, n = eac.kelly_fraction(outs, 10, mk.pos_of[d])
        want = 1.0 / len(mk.basket[d]) if n < 20 else None
        if want is not None and abs(kw[d][s] - want) > 1e-12:
            ok, detail = False, f"{s}: got {kw[d][s]}, want the equal weight {want}"
            break
    record("a name with fewer than 20 prior signals takes the cohort's equal weight",
           ok, detail)
    record("the Kelly diagnostics count the fallback",
           diag["name_day_weights_fallen_back_to_equal"] > 0
           and diag["name_day_weights_from_kelly"] > 0,
           f"{diag}")


def test_trailing_level() -> None:
    """The trailing distance must be 2 x the cohort's ATR%."""
    px, idx_close, _ = cf.load_market("us")
    mk = eac.build({s: px[s] for s in sorted(px)[:6]}, idx_close)
    d = next(dd for dd in mk.cal[300:] if len(mk.basket.get(dd, [])) >= 2)
    names = list(mk.basket[d])[:3]
    trail = eac.make_trail_fn(mk)
    got = trail(d, names)
    vals = [float(mk.atr[mk.pos_of[d], mk.col_of[s]]) for s in names
            if s in mk.col_of and np.isfinite(mk.atr[mk.pos_of[d], mk.col_of[s]])
            and mk.atr[mk.pos_of[d], mk.col_of[s]] > 0]
    want = 2.0 * float(np.mean(vals))
    record("the trailing distance is 2 x the cohort's mean ATR%",
           abs(got - want) < 1e-12, f"got {got}, want {want}")


def test_riskparity_scale() -> None:
    """The risk-parity scale must be a fraction in [lo, hi] and use past returns."""
    px, idx_close, _ = cf.load_market("us")
    mk = eac.build({s: px[s] for s in sorted(px)[:8]}, idx_close)
    w = eac.risk1pct_weights(mk)
    base = eac.run_cell(next(c for c in eac.build_grid() if c.cell_id == "baseline"),
                         mk, {"risk1pct": w}, cap_scale=0.6, cost_rt=0.0025)
    scale, diag = eac.riskparity_scale(base, mk.idx_close, mk.cal, 60, 0.5, 1.0)
    vals = np.array(list(scale.values()))
    record("the risk-parity scale stays inside its clip",
           bool((vals >= 0.5 - 1e-12).all() and (vals <= 1.0 + 1e-12).all()),
           f"min {vals.min()}, max {vals.max()}")
    record("the risk-parity scale actually moves (it is not a constant cap)",
           diag["distinct_scale_values"] > 1, f"{diag['distinct_scale_values']} distinct values")
    record("the ratio is not degenerate (it is not always exactly 1)",
           diag["frac_clipped_high"] < 1.0 or diag["frac_clipped_low"] > 0.0, f"{diag}")


def test_window_metrics() -> None:
    """Per-window CAGR and Sharpe must be computed from the curve, not assumed."""
    px, idx_close, _ = cf.load_market("us")
    mk = eac.build({s: px[s] for s in sorted(px)[:8]}, idx_close)
    w = eac.risk1pct_weights(mk)
    curve = eac.run_cell(next(c for c in eac.build_grid() if c.cell_id == "baseline"),
                          mk, {"risk1pct": w}, cap_scale=0.6, cost_rt=0.0025)
    w0 = eac.build_windows(mk.cal, 24, 1)[0]
    cagr, dd, ndays = eac.window_cagr(curve, w0.start, w0.end)
    eq = curve["equity"]
    m = (eq.index >= pd.Timestamp(w0.start)) & (eq.index <= pd.Timestamp(w0.end))
    c = eq.loc[m]
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    want = ((float(c.iloc[-1]) / float(c.iloc[0])) ** (1 / yrs) - 1) * 100
    record("the window CAGR is computed from the equity endpoints",
           abs(cagr - want) < 1e-9, f"got {cagr}, want {want}")
    record("the window reports the sessions it covered", ndays == len(c),
           f"{ndays} vs {len(c)}")


def main() -> int:
    print("Edge → Alpha Direction C self-tests")
    test_ofat_invariant()
    test_monthly_returns()
    test_windows()
    test_benjamini_hochberg()
    test_block_bootstrap()
    test_kelly_expansion_window()
    test_sizing_levels()
    test_trailing_level()
    test_riskparity_scale()
    test_window_metrics()
    test_prefix_consistency()
    total = PASSED + FAILED
    print("=" * 78)
    print(f"{PASSED}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
