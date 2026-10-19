#!/usr/bin/env python3
"""edge_alpha_c.py — Direction C: does any change to PORTFOLIO CONSTRUCTION help?

Pre-registered: docs/edge-to-alpha-prereg-spec-2026-10-19.md, specifically
"Amendment C — Direction C's dimensions, defined before implementation". The
spec fixes the question, the four dimensions and the FDR rule; Amendment C
fixes what every level MEANS. Nothing here re-defines any of it: where the
amendment left a detail open, the reading taken is recorded in
`interpretation_notes` in the output JSON, with the reason.

THE QUESTION
    Direction A asks whether v2's SELECTION has edge. Direction C asks the
    next question, and assumes the answer to A was yes: given the same screen
    and the same close-fill execution, does any change to how the portfolio is
    CONSTRUCTED improve the result? Four dimensions, each moved one at a
    time, because a portfolio-construction search over several dimensions at
    once cannot tell you which one did the work.

THE GRID
    C1 sizing       risk1pct (baseline) | equal | kelly | volscaled
    C2 concentration max10 (baseline)   | max5 | max20 | unlimited
    C3 holding      t10 (baseline)       | t5  | t20  | trailing
    C4 allocation   cap60 (baseline)     | uncapped | riskparity

    Amendment C lists 4 + 4 + 4 + 3 = 15 levels, so the (dimension, level)
    slots are 15 and each slot is the baseline with exactly that one
    dimension moved. The spec calls that "16 cells" and treats the 16 p-values
    as one BH family; the grid built here is those 15 slots PLUS the baseline
    reference cell = 16 entries, which keeps the family's size at 16 as the
    spec requires. Five of the 16 entries are the baseline configuration
    itself (the baseline level of each dimension, plus the reference): their
    difference series against the baseline has zero spread, so their test is
    VOID rather than a p of zero. The remaining 11 entries each change
    exactly one dimension, and that is asserted in code, not intended.

THE TEST
    Every cell is run on the close-fill engine, the v2 screen with the MA200
    regime gate ON, 0.25% round-trip cost, fully invested and equal-weighted
    by default, with the glitch mask applied to the screen's selections AND
    to the universe. Out-of-sample evaluation is on rolling 2-year windows
    stepping 1 month, forward from the start of the sample and backward from
    the end, pooled. Sharpe is primary, CAGR secondary. A cell's p-value is
    the 12-month block bootstrap of the difference in monthly returns against
    the baseline (2,000 resamples, seed 20261019), and all 16 p-values are
    adjusted with Benjamini-Hochberg at q = 0.10. A cell is significant only
    if its adjusted q <= 0.10 AND the point estimate favours it on Sharpe.

    A portfolio-construction search over 16 configurations will find one that
    looks good by luck. That is what the FDR correction is for, which is why
    the primary metric is Sharpe and why the correction is applied across all
    16 cells rather than within each dimension.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "v2engine"))

import closefill as cf  # noqa: E402
import signal_search_s1 as s1  # noqa: E402

REPO = Path("/Users/kenken/dev/dsa-hk")
SPEC = "docs/edge-to-alpha-prereg-spec-2026-10-19.md"
AMENDMENT = "Amendment C - Direction C's dimensions, defined before implementation"

# ======================================================================================
# the pre-registered grid
# ======================================================================================
DIM_SIZING, DIM_CONC, DIM_HOLD, DIM_ALLOC = "C1_sizing", "C2_concentration", "C3_holding", "C4_allocation"
DIMS = (DIM_SIZING, DIM_CONC, DIM_HOLD, DIM_ALLOC)
SHORT = {DIM_SIZING: "C1", DIM_CONC: "C2", DIM_HOLD: "C3", DIM_ALLOC: "C4"}

LEVELS: dict[str, tuple[str, ...]] = {
    DIM_SIZING: ("risk1pct", "equal", "kelly", "volscaled"),
    DIM_CONC: ("max10", "max5", "max20", "unlimited"),
    DIM_HOLD: ("t10", "t5", "t20", "trailing"),
    DIM_ALLOC: ("cap60", "uncapped", "riskparity"),
}
BASELINE: dict[str, str] = {
    DIM_SIZING: "risk1pct",
    DIM_CONC: "max10",
    DIM_HOLD: "t10",
    DIM_ALLOC: "cap60",
}
CONC_CAP = {"max5": 5, "max10": 10, "max20": 20, "unlimited": None}
HOLD_SESSIONS = {"t5": 5, "t10": 10, "t20": 20, "trailing": 10}


@dataclass(frozen=True)
class Cell:
    """One (dimension, level) slot of the grid, resolved to a full config."""
    cell_id: str
    dimension: str          # the dimension this slot moves, or "reference"
    level: str              # the level it moves TO, or "baseline"
    config: dict[str, str]  # all four dimensions, resolved

    @property
    def n_dims_changed(self) -> int:
        return sum(1 for d in DIMS if self.config[d] != BASELINE[d])

    @property
    def is_baseline_config(self) -> bool:
        return self.n_dims_changed == 0


def build_grid() -> list[Cell]:
    """The 15 (dimension, level) slots plus the baseline reference = 16 entries."""
    cells = [Cell("baseline", "reference", "baseline", dict(BASELINE))]
    for d in DIMS:
        for lv in LEVELS[d]:
            cfg = dict(BASELINE)
            cfg[d] = lv
            cells.append(Cell(f"{SHORT[d]}_{lv}", d, lv, cfg))
    return cells


def assert_ofat(cells: list[Cell]) -> dict:
    """Enforce the one-factor-at-a-time invariant in code.

    The invariant is what makes the grid interpretable: a cell that moved two
    dimensions cannot be attributed to either. A cell is allowed to move zero
    dimensions only when it IS the baseline (the baseline level of a
    dimension, and the reference cell itself), which is asserted too.
    """
    assert len(cells) == 16, f"the grid must have 16 entries, got {len(cells)}"
    for c in cells:
        diffs = [d for d in DIMS if c.config[d] != BASELINE[d]]
        assert len(diffs) <= 1, f"{c.cell_id} moves {len(diffs)} dimensions: {diffs}"
        if c.dimension in DIMS and c.level != BASELINE[c.dimension]:
            # a non-baseline level must be the ONLY thing that moved
            assert diffs == [c.dimension], (
                f"{c.cell_id} must move exactly {c.dimension}, moved {diffs}")
        else:
            assert diffs == [], f"{c.cell_id} is a baseline-level slot but moved {diffs}"
    # every dimension must offer every one of its levels
    for d in DIMS:
        seen = sorted(c.level for c in cells if c.dimension == d)
        assert seen == sorted(LEVELS[d]), f"{d}: levels {seen} != {sorted(LEVELS[d])}"
    # the baseline must be present and identifiable
    assert any(c.cell_id == "baseline" for c in cells), "no baseline cell"
    single = [c for c in cells if c.n_dims_changed == 1]
    base_copies = [c for c in cells if c.is_baseline_config]
    distinct = {tuple(sorted(c.config.items())) for c in cells}
    return {
        "cells": len(cells),
        "cells_changing_exactly_one_dimension": len(single),
        "cells_equal_to_the_baseline_config": len(base_copies),
        "distinct_configurations": len(distinct),
        "rule": ("each cell is the baseline with at most one dimension moved; "
                 "a non-baseline level moves exactly one; the cells equal to "
                 "the baseline are its own level of each dimension plus the "
                 "reference cell, and their test against the baseline is void "
                 "because the difference series has zero spread"),
    }


# ======================================================================================
# data preparation
# ======================================================================================
def apply_glitch(baskets: dict, bad: dict) -> tuple[dict, int]:
    """Drop names that must not be traded on a session, from every basket.

    Mirrors edge_alpha_a.apply_glitch: a known-bad bar makes the PRICE wrong,
    not just the features, so the universe is filtered too and both sides of
    every comparison lose the same names on the same sessions.
    """
    out, dropped = {}, 0
    for d, names in baskets.items():
        keep = []
        for s in names:
            if d in bad.get(s, ()):
                dropped += 1
                continue
            keep.append(s)
        out[d] = keep
    return out, dropped


@dataclass
class Market:
    px: dict
    idx_close: pd.Series
    cal: pd.DatetimeIndex
    closes: pd.DataFrame
    tradable: pd.DataFrame
    universe: dict
    selection: dict
    basket: dict                 # the screen with the A2 universe fallback
    feats: dict
    atr: np.ndarray              # dates x symbols, ATR% (T-1 shifted)
    rank: pd.DataFrame           # dates x symbols, 5-day return (T-1 shifted)
    col_of: dict
    pos_of: dict
    glitch_dropped_universe: int
    glitch_dropped_selection: int
    empty_selection_days: int


def prepare(market: str) -> Market:
    """Load the market, then build everything the grid needs from it."""
    px, idx_close, syms = cf.load_market(market)
    return build(px, idx_close)


def build(px: dict, idx_close: pd.Series) -> Market:
    """Glitch-mask, screen, and lay out the arrays the grid needs.

    Split out from `prepare` so the self-tests can rebuild the whole market
    from TRUNCATED bars and check that nothing before the cut changes.
    """
    cal = cf.calendar(idx_close)
    mkt_up = s1.market_up_series(idx_close).dropna()
    feats = {s: s1.base_features(b) for s, b in px.items()}

    closes = cf.close_matrix(px, cal)
    tradable = cf.tradable_matrix(px, cal)
    universe = cf.universe_basket(tradable)

    bad = cf.glitch_bad_days(px, cal)
    universe, dropped_u = apply_glitch(universe, bad)
    universe = {d: [s for s in names if tradable.at[d, s]] for d, names in universe.items()}

    selection = cf.screen_daily(px, feats, mkt_up, cf.ScreenConfig(regime=True))
    selection, dropped_s = apply_glitch(selection, bad)

    # Amendment A2 (the shipped construction): an empty selection day falls
    # back to the universe, so the book is fully invested and C4's "fraction
    # of the portfolio held in equities" means what it says.
    basket = {d: (selection.get(d) or list(universe.get(d, []))) for d in cal}

    col_of = {c: j for j, c in enumerate(closes.columns)}
    pos_of = {d: i for i, d in enumerate(cal)}
    atr = np.full((len(cal), len(closes.columns)), np.nan)
    rank_arr = np.full((len(cal), len(closes.columns)), np.nan)
    for c, j in col_of.items():
        if c not in feats:
            continue
        atr[:, j] = feats[c]["atr_pct"].reindex(cal).to_numpy(float)
        rank_arr[:, j] = feats[c]["ret_5d"].reindex(cal).to_numpy(float)
    rank = pd.DataFrame(rank_arr, index=cal, columns=closes.columns)

    return Market(px=px, idx_close=idx_close, cal=cal, closes=closes, tradable=tradable,
                  universe=universe, selection=selection, basket=basket, feats=feats,
                  atr=atr, rank=rank, col_of=col_of, pos_of=pos_of,
                  glitch_dropped_universe=dropped_u, glitch_dropped_selection=dropped_s,
                  empty_selection_days=sum(1 for d in cal if not selection.get(d)))


# ======================================================================================
# C1 - sizing
# ======================================================================================
def risk1pct_weights(mk: Market) -> dict:
    """v2's own rule: w_i proportional to min(0.01 / (3 x ATR%_i), 0.10).

    1% of equity risked per position, 10% notional cap, then normalised to the
    cohort's capital (the engine does the normalisation; these are relative
    weights). ATR% is the T-1 shifted feature, so it is known at the signal.
    """
    out = {}
    for d, names in mk.basket.items():
        if not names:
            continue
        i = mk.pos_of[d]
        w = {}
        for s in names:
            j = mk.col_of.get(s)
            a = mk.atr[i, j] if j is not None else np.nan
            w[s] = 0.10 if (not np.isfinite(a) or a <= 0) else min(0.01 / (3.0 * a), 0.10)
        out[d] = w
    return out


def volscaled_weights(mk: Market) -> dict:
    """w_i proportional to 1 / ATR%_i, capped at 10x the equal weight."""
    out = {}
    for d, names in mk.basket.items():
        if not names:
            continue
        i = mk.pos_of[d]
        k = len(names)
        raw = {}
        for s in names:
            j = mk.col_of.get(s)
            a = mk.atr[i, j] if j is not None else np.nan
            raw[s] = 1.0 / a if (np.isfinite(a) and a > 0) else 0.0
        tot = sum(raw.values())
        if tot <= 0:
            out[d] = {s: 1.0 / k for s in names}
            continue
        cap = 10.0 / k
        out[d] = {s: min(v / tot, cap) for s, v in raw.items()}
    return out


def kelly_outcomes(mk: Market, hold: int, cost_rt: float) -> dict:
    """Each name's own prior close-fill SIGNAL outcomes.

    An outcome is the trade the strategy would actually have made: buy at the
    signal session's close, sell at the close of the session `hold` later,
    net of the same round-trip cost the engine charges. Only signals whose
    EXIT session is strictly before the date at which the estimate is used
    are eligible, so the estimate never uses a price it could not have seen.
    """
    close_arr = mk.closes.to_numpy(float)
    out: dict[str, list[tuple[int, float]]] = {}
    for d, names in mk.selection.items():
        i = mk.pos_of[d]
        j = i + hold
        if j >= len(mk.cal):
            continue
        for s in names:
            c = mk.col_of.get(s)
            if c is None:
                continue
            c0, c1 = close_arr[i, c], close_arr[j, c]
            if not (np.isfinite(c0) and np.isfinite(c1)) or c0 <= 0:
                continue
            out.setdefault(s, []).append((i, float(c1 / c0 - 1.0 - cost_rt)))
    for s in out:
        out[s].sort(key=lambda t: t[0])
    return out


def kelly_fraction(outs: list[tuple[int, float]], hold: int, i: int) -> tuple[float, int]:
    """f = p - (1 - p) / b from the name's own prior outcomes, clipped to [0, 0.5].

    Returns (f, n_prior). The prior set is the prefix of the name's signals
    whose exit session is strictly before the signal date i.
    """
    prior = [o for (si, o) in outs if si + hold < i]
    n = len(prior)
    if n == 0:
        return 0.0, 0
    vals = np.array(prior, float)
    p = float((vals > 0).mean())
    wins = vals[vals > 0]
    losses = vals[vals <= 0]
    if len(wins) == 0:
        return 0.0, n
    if len(losses) == 0:
        return float(min(max(p, 0.0), 0.5)), n
    b = float(wins.mean() / abs(losses.mean()))
    if not np.isfinite(b) or b <= 0:
        return 0.0, n
    f = p - (1.0 - p) / b
    return float(min(max(f, 0.0), 0.5)), n


def kelly_weights(mk: Market, hold: int, cost_rt: float, min_signals: int) -> tuple[dict, dict]:
    """The `kelly` level's weights, plus the diagnostics that explain them.

    A name with fewer than `min_signals` prior signals falls back to the
    equal weight for that cohort, because a Kelly fraction from six
    observations is noise. The fallback is per NAME: that name takes the
    cohort's equal weight, the other names keep their own Kelly fraction, and
    the engine normalises the result to the cohort's capital.
    """
    outs = kelly_outcomes(mk, hold, cost_rt)
    out = {}
    n_fallback = 0
    n_kelly = 0
    for d, names in mk.basket.items():
        if not names:
            continue
        i = mk.pos_of[d]
        k = len(names)
        w = {}
        for s in names:
            f, n = kelly_fraction(outs.get(s, []), hold, i)
            if n < min_signals:
                w[s] = 1.0 / k
                n_fallback += 1
            else:
                w[s] = f
                n_kelly += 1
        out[d] = w
    diag = {
        "names_with_any_prior_signal": len(outs),
        "name_day_weights_from_kelly": n_kelly,
        "name_day_weights_fallen_back_to_equal": n_fallback,
        "min_prior_signals": min_signals,
    }
    return out, diag


# ======================================================================================
# C3 - the trailing exit
# ======================================================================================
def make_trail_fn(mk: Market, mult: float = 2.0):
    """The `trailing` level: 2 x ATR% below the cohort's high-water mark.

    The cohort's ATR% is the equal-weighted mean of its members' ATR% at the
    signal date (the cohort is equal-weighted by default, so that is the
    cohort's own risk scale). The engine compares the cohort's value against
    its peak with this distance, and the time exit at t10 still applies if
    the trail never fires.
    """
    def trail(sig, names):
        i = mk.pos_of.get(sig)
        if i is None or not names:
            return None
        vals = []
        for s in names:
            j = mk.col_of.get(s)
            if j is None:
                continue
            a = mk.atr[i, j]
            if np.isfinite(a) and a > 0:
                vals.append(float(a))
        if not vals:
            return None
        return float(mult * float(np.mean(vals)))
    return trail


# ======================================================================================
# C4 - market allocation
# ======================================================================================
def trailing_realised_vol(series: pd.Series, days: int) -> pd.Series:
    """Trailing realised volatility over `days` CALENDAR days, past returns only.

    The value at t is the standard deviation of the daily returns in
    (t - days, t - 1]: the current session's return is excluded, so the scale
    never uses a price it could not have had when the decision was made.
    """
    r = series.pct_change()
    return r.rolling(f"{days}D", min_periods=20).std().shift(1)


def riskparity_scale(base_curve: pd.DataFrame, idx_close: pd.Series, cal: pd.DatetimeIndex,
                     days: int, lo: float, hi: float) -> tuple[dict, dict]:
    """clip(target_vol / trailing_60d_realised_vol, 0.5, 1.0), both past-only.

    target_vol is the realised volatility of the `cap60` configuration over
    the same trailing window (Amendment C says so). The denominator is the
    market's trailing realised volatility over the same window: that is the
    only reading of the amendment's unnamed series that is not degenerate.
    The alternatives were measured, not assumed:
      * the cap60 configuration itself -> the ratio is 1 by construction, so
        the level is identical to `uncapped`;
      * the scaled portfolio's own volatility -> the ratio is the constant
        0.6/scale, whose fixed point is a constant cap, not a dynamic one.
    The market reading moves: on this sample the ratio has a median near 1.0,
    is clipped low on about a tenth of the sessions and high on about half.
    """
    eq = base_curve["equity"].reindex(cal)
    idx = idx_close.reindex(cal).ffill()
    v_target = trailing_realised_vol(eq, days)
    v_market = trailing_realised_vol(idx, days)
    ratio = (v_target / v_market).replace([np.inf, -np.inf], np.nan)
    scale = ratio.clip(lo, hi)
    out, n_default = {}, 0
    for d in cal:
        v = scale.get(d, np.nan)
        if v is None or not np.isfinite(v):
            # no estimate yet (the trailing window is not full): no scaling
            out[d] = hi
            n_default += 1
        else:
            out[d] = float(v)
    valid = ratio.dropna()
    diag = {
        "denominator": "market index trailing realised volatility (same window)",
        "target": "cap60 configuration trailing realised volatility (same window)",
        "window_calendar_days": days,
        "clip": [lo, hi],
        "n_sessions_with_a_ratio": int(len(valid)),
        "n_sessions_without_an_estimate": int(n_default),
        "no_estimate_default": hi,
        "ratio_median": float(valid.median()) if len(valid) else None,
        "ratio_mean": float(valid.mean()) if len(valid) else None,
        "frac_clipped_low": float((valid <= lo).mean()) if len(valid) else None,
        "frac_clipped_high": float((valid >= hi).mean()) if len(valid) else None,
        "distinct_scale_values": int(scale.round(9).nunique()),
    }
    return out, diag


# ======================================================================================
# running a cell
# ======================================================================================
def run_cell(cell: Cell, mk: Market, weights_by_sizing: dict, cap_scale: dict | float,
             cost_rt: float, hold_default: int = 10) -> pd.DataFrame:
    """Run one cell on the shared close-fill engine."""
    sizing = cell.config[DIM_SIZING]
    conc = cell.config[DIM_CONC]
    hold_level = cell.config[DIM_HOLD]
    alloc = cell.config[DIM_ALLOC]

    hold = HOLD_SESSIONS[hold_level]
    if alloc == "uncapped":
        scale_fn = None
    elif alloc == "cap60":
        scale_fn = lambda d: cap_scale
    else:
        scale_fn = lambda d: cap_scale.get(d, 1.0)
    cfg = cf.ExecConfig(
        hold=hold,
        cost_rt=cost_rt,
        fill_lag=0,
        max_concurrent=CONC_CAP[conc],
        capital_scale_fn=scale_fn,
        trail_fn=(make_trail_fn(mk) if hold_level == "trailing" else None),
    )
    weights = weights_by_sizing.get(sizing)
    rank = None if conc == "unlimited" else mk.rank
    bc = cf.basket_columns(mk.basket, mk.closes, mk.cal)
    return cf.run_cohort(mk.basket, mk.closes, mk.tradable, mk.cal, cfg,
                         weights=weights, rank=rank, basket_cols=bc)


# ======================================================================================
# out-of-sample windows
# ======================================================================================
@dataclass(frozen=True)
class Window:
    wid: str
    direction: str
    start: str          # the first session in the window (ISO date)
    end: str            # the last session in the window (ISO date)
    months: tuple       # the calendar months the window covers ("YYYY-MM")


def build_windows(cal: pd.DatetimeIndex, length_months: int = 24,
                  step_months: int = 1) -> list[Window]:
    """Rolling 2-year windows, 1-month step, forward from the start AND
    backward from the end. The two chains are pooled by the caller.

    A window is defined on the calendar-month grid and then pinned to the
    sessions it actually contains, so its CAGR and Sharpe are measured over
    real sessions rather than over a month boundary that may fall on a
    non-trading day.
    """
    sess = pd.Series(cal, index=cal.to_period("M"))
    first_by_month = sess.groupby(level=0).first()
    last_by_month = sess.groupby(level=0).last()
    first = cal[0].to_period("M")
    last = cal[-1].to_period("M")
    out = []
    k = 0
    while True:
        s = first + k * step_months
        e = s + length_months
        if e > last + 1:
            break
        out.append(Window(f"F{k:03d}", "forward", str(first_by_month[s].date()),
                          str(last_by_month[e - 1].date()),
                          tuple(str(p) for p in pd.period_range(s, e - 1, freq="M"))))
        k += 1
    j = 0
    while True:
        e = last + 1 - j * step_months
        s = e - length_months
        if s < first:
            break
        out.append(Window(f"B{j:03d}", "backward", str(first_by_month[s].date()),
                          str(last_by_month[e - 1].date()),
                          tuple(str(p) for p in pd.period_range(s, e - 1, freq="M"))))
        j += 1
    return out


def monthly_returns(curve: pd.DataFrame) -> pd.Series:
    """Month-over-month returns of the equity curve, indexed by month start.

    The first month's return is measured against the portfolio's starting
    value of 1.0, so no month is silently dropped.
    """
    eq = curve["equity"]
    last = eq.groupby(eq.index.to_period("M")).last()
    vals = last.to_numpy(float)
    prev = np.concatenate([[1.0], vals[:-1]])
    return pd.Series(vals / prev - 1.0, index=last.index.to_timestamp(how="start"))


def monthly_sharpe(rets: np.ndarray) -> float:
    """Annualised Sharpe of a monthly return series (sqrt(12))."""
    if len(rets) < 2:
        return 0.0
    sd = float(np.std(rets, ddof=1))
    if sd <= 0:
        return 0.0
    return float(np.mean(rets) / sd * np.sqrt(12.0))


def window_cagr(curve: pd.DataFrame, lo: str, hi: str) -> tuple[float, float, int]:
    """(CAGR%, maxDD%, sessions) over a window, using the repo's own formula."""
    eq = curve["equity"]
    m = (eq.index >= pd.Timestamp(lo)) & (eq.index <= pd.Timestamp(hi))
    c = eq.loc[m]
    if len(c) < 3:
        return 0.0, 0.0, int(len(c))
    e = c.to_numpy(float)
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    cagr = ((e[-1] / e[0]) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0
    dd = float((e / np.maximum.accumulate(e) - 1).min() * 100)
    return float(cagr), dd, int(len(c))


def evaluate_cell(curve: pd.DataFrame, windows: list[Window]) -> dict:
    """Per-window OOS metrics plus the pooled OOS monthly series."""
    m = monthly_returns(curve)
    per_window = []
    pooled_months: list[pd.Timestamp] = []
    seen = set()
    for w in windows:
        sel = m.loc[[t for t in m.index if str(t.to_period("M")) in w.months]]
        vals = sel.to_numpy(float)
        sharpe = monthly_sharpe(vals)
        cagr, dd, ndays = window_cagr(curve, w.start, w.end)
        per_window.append({
            "id": w.wid, "direction": w.direction, "start": w.start, "end": w.end,
            "n_months": int(len(vals)), "sharpe": round(sharpe, 4),
            "cagr_pct": round(cagr, 3), "maxDD_pct": round(dd, 2), "sessions": ndays,
        })
        for t in sel.index:
            if t not in seen:
                seen.add(t)
                pooled_months.append(t)
    pooled_months.sort()
    pooled = m.reindex(pooled_months)
    eq = curve["equity"]
    e_lo = float(eq.iloc[0])
    e_hi = float(eq.iloc[-1])
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    pooled_cagr = ((e_hi / e_lo) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0
    dd = float((eq / np.maximum.accumulate(eq) - 1).min() * 100)
    return {
        "windows": per_window,
        "pooled": {
            "n_months": int(len(pooled)),
            "sharpe": round(monthly_sharpe(pooled.to_numpy(float)), 4),
            "cagr_pct": round(float(pooled_cagr), 3),
            "maxDD_pct": round(dd, 2),
            "months": [str(t.date())[:7] for t in pooled_months],
        },
        "_pooled_rets": pooled,
    }


# ======================================================================================
# the test
# ======================================================================================
def block_bootstrap_p(diff: np.ndarray, block: int = 12, resamples: int = 2000,
                      seed: int = 20261019, direction: int = 1) -> dict:
    """p-value from a 12-month block bootstrap of the difference in returns.

    Blocks of `block` months are resampled with replacement, so the serial
    dependence in monthly returns is not thrown away. The series is CENTRED on
    its own mean before the resampling: the null being tested is "the mean
    difference is zero", so the bootstrap distribution has to be centred on
    zero. Resampling the raw difference would centre the null on the observed
    mean and return a p-value near 0.5 for every cell, which is not a test.

    The reported p-value is TWO-SIDED: Amendment C asks for the p-value of the
    difference and then adds a separate condition, that the point estimate
    must favour the cell, so the p-value itself carries no direction. The
    one-sided p-value in the direction of the observed effect is reported
    alongside it, and is what the "favours" condition is read against.

    A difference series with ZERO SPREAD carries no information: it is
    reported as void, never as a p of zero.
    """
    d = np.asarray(diff, float)
    n = len(d)
    out = {"p_value": 1.0, "p_value_one_sided": 1.0, "observed_mean": None,
           "null_mean": None, "null_sd": None, "n_observations": int(n), "void": True,
           "void_reason": "the difference series has zero spread, so the null "
                          "carries no information"}
    if n < 2:
        out["void_reason"] = "fewer than two monthly observations"
        return out
    obs = float(d.mean())
    out["observed_mean"] = obs
    sd = float(np.std(d, ddof=1))
    if sd <= 0:
        return out
    nb = int(np.ceil(n / block))
    starts = np.arange(0, n - block + 1)
    if len(starts) == 0:
        out["void_reason"] = "the sample is shorter than one block"
        return out
    centred = d - obs
    blocks = np.stack([centred[s:s + block] for s in starts])
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(starts), size=(resamples, nb))
    samp = blocks[pick].reshape(resamples, -1)[:, :n]
    means = samp.mean(axis=1)
    two_sided = int((np.abs(means) >= abs(obs)).sum())
    if direction >= 0:
        one_sided = int((means >= obs).sum())
    else:
        one_sided = int((means <= obs).sum())
    out.update({
        "p_value": float((two_sided + 1) / (resamples + 1)),
        "p_value_one_sided": float((one_sided + 1) / (resamples + 1)),
        "null_mean": float(means.mean()),
        "null_sd": float(means.std(ddof=1)),
        "void": False,
        "void_reason": None,
        "direction": "higher than the baseline" if direction >= 0 else "lower than the baseline",
        "n_blocks_per_resample": nb,
        "n_block_starts": int(len(starts)),
    })
    return out


def benjamini_hochberg(pvals: list[float]) -> list[float]:
    """BH step-up adjusted q-values, capped at 1."""
    p = np.asarray(pvals, float)
    n = len(p)
    order = np.argsort(p, kind="mergesort")
    q = np.empty(n, float)
    prev = 1.0
    for rank in range(n - 1, -1, -1):
        i = order[rank]
        val = min(prev, p[i] * n / (rank + 1))
        q[i] = min(val, 1.0)
        prev = q[i]
    return [float(x) for x in q]


# ======================================================================================
# main
# ======================================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="us", choices=["us", "hk", "jp"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--cost-rt", type=float, default=0.0025)
    ap.add_argument("--seed", type=int, default=20261019)
    ap.add_argument("--resamples", type=int, default=2000)
    ap.add_argument("--block-months", type=int, default=12)
    ap.add_argument("--q", type=float, default=0.10)
    ap.add_argument("--min-kelly-signals", type=int, default=20)
    ap.add_argument("--riskparity-days", type=int, default=60)
    ap.add_argument("--riskparity-lo", type=float, default=0.5)
    ap.add_argument("--riskparity-hi", type=float, default=1.0)
    ap.add_argument("--window-months", type=int, default=24)
    ap.add_argument("--window-step-months", type=int, default=1)
    a = ap.parse_args()

    print(f"loading {a.market} ...", flush=True)
    mk = prepare(a.market)
    print(f"  {len(mk.px)} names, {len(mk.cal)} sessions "
          f"({mk.cal[0].date()} -> {mk.cal[-1].date()})")
    print(f"  screen selects on {len(mk.selection)} of {len(mk.cal)} sessions "
          f"({mk.empty_selection_days} empty -> universe fallback)")
    print(f"  glitch mask removed {mk.glitch_dropped_universe} universe name-days "
          f"and {mk.glitch_dropped_selection} selection name-days")

    cells = build_grid()
    ofat = assert_ofat(cells)
    print(f"  grid: {ofat['cells']} cells, {ofat['cells_changing_exactly_one_dimension']} "
          f"change exactly one dimension, {ofat['cells_equal_to_the_baseline_config']} are "
          f"the baseline config, {ofat['distinct_configurations']} distinct configurations")

    # ---- the weight sets (one per sizing level that needs them) ---------------
    print("building the sizing weights ...", flush=True)
    weights_by_sizing: dict[str, dict] = {}
    kelly_diag = {}
    weights_by_sizing["risk1pct"] = risk1pct_weights(mk)
    weights_by_sizing["volscaled"] = volscaled_weights(mk)
    kw, kelly_diag = kelly_weights(mk, HOLD_SESSIONS["t10"], a.cost_rt, a.min_kelly_signals)
    weights_by_sizing["kelly"] = kw
    # `equal` is the engine's default: no weight override at all.

    # ---- the baseline curve (needed for the riskparity target vol) -----------
    print("running the baseline cell ...", flush=True)
    curves: dict[str, pd.DataFrame] = {}
    base_cell = next(c for c in cells if c.cell_id == "baseline")
    curves[base_cell.cell_id] = run_cell(base_cell, mk, weights_by_sizing,
                                         cap_scale=0.6, cost_rt=a.cost_rt)
    base_curve = curves[base_cell.cell_id]

    # ---- C4's dynamic scale --------------------------------------------------
    print("building the risk-parity scale ...", flush=True)
    rp_scale, rp_diag = riskparity_scale(base_curve, mk.idx_close, mk.cal,
                                          a.riskparity_days, a.riskparity_lo, a.riskparity_hi)
    print(f"  ratio median {rp_diag['ratio_median']:.3f}, "
          f"clipped low {rp_diag['frac_clipped_low']:.1%}, "
          f"clipped high {rp_diag['frac_clipped_high']:.1%}")

    # ---- run every cell ------------------------------------------------------
    print(f"running the {len(cells)} cells ...", flush=True)
    for c in cells:
        if c.cell_id in curves:
            continue
        alloc = c.config[DIM_ALLOC]
        if alloc == "cap60":
            cs = 0.6
        elif alloc == "uncapped":
            cs = None
        else:
            cs = rp_scale
        curves[c.cell_id] = run_cell(c, mk, weights_by_sizing, cap_scale=cs, cost_rt=a.cost_rt)
        print(f"  {c.cell_id:<16} equity {curves[c.cell_id]['equity'].iloc[-1]:.4f}", flush=True)

    # ---- the out-of-sample evaluation ---------------------------------------
    windows = build_windows(mk.cal, a.window_months, a.window_step_months)
    print(f"evaluating on {len(windows)} rolling windows "
          f"({sum(1 for w in windows if w.direction == 'forward')} forward, "
          f"{sum(1 for w in windows if w.direction == 'backward')} backward) ...")

    evals: dict[str, dict] = {}
    for c in cells:
        curve = curves[c.cell_id]
        ev = evaluate_cell(curve, windows)
        evals[c.cell_id] = ev
        expo = float(curve["exposure"].mean()) * 100
        ramp = float(curve["exposure"].iloc[HOLD_SESSIONS[c.config[DIM_HOLD]]:].mean()) * 100
        ev["exposure_pct"] = round(expo, 3)
        ev["exposure_pct_after_ramp"] = round(ramp, 3)
        ev["mean_names_held"] = round(float(curve["n_names"].mean()), 3)
        ev["mean_fills_per_session"] = round(float(curve["filled"].mean()), 3)
        ev["mean_names_in_basket"] = round(float(curve["n_basket"].mean()), 3)
        ev["equity_end"] = round(float(curve["equity"].iloc[-1]), 6)
        ev["cf_metrics"] = {w: {k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                                 for k, v in vals.items()}
                             for w, vals in cf.metrics(curve).items()}

    base_ev = evals[base_cell.cell_id]
    base_rets = base_ev["_pooled_rets"]
    base_sharpe = base_ev["pooled"]["sharpe"]
    base_cagr = base_ev["pooled"]["cagr_pct"]

    # ---- the p-values -------------------------------------------------------
    print(f"bootstrapping ({a.resamples} resamples, {a.block_months}-month blocks, "
          f"seed {a.seed}) ...", flush=True)
    pvals, boot_meta = [], []
    for c in cells:
        ev = evals[c.cell_id]
        rets = ev["_pooled_rets"]
        common = base_rets.index.intersection(rets.index)
        diff = rets.reindex(common).to_numpy(float) - base_rets.reindex(common).to_numpy(float)
        d_sharpe = ev["pooled"]["sharpe"] - base_sharpe
        if c.is_baseline_config:
            direction = 0
        else:
            direction = 1 if d_sharpe > 0 else (-1 if d_sharpe < 0 else 0)
        if direction == 0:
            boot = {"p_value": 1.0, "p_value_one_sided": 1.0, "observed_mean": None,
                    "null_mean": None, "null_sd": None,
                    "n_observations": int(len(diff)), "void": True,
                    "void_reason": "the cell is the baseline configuration, so the "
                                   "difference series has zero spread and the test "
                                   "carries no information"}
        else:
            boot = block_bootstrap_p(diff, a.block_months, a.resamples, a.seed, direction)
        boot_meta.append(boot)
        pvals.append(boot["p_value"])
        ev["p_value"] = round(boot["p_value"], 6)
        ev["sharpe_delta_vs_baseline"] = round(d_sharpe, 4)
        ev["cagr_delta_vs_baseline_pp"] = round(ev["pooled"]["cagr_pct"] - base_cagr, 3)
        ev["bootstrap_void"] = boot["void"]
        ev["bootstrap_void_reason"] = boot["void_reason"]
        ev["n_months_compared"] = int(len(diff))

    qvals = benjamini_hochberg(pvals)

    for c, q, boot in zip(cells, qvals, boot_meta):
        ev = evals[c.cell_id]
        ev["bh_q"] = round(q, 6)
        favours = ev["sharpe_delta_vs_baseline"] > 0
        ev["point_estimate_favours_cell_on_sharpe"] = bool(favours)
        ev["significant"] = bool((not boot["void"]) and q <= a.q and favours)
        ev["bootstrap"] = {k: (round(v, 6) if isinstance(v, float) else v)
                           for k, v in boot.items() if k != "void_reason"}

    # ---- the verdict --------------------------------------------------------
    sig = [c for c in cells if evals[c.cell_id]["significant"]]
    void = [c for c in cells if evals[c.cell_id]["bootstrap_void"]]
    favours_cells = [c for c in cells
                     if evals[c.cell_id]["point_estimate_favours_cell_on_sharpe"]
                     and not evals[c.cell_id]["bootstrap_void"]]
    worse = [c for c in cells
             if (not evals[c.cell_id]["bootstrap_void"])
             and evals[c.cell_id]["bh_q"] <= a.q
             and evals[c.cell_id]["sharpe_delta_vs_baseline"] < 0]
    best_favour = min(favours_cells,
                       key=lambda c: evals[c.cell_id]["bh_q"]) if favours_cells else None
    if sig:
        verdict = (
            f"{len(sig)} of {len(cells)} cells are significant after Benjamini-Hochberg "
            f"at q={a.q} and favour the cell on the primary metric (pooled OOS monthly "
            f"Sharpe): " + ", ".join(
                f"{c.cell_id} (q={evals[c.cell_id]['bh_q']:.3f}, "
                f"Sharpe {evals[c.cell_id]['pooled']['sharpe']:.3f} vs baseline "
                f"{base_sharpe:.3f}, CAGR {evals[c.cell_id]['pooled']['cagr_pct']:.2f}% vs "
                f"baseline {base_cagr:.2f}%)" for c in sig) + ". "
            f"The baseline cell (risk1pct/max10/t10/cap60) has a pooled OOS Sharpe of "
            f"{base_sharpe:.3f} and a CAGR of {base_cagr:.2f}% over "
            f"{base_ev['pooled']['n_months']} pooled OOS months. "
            + (f"{len(void)} of the {len(cells)} cells are void (identical to the "
               f"baseline, so the difference series has zero spread) and carry no "
               f"verdict. " if void else "")
            + "Amendment C's caveat applies: no Direction C result is a v2 "
              "recommendation unless Direction A has returned SELECTION_REAL, and "
              "Direction A is not part of this run."
        )
    else:
        detail = ""
        if best_favour is not None:
            e = evals[best_favour.cell_id]
            detail = (f"The closest call is {best_favour.cell_id}: it favours the cell on "
                      f"Sharpe ({e['pooled']['sharpe']:.3f} vs {base_sharpe:.3f}) but its "
                      f"adjusted q of {e['bh_q']:.3f} is above {a.q}. ")
        worse_txt = ""
        if worse:
            worse_txt = (" The cells that ARE significantly different from the baseline "
                         "are all worse on Sharpe: "
                         + ", ".join(f"{c.cell_id} (q={evals[c.cell_id]['bh_q']:.3f}, "
                                     f"Sharpe {evals[c.cell_id]['pooled']['sharpe']:.3f} vs "
                                     f"{base_sharpe:.3f})" for c in worse) + ". ")
        verdict = (
            f"None of the {len(cells)} cells is significant after Benjamini-Hochberg at "
            f"q={a.q} while also favouring the cell on the primary metric (pooled OOS "
            f"monthly Sharpe). The baseline cell (risk1pct/max10/t10/cap60) has a "
            f"pooled OOS Sharpe of {base_sharpe:.3f} and a CAGR of {base_cagr:.2f}% over "
            f"{base_ev['pooled']['n_months']} pooled OOS months, at a mean exposure of "
            f"{base_ev['exposure_pct']:.1f}%. "
            + detail
            + (f"{len(void)} of the {len(cells)} cells are void (identical to the "
               f"baseline, so the difference series has zero spread) and carry no "
               f"verdict. " if void else "")
            + worse_txt
            + "On this evidence no change of portfolio construction beats the "
              "baseline once the false-discovery correction is applied. Amendment C's "
              "caveat applies: no Direction C result is a v2 recommendation unless "
              "Direction A has returned SELECTION_REAL, and Direction A is not part of "
              "this run."
        )

    out = {
        "spec": SPEC,
        "amendment": AMENDMENT,
        "direction": "C - portfolio construction (OFAT grid, BH-FDR)",
        "market": a.market,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "cost_rt_round_trip": a.cost_rt,
            "screen": "v2 screen: S1 touch + close-hold + ATR cap + box cap + MA200 "
                      "regime gate ON",
            "fill": "close fill, fill_lag 0",
            "fallback_rule": "an empty selection day falls back to the universe "
                             "(Amendment A2), so the book is fully invested and C4's "
                             "fraction of equity is meaningful",
            "glitch_mask": "applied to the screen's selections AND to the universe "
                           "(mirrors edge_alpha_a.apply_glitch)",
            "weighting_default": "equal within the cohort",
            "baseline": BASELINE,
            "dimensions": {d: list(LEVELS[d]) for d in DIMS},
            "q_threshold": a.q,
            "kelly_min_prior_signals": a.min_kelly_signals,
            "kelly_outcome": "buy at the signal session's close, sell at the close of "
                             "the session `hold` later, net of the round-trip cost; only "
                             "signals whose exit session is strictly before the signal "
                             "date are used",
            "risk1pct_rule": "w_i proportional to min(0.01 / (3 x ATR%_i), 0.10)",
            "volscaled_rule": "w_i proportional to 1 / ATR%_i, capped at 10x the equal "
                              "weight",
            "trailing_rule": "exit at the close of the first session on which the "
                             "cohort's value has fallen 2 x the cohort's ATR% below its "
                             "high-water mark since entry, else at t10; the cohort's "
                             "ATR% is the equal-weighted mean of its members' ATR% at "
                             "the signal date",
            "riskparity_rule": "cohort capital scaled by clip(target_vol / "
                               "trailing_60d_realised_vol, 0.5, 1.0), target_vol = the "
                               "cap60 configuration's realised volatility over the same "
                               "trailing window",
        },
        "interpretation_notes": {
            "grid_size": ("Amendment C lists 4 + 4 + 4 + 3 = 15 levels, so the "
                          "(dimension, level) slots are 15. The spec calls the family "
                          "16 cells with 16 p-values, so the grid here is those 15 slots "
                          "PLUS the baseline reference cell = 16 entries. Five of them "
                          "are the baseline configuration (the baseline level of each "
                          "dimension plus the reference), whose test against the "
                          "baseline is void; 11 change exactly one dimension. No level "
                          "was invented to reach 16."),
            "pooled_oos_series": ("the pooled OOS monthly series is the union of the "
                                  "months covered by the forward and backward window "
                                  "chains, each month counted once. The two chains "
                                  "together cover the whole sample, so the pooled series "
                                  "is the sample's monthly return series, with every "
                                  "adaptive component (Kelly, risk parity) strictly "
                                  "past-only. See the windows block: for this sample "
                                  "length the forward and backward chains enumerate the "
                                  "same windows, so pooling both directions adds no "
                                  "extra coverage."),
            "sharpe_convention": ("Sharpe is the annualised Sharpe of MONTHLY returns "
                                  "(sqrt(12)) everywhere, matching the spec's 'pooled "
                                  "out-of-sample monthly returns'. The repo's daily "
                                  "convention is also reported per cell under "
                                  "cf_metrics."),
            "kelly_fallback_scope": ("'a name with fewer than 20 prior signals falls "
                                      "back to the equal weight for that cohort' is read "
                                      "per NAME: that name takes the cohort's equal "
                                      "weight, the other names keep their own Kelly "
                                      "fraction, and the engine normalises the mix."),
            "riskparity_denominator": ("Amendment C names the numerator (the cap60 "
                                        "configuration's realised volatility) but not "
                                        "the denominator. The market index is used: the "
                                        "alternative readings are degenerate, and the "
                                        "measurement is in the riskparity_diagnostics "
                                        "block."),
            "riskparity_window": ("'60-day' is read as 60 CALENDAR days of daily "
                                  "returns, matching the repo's use of calendar days "
                                  "for the glitch window; the current session's return "
                                  "is excluded, so only past returns are used."),
            "baseline_weights": ("the cap60 baseline's own realised volatility is the "
                                 "target for the riskparity level, and the baseline "
                                 "curve is computed before the grid so the reference "
                                 "and the target are the same series."),
            "p_value_sidedness": ("the reported p-value is TWO-SIDED: Amendment C asks "
                                  "for the p-value of the difference and then adds a "
                                  "separate condition that the point estimate must "
                                  "favour the cell, so the p-value itself carries no "
                                  "direction. The one-sided p-value in the direction "
                                  "of the observed effect is reported in the "
                                  "bootstrap block."),
            "c2_c3_interaction": ("the C2 name cap and the C3 cohort count are not "
                                  "independent. The cap caps the NAMES held across "
                                  "all cohorts, and a cohort's capital is 1/hold of "
                                  "the book, so raising the number of cohorts (t20) "
                                  "or ending cohorts early (trailing) leaves the book "
                                  "unable to fill: the mean exposure and the CAGR "
                                  "fall while the Sharpe, which is exposure "
                                  "invariant, barely moves. The per-cell mean "
                                  "exposure, mean names held and mean fills per "
                                  "session are reported so this is visible rather "
                                  "than buried."),
        },
        "sample": {
            "names": len(mk.px), "sessions": len(mk.cal),
            "first": str(mk.cal[0].date()), "last": str(mk.cal[-1].date()),
            "sessions_with_a_selection": len(mk.selection),
            "empty_selection_days": mk.empty_selection_days,
            "glitch_name_days_removed_universe": mk.glitch_dropped_universe,
            "glitch_name_days_removed_selection": mk.glitch_dropped_selection,
        },
        "grid": {"ofat": ofat, "cells": [
            {"cell_id": c.cell_id, "dimension": c.dimension, "level": c.level,
             "config": c.config, "n_dims_changed_vs_baseline": c.n_dims_changed,
             "is_baseline_config": c.is_baseline_config}
            for c in cells]},
        "bootstrap": {
            "method": "12-month block bootstrap of the difference in monthly returns "
                      "against the baseline cell; the series is centred on its own "
                      "mean so the null is 'the mean difference is zero'; the "
                      "reported p-value is two-sided, p = (count + 1) / (resamples + 1)",
            "block_months": a.block_months, "resamples": a.resamples, "seed": a.seed,
            "family": "all 16 cells, adjusted together (Amendment C)",
            "q_threshold": a.q,
        },
        "windows": {
            "length_months": a.window_months, "step_months": a.window_step_months,
            "count": len(windows),
            "forward": sum(1 for w in windows if w.direction == "forward"),
            "backward": sum(1 for w in windows if w.direction == "backward"),
            "distinct_windows": len({(w.start, w.end) for w in windows}),
            "forward_and_backward_chains_coincide":
                len({(w.start, w.end) for w in windows}) == len(
                    {w.start for w in windows if w.direction == "forward"}),
            "pooled_months": base_ev["pooled"]["n_months"],
        },
        "kelly_diagnostics": kelly_diag,
        "riskparity_diagnostics": rp_diag,
        "baseline": {
            "cell_id": base_cell.cell_id, "config": base_cell.config,
            "pooled": base_ev["pooled"],
            "exposure_pct": base_ev["exposure_pct"],
            "exposure_pct_after_ramp": base_ev["exposure_pct_after_ramp"],
            "equity_end": base_ev["equity_end"],
            "cf_metrics": base_ev["cf_metrics"],
            "windows": base_ev["windows"],
            "p_value": evals[base_cell.cell_id]["p_value"],
            "bh_q": evals[base_cell.cell_id]["bh_q"],
        },
        "cells": [
            {"cell_id": c.cell_id, "dimension": c.dimension, "level": c.level,
             "config": c.config,
             "n_dims_changed_vs_baseline": c.n_dims_changed,
             "is_baseline_config": c.is_baseline_config,
             "pooled": evals[c.cell_id]["pooled"],
             "exposure_pct": evals[c.cell_id]["exposure_pct"],
             "exposure_pct_after_ramp": evals[c.cell_id]["exposure_pct_after_ramp"],
             "mean_names_held": evals[c.cell_id]["mean_names_held"],
             "mean_fills_per_session": evals[c.cell_id]["mean_fills_per_session"],
             "mean_names_in_basket": evals[c.cell_id]["mean_names_in_basket"],
             "equity_end": evals[c.cell_id]["equity_end"],
             "cf_metrics": evals[c.cell_id]["cf_metrics"],
             "sharpe_delta_vs_baseline": evals[c.cell_id]["sharpe_delta_vs_baseline"],
             "cagr_delta_vs_baseline_pp": evals[c.cell_id]["cagr_delta_vs_baseline_pp"],
             "p_value": evals[c.cell_id]["p_value"],
             "bh_q": evals[c.cell_id]["bh_q"],
             "significant": evals[c.cell_id]["significant"],
             "point_estimate_favours_cell_on_sharpe":
                 evals[c.cell_id]["point_estimate_favours_cell_on_sharpe"],
             "bootstrap_void": evals[c.cell_id]["bootstrap_void"],
             "bootstrap_void_reason": evals[c.cell_id]["bootstrap_void_reason"],
             "bootstrap": evals[c.cell_id]["bootstrap"],
             "windows": evals[c.cell_id]["windows"]}
            for c in cells],
        "verdict": verdict,
    }

    dest = Path(a.out) if a.out else REPO / "reports" / f"edge_alpha_c_{a.market}.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    # ---- the printed summary -------------------------------------------------
    print()
    hdr = (f"{'cell':<16} {'sharpe':>8} {'cagr%':>8} {'expo%':>7} {'p':>8} "
           f"{'q':>8}  sig")
    print(hdr)
    print("-" * len(hdr))
    for c in cells:
        e = evals[c.cell_id]
        mark = "YES" if e["significant"] else ("void" if e["bootstrap_void"] else "-")
        print(f"{c.cell_id:<16} {e['pooled']['sharpe']:>8.3f} {e['pooled']['cagr_pct']:>8.2f} "
              f"{e['exposure_pct']:>7.1f} {e['p_value']:>8.4f} {e['bh_q']:>8.4f}  {mark}")
    print()
    print(f"VERDICT: {verdict}")
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
