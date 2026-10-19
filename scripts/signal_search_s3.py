#!/usr/bin/env python3
"""Sprint 3 — does the stock screen beat just holding the universe?

Pre-registered: docs/sprint-3-deployment-matched-benchmark-prereg-2026-10-19.md
as amended by Amendment 1 (the original pass criterion named an undefined null
and, taken literally, had sd = 0).

The question Sprint 2 could not answer: `regime=none` posted the best raw gaps
in Sprint 1, but at 2.4x the deployment of the arm it is compared against, and
against a null that recorded neither trades nor exposure. So its advantage may
be deployment rather than selection. Sprint 3 removes the confound by
construction: the arm's idle cash is put to work in the same-universe
equal-weight series, so both arms are 100% invested every day and the residual
cannot be an exposure artefact.

Two arms, gate on and gate off. Each is compared to equal-weight, and each is
compared to a deployment-matched randomization of its own screen (Amendment 1):
the benchmark is never shuffled, the screen is.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signal_search_s1 as s1
import signal_search_s2 as s2
from signal_search_s2 import cost_rt

REPO = Path("/Users/kenken/dev/dsa-hk")
SPEC = "docs/sprint-3-deployment-matched-benchmark-prereg-2026-10-19.md"
Z_CRIT = 1.96
TOL_EXPOSURE_PP = 1.0      # VOID rule, from the spec
TOL_TRADES_PCT = 5.0
WINDOWS = ("2016-2020", "2021-2026")


# ======================================================================================
# the construction
# ======================================================================================
def ew_daily_returns(ew_monthly: pd.Series, cal: pd.DatetimeIndex) -> pd.Series:
    """Spread each month's equal-weight return over that month's trading days.

    `ew_bh` is MONTHLY — 121 month-ends, median gap 31 days — while the trading
    calendar is daily (2,534 sessions) and `walk()` realises P&L daily. Blending
    the two directly, as a first implementation did, put a whole month of
    benchmark return onto one day and compared the result against a naive CAGR
    computed on a different grid entirely. Measured on arm B 2016-20 that
    mis-resolution was worth roughly 9pp/yr of the reported gap.

    Geometric spreading preserves the monthly total exactly: n days each earning
    (1 + r)^(1/n) - 1 reproduce r at the month end, so the benchmark's own CAGR is
    unchanged by the conversion.
    """
    rm = ew_monthly.pct_change()
    out = pd.Series(0.0, index=cal)
    periods = cal.to_period("M")
    for per, r in rm.items():
        if not np.isfinite(r) or r <= -1.0:
            continue
        days = periods == pd.Period(per, freq="M")
        n = int(days.sum())
        if n:
            out[days] = (1.0 + r) ** (1.0 / n) - 1.0
    return out


def match_to_ew(curve: pd.DataFrame, ew_ret: pd.Series, cal: pd.DatetimeIndex,
                force_expo: float | None = None) -> pd.DataFrame:
    """Put the arm's idle cash to work in the equal-weight index.

    On a day the arm has `w` of its book deployed, the remaining `1 - w` earns
    the equal-weight return instead of zero. Both arms are therefore 100%
    invested by construction, and the comparison cannot be an exposure artefact.

    `have_ew` is 1 only where the benchmark actually has a value on that date.
    Where it does not, the residual stays in cash and `expo` is reported below
    1 — which is what makes the VOID rule falsifiable rather than tautological.

    `force_expo` exists for the identity controls and must not be used in a real
    run.
    """
    if curve.empty:
        return pd.DataFrame(columns=["equity", "expo"])

    eq_arm = curve["equity"].reindex(cal).ffill().bfill()
    expo = curve["expo"].reindex(cal).ffill().bfill().clip(0.0, 1.0)
    if force_expo is not None:
        expo = pd.Series(float(force_expo), index=cal)

    # have_ew must be read BEFORE the fill: after fillna every day looks
    # invested, which would make the VOID rule unfalsifiable.
    have_ew = ew_ret.reindex(cal).notna().astype(float)
    ew_ret = ew_ret.reindex(cal).fillna(0.0)
    arm_ret = eq_arm.pct_change().fillna(0.0)

    # The weighting must be the exposure held DURING the interval, which is the
    # PREVIOUS period's reading, not this one. walk() records equity and expo
    # after that date's exits and entries, so arm_ret[t] is realised entirely on
    # exit dates -- exactly the dates where post-trade expo is lowest. Weighting
    # arm_ret[t] by expo[t] therefore substitutes the benchmark for the largest
    # moves in the series and quietly destroys the arm's own gains. The control
    # in sprint3_selftests pins this by hand arithmetic.
    #
    # An earlier version of this comment claimed the sign error was worth
    # 10.2pp/yr. That was wrong: the number came from comparing a monthly-grid
    # matched gap against an event-grid naive gap, which is the separate defect
    # below. On the monthly grid the shift alone moved arm B 2016-20 from +6.33
    # to +5.46. The two defects together moved it to +13.30, and they are not
    # separately attributable from the pilot runs on record.
    w = expo.shift(1)
    w.iloc[0] = expo.iloc[0]
    matched_ret = w * arm_ret + (1.0 - w) * have_ew * ew_ret
    matched_expo = w + (1.0 - w) * have_ew
    equity = 100_000.0 * (1.0 + matched_ret).cumprod()
    return pd.DataFrame({"equity": equity, "expo": matched_expo}, index=cal)


def cagr_gaps(matched: pd.DataFrame, ew_cagr: dict) -> dict:
    """Matched CAGR minus equal-weight CAGR, per window, on the SAME grid.

    Both sides are stored as GAPS. Sprint 2's axis A once stored raw CAGR here
    and compared it against a gap, which is an error of the whole benchmark
    (~26pp/yr) and made a null look like it beat the rule by 25pp/yr. Storing
    gaps on both sides is the fix; the identity control is what proves it holds.
    """
    return {w: s1.window_stats_series(matched["equity"], w)["cagr%"] - ew_cagr[w]
            for w in WINDOWS}


def naive_gaps(curve: pd.DataFrame, cal: pd.DatetimeIndex, ew_cagr: dict) -> dict:
    """The UNMATCHED comparison, on the same daily grid as the matched arm.

    `window_stats` on the raw event-date curve is what Sprint 1 published, and it
    starts the window at the first signal rather than at the window boundary, so
    it is not comparable with a daily-grid series. Both are reported; the
    exposure-story falsification uses this one.
    """
    eq = curve["equity"].reindex(cal).ffill().bfill()
    return {w: s1.window_stats_series(eq, w)["cagr%"] - ew_cagr[w] for w in WINDOWS}


def naive_gaps_event_grid(curve: pd.DataFrame, ew_cagr: dict) -> dict:
    """Sprint 1's convention, kept so the two grids can be compared directly."""
    return {w: s1.window_stats(curve, w)["cagr%"] - ew_cagr[w] for w in WINDOWS}


