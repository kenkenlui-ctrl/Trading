"""closefill.py — a close-fill, fully-invested cohort portfolio engine.

WHY THIS EXISTS
    Sprint 3 showed that no configuration of v2 produces a portfolio edge once
    deployment is matched: with the idle cash put to work in equal-weight, both
    the gated and the ungated arms land BELOW a random screen drawn from the
    same universe. But that test kept v2's plumbing — 10 slots, 1%-risk sizing,
    limit fills at S1. The per-trade close-hold edge is real (z 8.9-13.2) and is
    simply not expressed by that structure.

    This engine removes the plumbing. It builds a portfolio that is fully
    invested every day, equal-weighted, filled at a CLOSE (so no limit-fill
    asymmetry), and held for a fixed number of sessions. Whatever is left
    differing between two arms is the SELECTION, and nothing else.

CONSTRUCTION (this is the whole contract; nothing is implicit)
    * A cohort is formed on every trading day from that day's basket.
    * Capital is split into `hold` equal cohorts. Each day's cohort takes 1/hold
      of the portfolio's equity at that moment, equal-weighted within itself.
    * A cohort is liquidated at the close of the day it reaches age `hold`, and
      the day's new cohort is bought at the same close.
    * No stop. The stop is anchored to S1, and the control arm holds the WHOLE
      universe, most of which never touches S1 and therefore has no level to
      anchor a stop to. Removing it is the only symmetric option; Sprint 1's
      stop sweep (2x / 3x / 4x) separately found no stop setting clearing its
      own placebo bar, so the stop is not where the edge was hiding.
    * Costs: `cost_rt` round trip, charged on both legs of every fill.

NO-LOOK-AHEAD
    The screen's features are T-1 shifted (the same convention as the shipped
    engine in v2engine/core.py and as signal_search_s1): the S1 level, ATR,
    MA200 and its slope are all built from bars through t-1, and only the
    session's own OHLC decide whether the level was touched and held. The
    signal day t is therefore decided on information available at t-1 plus t's
    own bar, and the fill is at t's close.

    `fill_lag=1` shifts the fill to the NEXT session's close, for the strict
    variant in which the close that triggers the rule is not the close you
    trade at. That is the executable version of the same signal.

FILL AND EXPOSURE MATCHING
    Both arms fill at a close, so a fill either happens or the name had no bar
    that day; there is no partial fill and no limit queue. Exposure is a
    measured property, not an assumption: `run_cohort` reports the average
    invested fraction, and a basket that is empty on a day must fall back to
    something (see `universe_basket`) or the arm is not fully invested and the
    comparison is not matched.
"""
from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "v2engine"))

import signal_search_s1 as s1  # noqa: E402
from bt_clean import build_mask  # noqa: E402

WINDOWS = {
    "2016-2026": (pd.Timestamp("2016-01-01"), pd.Timestamp("2027-01-01")),
    "2016-2020": (pd.Timestamp("2016-01-01"), pd.Timestamp("2021-01-01")),
    "2021-2026": (pd.Timestamp("2021-01-01"), pd.Timestamp("2027-01-01")),
}


# ======================================================================================
# configuration
# ======================================================================================
@dataclass(frozen=True)
class ScreenConfig:
    """The v2 selection rule, as a set of switches.

    Every field is a v2 rule that the shipped engine applies. Turning one off
    is a separate experiment, not a variant of the same one, so each is named.
    """
    s1_lb: int = 20          # S1 = 20-session low of lows
    atr_max: float = 0.06    # v2 guard: ATR% must be below this
    box_max: float = 0.35    # v2 guard: box width must be below this
    regime: bool = True      # MA200 gate: prev_close > ma200, slope > 0, market up
    close_hold: bool = True  # the session must close at/above S1
    touch: bool = True       # the session must trade at/below S1


