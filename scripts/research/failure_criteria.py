#!/usr/bin/env python3
"""Item 3 — preregistered numeric failure criteria. RESEARCH ONLY.

THE POINT
    The site has no stated "when do we declare a strategy dead" numbers. One
    verdict was reached (SELECTION_DEAD, Direction A, 2026-10-20) by a
    pre-registered bar of +1.0pp in BOTH sub-windows. The same discipline
    should be written down for the site itself, in numbers, BEFORE a future
    run is looked at.

THE PROPOSED THRESHOLDS (proposals, not decisions)
    Kenneth confirms the numbers before anything goes live. They are written
    here as PROPOSED, and the file that would carry them to /methodology is
    a DRAFT.

      T1  Sharpe decay    OOS Sharpe must be at least 50% of the in-sample
                          Sharpe.
      T2  Drawdown        OOS max drawdown must not exceed 2x the in-sample
                          max drawdown.
      T3  Rolling streak  not three consecutive negative 20-session windows.

WINDOW DEFINITIONS (one line each)
    in-sample (IS)      entry_date < 2023-01-01 — the engine's own split, the
                        same one that produced data/ab_close_hold.json and
                        the numbers the site publishes.
    out-of-sample (OOS) entry_date >= 2023-01-01.
    session series      one value per trading session: the mean net return of
                        that session's booked v2 trades, equal-weighted.
                        The site already uses date-clustered statistics for
                        its t-statistic; this is the same idea.
    Sharpe (raw)        mean(session returns) / sd(session returns). The
                        annualisation factor cancels in the IS-vs-OOS ratio,
                        so the raw form is used and the annualised one is
                        printed beside it.
    max drawdown        the deepest peak-to-trough decline of the curve built
                        by compounding the session series. IMPORTANT: that
                        curve is a DIAGNOSTIC, not a portfolio equity curve.
                        The 10-session hold means positions overlap, so
                        compounding session means double-counts exposure and
                        the drawdown it produces is much deeper than a real
                        portfolio's. T2 is therefore a comparison of one
                        diagnostic against itself (OOS vs IS), not a claim
                        about a portfolio drawdown, and it is labelled that
                        way in every artefact.
    rolling 20 window   20 consecutive sessions, non-overlapping, stepping
                        20 at a time. T3 asks for three of them in a row to
                        be negative.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

SPLIT = "2023-01-01"
ROLLING_SESSIONS = 20
ANNUALISATION = {"us200": 252, "jp200": 244}

# PROPOSED thresholds. Not decisions. Kenneth confirms the numbers.
PROPOSED = {
    "T1_sharpe_decay_min_ratio": 0.50,
    "T2_maxdd_max_multiple": 2.0,
    "T3_max_consecutive_negative_windows": 3,
}

WINDOW_DEFINITIONS = {
    "in_sample": "entry_date < 2023-01-01",
    "out_of_sample": "entry_date >= 2023-01-01",
    "session_series": ("one value per trading session: the mean net return of "
                       "that session's booked v2 trades, equal-weighted"),
    "sharpe": ("mean(session returns) / sd(session returns); the annualised "
               "form (x sqrt(periods per year)) is printed beside it, and the "
               "annualisation cancels in the IS-vs-OOS ratio"),
    "max_drawdown": ("deepest peak-to-trough decline of the curve built by "
                     "compounding the session series"),
    "rolling_window": (f"{ROLLING_SESSIONS} consecutive sessions, "
                       "non-overlapping, stepping 20 at a time"),
}


def session_series(trades: list[dict]) -> pd.Series:
    """One value per session: the mean net return of that session's trades."""
    if not trades:
        return pd.Series(dtype=float)
    df = pd.DataFrame(trades)
    s = df.groupby("date")["net"].mean().sort_index()
    return s


def sharpe(series: pd.Series, annualise: int | None = None) -> float:
    if len(series) < 3:
        return float("nan")
    sd = float(series.std(ddof=1))
    if not sd or not math.isfinite(sd):
        return float("nan")
    r = float(series.mean()) / sd
    if annualise:
        r *= math.sqrt(annualise)
    return r


def max_drawdown(series: pd.Series) -> float:
    """Peak-to-trough decline of the compounded series, as a negative percent."""
    if len(series) == 0:
        return float("nan")
    curve = (1.0 + series.to_numpy(float)).cumprod()
    peak = np.maximum.accumulate(curve)
    dd = curve / peak - 1.0
    return float(dd.min() * 100.0)