# ======================================================================================
# the null (Amendment 1)
# ======================================================================================
def placebo_matched(real: list[dict], pool: dict, ordinal: dict, cost: float,
                    ew_ret: pd.Series, cal: pd.DatetimeIndex, ew_cagr: dict,
                    trials: int, seed: int, rng=None
                    ) -> tuple[dict, list[float], list[float]]:
    """Randomize the screen, match the deployment, then blend — as the real arm.

    Same sessions, same entry mechanics, same sizing; the name is drawn from the
    whole universe with the regime gate, ATR cap and box cap removed, because
    those ARE the selection rule under test. The null is then blended with the
    equal-weight series by its OWN time-varying exposure, so it is matched to the
    real arm by construction rather than by after-the-fact adjustment.
    """
    rng = rng or np.random.default_rng(seed)
    by_date: dict = {}
    for rc in real:
        by_date.setdefault(rc["entry_date"], []).append(rc)

    gaps = {w: [] for w in WINDOWS}
    trades, exposure = [], []
    for _ in range(trials):
        cands = []
        for dt, rcs in by_date.items():
            alts = pool.get(dt)
            if not alts:
                cands.extend(rcs)          # no universe candidate that session
                continue
            idx = [int(x) for x in rng.permutation(len(alts))][:len(rcs)]
            for j in idx:
                cands.append({**alts[j], "why": "p"})
        curve, meta = s1.walk(cands, cost, ordinal)
        matched = match_to_ew(curve, ew_ret, cal)
        trades.append(meta["trades"])
        exposure.append(float(matched["expo"].mean()) * 100 if not matched.empty
                        else 0.0)
        for w, v in cagr_gaps(matched, ew_cagr).items():
            gaps[w].append(v)
    return {"gaps": gaps}, trades, exposure