@dataclass(frozen=True)
class ExecConfig:
    """How the portfolio is built once the basket is known."""
    hold: int = 10
    cost_rt: float = 0.0025      # round trip, charged on both legs
    fill_lag: int = 0            # 0 = signal-day close, 1 = next session's close
    max_concurrent: int | None = None   # C2 concentration cap (None = unlimited)
    max_cohort_names: int | None = None  # per-cohort basket cap
    name_cap_rank: str = "ret5d"  # which names survive a cap: 'ret5d' or 'atr'
    # C4: a callable date -> float that scales the cohort's capital, so the
    # book can be held at less than 100% equity (the <=60% baseline, and the
    # risk-parity scaling). None means every cohort takes 1/hold of the book.
    # NOTE: a dataclass field REQUIRES an annotation -- an unannotated
    # assignment is only a class attribute and cannot be passed to __init__.
    capital_scale_fn: Optional[Callable[[pd.Timestamp], float]] = None
    # C3: a callable (signal_date, names) -> float giving the trailing-stop
    # distance for that cohort, as a fraction. None disables the trailing exit.
    trail_fn: Optional[Callable[[pd.Timestamp, list], Optional[float]]] = None


# ======================================================================================
# data
# ======================================================================================
def load_market(mkt: str = "us"):
    """Universe + bars, using the same loader the Sprint 1/2 harnesses used."""
    px, idx_close, syms = s1.load_market(mkt)
    if idx_close is None or len(px) < 50:
        raise SystemExit(f"{mkt} data unusable ({len(px)} tickers)")
    return px, idx_close, syms


def calendar(idx_close: pd.Series) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(sorted(set(idx_close.index)))


def close_matrix(px: dict[str, pd.DataFrame], cal: pd.DatetimeIndex) -> pd.DataFrame:
    """Dates x symbols close matrix, forward-filled for VALUATION only.

    A name with no bar on a day is not tradable that day, so it may not be
    bought; but a holding must still be worth something, so valuation carries
    the last known close. `ffill` here is for marking, never for entry.
    """
    data = {}
    for sym, b in px.items():
        s = b.close.reindex(cal)
        data[sym] = s
    df = pd.DataFrame(data, index=cal)
    return df.ffill()


def tradable_matrix(px: dict[str, pd.DataFrame], cal: pd.DatetimeIndex) -> pd.DataFrame:
    """Dates x symbols True where the name actually has a bar that day."""
    data = {sym: b.close.reindex(cal).notna() for sym, b in px.items()}
    return pd.DataFrame(data, index=cal)


# ======================================================================================
# the screen
# ======================================================================================
def screen_daily(px: dict[str, pd.DataFrame], feats: dict[str, pd.DataFrame],
                 mkt_up: pd.Series, cfg: ScreenConfig) -> dict[pd.Timestamp, list[str]]:
    """The v2 selection, evaluated per session.

    Mirrors the shipped rule exactly: features are T-1 shifted, the S1 level is
    known before the session, and the session's own OHLC decide the touch and
    the close-hold. Returns {date: [symbols]}; a date absent from the dict is a
    date with no selection.
    """
    out: dict[pd.Timestamp, list[str]] = {}
    for sym, b in px.items():
        f = feats[sym]
        mu = mkt_up.reindex(b.index).fillna(False).to_numpy(bool)
        O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        s1v = s1.s1_series(b, cfg.s1_lb).to_numpy()
        atr = f.atr_pct.to_numpy()
        ma200 = f.ma200.to_numpy()
        slope = f.ma200_slope.to_numpy()
        pc = f.prev_close.to_numpy()
        boxw = (f.R1.to_numpy() / s1v - 1)

        valid = ~np.isnan(s1v) & ~np.isnan(atr) & ~np.isnan(ma200) & ~np.isnan(slope)
        valid &= (atr < cfg.atr_max) & (boxw < cfg.box_max)
        if cfg.regime:
            valid &= (pc > ma200) & (slope > 0) & mu
        for i in np.where(valid)[0]:
            if not np.isfinite(s1v[i]) or not np.isfinite(atr[i]) or atr[i] <= 0:
                continue
            if cfg.touch and not (O[i] > s1v[i] and L[i] <= s1v[i]):
                continue
            if cfg.close_hold and C[i] < s1v[i]:
                continue
            out.setdefault(b.index[i], []).append(sym)
    for d in out:
        out[d] = sorted(out[d])
    return out


