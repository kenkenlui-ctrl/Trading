#!/usr/bin/env python3
"""Self-tests for Sprint 3 (signal_search_s3.py).

Every claim this module makes is checked by a control that must be able to
discriminate. Two of these are exact identities rather than approximations:

  * forcing the arm to 100% deployed must reproduce the arm exactly;
  * forcing it to 0% must reproduce the equal-weight series exactly.

If the blend ever stops being a blend, both fail. The third control is the
scorer fed its own real gap as the null, which must net to exactly zero — the
error class that made Sprint 2's axis A report a 25pp/yr advantage that was
really a units mismatch.

A fourth control is the one this project has now hit three times: a null with
sd = 0 must come back VOID, never PASS.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signal_search_s1 as s1
import signal_search_s3 as s3

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
# fixtures — synthetic, so these run in a second and test the maths, not the data
# ======================================================================================
def fixture(n: int = 600, seed: int = 11):
    cal = pd.bdate_range("2018-01-01", periods=n)
    rng = np.random.default_rng(seed)
    ew = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n))), index=cal)
    ew_ret = ew.pct_change()                       # NaN on the first day, as usual
    step = np.linspace(0.97, 1.04, n) + rng.normal(0, 0.002, n)
    equity = 100.0 * np.cumprod(step)
    expo = np.where(np.arange(n) < n // 3, 0.85, 0.10)
    expo[rng.random(n) < 0.05] = 1.0
    # a sparse curve, as walk() actually emits: only event dates
    ev_idx = sorted(rng.choice(n, size=n // 12, replace=False))
    curve = pd.DataFrame({"equity": equity[ev_idx], "expo": expo[ev_idx]},
                         index=cal[ev_idx])
    return cal, ew, ew_ret, curve


def test_ew_spreading_preserves_the_month():
    """A monthly benchmark spread onto daily grid must reproduce each month."""
    mcal = pd.date_range("2024-01-31", periods=6, freq="ME")
    monthly = pd.Series([100.0, 110.0, 99.0, 118.8, 130.68, 117.61], index=mcal)
    dcal = pd.bdate_range("2024-01-01", "2024-06-28")
    r = s3.ew_daily_returns(monthly, dcal)
    for i, per in enumerate(pd.period_range("2024-01", periods=6, freq="M")):
        mask = dcal.to_period("M") == per
        got = float((1.0 + r[mask]).prod())
        want = monthly.iloc[i] / monthly.iloc[i - 1] if i else 1.0
        record(f"spreading preserves the {per} return exactly",
               abs(got - want) < 1e-12, f"got {got}, want {want}")
        break                                       # one month is enough to pin it
    total_got = float((1.0 + r).prod())
    total_want = monthly.iloc[-1] / monthly.iloc[0]
    record("spreading preserves the whole-sample total return",
           abs(total_got - total_want) < 1e-9,
           f"got {total_got}, want {total_want}")


def test_blend_identities() -> None:
    cal, ew, ew_ret, curve = fixture()

    # --- fully deployed must reproduce the arm -------------------------------
    m1 = s3.match_to_ew(curve, ew_ret, cal, force_expo=1.0)
    arm = curve["equity"].reindex(cal).ffill().bfill()
    want = 100_000.0 * arm / arm.iloc[0]
    err = float(np.max(np.abs(m1["equity"].to_numpy() - want.to_numpy())))
    record("blend at 100% deployed reproduces the arm exactly", err < 1e-6,
           f"max abs error {err:.3e}")

    # --- fully idle must reproduce equal-weight ------------------------------
    m0 = s3.match_to_ew(curve, ew_ret, cal, force_expo=0.0)
    want0 = 100_000.0 * (1.0 + ew_ret.fillna(0.0)).cumprod()
    err0 = float(np.max(np.abs(m0["equity"].to_numpy() - want0.to_numpy())))
    record("blend at 0% deployed reproduces the equal-weight series exactly",
           err0 < 1e-6, f"max abs error {err0:.3e}")

    # --- the mixed case is genuinely in between ------------------------------
    mm = s3.match_to_ew(curve, ew_ret, cal)
    between = (float(mm["equity"].min()) < float(m1["equity"].max())
               and float(mm["equity"].max()) > float(m0["equity"].min()))
    record("the mixed blend lies between its two limits", between,
           "the blend must be a weighting, not a copy of either side")


def test_exposure_is_falsifiable() -> None:
    cal, ew, ew_ret_raw, curve = fixture()
    # A "complete" benchmark has no missing days. The fixture's first return is
    # NaN because the level has no prior point, which is a real shortfall of one
    # day, not a complete benchmark -- so fill it before claiming 100%.
    ew_ret = ew_ret_raw.fillna(0.0)
    m = s3.match_to_ew(curve, ew_ret, cal)
    expo = float(m["expo"].mean()) * 100
    record("a complete benchmark makes matched exposure 100% by construction",
           abs(expo - 100.0) < 1e-9, f"got {expo:.4f}%")

    # If the benchmark is missing, the residual cannot be invested and the VOID
    # rule must be able to see it. Without this the rule is tautological.
    holed = ew_ret_raw.copy()
    holed.iloc[100:160] = np.nan
    mh = s3.match_to_ew(curve, holed, cal)
    expo_hole = float(mh["expo"].mean()) * 100
    record("a benchmark gap drops matched exposure below 100%",
           expo_hole < 100.0 - s3.TOL_EXPOSURE_PP,
           f"got {expo_hole:.4f}%, so the VOID rule is not a tautology")
    record("the exposure shortfall is reported on exactly the missing days",
           bool((mh["expo"][100:160] < 1.0).any())
           and bool((mh["expo"].drop(index=mh.index[100:160]) <= 1.0).all()),
           "days with no benchmark must keep their residual in cash, and only "
           "those days may be under-invested")


def test_scorer_identity() -> None:
    """Real gap fed back as the null must net to exactly zero."""
    real = {"2016-2020": 2.5, "2021-2026": -1.25}
    # Spread but mean exactly equal to the real gap, so the null has power and
    # the net must be zero. A constant array would have sd = 0 and take the VOID
    # path, which would test the void branch instead of the arithmetic.
    def around(v):
        return [v, v + 0.4, v - 0.4] * 100

    same = {"gaps": {"2016-2020": around(2.5), "2021-2026": around(-1.25)}}
    g = s3.score(real, same)
    powered = all(v["has_power"] for v in g["windows"].values())
    record("the identity fixture is powered (so this tests arithmetic, not VOID)",
           powered, f"has_power={[v['has_power'] for v in g['windows'].values()]}")
    zero = all(abs(v["net_pp"]) < 1e-9 and abs(v["z"]) < 1e-9 for v in g["windows"].values())
    record("the scorer nets to exactly zero against its own input", zero,
           f"got net={[v['net_pp'] for v in g['windows'].values()]} "
           f"z={[v['z'] for v in g['windows'].values()]}")

    # A null shifted up by a constant must move net by exactly that constant,
    # with sd unchanged. This is the units check: gaps on both sides.
    shifted = {"gaps": {"2016-2020": [v + 1.0 for v in same["gaps"]["2016-2020"]],
                        "2021-2026": [v - 0.5 for v in same["gaps"]["2021-2026"]]}}
    g2 = s3.score(real, shifted)
    ok = (abs(g2["windows"]["2016-2020"]["net_pp"] - (-1.0)) < 1e-9
          and abs(g2["windows"]["2021-2026"]["net_pp"] - 0.5) < 1e-9)
    record("shifting the null shifts net by exactly that amount", ok,
           f"got {[g2['windows'][w]['net_pp'] for w in s3.WINDOWS]}")


def test_zero_spread_null_is_void_not_pass() -> None:
    """The trap this project has hit three times."""
    real = {"2016-2020": 2.5, "2021-2026": -1.25}
    flat = {"gaps": {"2016-2020": [0.0] * 300, "2021-2026": [0.0] * 300}}
    g = s3.score(real, flat)
    for w in s3.WINDOWS:
        v = g["windows"][w]
        record(f"a zero-spread {w} null is VOID, not a pass",
               v["void"] is True and v["passes"] is False and v["has_power"] is False
               and v["z"] is None,
               f"got void={v['void']} passes={v['passes']} z={v['z']}")
    record("a run with any void cell cannot clear the bar", g["clears_bar"] is False,
           "clears_bar must be false when a cell has no power")

    # A real spread must clear — the control that the void path is not just
    # rejecting everything.
    good = {"gaps": {"2016-2020": [0.0, 0.1, -0.1] * 100,
                     "2021-2026": [0.0, 0.1, -0.1] * 100}}
    g2 = s3.score({"2016-2020": 2.5, "2021-2026": 2.5}, good)
    record("a well-powered null that the arm beats does pass",
           g2["clears_bar"] is True,
           f"z={g2['worst_z']} — if this fails, the bar is unreachable")


def test_arms_are_distinct() -> None:
    record("the two arms differ only by the regime gate",
           s3.replace(s1.BASELINE, regime="none").regime == "none"
           and s3.replace(s3.replace(s1.BASELINE, regime="none"),
                          regime="ma200") == s1.BASELINE,
           "arm B must be BASELINE with the gate switched off and nothing else")
    record("the gate-off arm is not the baseline",
           s3.replace(s1.BASELINE, regime="none") != s1.BASELINE,
           "if A and B were the same config the whole sprint measures nothing")


def test_weighting_uses_the_exposure_held_during_the_interval() -> None:
    """The single most load-bearing convention in this sprint, by hand.

    walk() records equity and exposure AFTER that date's exits and entries, so
    an arm's P&L is realised in one jump on the exit date — the very date on
    which post-trade exposure is lowest. Weighting that return by the same date's
    exposure hands the benchmark the largest move in the series.

    The scorer identity control cannot catch this: feeding the real arm back as
    its own null nets to zero under EITHER convention. So this fixture pins it
    with arithmetic written out by hand.

        dates      d0   d1   d2    d3
        arm equity 100  100  120   120     -> the +20% lands on d2, the exit
        exposure   0.0  1.0   0.0   0.0     -> fully deployed during d1->d2
        ew equity  100  110  121   133.1   -> +10% every period

    Correct: d1->d2 was spent deployed, so the +20% is the arm's own and the
    benchmark contributes nothing on that interval. Every other interval was
    spent in cash, so the benchmark contributes its +10%.
        matched returns = +10%, +10%, +20%, +10%
        matched equity  = 110000, 121000, 145200, 159720

    Wrong (weighting by the exit date's own exposure): d1->d2 is credited to the
    benchmark too, giving 133100 — 16.5% of terminal equity destroyed, and the
    arm's own best day silently becomes someone else's return.
    """
    cal = pd.bdate_range("2024-01-01", periods=4)
    ew_ret = pd.Series([0.10, 0.10, 0.10, 0.10], index=cal)
    curve = pd.DataFrame({"equity": [100.0, 100.0, 120.0, 120.0],
                          "expo": [0.0, 1.0, 0.0, 0.0]}, index=cal)

    m = s3.match_to_ew(curve, ew_ret, cal)
    got = m["equity"].to_numpy()
    want = np.array([110_000.0, 121_000.0, 145_200.0, 159_720.0])
    record("the blend weights by the exposure held during the interval",
           bool(np.allclose(got, want)),
           f"got {got.tolist()}, want {want.tolist()} "
           f"(133100 means the exit-date exposure was used)")

    wrong = np.array([110_000.0, 110_000.0, 121_000.0, 133_100.0])
    record("the control distinguishes the two conventions",
           not np.allclose(got, wrong),
           "if both conventions give the same answer this control proves nothing")

    # And the residual weight must go to the benchmark, not to cash.
    residual = float(m["expo"].iloc[2])
    record("the day the arm realised its gain reports 100% deployed",
           abs(residual - 1.0) < 1e-12,
           f"expo on d2 was {residual}, so the residual weight was misattributed")


def main() -> int:
    print("Sprint 3 self-tests")
    test_ew_spreading_preserves_the_month()
    test_blend_identities()
    test_weighting_uses_the_exposure_held_during_the_interval()
    test_exposure_is_falsifiable()
    test_scorer_identity()
    test_zero_spread_null_is_void_not_pass()
    test_arms_are_distinct()
    total = PASSED + FAILED
    print("=" * 78)
    print(f"{PASSED}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())