# ======================================================================================
# scoring
# ======================================================================================
def score(real_gap: dict, null: dict) -> dict:
    out = {}
    for w in WINDOWS:
        arr = np.asarray(null["gaps"][w], float)
        mu, sd = float(arr.mean()), float(arr.std(ddof=1))
        g = real_gap[w]
        net = g - mu
        z = net / sd if sd > 0 else float("nan")
        out[w] = {
            "real_gap_pp": round(g, 2),
            "null_mean_pp": round(mu, 2),
            "null_sd_pp": round(sd, 3),
            "net_pp": round(net, 2),
            "z": round(z, 3) if z == z else None,
            "null_percentile": round(float((arr < g).mean() * 100), 1),
            "n": int(len(arr)),
            "has_power": bool(sd > 0),
            # VOID is not FAIL: a zero-spread null has not shown the screen
            # fails, it has shown the test had no power.
            "passes": bool(sd > 0 and net > 0 and z >= Z_CRIT),
            "void": bool(sd <= 0),
        }
    zs = [v["z"] for v in out.values() if v["z"] is not None]
    return {
        "windows": out,
        "clears_bar": all(v["passes"] for v in out.values()),
        "any_void": any(v["void"] for v in out.values()),
        "worst_z": min(zs) if zs else None,
        "worst_net_pp": min(v["net_pp"] for v in out.values()),
        "z_crit": Z_CRIT,
    }


def deployment_note(real_trades: int, real_expo: float, trades: list,
                    exposure: list) -> tuple[bool, str]:
    mt = float(np.mean(trades)) if trades else 0.0
    me = float(np.mean(exposure)) if exposure else 0.0
    dt = (mt - real_trades) / real_trades * 100 if real_trades else 999.0
    de = me - real_expo
    ok = abs(dt) <= TOL_TRADES_PCT and abs(de) <= TOL_EXPOSURE_PP
    return ok, (f"null trades {mt:.0f} ({dt:+.1f}% vs real {real_trades}), "
                f"exposure {me:.2f}% ({de:+.2f}pp vs real {real_expo:.2f}%)")


def identity_check(real_gap: dict, real: list[dict], ordinal: dict, cost: float,
                   ew_ret: pd.Series, cal: pd.DatetimeIndex, ew_cagr: dict) -> dict:
    """Feed the real candidates back in as the null. net must be exactly 0.

    Sprint 2's axis A shipped a 25pp/yr error because the null series held raw
    CAGR while the scorer compared it against a gap, and nothing in the pipeline
    noticed. This control fails loudly if that class of error returns, and it
    costs one walk because the candidate list is reused rather than redrawn.
    """
    curve, _ = s1.walk(real, cost, ordinal)
    matched = match_to_ew(curve, ew_ret, cal)
    g = cagr_gaps(matched, ew_cagr)
    return {w: round(g[w] - real_gap[w], 10) for w in WINDOWS}


