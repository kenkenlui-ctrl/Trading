#!/usr/bin/env python3
"""Item 1 — executability tiers on every signal. RESEARCH ONLY.

WHAT THIS IS
    The site publishes a v2 signal list and a win rate computed from a
    backtest in which every recorded fill is assumed to have been executable.
    This module asks the question the backtest never asks: of the fills it
    recorded, how many could a trader actually have got?

    It re-runs the LIVE v2 rule over the frozen 10-year bar store and labels
    every session with one of four tiers, then reports what removing the
    unfillable ones does to the per-trade average.

THE FOUR TIERS (one line each, and the order they are decided in)
    UNJUDGED        the session has no usable bar — the row is absent from the
                    store, or a required field is missing or not a finite
                    number, or S1 could not be computed. A data gap is NEVER
                    defaulted to halted, and NEVER defaulted to fillable.
    UNFILLABLE      the session carried no executable market: volume <= 0 (halted
                    / suspended / nothing traded), or high == low (one printed
                    price only, so there was no price traded AT S1).
    NOT_TRIGGERED   the session was tradeable but never came down to S1
                    (low > S1). The limit was never touched: no fill, and not
                    a failure of the rule.
    FILLABLE        the session was tradeable, reached S1 (low <= S1) and closed
                    above it (close > S1) — a fill that could have happened and
                    held.

    One session that does NOT fit the four, handled explicitly rather than
    forced into one of them: it reached S1 but closed at or below it. The v2
    close-hold rule rejects those trades before they are published, so they are
    not signals and they are not fills. Counting them as UNFILLABLE would make
    the published set empty by construction and turn the win-rate exclusion
    into a tautology; counting them as NOT_TRIGGERED would be a lie, because
    the limit WAS touched. They are reported as REACHED_S1_NOT_HELD, in their
    own column, and never enter the win-rate denominator.

WHY THE TIER IS EVALUATED AGAINST THE RAW STORE
    bt_clean.load() drops any row whose OHLC is null and sorts by date. A trade
    can only exist if its entry session survived that drop, so evaluating the
    tier on the clean frame alone could never produce an UNJUDGED verdict. The
    tier is therefore looked up in the RAW store, which still holds the rows
    the cleaner removed. The ENGINE still runs on the clean frame, so the
    reproduction of the frozen A/B numbers is exact.

WHAT THIS MODULE DOES NOT DO
    It does not touch production. It does not change the signal set, the win
    rate, or any published page. The tier is a research label; wiring it onto
    the site is a separate, flagged, Kenneth-approved step.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
sys.path.insert(0, str(REPO / "scripts"))
from core import _simulate, cost_rt, features  # noqa: E402
from market_filter import verdicts_for  # noqa: E402
from bt_clean import _load_mask_cache, bars_dir, load as load_clean  # noqa: E402

BARS = bars_dir()

# The market proxy each market filter is read from — the same pair
# ab_close_hold.py uses, so the two agree by construction.
MARKETS = {"us200": "SPY", "jp200": "1306_T"}
SPLIT = pd.Timestamp("2023-01-01")
HOLD = 10

TIER_FILLABLE = "FILLABLE"
TIER_NOT_TRIGGERED = "NOT_TRIGGERED"
TIER_UNFILLABLE = "UNFILLABLE"
TIER_UNJUDGED = "UNJUDGED"
TIER_REACHED_NOT_HELD = "REACHED_S1_NOT_HELD"

TIERS = (TIER_FILLABLE, TIER_NOT_TRIGGERED, TIER_UNFILLABLE, TIER_UNJUDGED)
ALL_TIER_LABELS = TIERS + (TIER_REACHED_NOT_HELD,)

# The site's published sub-windows, plus the engine's own train/test split.
# Both are reported: the spec asks for "2016-26 and both sub-windows", and the
# two windowing schemes in this repo are not the same thing. Which numbers get
# quoted is a choice, so every choice is shown.
WINDOWS = {
    "full_2016_2026": (None, None),
    "pre_2023": (None, "2023-01-01"),
    "post_2023": ("2023-01-01", None),
    "early_2016_2020": ("2016-01-01", "2021-01-01"),
    "late_2021_2026": ("2021-01-01", None),
}

REQUIRED_BAR_FIELDS = ("open", "high", "low", "close", "volume")


# --------------------------------------------------------------------------
# The tier classifier. Pure function: bar in, (tier, reason) out.
# --------------------------------------------------------------------------
def classify_bar(bar, s1) -> tuple[str, str]:
    """Classify one session-day.

    bar : the raw bar for that session (dict) or None when the store has no
          row for it.
    s1  : the v2 entry level for that session (float) or None.

    The decision order is fixed and total: every input lands in exactly one
    tier, and no branch guesses. A missing bar is UNJUDGED before anything
    else is considered.
    """
    # 1. UNJUDGED — nothing to judge.
    if bar is None:
        return TIER_UNJUDGED, "missing_bar"
    if s1 is None or not isinstance(s1, (int, float)) or not math.isfinite(float(s1)):
        return TIER_UNJUDGED, "no_s1"
    try:
        o = float(bar["open"]); h = float(bar["high"]); l = float(bar["low"])
        c = float(bar["close"]); v = float(bar["volume"])
    except (KeyError, TypeError, ValueError):
        return TIER_UNJUDGED, "malformed_bar"
    if not all(math.isfinite(x) for x in (o, h, l, c, v)):
        return TIER_UNJUDGED, "non_finite_bar"
    s1 = float(s1)

    # 2. UNFILLABLE — no executable market that day.
    if v <= 0:
        return TIER_UNFILLABLE, "no_volume"
    if h == l:
        return TIER_UNFILLABLE, "zero_range_no_price_at_s1"

    # 3. NOT_TRIGGERED — tradeable, but S1 was never reached.
    if l > s1:
        return TIER_NOT_TRIGGERED, "low_above_s1"

    # 4. Reached S1.
    if c > s1:
        return TIER_FILLABLE, "low_touched_s1_closed_above"
    return TIER_REACHED_NOT_HELD, "closed_at_or_below_s1"


def na_set_for_bar(bar, s1) -> list[str]:
    """The set of inputs the classifier could NOT get for this session.

    This is the thing the side-change mutation arm watches: if a code path
    serves a cached verdict instead of recomputing, the NA set does not move
    when the required inputs change, and the arm catches it.
    """
    missing: list[str] = []
    if bar is None:
        return ["bar"]
    for f in REQUIRED_BAR_FIELDS:
        if f not in bar or bar[f] is None:
            missing.append(f)
            continue
        try:
            if not math.isfinite(float(bar[f])):
                missing.append(f)
        except (TypeError, ValueError):
            missing.append(f)
    if s1 is None or not math.isfinite(float(s1)):
        missing.append("s1")
    return sorted(missing)


# --------------------------------------------------------------------------
# Raw bar access (the tier is judged on the RAW store, not the clean frame)
# --------------------------------------------------------------------------
def raw_bars(sym: str) -> list[dict]:
    p = BARS / f"{sym}.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def raw_index(sym: str) -> dict:
    """date string -> raw bar dict, for one symbol."""
    out: dict[str, dict] = {}
    for r in raw_bars(sym):
        d = r.get("date")
        if isinstance(d, str):
            out[d[:10]] = r
    return out


def is_jp(sym: str) -> bool:
    return sym.endswith("_T")


def is_us(sym: str) -> bool:
    return (not is_jp(sym) and "_" not in sym
            and not sym.startswith(("HK_", "JP_")))


def symbol_for_market(mkt: str) -> list[str]:
    return sorted(p.stem for p in BARS.glob("*.json")
                  if (is_jp(p.stem) if mkt == "jp200" else is_us(p.stem)))


# --------------------------------------------------------------------------
# The re-run: the live v2 rule, with the tier attached
# --------------------------------------------------------------------------
def _hypothetical(mkt: str, i: int, s1v: float, atr: float,
                  O, H, L, C) -> tuple[float, float]:
    """What this signal WOULD have returned under the same engine rules, had
    the entry qualification not excluded it.

    Same entry (at S1), same stop (3 x ATR below S1), same exit (10 sessions,
    stop first), same cost model. Only the entry qualification differs, so
    the difference in outcome is attributable to the exclusion itself and
    not to a different rule.

    Returns (net, exit_px); (nan, nan) when the data ran out before the
    horizon closed, which is then reported as UNKNOWN rather than 0.
    """
    stop = s1v * (1 - 3 * atr)
    if stop <= 0 or stop >= s1v:
        return float("nan"), float("nan")
    res = _simulate(1, i, s1v, stop, 1e12, O, H, L, C, HOLD, True)
    if res is None:
        return float("nan"), float("nan")
    _j, xp, why = res
    gross = xp / s1v - 1
    return gross - cost_rt(mkt, "site", why == "stop"), float(xp)


def _excluded_row(mkt: str, sym: str, dstr: str, tier: str, reason: str,
                  s1: float, atr_pct: float, why: str,
                  net: float, mark_px: float = float("nan")) -> dict:
    """One signal that the engine recognised but did not book as a trade.

    `net` is the hypothetical net return under the same engine rules, or NaN
    when it cannot be measured (the position was still open at the edge of
    the store). NaN is reported as UNKNOWN, never as 0 — a missing measurement
    booked as zero would pull the excluded cohort's average toward the middle
    and hide the very thing this cohort is being measured for.
    """
    return {
        "mkt": mkt, "sym": sym, "date": dstr,
        "s1": round(s1, 4), "atr_pct": round(atr_pct, 5),
        "tier": tier, "tier_reason": reason,
        "exclusion": why,
        "mark_px": round(mark_px, 4) if mark_px == mark_px else None,
        "forward_net": None if net != net else net,
        "forward_net_known": bool(net == net),
    }


def market_verdicts(mkt: str) -> dict[str, bool]:
    """Per-session market verdicts for one market — the live rule.

    The rule and the session it is read on are documented in
    scripts/v2engine/market_filter.py. The proxy is the pair the live path
    prefers and the pair ab_close_hold.py uses, so this mirror and the frozen
    engine cannot disagree about which sessions the market allowed.
    """
    return verdicts_for(mkt, BARS)


def rerun_v2(mkt: str, mask_all: dict | None = None,
             collect_candidates: bool = True) -> tuple[list[dict], list[dict], list[dict]]:
    """Re-run the live v2 rule (close-hold) over the frozen store.

    Returns (trades, candidates, excluded):
      trades     — every published v2 signal, with its tier attached
      candidates — every screen-passing session, with its tier attached.
                   The candidates are the denominator: they include the
                   sessions where the limit was never touched.
      excluded   — signals the engine recognised but did not book: still open
                   at the edge of the store, or dropped because a masked bar
                   sits in their window. This is the cohort that makes
                   realised-only accounting misleading about sizing.

    The trade loop is a line-for-line mirror of ab_close_hold.trades_for()
    with close_hold=True, so the per-window numbers this reproduces must
    match data/ab_close_hold.json exactly. That equivalence is asserted by
    scripts/research/selftests.py, not assumed here.
    """
    if mask_all is None:
        mask_all = load_mask()

    trades: list[dict] = []
    candidates: list[dict] = []
    excluded: list[dict] = []
    mv = market_verdicts(mkt)

    for sym in symbol_for_market(mkt):
        p = BARS / f"{sym}.json"
        df = load_clean(p)
        if df is None:
            continue
        mask = mask_all.get(sym)
        raw = raw_index(sym)

        f = features(df)
        O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = f.atr_pct.to_numpy()
        s1 = f.S1.to_numpy()
        ma200 = f.ma200.to_numpy()
        slope = f.ma200_slope.to_numpy()
        boxw = f.box_w.to_numpy()
        idx = df.index

        ok = ((f.prev_close.to_numpy() > ma200) & (slope > 0) &
              (a < 0.06) & (boxw < 0.35))

        for i in np.where(ok)[0]:
            if np.isnan(s1[i]) or np.isnan(a[i]) or a[i] <= 0:
                continue
            dstr = idx[i].strftime("%Y-%m-%d")
            # THE MARKET GATE. A session the market filter did not pass is a
            # session the live product published nothing on — so it is
            # neither a plan nor a candidate, and it cannot be in the cohort
            # this module re-derives (2026-11-14: the frozen engine had no
            # market gate at all, so this mirror and the baseline it is
            # supposed to reproduce were measuring different rules).
            if not mv.get(dstr):
                continue
            s1v = float(s1[i])
            bar = raw.get(dstr)
            tier, reason = classify_bar(bar, s1v)

            touched = (L[i] <= s1v)
            if collect_candidates:
                candidates.append({
                    "mkt": mkt, "sym": sym, "date": dstr,
                    "s1": round(s1v, 4), "atr_pct": round(float(a[i]), 5),
                    "tier": tier, "tier_reason": reason,
                    "reached_s1": bool(touched),
                    "open_above_s1": bool(O[i] > s1v),
                    "close_above_s1": bool(C[i] > s1v),
                    "na_set": na_set_for_bar(bar, s1v),
                })

            # The live v2 entry qualification.
            #
            # A candidate can fail it for three different reasons, and the
            # difference matters: one is a rule decision, two are data
            # conditions. Each is recorded separately so the excluded cohort
            # can be reported as a line of its own instead of vanishing.
            reached = (L[i] <= s1v)
            opened_above = (O[i] > s1v)
            held = (C[i] >= s1v)
            if not (opened_above and reached):
                if reached and not opened_above:
                    # The session gapped down through S1. The tier calls it
                    # FILLABLE (it reached S1 and closed above it) but the
                    # live rule refuses it, because a session that OPENED at
                    # or below the level cannot be told apart from one that
                    # broke it. This cohort is recorded, not dropped.
                    excl = "opened_at_or_below_s1"
                elif not reached:
                    excl = None            # never reached S1: NOT_TRIGGERED
                else:
                    excl = "opened_above_but_unreachable"
                if excl:
                    hnet, hpx = _hypothetical(mkt, i, s1v, float(a[i]), O, H, L, C)
                    excluded.append(_excluded_row(mkt, sym, dstr, tier, reason,
                                                  s1v, float(a[i]), excl, hnet, hpx))
                continue
            if C[i] < s1v:            # close-hold: the shipped rule
                continue
            stop = s1v * (1 - 3 * a[i])
            if stop <= 0 or stop >= s1v:
                excluded.append(_excluded_row(mkt, sym, dstr, tier, reason,
                                              s1v, float(a[i]), "stop_invalid",
                                              float("nan")))
                continue
            res = _simulate(1, i, s1v, stop, 1e12, O, H, L, C, HOLD, True)
            if res is None:
                # The trade never closed inside the data: it is still open at
                # the edge of the store. That is an EXCLUDED cohort, not a
                # failure — and it is exactly the cohort that makes realised-
                # only accounting misleading ("should I size up"), so it is
                # counted and its forward return is marked UNKNOWN rather
                # than dropped silently.
                excluded.append(_excluded_row(mkt, sym, dstr, tier, reason,
                                              s1v, float(a[i]),
                                              "open_at_data_edge",
                                              float("nan"), float("nan")))
                continue
            j, xp, why = res
            if mask:
                lo = idx[i] - pd.Timedelta(days=304)
                if any(lo <= d <= idx[j] for d in mask):
                    gnet = (xp / s1v - 1) - cost_rt(mkt, "site", why == "stop")
                    excluded.append(_excluded_row(mkt, sym, dstr, tier, reason,
                                                  s1v, float(a[i]),
                                                  "glitch_masked_window",
                                                  gnet, float(xp)))
                    continue
            gross = xp / s1v - 1
            net = gross - cost_rt(mkt, "site", why == "stop")
            trades.append({
                "mkt": mkt, "sym": sym, "date": dstr,
                "exit_date": idx[j].strftime("%Y-%m-%d"),
                "s1": round(s1v, 4), "entry": round(s1v, 4),
                "stop": round(float(stop), 4), "exit": round(float(xp), 4),
                "atr_pct": round(float(a[i]), 5),
                "gross": gross, "net": net, "reason": why,
                "held": int(j - i),
                "tier": tier, "tier_reason": reason,
                "na_set": na_set_for_bar(bar, s1v),
            })
    return trades, candidates, excluded


def load_mask() -> dict:
    """The glitch mask, read WITHOUT the write side effect.

    bt_clean._load_mask_cache() rebuilds and REWRITES
    data/bt10y_glitch_mask.json when the cache is missing or its _source does
    not match the bar store. A research script must not mutate the repo's data
    directory as a side effect of reading a mask, so the file is read here
    directly and the same validity rule is applied.
    """
    cp = REPO / "data" / "bt10y_glitch_mask.json"
    try:
        blob = json.loads(cp.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if blob.get("_source") != BARS.name:
        return {}
    out: dict[str, set] = {}
    for k, v in (blob.get("mask") or {}).items():
        try:
            out[k] = {pd.Timestamp(d) for d in v}
        except Exception:
            continue
    return out


def stats(net: np.ndarray) -> dict:
    """The same summary shape ab_close_hold.stats() produces, so the two are
    comparable without a translation layer."""
    if net is None or len(net) == 0:
        return {"n": 0, "win%": 0.0, "avg%": 0.0, "pf": 0.0, "t": 0.0}
    wins, losses = net[net > 0], net[net <= 0]
    by_date: dict[str, list[float]] = {}
    return {"n": int(len(net)), "win%": round(float((net > 0).mean() * 100), 1),
            "avg%": round(float(net.mean() * 100), 3),
            "pf": round(float(wins.sum() / abs(losses.sum())), 2) if len(losses) and losses.sum() != 0 else 0.0,
            "t": 0.0}


def tier_counts(rows: list[dict]) -> dict:
    c = {t: 0 for t in ALL_TIER_LABELS}
    for r in rows:
        c[r["tier"]] = c.get(r["tier"], 0) + 1
    return c


def in_window(date: str, lo: str | None, hi: str | None) -> bool:
    if lo and date < lo:
        return False
    if hi and date >= hi:
        return False
    return True


def _selftest() -> int:
    """A real entry point for the hard gate.

    The hard gate counts an entry as EXECUTED only if running it did actual
    work. A module that merely imports and exits is not an execution — it is
    a line in a file, which is exactly what the addendum says coverage is
    NOT. So each research module has a --selftest that exercises its own
    logic and returns non-zero when something is wrong.
    """
    raise SystemExit(0 if _selftest_body() else 1)


def _selftest_body() -> bool:
    ok = True
    # a halted session, a normal fill, and a missing bar
    halted = {"date": "2020-03-16", "open": 100.0, "high": 100.0, "low": 100.0,
              "close": 100.0, "volume": 0.0}
    normal = {"date": "2020-03-17", "open": 105.0, "high": 106.0, "low": 99.5,
              "close": 104.0, "volume": 1_000_000.0}
    ok &= classify_bar(halted, 99.0)[0] == TIER_UNFILLABLE
    ok &= classify_bar(normal, 100.0)[0] == TIER_FILLABLE
    ok &= classify_bar(None, 100.0)[0] == TIER_UNJUDGED
    ok &= classify_bar(None, 100.0)[0] != TIER_FILLABLE
    ok &= classify_bar(None, 100.0)[0] != TIER_UNFILLABLE
    # the mask must be readable without writing to the repo
    ok &= isinstance(load_mask(), dict)
    return bool(ok)


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--selftest", action="store_true")
    _a = _ap.parse_args()
    raise SystemExit(_selftest() if _a.selftest else 0)
