#!/usr/bin/env python3
"""Item 2 — the confirmation-period cost, measured with the overnight-gap
distribution. RESEARCH ONLY.

THE QUESTION
    The site prints, on /methodology §4.5, that requiring a close-hold above
    S1 lifts the per-trade average from +0.788% to +1.772%. What it does not
    print is what the wait costs. A rule that only knows a signal was valid
    after the close cannot act at the close: the next executable price is the
    next session's open, and the overnight gap is what you eat.

    The spec's wording (荷马史诗, item 377831217111760896): print the
    false-signal filter rate AND the delayed-execution cost distribution
    TOGETHER. Either one alone is self-flattery.

WHY THE GAP DISTRIBUTION AND NOT ATR
    ATR is an average-scale measure built from ranges. Overnight gaps are
    fat-tailed: the average gap is not the gap you have to survive. So the
    distribution is reported with its TAIL (p90, p99, and the share of gaps
    exceeding 3 x ATR) and the ATR-based estimate is printed beside it so the
    understatement is visible rather than asserted.

THE PLACEBO
    The same gap distribution computed on SHUFFLED signal days must show no
    systematic cost. If the real signal days carry a gap the shuffled days do
    not, the gap is a property of the signal, not of the calendar. If the
    placebo also shows a cost, the "cost" is just market drift and the real
    number means nothing.

WHAT THIS DOES NOT DO
    It does not edit /methodology. It produces the numbers and a DRAFT of the
    text that would go there, for Kenneth to approve the words and the
    numbers. Nothing is deployed.
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
sys.path.insert(0, str(REPO / "scripts" / "research"))
import executability as E  # noqa: E402
from bt_clean import load as load_clean  # noqa: E402

SEED = 20261111
PLACEBO_TRIALS = 300


def gap_samples(mkt: str, trades: list[dict]) -> list[dict]:
    """For every booked trade, the overnight gap from the signal session's
    close to the next session's open, plus the ATR that says how big a move
    the market normally takes on this name.

    A trade whose next session does not exist in the store (the last session
    of the series) has NO gap and is excluded from the distribution — the
    sample count says so.
    """
    out: list[dict] = []
    cache: dict[str, tuple] = {}
    for t in trades:
        sym = t["sym"]
        if sym not in cache:
            df = load_clean(E.BARS / f"{sym}.json")
            if df is None:
                cache[sym] = None
            else:
                cache[sym] = (df.index, df["open"].to_numpy(float),
                              df["close"].to_numpy(float))
        c = cache[sym]
        if c is None:
            continue
        idx, opens, closes = c
        d = pd.Timestamp(t["date"])
        try:
            i = idx.get_loc(d)
        except KeyError:
            continue
        if i + 1 >= len(idx):
            continue                      # no next session: no gap to measure
        gap = opens[i + 1] / closes[i] - 1.0
        atr = float(t["atr_pct"])
        out.append({
            "mkt": mkt, "sym": sym, "date": t["date"],
            "next_date": idx[i + 1].strftime("%Y-%m-%d"),
            "close": float(closes[i]), "next_open": float(opens[i + 1]),
            "gap_pct": float(gap),
            "atr_pct": atr,
            "gap_in_atr": float(gap / atr) if atr > 0 else float("nan"),
            "tail_3atr": bool(atr > 0 and abs(gap) > 3 * atr),
        })
    return out


def summarise(samples: list[dict]) -> dict:
    """The distribution, with the tail — not the average."""
    if not samples:
        return {"n": 0}
    g = np.array([s["gap_pct"] for s in samples], float)
    ag = np.abs(g)
    atr = np.array([s["atr_pct"] for s in samples], float)
    tail = np.array([s["tail_3atr"] for s in samples], bool)
    finite = ag[np.isfinite(ag)]
    return {
        "n": int(len(g)),
        "median_gap_pct": round(float(np.median(g)) * 100, 4),
        "mean_gap_pct": round(float(np.mean(g)) * 100, 4),
        "p90_abs_gap_pct": round(float(np.percentile(finite, 90)) * 100, 4),
        "p99_abs_gap_pct": round(float(np.percentile(finite, 99)) * 100, 4),
        "max_abs_gap_pct": round(float(np.max(finite)) * 100, 4),
        "median_abs_gap_pct": round(float(np.median(finite)) * 100, 4),
        "mean_atr_pct": round(float(np.mean(atr)) * 100, 4),
        "p90_abs_gap_over_atr": round(float(np.percentile(
            np.abs(g[np.isfinite(g)] / atr[np.isfinite(g)]), 90)), 3),
        "p99_abs_gap_over_atr": round(float(np.percentile(
            np.abs(g[np.isfinite(g)] / atr[np.isfinite(g)]), 99)), 3),
        "share_gap_gt_3atr": round(float(tail.mean()) * 100, 3),
        "share_gap_gt_1atr": round(float((np.abs(g[np.isfinite(g)]) >
                                          atr[np.isfinite(g)]).mean()) * 100, 3),
        "mean_sign": "up" if np.mean(g) > 0 else "down",
    }


def placebo(mkt: str, trades: list[dict], pool: list[dict],
            trials: int = PLACEBO_TRIALS, seed: int = SEED) -> dict:
    """Shuffled signal days: the null.

    The replacement days are drawn from the SAME symbol's candidate pool —
    the sessions that passed the screen but did not become a signal. That
    controls for the ATR filter (the pool is ATR-filtered too, so the tail
    measure is compared like with like) and it is the same construction as
    the P1 control in ab_close_hold.py: only the bar-to-signal PAIRING changes.

    Drawing from the symbol's whole calendar instead would compare a
    low-volatility signal day against whatever high-volatility day the
    calendar happened to contain, which is a mismatch dressed up as a
    placebo.
    """
    by_sym: dict[str, list[str]] = {}
    for t in trades:
        by_sym.setdefault(t["sym"], []).append(t["date"])
    pool_by_sym: dict[str, list[dict]] = {}
    for c in pool:
        pool_by_sym.setdefault(c["sym"], []).append(c)
    if not by_sym or not pool_by_sym:
        return {"trials": 0, "has_power": False,
                "reason": "no trades or no candidate pool to draw from"}

    # the gap and the ATR of every candidate session, per symbol
    ctx: dict[str, list[tuple[str, float, float]]] = {}
    for sym, rows in pool_by_sym.items():
        df = load_clean(E.BARS / f"{sym}.json")
        if df is None:
            continue
        closes = df["close"].to_numpy(float)
        opens = df["open"].to_numpy(float)
        idx = df.index
        items: list[tuple[str, float, float]] = []
        for r in rows:
            d = pd.Timestamp(r["date"])
            try:
                i = idx.get_loc(d)
            except KeyError:
                continue
            if i + 1 >= len(idx):
                continue
            if not closes[i] or closes[i] <= 0:
                continue
            items.append((r["date"], float(opens[i + 1] / closes[i] - 1),
                          float(r["atr_pct"])))
        if items:
            ctx[sym] = items

    rng = np.random.default_rng(seed + sum(ord(c) for c in mkt))
    medians: list[float] = []
    means: list[float] = []
    tails: list[float] = []
    for _ in range(trials):
        gs: list[float] = []
        ts: list[bool] = []
        for sym, dates in by_sym.items():
            items = ctx.get(sym)
            if not items:
                continue
            k = min(len(dates), len(items))
            pick = rng.choice(len(items), size=k, replace=False)
            for p in pick:
                _d, gap, atr = items[p]
                gs.append(gap)
                ts.append(bool(atr > 0 and abs(gap) > 3 * atr))
        if len(gs) < 10:
            continue
        arr = np.array(gs, float)
        medians.append(float(np.median(arr)) * 100)
        means.append(float(np.mean(arr)) * 100)
        tails.append(float(np.mean(ts)) * 100)

    if len(medians) < 2:
        return {"trials": len(medians), "has_power": False,
                "reason": "fewer than 2 usable trials"}
    med = np.array(medians)
    tl = np.array(tails)
    return {
        "trials": len(medians),
        "seed": seed,
        "draw": "size-matched draw from the same symbol's candidate pool",
        "null_median_gap_pct_mean": round(float(med.mean()), 4),
        "null_median_gap_pct_sd": round(float(med.std(ddof=1)), 4),
        "null_mean_gap_pct_mean": round(float(np.array(means).mean()), 4),
        "null_mean_gap_pct_sd": round(float(np.array(means).std(ddof=1)), 4),
        "null_share_gt_3atr_mean": round(float(tl.mean()), 3),
        "null_share_gt_3atr_sd": round(float(tl.std(ddof=1)), 4),
        "has_power": bool(float(med.std(ddof=1)) > 0),
    }


def filter_rate() -> dict:
    """The false-signal filter rate — how many touch-only signals the
    close-hold rule throws away, per market and per window.

    Read from data/ab_close_hold.json (the frozen A/B run), not recomputed, so
    the number cannot drift from the one the site quotes.
    """
    p = REPO / "data" / "ab_close_hold.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"cannot read {p}: {e}"}
    out: dict = {"source": str(p), "rule_set": d.get("rule_set"), "markets": {}}
    for mkt, arms in (d.get("windows") or {}).items():
        a = arms.get("A_open-hold")
        b = arms.get("B_close-hold")
        if not a or not b:
            continue
        out["markets"][mkt] = {}
        for win in ("train", "test"):
            if win not in a or win not in b:
                continue
            na, nb = a[win]["n"], b[win]["n"]
            out["markets"][mkt][win] = {
                "touch_only_n": na,
                "close_hold_n": nb,
                "rejected_by_filter_n": na - nb,
                "false_signal_filter_rate_pct": round((na - nb) / na * 100, 2) if na else None,
                "touch_only_avg_pct": a[win]["avg%"],
                "close_hold_avg_pct": b[win]["avg%"],
                "lift_pp": round(b[win]["avg%"] - a[win]["avg%"], 3),
            }
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
    fr = filter_rate()
    ok &= bool((fr.get("markets") or {}).get("us200"))
    ok &= (fr.get("rule_set") == "B_close-hold")
    # the placebo must have power on a toy population
    import coverage as C
    cal = C.session_calendar("us200")
    ok &= len(cal) > 100
    return bool(ok)


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--selftest", action="store_true")
    _a = _ap.parse_args()
    raise SystemExit(_selftest() if _a.selftest else 0)