def universe_basket(tradable: pd.DataFrame) -> dict[pd.Timestamp, list[str]]:
    """Every name that could be traded that day."""
    return {d: sorted(tradable.columns[tradable.loc[d].to_numpy(bool)].tolist())
            for d in tradable.index}


def random_basket(real: dict[pd.Timestamp, list[str]],
                  universe: dict[pd.Timestamp, list[str]],
                  rng: np.random.Generator) -> dict[pd.Timestamp, list[str]]:
    """The placebo: same NAME COUNT per day, drawn at random from the universe.

    Matching the count is what makes the placebo deployment-matched: the arm
    holds the same number of names on every day, so any difference in return is
    the selection and not the number of bets. A day with no selection stays
    empty, so the placebo is empty there too.
    """
    out: dict[pd.Timestamp, list[str]] = {}
    for d, names in real.items():
        pool = universe.get(d, [])
        if not names or not pool:
            out[d] = []
            continue
        k = min(len(names), len(pool))
        idx = rng.choice(len(pool), size=k, replace=False)
        out[d] = sorted(pool[i] for i in idx)
    return out


# ======================================================================================
# the portfolio
# ======================================================================================
def basket_columns(baskets: dict[pd.Timestamp, list[str]],
                   closes: pd.DataFrame,
                   cal: pd.DatetimeIndex) -> dict[pd.Timestamp, tuple]:
    """Pre-resolve each day's basket to (names, column indices).

    The universe arm forms a 179-name basket on every one of ~2,500 sessions.
    Resolving the names to column indices once, up front, turns the per-day
    work into numpy slicing instead of ~450,000 dictionary lookups, and takes a
    single universe-arm run from ~70 seconds to well under a second. That
    matters because the placebo needs hundreds of runs.
    """
    col_of = {c: j for j, c in enumerate(closes.columns)}
    out = {}
    for d in cal:
        names = baskets.get(d, [])
        cols = [col_of[s] for s in names if s in col_of]
        if cols:
            keep = [k for k, s in enumerate(names) if s in col_of]
            out[d] = (np.array([names[k] for k in keep], dtype=object),
                      np.array(cols, dtype=np.int64))
        else:
            out[d] = (np.array([], dtype=object), np.array([], dtype=np.int64))
    return out


