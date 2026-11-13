#!/usr/bin/env python3
"""Part B — how much of the published v2 signal set is ALREADY EXTENDED.

RESEARCH ONLY. Nothing here is imported by a production build path.

THE QUESTION
    The site publishes long v2 signals. Part A asks whether a SHORT side
    should be added. This module asks the cheaper question: of the signals
    v2 ALREADY publishes, how many are "already at the top" and should not
    be chased at all? If a label can be attached to an existing signal and
    it separates good trades from bad ones, that is far cheaper than a new
    strategy — no new claim, no new position, no new book.

WHAT IS MEASURED
    For every published v2 signal, four extension features at the signal's
    own decision bar:

      atr_pct        core.features atr_pct — the v2 engine's own volatility
                     number, T-1 by construction
      box_w          core.features box_w — the 20-session box width
      dist_to_r1_pct (R1 - T-1 close) / T-1 close, in percent. How far the
                     price still has to climb to reach the top of the box.
                     NEGATIVE means the close is already above R1.
      dist_to_s1_pct (T-1 close - S1) / S1, in percent. How far the entry
                     level already is below the price.
      dist_to_52w_high_pct (T-1 close) / (250-session rolling max of close,
                     shifted one) - 1, in percent. How far the price is
                     below its own 52-week high. The 52-week high is NOT a
                     core.features output, so it is computed here, T-1.

    Every one of these is known at the moment v2 publishes the signal
    (the T-1 close), because core.features is shifted by one. Nothing here
    looks ahead.

THE THRESHOLDS ARE PROPOSALS, NOT DECISIONS
    Three families, three levels each, nine cells in all. Every cell is
    reported, including the ones that do not work. Kenneth picks the
    number; this module does not pick it for him. The numbers are written
    down BEFORE the results are read, which is the only way a threshold
    family that was chosen after seeing the data can be told apart from
    one that was not.

      T1  the close is within X% of the top of the box (R1)
      T2  the close is within Y% of its own 52-week high
      T3  ATR% is above Z — the move is already wide

THE QUESTION THAT MATTERS MOST
    For each threshold: are the triggered signals WORSE than the ones that
    did not trigger? A threshold that fires on 3% of the signals and does
    not separate them is decoration. A threshold that fires often AND
    separates them is worth a label.

Usage:
    python3 scripts/research/extended_reach.py --selftest
    python3 scripts/research/run_short_side.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

from core import features            # noqa: E402
import short_side as SS               # noqa: E402

MARKETS = SS.MARKETS
SPLIT = SS.SPLIT

# The nine candidate thresholds. Written down before any result is read.
# (family, label, feature, operator, level)
THRESHOLDS = [
    ("T1_close_near_box_top", "T1a_r1_within_2pct",  "dist_to_r1_pct",       "<",  2.0),
    ("T1_close_near_box_top", "T1b_r1_within_5pct",  "dist_to_r1_pct",       "<",  5.0),
    ("T1_close_near_box_top", "T1c_r1_within_10pct", "dist_to_r1_pct",       "<", 10.0),
    ("T2_close_near_52w_high", "T2a_at_52w_high",    "dist_to_52w_high_pct", ">",  0.0),
    ("T2_close_near_52w_high", "T2b_within_1pct",    "dist_to_52w_high_pct", ">", -1.0),
    ("T2_close_near_52w_high", "T2c_within_3pct",    "dist_to_52w_high_pct", ">", -3.0),
    ("T3_high_atr",          "T3a_atr_above_3pct",   "atr_pct_pct",          ">",  3.0),
    ("T3_high_atr",          "T3b_atr_above_4pct",   "atr_pct_pct",          ">",  4.0),
    ("T3_high_atr",          "T3c_atr_above_5pct",   "atr_pct_pct",          ">",  5.0),
]


def extension_features(df: pd.DataFrame) -> pd.DataFrame:
    """The extension features, on the same rows core.features uses.

    The 52-week high is a 250-session rolling max of the CLOSE, shifted by
    one so it never sees the entry session's own close.
    """
    f = features(df)
    c = df["close"].astype(float)
    high52 = c.rolling(250).max().shift(1)
    out = pd.DataFrame(index=df.index)
    out["atr_pct_pct"] = f["atr_pct"] * 100.0
    out["box_w_pct"] = f["box_w"] * 100.0
    prev = f["prev_close"]
    out["dist_to_r1_pct"] = (f["R1"] / prev - 1.0) * 100.0
    out["dist_to_s1_pct"] = (prev / f["S1"] - 1.0) * 100.0
    out["dist_to_52w_high_pct"] = (prev / high52 - 1.0) * 100.0
    out["high52"] = high52
    out["prev_close"] = prev
    return out


def attach_features(long_rows: list[dict]) -> list[dict]:
    """Join the extension features onto every published v2 signal."""
    out = []
    by_sym: dict[str, list[dict]] = {}
    for r in long_rows:
        by_sym.setdefault(r["sym"], []).append(r)
    for sym, rlist in by_sym.items():
        df = SS.load_bars(SS.BARS / f"{sym}.json")
        if df is None:
            continue
        ex = extension_features(df)
        for r in rlist:
            i = r["entry_i"]
            if i < 0 or i >= len(ex):
                continue
            row = dict(r)
            for k in ("atr_pct_pct", "box_w_pct", "dist_to_r1_pct",
                      "dist_to_s1_pct", "dist_to_52w_high_pct"):
                v = float(ex[k].iloc[i])
                row[k] = v if v == v else None
            out.append(row)
    return out


def _hits(feature: str, op: str, level: float, value) -> bool | None:
    if value is None or (isinstance(value, float) and value != value):
        return None
    return (value < level) if op == "<" else (value > level)


def evaluate(rows: list[dict]) -> list[dict]:
    """One row per (threshold, market, window) with the triggered and the
    not-triggered cohort measured side by side."""
    out = []
    for family, label, feature, op, level in THRESHOLDS:
        for mkt in list(MARKETS) + ["all"]:
            for win in ("train", "test", "full"):
                if mkt == "all":
                    sub = [r for r in rows]
                else:
                    sub = [r for r in rows if r["mkt"] == mkt]
                if win != "full":
                    sub = [r for r in sub if SS.window_of(r["date"]) == win]
                trig, no, unknown = [], [], 0
                for r in sub:
                    h = _hits(feature, op, level, r.get(feature))
                    if h is None:
                        unknown += 1
                        continue
                    (trig if h else no).append(r)
                rec = {
                    "family": family, "threshold": label, "feature": feature,
                    "op": op, "level": level, "market": mkt, "window": win,
                    "n_total": len(trig) + len(no), "n_unknown": unknown,
                    "trigger_rate_pct": round(len(trig) / max(1, len(trig) + len(no)) * 100, 2),
                }
                for name, grp in (("triggered", trig), ("not_triggered", no)):
                    st = SS.stats_for(grp)
                    rec[f"{name}_n"] = st["n"]
                    rec[f"{name}_avg%"] = st["avg%"]
                    rec[f"{name}_win%"] = st["win%"]
                    rec[f"{name}_avg_fwd10%"] = st["avg_fwd10%"]
                    rec[f"{name}_t"] = st["t"]
                rec["delta_avg_pp"] = round(rec["triggered_avg%"] - rec["not_triggered_avg%"], 3)
                rec["delta_win_pp"] = round(rec["triggered_win%"] - rec["not_triggered_win%"], 1)
                rec["delta_fwd10_pp"] = round(rec["triggered_avg_fwd10%"]
                                              - rec["not_triggered_avg_fwd10%"], 3)
                # what the v2 book would look like if the flagged cohort were dropped
                kept = SS.stats_for(no)
                rec["kept_avg%"] = kept["avg%"]
                rec["kept_win%"] = kept["win%"]
                rec["kept_n"] = kept["n"]
                out.append(rec)
    return out


def _selftest() -> int:
    raise SystemExit(0 if _selftest_body() else 1)


def _selftest_body() -> bool:
    ok = True

    # 1. the 52-week high must be the rolling max of the CLOSE, shifted one
    n = 400
    idx = pd.bdate_range("2020-01-01", periods=n)
    c = np.linspace(100.0, 200.0, n)
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c,
                       "volume": 1e6}, index=idx)
    ex = extension_features(df)
    i = 300
    want = df["close"].iloc[:i].rolling(250).max().iloc[-1]
    got = float(ex["high52"].iloc[i])
    good = abs(got - want) < 1e-9
    print(f"  {'PASS' if good else 'FAIL'}  the 52-week high is the shifted "
          f"250-session close max ({got:.4f} vs {want:.4f})")
    ok &= good

    # 2. the extension features must not see the entry session's own close
    mutated = df.copy()
    mutated.iloc[i, mutated.columns.get_loc("close")] = 9999.0
    mutated.iloc[i, mutated.columns.get_loc("high")] = 9999.0
    ex2 = extension_features(mutated)
    same = all(abs(float(ex[k].iloc[i]) - float(ex2[k].iloc[i])) < 1e-9
               for k in ("atr_pct_pct", "dist_to_r1_pct", "dist_to_s1_pct",
                         "dist_to_52w_high_pct"))
    print(f"  {'PASS' if same else 'FAIL'}  the extension features do not move "
          f"when the entry session's close is mutated")
    ok &= same

    # 3. the threshold comparison operators
    good = (_hits("x", "<", 5.0, 4.9) and not _hits("x", "<", 5.0, 5.1)
            and _hits("x", ">", -1.0, -0.5) and not _hits("x", ">", -1.0, -1.5)
            and _hits("x", "<", 5.0, None) is None)
    print(f"  {'PASS' if good else 'FAIL'}  the threshold operators behave")
    ok &= good

    # 4. there are nine candidate thresholds and they are all declared
    good = len(THRESHOLDS) == 9 and len({t[1] for t in THRESHOLDS}) == 9
    print(f"  {'PASS' if good else 'FAIL'}  nine candidate thresholds are "
          f"declared")
    ok &= good

    return bool(ok)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        print("=" * 72)
        print("EXTENDED-REACH SELF-TEST")
        print("=" * 72)
        raise SystemExit(_selftest())
    print("extended_reach.py is an engine. Run scripts/research/run_short_side.py "
          "for the full report, or --selftest for the checks.")
    raise SystemExit(0)