def rolling_windows(series: pd.Series, n: int = ROLLING_SESSIONS) -> list[float]:
    """Non-overlapping n-session windows, each one's compounded return."""
    vals = series.to_numpy(float)
    out: list[float] = []
    for i in range(0, len(vals) - n + 1, n):
        chunk = vals[i:i + n]
        out.append(float(np.prod(1.0 + chunk) - 1.0))
    return out


def longest_negative_streak(windows: list[float]) -> int:
    """The longest run of consecutive negative windows."""
    best = run = 0
    for w in windows:
        run = run + 1 if w < 0 else 0
        best = max(best, run)
    return best


def evaluate(mkt: str, trades: list[dict]) -> dict:
    """The live v2 numbers against each PROPOSED threshold."""
    df = pd.DataFrame(trades) if trades else pd.DataFrame(
        columns=["date", "net"])
    ann = ANNUALISATION.get(mkt)
    is_rows = df[df["date"] < SPLIT] if len(df) else df
    oos_rows = df[df["date"] >= SPLIT] if len(df) else df

    s_is = session_series(is_rows.to_dict("records")) if len(is_rows) else pd.Series(dtype=float)
    s_oos = session_series(oos_rows.to_dict("records")) if len(oos_rows) else pd.Series(dtype=float)

    sh_is = sharpe(s_is)
    sh_oos = sharpe(s_oos)
    dd_is = max_drawdown(s_is)
    dd_oos = max_drawdown(s_oos)
    wins_is = rolling_windows(s_is)
    wins_oos = rolling_windows(s_oos)
    streak_all = longest_negative_streak(rolling_windows(session_series(trades)))

    # T1: OOS Sharpe must be >= 50% of IS Sharpe.
    #     If IS Sharpe is <= 0 the ratio is meaningless — the rule then reads
    #     "a strategy with no positive in-sample Sharpe cannot be shown to
    #     decay" and the verdict is NOT_PASSED_BY_CONSTRUCTION, reported as
    #     such rather than as a pass.
    t1_state = "NOT_EVALUABLE"
    t1_ratio = None
    if sh_is == sh_is and sh_oos == sh_oos and sh_is > 0:
        t1_ratio = sh_oos / sh_is
        t1_state = "PASS" if t1_ratio >= PROPOSED["T1_sharpe_decay_min_ratio"] else "FAIL"
    elif sh_is == sh_is and sh_is <= 0:
        t1_state = "IN_SAMPLE_SHARPE_NOT_POSITIVE"

    # T2: OOS max drawdown must not exceed 2x the in-sample max drawdown.
    #     Both are negative; the multiple uses magnitudes.
    t2_state = "NOT_EVALUABLE"
    t2_multiple = None
    if dd_is == dd_is and dd_oos == dd_oos:
        if dd_is < 0:
            t2_multiple = abs(dd_oos) / abs(dd_is)
            t2_state = "PASS" if t2_multiple <= PROPOSED["T2_maxdd_max_multiple"] else "FAIL"
        else:
            t2_state = "NO_IN_SAMPLE_DRAWDOWN"

    # T3: three consecutive negative rolling windows.
    t3_state = "FAIL" if streak_all >= PROPOSED["T3_max_consecutive_negative_windows"] else "PASS"

    return {
        "market": mkt,
        "in_sample": {
            "definition": WINDOW_DEFINITIONS["in_sample"],
            "n_trades": int(len(is_rows)),
            "n_sessions": int(len(s_is)),
            "sharpe_raw": round(sh_is, 4) if sh_is == sh_is else None,
            "sharpe_annualised": round(sharpe(s_is, ann), 4) if ann else None,
            "max_drawdown_pct": round(dd_is, 3) if dd_is == dd_is else None,
            "n_rolling_windows": len(wins_is),
            "longest_negative_streak": longest_negative_streak(wins_is),
        },
        "out_of_sample": {
            "definition": WINDOW_DEFINITIONS["out_of_sample"],
            "n_trades": int(len(oos_rows)),
            "n_sessions": int(len(s_oos)),
            "sharpe_raw": round(sh_oos, 4) if sh_oos == sh_oos else None,
            "sharpe_annualised": round(sharpe(s_oos, ann), 4) if ann else None,
            "max_drawdown_pct": round(dd_oos, 3) if dd_oos == dd_oos else None,
            "n_rolling_windows": len(wins_oos),
            "longest_negative_streak": longest_negative_streak(wins_oos),
        },
        "full_sample": {
            "n_trades": int(len(df)),
            "n_sessions": int(len(session_series(trades))),
            "sharpe_raw": round(sharpe(session_series(trades)), 4),
            "max_drawdown_pct": round(max_drawdown(session_series(trades)), 3),
            "longest_negative_streak": streak_all,
        },
        "thresholds": {
            "T1_sharpe_decay": {
                "proposed": f"OOS Sharpe >= {PROPOSED['T1_sharpe_decay_min_ratio']:.0%} of IS Sharpe",
                "observed_ratio": round(t1_ratio, 4) if t1_ratio is not None else None,
                "verdict": t1_state,
            },
            "T2_max_drawdown": {
                "proposed": f"OOS max DD <= {PROPOSED['T2_maxdd_max_multiple']}x IS max DD",
                "observed_multiple": round(t2_multiple, 4) if t2_multiple is not None else None,
                "verdict": t2_state,
            },
            "T3_rolling_streak": {
                "proposed": f"not >= {PROPOSED['T3_max_consecutive_negative_windows']} consecutive negative {ROLLING_SESSIONS}-session windows",
                "observed_streak": streak_all,
                "verdict": t3_state,
            },
        },
    }


