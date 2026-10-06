"""ab_close_hold.py — A/B test: does requiring a close-hold above S1 improve v2?

THE QUESTION
    2026-10-01, a peer agent answered this directly on the EigenFlux network
    to Kenneth's own ASK 1: for LONG entries at support, a touch that closes
    below the level means the level failed — you caught the break, you did not
    buy the level. Requiring a close-hold above S1 removed 26% of their trades
    and those removed trades averaged -3.90% each (their t went +0.39 -> +7.68).

    v2 currently does NOT do this. It only requires the session to OPEN above
    S1. So the rule that is live is a different rule from the one the finding
    recommends, and the published v2 statistics were computed without it.

    This measures it on OUR bars, in OUR engine, with the same fill rules.
    If it is a real improvement it must show up on both the train window
    (2016-2022) and the held-out test window (2023-2026). A change that only
    helps on one side is a window artefact, not an edge.

DATA
    data/bt10y/*.json — 10y daily bars fetched from Yahoo (fetch_10y.py).
    HK is absent from this store (the provider does not serve .HK single
    names), so this can only be run on US and JP. That is stated in the
    output rather than silently omitted.

METHOD NOTE
    Both variants use the same stop, the same exit, the same costs. The ONLY
    difference is the entry qualification:

      A  open-hold   (shipped v2)  : session opens above S1
      B  close-hold  (the finding)  : opens above S1 AND closes at/above S1

    "Closes below S1 after touching it" is the case under test: in variant A
    that trade is taken, in variant B it is rejected.

Usage:
    python3 scripts/ab_close_hold.py
    python3 scripts/ab_close_hold.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
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
               mask: set | None = None) -> pd.DataFrame | None:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
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

    if hk:
        print(f"\nNOTE: HK cannot be tested — {len(hk)} HK bar file(s) in data/bt10y "
              f"(the provider does not serve .HK single names).")

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

    if a.json:
        print("\n" + json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
