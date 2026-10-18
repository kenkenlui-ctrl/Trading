"""ab_close_hold.py — A/B test: does requiring a close-hold above S1 improve v2?

THE QUESTION
    2026-10-01, a peer agent answered this directly on the EigenFlux network
    to Kenneth's own ASK 1: for LONG entries at support, a touch that closes
    below the level means the level failed — you caught the break, you did not
    buy the level. Requiring a close-hold above S1 removed 26% of their trades
    and those removed trades averaged -3.90% each (their t went +0.39 -> +7.68).

    CORRECTION (2026-10-18). This docstring used to read "v2 currently does NOT
    do this. It only requires the session to OPEN above S1." That was already
    false when it was written: build_v2_signals.py has shipped CLOSE_HOLD = True
    since 2026-10-05. So arm A is NOT "shipped v2" — it is the weaker
    pre-2026-10-05 rule, and the question this file answers is the one that
    actually matters for the live rule: does the close-hold requirement earn
    the half of its trades it throws away?

    It measures that on OUR bars, in OUR engine, with the same fill rules. If
    it is a real improvement it must show up on both the train window
    (2016-2022) and the held-out test window (2023-2026). A change that only
    helps on one side is a window artefact, not an edge.

    A bare "B beats A" would not settle it, because B keeps only about half of
    A's trades. Two readings survive that observation — the close carries
    information, or the rule simply trades less — and only a placebo control
    separates them. That is why --placebo exists and why the thresholds were
    written down in docs/close-confirmation-prereg-2026-10-18.md BEFORE the
    numbers were looked at. Without a pre-registered control, a ~1pp/trade gap
    is exactly what "traded less, so averaged better" looks like.

DATA
    bars_dir() — currently data/bt10y2, 10y daily bars fetched from Yahoo
    (407 symbols; 8 cached files were corrupt in the earlier data/bt10y store
    and the set was re-fetched one at a time). Bars whose [entry-210d, exit]
    window touches a known data glitch are excluded; see bt_clean.py.
    HK is absent from this store (the provider does not serve .HK single
    names), so this can only be run on US and JP. That is stated in the
    output rather than silently omitted.

METHOD NOTE
    Both variants use the same stop, the same exit, the same costs, the same
    10-session hold. The ONLY difference is the entry qualification:

      A  open-hold   (pre-2026-10-05 v2)  : session opens above S1
      B  close-hold  (the shipped rule)    : opens above S1 AND closes
                                             at/above S1

    "Closes below S1 after touching it" is the case under test: in variant A
    that trade is taken, in variant B it is rejected.

WHAT IS WRITTEN WHERE
    data/ab_close_hold.csv  — per-window summary table
    data/ab_close_hold.json — the single source both consumers read:
                              build_v2_signals.py::_oos() and
                              rewrite_methodology_v2.py. Do not hand-copy a
                              number out of stdout into either of them; a
                              hand-typed copy already drifted once.

Usage:
    python3 scripts/ab_close_hold.py
    python3 scripts/ab_close_hold.py --placebo 300 --seed 20261018
    python3 scripts/ab_close_hold.py --json

    --placebo 0 (the default) SKIPS the controls entirely and writes
    "placebo": null. A run without the placebo cannot support the shipped
    rule's rationale, only the raw A/B delta.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
sys.path.insert(0, str(REPO / "scripts"))
from core import features, _simulate, cost_rt  # noqa: E402
from bt_clean import bars_dir, load as load_clean, _load_mask_cache  # noqa: E402

BARS = bars_dir()
OUT = REPO / "data" / "ab_close_hold.csv"
OUT_JSON = REPO / "data" / "ab_close_hold.json"
SPLIT = pd.Timestamp("2023-01-01")     # train < SPLIT <= test
HOLD = 10
MARKETS = {"us200": "SPY", "jp200": "1306_T"}


def load(path: Path) -> pd.DataFrame | None:
    return load_clean(path)


def market_up(mkt: str) -> bool | None:
    """Market filter: index T-1 close above its own 200d MA."""
    for sym in (MARKETS[mkt], "^GSPC" if mkt == "us200" else "^N225"):
        p = BARS / f"{sym}.json"
        if not p.exists():
            continue
        b = load(p)
        if b is None or len(b) < 260:
            continue
        f = features(b)
        last = f.dropna(subset=["ma200"]).iloc[-1]
        return bool(last.prev_close > last.ma200)
    return None


def trades_for(df: pd.DataFrame, mkt: str, close_hold: bool,
               mask: set | None = None,
               allow: set | None = None) -> pd.DataFrame | None:
    """mask = the glitch dates for THIS symbol. A trade is dropped when any masked
    bar sits in [entry - 210 calendar-ish days, exit], because ma200/S1/atr are
    built from shifted closes and one bad bar poisons the features behind the
    entry, not just the exit."""
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
    rows = []
    for i in np.where(ok)[0]:
        if np.isnan(s1[i]) or np.isnan(a[i]) or a[i] <= 0:
            continue
        # v2 entry: limit buy at S1, valid next session, only if it opens above S1
        if allow is not None and i not in allow:
            continue                      # placebo: restrict eligible sessions
        if not (O[i] > s1[i] and L[i] <= s1[i]):
            continue
        if close_hold and C[i] < s1[i]:
            continue                      # the trade under test
        stop = s1[i] * (1 - 3 * a[i])
        if stop <= 0 or stop >= s1[i]:
            continue
        res = _simulate(1, i, s1[i], stop, 1e12, O, H, L, C, HOLD, True)
        if res is None:
            continue
        j, xp, why = res
        if mask:
            lo = idx[i] - pd.Timedelta(days=304)
            if any(lo <= d <= idx[j] for d in mask):
                continue
        rows.append((mkt, idx[i], xp / s1[i] - 1, why))
    return pd.DataFrame(rows, columns=["mkt", "date", "gross", "reason"]) if rows else None


def stats(df: pd.DataFrame) -> dict:
    if df is None or len(df) == 0:
        return {"n": 0, "win%": 0.0, "avg%": 0.0, "pf": 0.0, "t": 0.0}
    net = df.net.to_numpy(float)
    wins, losses = net[net > 0], net[net <= 0]
    # date-clustered t: group by session so simultaneous entries don't
    # masquerade as independent samples
    by_date = df.groupby("date").net.mean()
    t = 0.0
    if len(by_date) > 2 and by_date.std(ddof=1) > 0:
        t = by_date.mean() / (by_date.std(ddof=1) / np.sqrt(len(by_date)))
    pf = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else 0.0
    return {"n": len(net), "win%": round(float((net > 0).mean() * 100), 1),
            "avg%": round(float(net.mean() * 100), 3), "pf": round(float(pf), 2),
            "t": round(float(t), 2)}


# --------------------------------------------------------------------------
# Placebo controls — docs/close-confirmation-prereg-2026-10-18.md
#
# B keeps only ~51% of A's trades, so "B beats A" has two live explanations:
# the close carries information (H1) or the rule simply trades less (H2).
# Nothing in this file distinguished them until now.
#
# P2 (primary): draw a size-matched random subset of A. Universe, market
# filter, entry level, stop, exit, costs and sample size are all held
# constant; only the SELECTION rule differs. If H2 were true, B lands inside
# this null's spread.
#
# P1 (secondary): move each signal to a different session, drawn from the same
# symbol's full candidate pool. This changes the bar->signal pairing, which is
# what makes a placebo able to fail. (Sprint 1's first placebo used a
# permutation over bar indices, which reordered a set without changing any
# pairing and returned sd = 0.00 — a guardrail that passed while testing
# nothing.)
Z_CRIT = 1.96


def _mean_of(df: pd.DataFrame, n: int, rng: np.random.Generator) -> float:
    if df is None or len(df) == 0 or n <= 0:
        return float("nan")
    k = min(n, len(df))
    idx = rng.choice(len(df), size=k, replace=False)
    return float(df.net.to_numpy(float)[idx].mean() * 100)


def candidates_for(df: pd.DataFrame) -> np.ndarray:
    """Indices that pass the full screen and the opens-above-S1 test.

    This is the pool P1 draws from, so a placebo trial still runs the real
    rule against a real session — just not the session the signal chose."""
    f = features(df)
    O, L = df["open"].to_numpy(float), df["low"].to_numpy(float)
    s1 = f.S1.to_numpy()
    a = f.atr_pct.to_numpy()
    ma200 = f.ma200.to_numpy()
    slope = f.ma200_slope.to_numpy()
    boxw = f.box_w.to_numpy()
    ok = ((f.prev_close.to_numpy() > ma200) & (slope > 0) &
          (a < 0.06) & (boxw < 0.35))
    out = []
    for i in np.where(ok)[0]:
        if np.isnan(s1[i]) or np.isnan(a[i]) or a[i] <= 0:
            continue
        if O[i] > s1[i] and L[i] <= s1[i]:
            out.append(int(i))
    return np.array(out, dtype=int)


def p2_matched_count(a_df: pd.DataFrame, b_n: int, trials: int,
                     seed: int) -> tuple[list[float], float, float]:
    """Size-matched random subsets of arm A. Returns (means, mean, sd)."""
    if a_df is None or not len(a_df) or b_n <= 0:
        return [], float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = [_mean_of(a_df, b_n, rng) for _ in range(trials)]
    return means, float(np.mean(means)), float(np.std(means, ddof=1))


def p1_shuffled_sessions(bars: dict, mkt: str, n_b: int, trials: int,
                         seed: int, mask_all: dict) -> tuple[list[float], float, float]:
    """Move signals to different sessions, drawn from the same symbol's pool.

    `bars` maps symbol -> (DataFrame, candidate index array). Only sessions
    that pass the full screen AND the opens-above-S1 test are eligible, so the
    placebo trades a real rule at a real time of day, only not the chosen one.
    """
    if not bars or n_b <= 0:
        return [], float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    per_trial = max(1, n_b // max(1, len(bars)))
    means = []
    for t in range(trials):
        picks = []
        for sym, (df, cand) in bars.items():
            if len(cand) == 0:
                continue
            allow = set(rng.choice(cand, size=min(per_trial, len(cand)),
                                   replace=False).tolist())
            tr = trades_for(df, mkt, True, mask_all.get(sym), allow=allow)
            if tr is not None:
                picks.append(tr)
        if not picks:
            continue
        d = pd.concat(picks, ignore_index=True)
        d["net"] = d.gross - cost_rt(mkt, "site", d.reason.eq("stop").to_numpy())
        means.append(float(d.net.mean() * 100) if len(d) else float("nan"))
    means = [m for m in means if m == m]
    if len(means) < 2:
        return means, float("nan"), float("nan")
    return means, float(np.mean(means)), float(np.std(means, ddof=1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--placebo", type=int, default=0,
                    help="trials per control; 0 skips the placebo entirely")
    ap.add_argument("--seed", type=int, default=20261018)
    a = ap.parse_args()

    frames = {}
    mask_all = _load_mask_cache()
    print(f"bar source: {BARS}")
    print(f"glitch-masked symbols: {len(mask_all)}  masked bars: {sum(len(v) for v in mask_all.values())}")
    for mkt in MARKETS:
        up = market_up(mkt)
        if up is not True:
            print(f"  {mkt}: market filter currently not 'up' — reporting both windows anyway")
        for variant, ch in (("A_open-hold", False), ("B_close-hold", True)):
            parts = []
            for p in sorted(BARS.glob("*.json")):
                s = p.stem
                is_jp = s.endswith("_T")
                is_us = not is_jp and "_" not in s and not s.startswith(("HK_", "JP_"))
                if mkt == "jp200" and not is_jp:
                    continue
                if mkt == "us200" and not is_us:
                    continue
                b = load(p)
                if b is None:
                    continue
                t = trades_for(b, mkt, ch, mask_all.get(s))
                if t is not None:
                    t["sym"] = s
                    parts.append(t)
            if not parts:
                continue
            d = pd.concat(parts, ignore_index=True)
            d["net"] = d.gross - cost_rt(mkt, "site", d.reason.eq("stop").to_numpy())
            frames[(mkt, variant)] = d

    hk = [p for p in sorted(BARS.glob("*.json")) if p.stem.startswith(("HK_",)) ]
    report = {"windows": {}, "note": ""}
    rows = []
    for mkt in MARKETS:
        for variant in ("A_open-hold", "B_close-hold"):
            d = frames.get((mkt, variant))
            if d is None:
                continue
            tr = d[d.date < SPLIT]
            te = d[d.date >= SPLIT]
            s_tr, s_te = stats(tr), stats(te)
            report["windows"].setdefault(mkt, {})[variant] = {"train": s_tr, "test": s_te}
            rows.append({"market": mkt, "variant": variant,
                         "train_n": s_tr["n"], "train_avg%": s_tr["avg%"],
                         "train_win%": s_tr["win%"], "train_t": s_tr["t"],
                         "test_n": s_te["n"], "test_avg%": s_te["avg%"],
                         "test_win%": s_te["win%"], "test_t": s_te["t"]})
            print(f"\n{mkt}  {variant}")
            print(f"  train 2016-22  n={s_tr['n']:>6}  avg={s_tr['avg%']:+.3f}%  "
                  f"win={s_tr['win%']:.1f}%  PF={s_tr['pf']:.2f}  t={s_tr['t']:+.2f}")
            print(f"  test  2023-26  n={s_te['n']:>6}  avg={s_te['avg%']:+.3f}%  "
                  f"win={s_te['win%']:.1f}%  PF={s_te['pf']:.2f}  t={s_te['t']:+.2f}")

    # ---------------- placebo controls (pre-registered) ----------------
    placebo_report = None
    if a.placebo and a.placebo > 0:
        print("\n" + "=" * 72)
        print(f"PLACEBO CONTROLS — {a.placebo} trials/cell, seed {a.seed}")
        print("Pre-registered in docs/close-confirmation-prereg-2026-10-18.md.")
        print("P2 primary: size-matched random subset of arm A.")
        print("Power check first: a control with sd = 0.00 tests nothing.")
        placebo_report = {"trials": a.placebo, "seed": a.seed, "cells": {},
                          "z_crit": Z_CRIT}
        for mkt in MARKETS:
            A_df, B_df = frames.get((mkt, "A_open-hold")), frames.get((mkt, "B_close-hold"))
            if A_df is None or B_df is None:
                continue
            placebo_report["cells"][mkt] = {}
            for win, mask_df in (("train", lambda d: d.date < SPLIT),
                                 ("test", lambda d: d.date >= SPLIT)):
                a_w, b_w = A_df[mask_df(A_df)], B_df[mask_df(B_df)]
                if not len(a_w) or not len(b_w):
                    continue
                b_mean = float(b_w.net.mean() * 100)
                means, mu, sd = p2_matched_count(a_w, len(b_w), a.placebo,
                                                 a.seed + len(mkt) + len(win))
                z = (b_mean - mu) / sd if sd and sd == sd and sd > 0 else float("nan")
                placebo_report["cells"][mkt][win] = {
                    "B_avg%": round(b_mean, 3), "B_n": int(len(b_w)),
                    "P2_null_mean%": round(mu, 3) if mu == mu else None,
                    "P2_null_sd%": round(sd, 4) if sd == sd else None,
                    "P2_trials": len(means),
                    "gap_vs_null_pp": round(b_mean - mu, 3) if mu == mu else None,
                    "z": round(z, 3) if z == z else None,
                    "passes": bool(z == z and z >= Z_CRIT and b_mean - mu > 0),
                    "has_power": bool(sd == sd and sd > 0),
                }
                c = placebo_report["cells"][mkt][win]
                print(f"\n{mkt} {win}")
                print(f"  B            n={c['B_n']:>6}  avg={b_mean:+.3f}%")
                print(f"  P2 null      n={len(b_w):>6}  avg={c['P2_null_mean%']:+.3f}%  "
                      f"sd={c['P2_null_sd%']}  ({len(means)} trials)")
                print(f"  gap          {c['gap_vs_null_pp']:+.3f}pp   z={c['z']}   "
                      f"-> {'PASS' if c['passes'] else 'FAIL'}"
                      + ("" if c["has_power"] else "   [NO POWER — sd=0, test void]"))
        all_cells = [c for m in placebo_report["cells"].values()
                     for c in m.values()] if placebo_report["cells"] else []
        n_pass = sum(1 for c in all_cells if c["passes"])
        powered = all(c["has_power"] for c in all_cells) if all_cells else False
        verdict = ("PASS" if powered and all_cells and n_pass == len(all_cells)
                   else "VOID (a control had no power)" if all_cells and not powered
                   else "MIXED" if n_pass else "FAIL")
        placebo_report["verdict"] = verdict
        placebo_report["cells_passing"] = f"{n_pass}/{len(all_cells)}"
        print("\n" + "=" * 72)
        print(f"PRE-REGISTERED VERDICT: {verdict}  ({n_pass}/{len(all_cells)} cells pass, "
              f"z_crit {Z_CRIT}, all four required)")
        if verdict == "FAIL":
            print("H2 — the close-hold rule is 'trade less', not 'trade better'.")
        elif verdict == "PASS":
            print("H1 — the close carries information beyond sample size.")

    if hk:
        print(f"\nNOTE: HK cannot be tested — {len(hk)} HK bar file(s) in "
              f"{BARS} (the provider does not serve .HK single names).")

    # the actual comparison
    print("\n" + "=" * 72)
    print("DOES CLOSE-HOLD HELP?  (B minus A, same windows, same costs)")
    for mkt in MARKETS:
        w = report["windows"].get(mkt)
        if not w or "B_close-hold" not in w:
            continue
        A, B = w["A_open-hold"], w["B_close-hold"]
        for win in ("train", "test"):
            an, bn = A[win]["n"], B[win]["n"]
            if not an:
                continue
            d_avg = B[win]["avg%"] - A[win]["avg%"]
            d_t = B[win]["t"] - A[win]["t"]
            kept = bn / an * 100
            verdict = ("improves" if (d_avg > 0 and d_t > 0) else
                       "worse" if (d_avg < 0 and d_t < 0) else "mixed")
            print(f"  {mkt:6} {win:5}  kept {kept:5.1f}% of trades | "
                  f"avg {A[win]['avg%']:+.3f}% -> {B[win]['avg%']:+.3f}% ({d_avg:+.3f}) | "
                  f"t {A[win]['t']:+.2f} -> {B[win]['t']:+.2f} ({d_t:+.2f})  => {verdict}")

    if rows:
        pd.DataFrame(rows).to_csv(OUT, index=False)
        print(f"\nwrote {OUT}")

    # 2026-10-18: this script's numbers were being re-typed by hand into
    # build_v2_signals.py (V2_OOS) and rewrite_methodology_v2.py. The two
    # copies drifted — the methodology page shipped n=2,992 / 60.2% / 1.78%
    # where this script measures 2,982 / 60.3% / 1.772%, and the JP row was
    # wrong in every field including profit factor and t. The JSON payload was
    # only ever printed to stdout, so neither consumer could read it. It is
    # written to a file now, and both read it from here.
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "bars_dir": BARS.name,
        "split": SPLIT.strftime("%Y-%m-%d"),
        "hold_days": HOLD,
        "rule_set": "B_close-hold",
        "engine": "v2 (core.features + _simulate, cost_rt)",
        "windows": report["windows"],
        "placebo": placebo_report,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"wrote {OUT_JSON}")

    if a.json:
        print("\n" + json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