def run_cohort(baskets: dict[pd.Timestamp, list[str]],
               closes: pd.DataFrame,
               tradable: pd.DataFrame,
               cal: pd.DatetimeIndex,
               cfg: ExecConfig,
               weights: dict | None = None,
               rank: pd.DataFrame | None = None,
               basket_cols: dict | None = None) -> pd.DataFrame:
    """Build the portfolio and return a daily curve.

    `weights` is an optional {signal_date: {sym: relative weight}} override,
    used by Direction C's sizing dimension. Without it every basket is
    equal-weighted.

    `rank` is an optional {date x sym} score used to decide which names survive
    a concentration cap -- higher is kept. It is needed only when a cap binds;
    without it a cap would either be ignored or would need an arbitrary
    tie-break.

    `basket_cols` is the pre-resolved form of `baskets`; supply it for repeated
    runs (the placebo) and leave it None for a single run.

    The curve columns are: equity (starts at 1.0), exposure (invested fraction
    of equity), n_names (positions held), n_basket (names in the day's basket),
    and filled (names actually bought).
    """
    close_arr = closes.to_numpy(float)
    trad_arr = tradable.to_numpy(bool)
    col_of = {c: j for j, c in enumerate(closes.columns)}

    side = cfg.cost_rt / 2.0
    cash = 1.0
    # A cohort lives in a SLOT, so a cohort that exits early (on a trailing
    # stop) leaves its slot empty until that slot's scheduled refill rather
    # than shifting every other cohort's age.
    slots: dict[int, dict] = {}
    rows = []

    # A fill lag means the basket decided on day s is bought at the close of
    # day s + lag, and the holding period of `hold` sessions runs from the FILL,
    # not from the signal. So on day i the basket is the one decided on day
    # i - lag, and the fill happens at this day's close. Marking a position
    # bought on a later day at an earlier day's price is not a lag, it is an
    # accounting error, so the buy index is the day the fill happens.
    lag = max(0, int(cfg.fill_lag))

    def basket_for(i):
        d = cal[i]
        if basket_cols is not None:
            names, cols = basket_cols.get(d, (None, None))
        else:
            names = np.array(baskets.get(d, []), dtype=object)
            cols = np.array([col_of[s] for s in names if s in col_of], dtype=np.int64)
            if len(cols) != len(names):
                keep = [k for k, s in enumerate(names) if s in col_of]
                names = np.array([names[k] for k in keep], dtype=object)
        if len(cols) == 0:
            return names, cols
        ok = trad_arr[i, cols]
        return names[ok], cols[ok]

    def value(i, cols, px):
        v = close_arr[i, cols]
        bad = ~np.isfinite(v) | (v <= 0)
        if bad.any():
            v = np.where(bad, px, v)
        return v

    def liquidate(c, i):
        nonlocal cash
        v = value(i, c["cols"], c["px"])
        cash += float(np.dot(c["sh"], v)) * (1.0 - side)

    for i, t in enumerate(cal):
        # 1) liquidate cohorts that have reached their age, and any cohort
        #    whose trailing stop has fired
        for k, c in list(slots.items()):
            v = value(i, c["cols"], c["px"])
            c["value"] = float(np.dot(c["sh"], v))
            if c["value"] > c["peak"]:
                c["peak"] = c["value"]
            if i - c["i"] >= cfg.hold:
                liquidate(c, i)
                del slots[k]
            elif c["trail"] is not None and c["value"] < c["peak"] * (1.0 - c["trail"]):
                liquidate(c, i)
                del slots[k]

        # 2) mark to market
        eq = cash
        for c in slots.values():
            eq += c.get("value", 0.0)

        # 3) the basket decided `lag` sessions ago, filled at today's close.
        # The signal date supplies the basket; today's tradability decides
        # which of those names can actually be bought today.
        sig = cal[i - lag] if i >= lag else None
        if sig is None:
            names = np.array([], dtype=object)
            cols = np.array([], dtype=np.int64)
        else:
            if basket_cols is not None:
                snames, scols = basket_cols.get(
                    sig, (np.array([], dtype=object), np.array([], dtype=np.int64)))
            else:
                raw = baskets.get(sig, [])
                snames = np.array(raw, dtype=object)
                keep = [k for k, s in enumerate(raw) if s in col_of]
                snames = snames[keep]
                scols = np.array([col_of[s] for s in snames], dtype=np.int64)
            if len(scols):
                ok = trad_arr[i, scols]
                snames, scols = snames[ok], scols[ok]
            names, cols = snames, scols

        # 3a) concentration caps. A cap has to choose WHICH names survive, so
        # the choice is pre-registered: highest rank first, ties by symbol.
        if rank is not None and len(cols):
            rk = rank.to_numpy(float)[i - lag] if i >= lag else None
            if rk is not None:
                rvals = rk[cols]
                rvals = np.where(np.isfinite(rvals), rvals, -np.inf)
                order = np.lexsort((np.array([str(s) for s in names]), -rvals))
                if cfg.max_cohort_names is not None and len(cols) > cfg.max_cohort_names:
                    sel = order[:cfg.max_cohort_names]
                    names, cols = names[sel], cols[sel]
                if cfg.max_concurrent is not None:
                    held = sum(len(c["cols"]) for c in slots.values())
                    room = cfg.max_concurrent - held
                    if room < len(cols):
                        if room <= 0:
                            names, cols = names[:0], cols[:0]
                        else:
                            sel = order[:room]
                            names, cols = names[sel], cols[sel]

        # 4) buy the cohort
        filled = 0
        if len(cols) and eq > 0:
            scale = 1.0 if cfg.capital_scale_fn is None else float(cfg.capital_scale_fn(t))
            cap = eq / cfg.hold * scale
            if weights and sig is not None and sig in weights:
                wmap = weights[sig]
                w = np.array([abs(wmap.get(str(s), 0.0)) for s in names], dtype=float)
                tot = w.sum() or 1.0
                w = w / tot
            else:
                w = np.full(len(cols), 1.0 / len(cols), dtype=float)
            px = close_arr[i, cols]
            ok = np.isfinite(px) & (px > 0)
            if not ok.all():
                names, cols, px, w = names[ok], cols[ok], px[ok], w[ok]
            if len(cols):
                sh = (cap * w) / px
                cash -= float(np.dot(sh, px)) * (1.0 + side)
                trail = None
                if cfg.trail_fn is not None and sig is not None:
                    trail = cfg.trail_fn(sig, list(names))
                val0 = float(np.dot(sh, px))
                slots[i % max(1, cfg.hold)] = {
                    "i": i, "cols": cols, "sh": sh, "px": px, "names": names,
                    "peak": val0, "value": val0, "trail": trail}
                filled = len(cols)

        # 5) mark again with the new cohort in place
        eq2 = cash
        for c in slots.values():
            v = value(i, c["cols"], c["px"])
            c["value"] = float(np.dot(c["sh"], v))
            if c["value"] > c["peak"]:
                c["peak"] = c["value"]
            eq2 += c["value"]
        invested2 = eq2 - cash

        rows.append({"date": t, "equity": eq2,
                     "exposure": (invested2 / eq2) if eq2 > 0 else 0.0,
                     "n_names": sum(len(c["cols"]) for c in slots.values()),
                     "n_basket": len(cols), "filled": filled})

    return pd.DataFrame(rows).set_index("date")


