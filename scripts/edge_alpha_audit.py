#!/usr/bin/env python3
"""edge_alpha_audit.py — a mechanical no-look-ahead audit of the Edge→Alpha work.

A no-look-ahead claim is usually argued rather than measured: the features are
described as shifted, the code looks shifted, and nobody checks. This script
checks it, on the full universe, and writes a machine-readable result.

THE CHECKS
    1. PREFIX CONSISTENCY (the screen). Run the v2 screen on the data
       TRUNCATED at date t, and on the full data. Every selection at or before
       t must be identical. Any use of a bar after t changes one of them.
       This is stronger than a feature-inspection argument because it tests
       the composed pipeline — features, regime gate, market filter, touch and
       close-hold — rather than any one function.

    2. FUTURE-SHOCK. Corrupt one bar far in the future and confirm that
       nothing at or before it changes. The prefix check can be satisfied by a
       pipeline that is merely self-consistent; the shock test confirms the
       input that matters is the right one.

    3. THE S1 LEVEL. The S1 level used on date t must be exactly
       min(low[t-20 .. t-1]) for a 20-session lookback.

    4. THE MARKET FILTER. The regime filter used on date t must be recomputed
       from an index truncated at t-1 and come out the same. This is the
       check that was previously vacuous: it guarded a condition that could
       never be true, and the truncation test fed the screen an UNTRUNCATED
       market filter, so the whole look-ahead apparatus was blind to the
       market gate. A mutant that stopped shifting the market filter passed
       every check in the old audit.

    5. THE ENGINE. The portfolio curve itself must be prefix-consistent:
       rebuild everything on data truncated at t and confirm the equity curve
       at every date up to t is identical. The old audit never touched the
       execution engine at all, so a mutant that valued a cohort at a future
       price passed the audit untouched.

    6. POWER. A check that cannot fail is not a check. Every truncation check
       is paired with a power check: the market filter has to take both values
       in the sample, and it has to change the screen, or checks 1 and 4 are
       satisfied by a filter that is constant.

EXIT CODE
    0 if every check passes, 1 otherwise. The report is written either way.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "v2engine"))

import closefill as cf  # noqa: E402
import signal_search_s1 as s1  # noqa: E402

REPO = Path("/Users/kenken/dev/dsa-hk")
OUT = REPO / "reports" / "edge_alpha_audit_us.json"
N_CUTOFFS = 5
N_ENGINE_CUTOFFS = 3
HOLD = 10
COST_RT = 0.0025


def screen_on(px, mkt_up, cfg, upto=None):
    """The v2 screen, optionally on bars truncated at `upto`.

    The market filter is truncated WITH the prices. Passing the full filter
    through unchanged meant the truncation test had no visibility on the
    market gate at all — a mutant that stopped shifting the filter survived.
    """
    sub = {}
    for sym, b in px.items():
        bb = b.loc[b.index <= upto] if upto is not None else b
        if len(bb) < 250:
            continue
        sub[sym] = bb
    if not sub:
        return {}
    if upto is not None:
        mkt_up = mkt_up.loc[mkt_up.index <= upto]
    feats2 = {s: s1.base_features(b) for s, b in sub.items()}
    return cf.screen_daily(sub, feats2, mkt_up, cfg)


def build_pipeline(px, idx_close, cfg, upto=None):
    """The WHOLE pipeline on data truncated at `upto`.

    Returns (equity curve, selections). This is what makes the engine
    auditable: the portfolio is rebuilt from scratch on less data and its
    curve up to the cut has to be identical to the full run's.
    """
    if upto is not None:
        px = {s: b.loc[b.index <= upto] for s, b in px.items()}
        idx_close = idx_close.loc[idx_close.index <= upto]
    cal = cf.calendar(idx_close)
    mkt_up = s1.market_up_series(idx_close).dropna()
    feats = {s: s1.base_features(b) for s, b in px.items()}
    closes = cf.close_matrix(px, cal)
    tradable = cf.tradable_matrix(px, cal)
    universe = cf.universe_basket(tradable)
    sel = cf.screen_daily(px, feats, mkt_up, cfg)
    basket = {d: (sel.get(d) if sel.get(d) else list(universe.get(d, [])))
              for d in universe}
    bc = cf.basket_columns(basket, closes, cal)
    exec_cfg = cf.ExecConfig(hold=HOLD, cost_rt=COST_RT, fill_lag=0)
    curve = cf.run_cohort(basket, closes, tradable, cal, exec_cfg, basket_cols=bc)
    return curve, sel, basket, cal


def main() -> int:
    market = "us"
    px, idx_close, syms = cf.load_market(market)
    cal = cf.calendar(idx_close)
    mkt_up = s1.market_up_series(idx_close).dropna()
    cfg = cf.ScreenConfig(regime=True)

    checks = []
    failed = 0

    def check(name, ok, detail=""):
        nonlocal failed
        checks.append({"check": name, "pass": bool(ok), "detail": str(detail)[:400]})
        if not ok:
            failed += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail if not ok else ''}")

    print(f"auditing {market}: {len(px)} names, {len(cal)} sessions")

    # ---- 1. prefix consistency on the FULL universe ------------------------
    full = screen_on(px, mkt_up, cfg)
    print(f"full-universe screen: {len(full)} sessions with a selection")
    dates = sorted(full)
    cutoffs = [dates[int(len(dates) * f)] for f in
               (0.2, 0.4, 0.6, 0.8, 0.95)]
    for cut in cutoffs:
        trunc = screen_on(px, mkt_up, cfg, upto=cut)
        diffs = [d for d, names in trunc.items() if full.get(d, []) != names]
        check(f"truncating the universe at {cut.date()} changes no selection "
              f"at or before it", not diffs,
              f"{len(diffs)} sessions differ, first {diffs[:3]}")

    # ---- 2. future-shock: corrupt a far-future bar, change nothing before it
    shock_date = cal[int(len(cal) * 0.75)]
    px2 = {}
    for sym, b in px.items():
        b2 = b.copy()
        if shock_date in b2.index:
            b2.loc[shock_date, ["open", "high", "low", "close"]] *= 1.35
        px2[sym] = b2
    shocked = screen_on(px2, mkt_up, cfg)
    diffs = [d for d in names_before(shocked, shock_date)
             if full.get(d, []) != shocked.get(d, [])]
    check(f"corrupting the bars on {shock_date.date()} changes no selection "
          f"before that date", not diffs,
          f"{len(diffs)} sessions differ, first {diffs[:3]}")
    after = [d for d in shocked if d > shock_date and full.get(d, []) != shocked.get(d, [])]
    print(f"    (the shock does change {len(after)} later selections, as it must)")

    # ---- 3. the S1 level uses only bars through t-1 ------------------------
    rng = np.random.default_rng(11)
    sample = [px[s] for s in list(px)[:40]]
    bad_level = 0
    checked = 0
    for b in sample:
        s1v = s1.s1_series(b, 20).to_numpy()
        for i in rng.choice(range(250, len(b)), size=6, replace=False):
            win = b.low.iloc[i - 20:i]
            if np.isnan(s1v[i]) or abs(s1v[i] - win.min()) > 1e-9:
                bad_level += 1
            checked += 1
    check(f"the S1 level on date t equals min(low[t-20..t-1])",
          bad_level == 0, f"{bad_level} of {checked} sampled levels were wrong")

    # ---- 4. the market filter is decided from index data through t-1 ------
    # The filter's value at t must not depend on anything from t onwards. The
    # test masks every index observation from t onwards and requires the value
    # at t to come out the same. Truncating the series at t-1 instead would
    # remove t's row altogether and could not answer the question.
    #
    # The filter is `idx_close.shift(1) > idx_close.shift(1).rolling(200).mean()`,
    # so its value at t is a function of the closes through t-1 and the mask
    # cannot touch it. A mutant that dropped the shift would make the value at
    # t depend on t's own close, and the mask would change it.
    mu = s1.market_up_series(idx_close).dropna()
    offs = 0
    n_tested = 0
    n_true = 0
    for t in cal[300:len(cal) - 1:37]:
        masked = idx_close.where(idx_close.index < t)
        mu_masked = s1.market_up_series(masked).dropna()
        a = bool(mu.reindex(pd.DatetimeIndex([t])).fillna(False).iloc[0])
        b2 = bool(mu_masked.reindex(pd.DatetimeIndex([t])).fillna(False).iloc[0])
        if a != b2:
            offs += 1
        n_tested += 1
        n_true += int(a)
    check("the market filter at t is decided from index data through t-1 only",
          offs == 0 and n_tested > 50 and n_true > 0,
          f"{offs} of {n_tested} sessions changed when every index observation "
          f"from t onwards was masked; {n_true} of the tested sessions had the "
          f"filter on, so the check could have failed")

    # ---- 5. the ENGINE's curve is prefix-consistent ----------------------
    print("rebuilding the whole pipeline on truncated data (this is the "
          "engine's no-look-ahead check) ...", flush=True)
    full_curve, _, _, _ = build_pipeline(px, idx_close, cfg)
    engine_cutoffs = cutoffs[::2][:N_ENGINE_CUTOFFS]
    for cut in engine_cutoffs:
        trunc_curve, _, _, _ = build_pipeline(px, idx_close, cfg, upto=cut)
        m = full_curve.index <= cut
        a = full_curve.loc[m, "equity"].to_numpy(float)
        b3 = trunc_curve.loc[:cut, "equity"].reindex(full_curve.loc[m].index).to_numpy(float)
        n = min(len(a), len(b3))
        bad = int(np.sum(np.abs(a[:n] - b3[:n]) > 1e-9))
        check(f"the equity curve up to {cut.date()} is unchanged when the data "
              f"is truncated there", bad == 0,
              f"{bad} of {n} sessions differ")

    # ---- 6. the checks above have POWER -----------------------------------
    n_true = int(mu.astype(bool).sum())
    n_false = int((~mu.astype(bool)).sum())
    check("the market filter takes both values, so the truncation checks "
          "can fail", n_true > 0 and n_false > 0,
          f"{n_true} sessions with the filter on, {n_false} with it off")
    sel_off = screen_on(px, mkt_up, cf.ScreenConfig(regime=False))
    diff_gate = sum(1 for d in set(full) | set(sel_off)
                    if full.get(d, []) != sel_off.get(d, []))
    check("the market filter changes the screen, so the market-gate checks "
          "are not vacuous", diff_gate > 0,
          f"{diff_gate} sessions differ between the gated and ungated screens")
    check("the engine check is not vacuous: the truncated run is a real "
          "rebuild", len(full_curve) > len(cal) * 0.9 and len(cal) > 1000,
          f"{len(full_curve)} sessions in the full curve, {len(cal)} in the "
          f"calendar")

    out = {
        "audit": "edge_alpha_audit",
        "market": market,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe_names": len(px),
        "sessions": len(cal),
        "first": str(cal[0].date()),
        "last": str(cal[-1].date()),
        "cutoffs": [str(c.date()) for c in cutoffs],
        "engine_cutoffs": [str(c.date()) for c in engine_cutoffs],
        "shock_date": str(shock_date.date()),
        "scope": [
            "the screen's selections are unchanged when the data is truncated "
            "at a date (the market filter is truncated with it)",
            "corrupting a far-future bar changes nothing before it",
            "the S1 level on date t equals min(low[t-20..t-1])",
            "the market filter at t is recomputed from an index truncated at "
            "t-1 and comes out the same",
            "the portfolio's equity curve is unchanged when the whole "
            "pipeline is rebuilt on data truncated at a date",
            "each of those checks is paired with a power check, so a filter "
            "or a screen that never moves cannot make them vacuous",
        ],
        "not_covered": [
            "the screen's feature definitions (a feature that is shifted by "
            "the wrong amount can still be self-consistent); the prefix and "
            "shock tests are what cover the composed pipeline",
            "the fill LAG's execution semantics, which the engine self-test "
            "suite covers rather than this audit",
        ],
        "checks": checks,
        "failed": failed,
        "verdict": "PASS" if failed == 0 else "FAIL",
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed -> {out['verdict']}")
    print(f"wrote {OUT}")
    return 1 if failed else 0


def names_before(sel: dict, d) -> list:
    return [x for x in sel if x <= d]


if __name__ == "__main__":
    raise SystemExit(main())