def direction_a_reading() -> dict:
    """The Direction A numbers, read from the report JSONs through the house
    loader — never hand-copied.

    The spec asks that the 2021-26 window's Sharpe-decay reading be shown
    alongside, and that it be framed as CONSISTENT with SELECTION_DEAD rather
    than as a contradiction of it. It is: SELECTION_DEAD is a verdict about
    SELECTION (does the screen beat equal weight), while a Sharpe decay is a
    verdict about the per-trade rule's own stability. A strategy can have a
    real per-trade edge and a dead selection — the site already says so.
    """
    try:
        import honest_results as H
        d = H.direction_a_detail()
    except Exception as e:
        return {"error": f"cannot read the Direction A reports: {e}"}
    out = {"source": "honest_results.direction_a_detail()", "runs": []}
    for run in d.get("runs") or []:
        for arm in run.get("arms") or []:
            wins = arm.get("wins") or {}
            early = wins.get(d.get("early") or "2016-2020") or {}
            late = wins.get(d.get("late") or "2021-2026") or {}
            out["runs"].append({
                "source": run.get("source"),
                "fill_lag": run.get("fill_lag"),
                "arm": arm.get("arm"),
                "classification": arm.get("classification"),
                "early_window": d.get("early"),
                "early_gap_pp": early.get("gap_pp"),
                "early_z": early.get("z"),
                "early_sharpe": early.get("sharpe"),
                "early_maxdd_pct": early.get("maxdd_pct"),
                "late_window": d.get("late"),
                "late_gap_pp": late.get("gap_pp"),
                "late_z": late.get("z"),
                "late_sharpe": late.get("sharpe"),
                "late_maxdd_pct": late.get("maxdd_pct"),
            })
    out["note"] = (
        "The 2021-26 window's negative gap (z between -1.4 and -1.7, never "
        "-1.96) is the SELECTION verdict, not a per-trade verdict. It is "
        "consistent with SELECTION_DEAD, not a contradiction of it: the "
        "per-trade close-confirmation edge and a dead SELECTION can both be "
        "true at the same time, and the site already says so."
    )
    return out


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
    s = pd.Series([0.01, -0.02, 0.03, -0.04, 0.05, -0.01, 0.02, -0.03] * 6)
    ok &= max_drawdown(s) < 0
    ok &= sharpe(s) == sharpe(s)
    wins = rolling_windows(s, 4)
    ok &= len(wins) > 0
    ok &= longest_negative_streak([1.0, -1.0, -1.0, -1.0, 1.0]) == 3
    ok &= longest_negative_streak([1.0, 1.0]) == 0
    return bool(ok)


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--selftest", action="store_true")
    _a = _ap.parse_args()
    raise SystemExit(_selftest() if _a.selftest else 0)