def metrics(curve: pd.DataFrame, windows=WINDOWS) -> dict:
    """CAGR, Sharpe, max drawdown and the average exposure, per window."""
    out = {}
    eq = curve["equity"]
    for w, (lo, hi) in windows.items():
        c = eq.loc[(eq.index >= lo) & (eq.index < hi)]
        if len(c) < 3:
            out[w] = {"cagr%": 0.0, "sharpe": 0.0, "maxDD%": 0.0, "exposure%": 0.0, "days": 0}
            continue
        e = c.to_numpy(float)
        yrs = (c.index[-1] - c.index[0]).days / 365.25
        cagr = ((e[-1] / e[0]) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0
        rets = e[1:] / e[:-1] - 1
        sd = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
        sharpe = float(rets.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
        dd = float((e / np.maximum.accumulate(e) - 1).min() * 100)
        expo = float(curve["exposure"].loc[(curve.index >= lo) & (curve.index < hi)].mean() * 100)
        out[w] = {"cagr%": round(cagr, 3), "sharpe": round(sharpe, 3),
                  "maxDD%": round(dd, 2), "exposure%": round(expo, 3), "days": int(len(c))}
    return out


# ======================================================================================
# glitch handling
# ======================================================================================
def glitch_bad_days(px: dict[str, pd.DataFrame], cal: pd.DatetimeIndex,
                    lookback_days: int = 304, forward_days: int = 21) -> dict[str, set]:
    """Days on which a name must not be traded, because a known bad bar poisons
    the features behind it.

    The shipped engine drops a candidate whose [entry - 304d, exit] window
    touches a glitch date, because ma200/S1/atr are built from shifted closes
    and one bad bar contaminates the features, not just that day's price. The
    same rule is applied here.
    """
    mask = build_mask()
    bad: dict[str, set] = {}
    for sym, b in px.items():
        g = mask.get(sym)
        if not g:
            continue
        gl = sorted(g)
        s = set()
        for d in cal:
            lo = d - pd.Timedelta(days=lookback_days)
            hi = d + pd.Timedelta(days=forward_days)
            for x in gl:
                if lo <= x <= hi:
                    s.add(d)
                    break
        bad[sym] = s
    return bad