# ======================================================================================
def run_arm(name: str, cfg, px, feats, mkt_up, ew_ret, ordinal, cost, cal,
            ew_cagr: dict, pool: dict, trials: int, seed: int,
            identity: bool) -> dict:
    real = s1.build_candidates(px, feats, cfg, mkt_up)
    curve, meta = s1.walk(real, cost, ordinal)

    naive = naive_gaps(curve, cal, ew_cagr)
    naive_evt = naive_gaps_event_grid(curve, ew_cagr)
    matched = match_to_ew(curve, ew_ret, cal)
    real_gap = cagr_gaps(matched, ew_cagr)
    matched_expo = float(matched["expo"].mean()) * 100 if not matched.empty else 0.0

    null, trades, exposure = placebo_matched(
        real, pool, ordinal, cost, ew_ret, cal, ew_cagr, trials, seed)
    ok, note = deployment_note(meta["trades"], matched_expo, trades, exposure)
    gate = score(real_gap, null)

    # The exposure-story falsification: if matching barely moved the gap, the
    # premise that deployment explains part of it is wrong.
    moved = {w: abs(real_gap[w] - naive[w]) for w in WINDOWS}

    res = {
        "arm": name,
        "regime": cfg.regime,
        "signals": len(real),
        "trades": meta["trades"],
        "unmatched_exposure_pct": round(meta["avg_exposure"] * 100, 2),
        "matched_exposure_pct": round(matched_expo, 4),
        "void_by_exposure": bool(abs(matched_expo - 100.0) > TOL_EXPOSURE_PP),
        "naive_gap_pp": {w: round(naive[w], 2) for w in WINDOWS},
        "naive_gap_event_grid_pp": {w: round(naive_evt[w], 2) for w in WINDOWS},
        "matched_gap_pp": {w: round(real_gap[w], 2) for w in WINDOWS},
        "gap_moved_by_matching_pp": {w: round(moved[w], 2) for w in WINDOWS},
        "null_trades_mean": round(float(np.mean(trades)), 1) if trades else None,
        "null_exposure_mean_pct": round(float(np.mean(exposure)), 3) if exposure else None,
        "deployment_matched": ok,
        "deployment_note": note,
        "gate": gate,
    }
    if identity:
        res["identity_residual_pp"] = identity_check(
            real_gap, real, ordinal, cost, ew_ret, cal, ew_cagr)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="us", choices=["us", "hk"])
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261019)
    ap.add_argument("--cost", default="realistic", choices=["site", "realistic", "pess"])
    ap.add_argument("--identity", action="store_true",
                    help="also run the scorer identity control")
    a = ap.parse_args()

    bars_dir, ipath, _, mkt_key = s1.BARS[a.market]
    px, idx_close, syms = s1.load_market(a.market)
    if idx_close is None or len(px) < 50:
        print(f"FATAL: {a.market} data unusable ({len(px)} tickers)")
        return 2
    mkt_up = s1.market_up_series(idx_close).dropna()
    feats = {s: s1.base_features(b) for s, b in px.items()}
    ew = s1.ew_bh(px)
    ordinal = s1.calendar_ordinal(idx_close)
    scen = "realistic" if a.cost == "pess" else a.cost
    cost = cost_rt(mkt_key, scen, False)

    # The blend grid is the DAILY trading calendar, not the benchmark's month
    # ends. Mixing a daily realised-P&L arm with a monthly benchmark, or
    # comparing a daily-grid CAGR against an event-grid one, silently moves the
    # answer by several pp/yr. See ew_daily_returns().
    cal = pd.DatetimeIndex(sorted(set(idx_close.index)))
    ew_ret = ew_daily_returns(ew, cal)
    ew_eq_daily = 100.0 * (1.0 + ew_ret).cumprod()
    ew_cagr = {w: s1.window_stats_series(ew_eq_daily, w)["cagr%"] for w in WINDOWS}
    ew_cagr_monthly = {w: s1.window_stats_series(ew, w)["cagr%"] for w in WINDOWS}

    print(f"{a.market}: {len(px)} tickers, {cal[0].date()} -> {cal[-1].date()}, "
          f"cost={scen}, trials={a.trials}, seed={a.seed}")

    print("building universe pool (screen removed) ...")
    pool = s2.build_universe_pool(px, feats, s1.BASELINE)
    print(f"  universe pool: {len(pool)} dates, "
          f"{sum(len(v) for v in pool.values())} candidates")

    # A frozen dataclass, so the gate-off arm is BASELINE with one field changed.
    arms = [("A", "gate on", s1.BASELINE),
            ("B", "gate off", replace(s1.BASELINE, regime="none"))]

    out_arms = []
    for key, desc, cfg in arms:
        print(f"\narm {key} — {desc} ({cfg.regime})")
        r = run_arm(key, cfg, px, feats, mkt_up, ew_ret, ordinal, cost, cal,
                    ew_cagr, pool, a.trials, a.seed, a.identity)
        print(f"  signals {r['signals']}  trades {r['trades']}  "
              f"exposure {r['unmatched_exposure_pct']:.1f}% -> "
              f"matched {r['matched_exposure_pct']:.2f}%")
        for w in WINDOWS:
            v = r["gate"]["windows"][w]
            print(f"  {w}  naive {r['naive_gap_pp'][w]:+7.2f}  "
                  f"matched {v['real_gap_pp']:+7.2f}  "
                  f"null {v['null_mean_pp']:+7.2f} (sd {v['null_sd_pp']:.3f})  "
                  f"net {v['net_pp']:+7.2f}  z {str(v['z']):>7}  "
                  f"{'PASS' if v['passes'] else ('VOID' if v['void'] else 'fail')}")
        if "identity_residual_pp" in r:
            print(f"  identity residual (must be 0): {r['identity_residual_pp']}")
        out_arms.append(r)

    rep = {
        "spec": SPEC,
        "amendment": "Amendment 1 — the null was undefined in the original "
                     "pass criterion; the benchmark is not shuffled, the screen is",
        "market": a.market,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "trials": a.trials,
        "seed": a.seed,
        "cost_scenario": scen,
        "config": {"s1_lb": s1.BASELINE.s1_lb, "close_conf": s1.BASELINE.close_conf,
                   "stop_atr": s1.BASELINE.stop_atr, "hold": s1.BASELINE.hold,
                   "max_slots": s1.MAX_SLOTS, "risk": s1.RISK},
        "ew_cagr_pct": {w: round(ew_cagr[w], 2) for w in WINDOWS},
        "ew_cagr_pct_monthly_grid": {w: round(ew_cagr_monthly[w], 2) for w in WINDOWS},
        "blend_grid": "daily trading calendar",
        "universe_pool_dates": len(pool),
        "universe_pool_candidates": sum(len(v) for v in pool.values()),
        "arms": out_arms,
        "verdict": ("both arms clear" if all(r["gate"]["clears_bar"] for r in out_arms)
                    else "no arm clears the deployment-matched bar"),
    }
    out = REPO / "reports" / f"signal_search_s3_{a.market}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out}\nVERDICT: {rep['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())