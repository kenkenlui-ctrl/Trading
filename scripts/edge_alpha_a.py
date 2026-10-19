#!/usr/bin/env python3
"""edge_alpha_a.py — Direction A: does v2's SELECTION have edge?

Pre-registered: docs/edge-to-alpha-prereg-spec-2026-10-19.md, as amended by
Amendment A (the construction the spec did not specify) and Amendment B (how
each outcome is read).

THE QUESTION
    Sprint 3 showed that no configuration of v2 produces a portfolio edge once
    deployment is matched — with the idle cash put to work in equal-weight,
    both the gated and the ungated arms land BELOW a random screen drawn from
    the same universe. But that test kept v2's plumbing: 10 slots, 1%-risk
    sizing, limit fills at S1. Direction A removes the plumbing and asks
    whether the SELECTION is what is worth keeping.

THE ARMS
    arm1        v2's screen, close-fill, fully invested, equal-weighted
    arm1_nogate the same screen with the MA200 regime gate removed (secondary)
    arm2        the whole universe, close-fill — the control
    placebo     random names, the same COUNT per session as the arm it tests

    Arm 1 and arm 2 differ in exactly one thing: which names they hold. Both
    fill at a close, so neither has a limit-fill asymmetry, and both are fully
    invested every day, so neither has an exposure advantage. That is what
    "fill-matched and exposure-matched" has to mean in practice, and it is
    measured rather than assumed.

THE VERDICT
    The spec's rule, applied as written: arm1 beats arm2 by at least +1.0pp/yr
    with a consistent sign in both sub-periods. The placebo then decides what
    that means — a screen that beats the universe but not a random draw of the
    same size has not shown selection skill. See Amendment B.
"""
from __future__ import annotations

import argparse
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
SPEC = "docs/edge-to-alpha-prereg-spec-2026-10-19.md"
Z_CRIT = 1.96
THRESHOLD_PP = 1.0          # the spec's +1.0pp/yr
W = ("2016-2020", "2021-2026")


# ======================================================================================
def apply_glitch(baskets: dict, bad: dict) -> tuple[dict, int]:
    """Drop names that must not be traded on a session, from every arm.

    The universe arm is filtered too: a known-bad bar makes the PRICE wrong,
    not just the features, so holding it would corrupt the control's return.
    Both arms lose the same names on the same sessions, so the comparison
    stays symmetric.
    """
    out, dropped = {}, 0
    for d, names in baskets.items():
        keep = []
        for s in names:
            if d in bad.get(s, ()):  # glitch-affected session
                dropped += 1
                continue
            keep.append(s)
        out[d] = keep
    return out, dropped


def with_fallback(sel: dict, universe: dict) -> dict:
    """Amendment A2: an empty selection day falls back to the universe."""
    out = {}
    for d in universe:
        s = sel.get(d, [])
        out[d] = s if s else list(universe[d])
    return out


def cagr_of(curve: pd.DataFrame, win: str) -> float:
    lo, hi = cf.WINDOWS[win]
    e = curve["equity"].loc[(curve.index >= lo) & (curve.index < hi)]
    if len(e) < 3:
        return 0.0
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    return ((e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0


def expo_after_ramp(curve: pd.DataFrame, win: str, hold: int) -> float:
    lo, hi = cf.WINDOWS[win]
    m = (curve.index >= lo) & (curve.index < hi)
    c = curve["exposure"][m]
    if len(c) <= hold:
        return float(c.mean()) if len(c) else 0.0
    return float(c.iloc[hold:].mean()) * 100


def score(gap_real: dict, null_gaps: dict) -> dict:
    """The placebo null: the same arm with random names, same count per day."""
    out = {}
    for w in W:
        arr = np.asarray(null_gaps[w], float)
        mu, sd = float(arr.mean()), float(arr.std(ddof=1))
        g = gap_real[w]
        net = g - mu
        z = net / sd if sd > 0 else float("nan")
        out[w] = {
            "gap_pp": round(g, 3),
            "null_mean_pp": round(mu, 3),
            "null_sd_pp": round(sd, 4),
            "net_pp": round(net, 3),
            "z": round(z, 3) if z == z else None,
            "null_percentile": round(float((arr < g).mean() * 100), 1),
            "n": int(len(arr)),
            "has_power": bool(sd > 0),
            "void": bool(sd <= 0),
            "beats_null": bool(sd > 0 and z >= Z_CRIT),
        }
    return out


def classify(gaps: dict, sc: dict) -> str:
    """Amendment B: the spec's rule, then what the placebo makes of it."""
    clears = all(gaps[w] >= THRESHOLD_PP for w in W)
    if any(sc[w]["void"] for w in W):
        return "VOID"
    if not clears:
        return "SELECTION_DEAD"
    beats = all(sc[w]["beats_null"] for w in W)
    return "SELECTION_REAL" if beats else "SELECTION_LUCKY"


# ======================================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="us", choices=["us", "hk", "jp"])
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261019)
    ap.add_argument("--cost-rt", type=float, default=0.0025)
    ap.add_argument("--hold", type=int, default=10)
    ap.add_argument("--lag", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    px, idx_close, syms = cf.load_market(a.market)
    cal = cf.calendar(idx_close)
    mkt_up = s1.market_up_series(idx_close).dropna()
    feats = {s: s1.base_features(b) for s, b in px.items()}

    print(f"{a.market}: {len(px)} names, {len(cal)} sessions "
          f"({cal[0].date()} -> {cal[-1].date()}), cost_rt={a.cost_rt}")

    closes = cf.close_matrix(px, cal)
    tradable = cf.tradable_matrix(px, cal)
    universe = cf.universe_basket(tradable)

    print("applying the glitch mask ...")
    bad = cf.glitch_bad_days(px, cal)
    universe, dropped_u = apply_glitch(universe, bad)
    universe = {d: [s for s in names if tradable.at[d, s]] for d, names in universe.items()}
    print(f"  glitch-affected name-days removed from the universe: {dropped_u}")

    print("running the v2 screen ...")
    sel_on = cf.screen_daily(px, feats, mkt_up, cf.ScreenConfig(regime=True))
    sel_off = cf.screen_daily(px, feats, mkt_up, cf.ScreenConfig(regime=False))
    sel_on, d1 = apply_glitch(sel_on, bad)
    sel_off, d2 = apply_glitch(sel_off, bad)
    print(f"  gate on : {len(sel_on)} sessions with a selection "
          f"({d1} glitch-affected name-days removed)")
    print(f"  gate off: {len(sel_off)} sessions with a selection "
          f"({d2} glitch-affected name-days removed)")

    arm1_basket = with_fallback(sel_on, universe)
    arm1ng_basket = with_fallback(sel_off, universe)
    empty_on = sum(1 for d in cal if not sel_on.get(d))
    empty_off = sum(1 for d in cal if not sel_off.get(d))

    cfg = cf.ExecConfig(hold=a.hold, cost_rt=a.cost_rt, fill_lag=a.lag)

    def run(basket):
        bc = cf.basket_columns(basket, closes, cal)
        return cf.run_cohort(basket, closes, tradable, cal, cfg, basket_cols=bc)

    print("building the arms ...")
    c_arm1 = run(arm1_basket)
    c_arm1ng = run(arm1ng_basket)
    c_arm2 = run(universe)
    print(f"  arm1      equity {c_arm1['equity'].iloc[-1]:.4f}")
    print(f"  arm1_nogate equity {c_arm1ng['equity'].iloc[-1]:.4f}")
    print(f"  arm2      equity {c_arm2['equity'].iloc[-1]:.4f}")

    # ---- placebo: same name count per session, random names ----------------
    def placebo_for(arm_basket, trials, seed):
        rng = np.random.default_rng(seed)
        gaps = {w: [] for w in W}
        trades, expo = [], []
        for _ in range(trials):
            pb = {}
            for d in cal:
                names = arm_basket.get(d, [])
                pool = universe.get(d, [])
                if not names or not pool:
                    pb[d] = []
                    continue
                k = min(len(names), len(pool))
                idx = rng.choice(len(pool), size=k, replace=False)
                pb[d] = [pool[i] for i in idx]
            c = run(pb)
            trades.append(float(c["filled"].sum()))
            expo.append(float(c["exposure"].mean()))
            for w in W:
                gaps[w].append(cagr_of(c, w) - cagr_of(c_arm2, w))
        return gaps, trades, expo

    print(f"running the placebo: {a.trials} trials per arm ...")
    null_on, tr_on, ex_on = placebo_for(arm1_basket, a.trials, a.seed)
    null_ng, tr_ng, ex_ng = placebo_for(arm1ng_basket, a.trials, a.seed + 1)

    def arm_report(curve, name, null_gaps, tr, ex, other):
        gaps = {w: cagr_of(curve, w) - cagr_of(other, w) for w in W}
        sc = score(gaps, null_gaps)
        m = cf.metrics(curve)
        return {
            "arm": name,
            "cagr_pct": {w: round(cagr_of(curve, w), 3) for w in W},
            "sharpe": {w: m[w]["sharpe"] for w in W},
            "maxDD_pct": {w: m[w]["maxDD%"] for w in W},
            "exposure_pct_after_ramp": {w: round(expo_after_ramp(curve, w, a.hold), 3)
                                        for w in W},
            "gap_vs_arm2_pp": {w: round(gaps[w], 3) for w in W},
            "null": sc,
            "clears_threshold": all(gaps[w] >= THRESHOLD_PP for w in W),
            "classification": classify(gaps, sc),
            "null_trades_mean": round(float(np.mean(tr)), 1) if tr else None,
            "null_exposure_mean_pct": round(float(np.mean(ex)), 3) if ex else None,
        }

    rep_arm1 = arm_report(c_arm1, "arm1_v2_screen", null_on, tr_on, ex_on, c_arm2)
    rep_ng = arm_report(c_arm1ng, "arm1_nogate", null_ng, tr_ng, ex_ng, c_arm2)

    expo_gap = {w: round(abs(rep_arm1["exposure_pct_after_ramp"][w]
                            - rep_arm2_expo(c_arm2, w, a.hold)), 3) for w in W}
    out = {
        "spec": SPEC,
        "amendments": "A (construction) and B (outcome readings)",
        "market": a.market,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {"hold": a.hold, "cost_rt": a.cost_rt, "fill_lag": a.lag,
                   "trials": a.trials, "seed": a.seed, "z_crit": Z_CRIT,
                   "threshold_pp": THRESHOLD_PP,
                   "selection": "v2 screen (S1 touch + close-hold + ATR cap + box cap"
                                " + MA200 regime gate)"},
        "universe": {"names": len(px), "sessions": len(cal),
                     "first": str(cal[0].date()), "last": str(cal[-1].date()),
                     "glitch_name_days_removed": dropped_u},
        "screen": {"sessions_with_selection_gate_on": len(sel_on),
                   "sessions_with_selection_gate_off": len(sel_off),
                   "empty_sessions_gate_on": empty_on,
                   "empty_sessions_gate_off": empty_off,
                   "fallback_rule": "empty selection -> the whole universe "
                                    "(Amendment A2)"},
        "arm2_universe": {
            "cagr_pct": {w: round(cagr_of(c_arm2, w), 3) for w in W},
            "sharpe": {w: cf.metrics(c_arm2)[w]["sharpe"] for w in W},
            "exposure_pct_after_ramp": {w: round(expo_after_ramp(c_arm2, w, a.hold), 3)
                                        for w in W},
            "equity_end": round(float(c_arm2["equity"].iloc[-1]), 6),
        },
        "arm1": rep_arm1,
        "arm1_nogate": rep_ng,
        "exposure_gap_arm1_vs_arm2_pp": expo_gap,
        "verdict": rep_arm1["classification"],
        "verdict_secondary_nogate": rep_ng["classification"],
    }
    dest = Path(a.out) if a.out else REPO / "reports" / f"edge_alpha_a_{a.market}.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    print()
    for rep in (rep_arm1, rep_ng):
        print(f"{rep['arm']}:")
        for w in W:
            v = rep["null"][w]
            print(f"  {w}  gap {rep['gap_vs_arm2_pp'][w]:+7.2f}  "
                  f"null {v['null_mean_pp']:+7.2f} (sd {v['null_sd_pp']:.3f})  "
                  f"z {str(v['z']):>7}  pct {v['null_percentile']:>5}%  "
                  f"{'PASS' if v['beats_null'] else 'fail'}")
        print(f"  -> {rep['classification']}")
    print(f"\nVERDICT: {out['verdict']}   (nogate arm: {out['verdict_secondary_nogate']})")
    print(f"wrote {dest}")
    return 0


def arm2_expo(curve, w, hold) -> float:
    return expo_after_ramp(curve, w, hold)


if __name__ == "__main__":
    raise SystemExit(main())
