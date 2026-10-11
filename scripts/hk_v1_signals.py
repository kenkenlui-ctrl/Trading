#!/usr/bin/env python3
"""
hk_v1_signals.py — regenerate data/hk_v1_signals_cache.json.

WHY THIS EXISTS
    The cache was produced by vol_regime_test.py --market hk200
    --rebuild-signals. That script was removed on 2026-10-11 together with the
    volatility study it belonged to, which left the 13 MB cache as the only copy
    of every HK v1 signal — and hk_v1_fill_realistic.py, the study that found
    the published HK v1 numbers were phantom fills, depends on it. A cache
    nobody can rebuild is a result nobody can check.

    So the generator is separated from the study. This file emits the records;
    nothing here computes, thresholds or reports volatility.

WHAT IS AND IS NOT COPIED
    Copied unchanged, so the output is byte-identical:
        hk_v1_records      the per-ticker worker (daily_sr loop)
        _fill_supply       penetration / dist_to_entry / touched_on_bar
        RECORD_VERSION     cache schema version, must match or the cache is stale
        HK_VOL_START       the window anchor, see below

    Deliberately NOT copied: load_vol, the regime split, the gate, the placebo,
    the page writer, and every vix / vix_config reference. Those were the study.

THE VOL_START ANCHOR IS NOT COSMETIC
    hk_v1_records cuts the frame at searchsorted(VOL_START) - 300 bars and walks
    forward from there. Change the anchor and the 300-bar lookback moves, so a
    different slice of each ticker's history is scanned and a different set of
    signals comes out. The value below is the one the retained cache was built
    with and is recorded in the cache as `_vol_start`; changing it does not
    invalidate the cache, it silently produces a different study.

EXTERNAL DEPENDENCY — daily_sr IS NOT IN THIS REPOSITORY
    The phase detection and action plan come from the daily-sr-chart skill at
    /Users/kenken/.minimax/skills/daily-sr-chart/scripts. That path is a local
    install, not a tracked file, so "the cache is regenerable" holds only where
    that skill is present. The import is inside the worker on purpose: a missing
    daily_sr raises out of ex.map and aborts the run rather than emitting a
    short, plausible-looking cache. If you relocate the skill, change the
    sys.path line above, not the worker.

USAGE
    python3 scripts/hk_v1_signals.py                 # write the real cache
    python3 scripts/hk_v1_signals.py --out /tmp/x.json --verify
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, "/Users/kenken/.minimax/skills/daily-sr-chart/scripts")

from bt_clean import load                                      # noqa: E402

DATA = REPO / "data"
HK_SIGNAL_CACHE = DATA / "hk_v1_signals_cache.json"
HK_BARS_DIR = DATA / "bt10y2_hk"

# See THE VOL_START ANCHOR IS NOT COSMETIC.
HK_VOL_START = "2021-10-12"

# Bump when the emitted record gains or changes a field. A stale cache whose
# rows lack the new field loads fine and then KeyErrors three scripts later.
RECORD_VERSION = 2


# ---------------------------------------------------------------------------
# per-ticker signal emission
# ---------------------------------------------------------------------------
def _fill_supply(bars, rows: list[dict]) -> list[dict]:
    """Attach how fillable each candidate actually was.

    Penetration = how deep the signal bar's ADVERSE extreme went past the
    trigger. Deeper penetration means the order only filled because of a
    single intrabar spike.

    For HK v1 this is the field that matters most: v1 fires off a close-based
    action plan, so the resting limit at the trigger may never trade inside the
    signal bar at all. `touched_on_bar` is that test, and hk_v1_fill_realistic
    exists because `touched_on_bar` is false for most of these rows.
    """
    out = []
    for r in rows:
        b = bars.get(r["sym"]) if isinstance(bars, dict) else bars
        if b is None:
            continue
        d = pd.Timestamp(r["entry_date"])
        sel = b.index[b.index <= d]
        if not len(sel):
            continue
        bar = b.loc[sel[-1]]
        trig, low, high, close = (float(r["entry"]), float(bar["low"]),
                                  float(bar["high"]), float(bar["close"]))
        if min(low, high, close, trig) <= 0:
            continue
        side = int(r.get("side", 1))
        pen = ((trig - low) if side > 0 else (high - trig)) / trig * 100
        r = dict(r)
        r["penetration_pct"] = pen
        r["dist_to_entry_pct"] = ((close - trig) if side > 0 else (trig - close)) / close * 100
        r["touched_on_bar"] = bool(pen >= 0)
        out.append(r)
    return out


def hk_v1_records(arg) -> list[dict]:
    """One record per v1 signal, for every HK ticker. Runs in a worker process.

    This is backtest_hk_us.simulate_strategy_on_history's loop, unchanged in
    what it decides, changed in what it RETURNS: that function collapses to
    aggregate stats, which cannot be split by any later study. The phase
    detection and the action plan are pure functions of the trailing window,
    so emitting every verdict's trade in one pass is identical to calling it
    once per strategy.

    One thing that is NOT identical and must not be: the four strategy names
    in backtest_hk_us are two rules under four labels. BUY_S1 and BREAK_LONG
    both fire on every BUY verdict and SELL_R1 and BREAK_SHORT on every SELL
    verdict, and nothing after that branch reads the strategy name — verified
    numerically, n and total_pnl identical in pairs. Emitting all four would
    double-count every trade.
    """
    import daily_sr  # imported in the worker so the fork does not inherit it

    sym, path, vol_start = arg
    b = load(Path(path))
    if b is None:
        return []
    pos = b.index.searchsorted(pd.Timestamp(vol_start))
    if pos < 300:
        return []
    df = b.iloc[pos - 300:].copy()
    c5 = df["close"]
    ret5d = (c5 / c5.shift(5) - 1) * 100.0

    recs: list[dict] = []
    W = 200
    for i in range(W, len(df)):
        w = df.iloc[i - W:i + 1].copy().reset_index(drop=False)
        w["date"] = pd.to_datetime(w.iloc[:, 0])
        cur = float(df["close"].iloc[i])
        try:
            ph = daily_sr.detect_phase(w)
            daily_sr.build_support_ladder(w, cur, ph["peak"]["price"],
                                          ph["trough"]["price"], str(ph["trough"]["date"]))
            daily_sr.build_resistance_ladder(w, cur, ph["peak"]["price"],
                                             str(ph["peak"]["date"]), ph["trough"]["price"],
                                             str(ph["trough"]["date"]), ph["box_top"]["price"],
                                             str(ph["box_top"]["date"]))
            act = daily_sr.compute_action_plan(w, ph["phase"], cur)
        except Exception:
            continue
        ap = act.get("verdict")
        trig = act.get("trigger_price")
        target = act.get("target_price")
        stop = act.get("stop_price")
        if not ap or not trig or not target or not stop or ap not in ("BUY", "SELL"):
            continue
        if min(trig, target, stop) <= 0:
            continue

        for j in range(i + 1, min(i + 200, len(df))):
            close_j = float(df["close"].iloc[j])
            if ap == "BUY":
                hit_t, hit_s = close_j >= target, close_j <= stop
            else:
                hit_t, hit_s = close_j <= target, close_j >= stop
            if not (hit_t or hit_s):
                continue
            why = "target" if hit_t else "stop"
            gross = ((close_j - trig) / trig) if ap == "BUY" else ((trig - close_j) / trig)
            recs.append({
                "sym": sym, "side": 1 if ap == "BUY" else -1,
                "strategy": "BUY_S1|BREAK_LONG" if ap == "BUY" else "SELL_R1|BREAK_SHORT",
                "entry_date": df.index[i], "exit_date": df.index[j],
                "entry": float(trig), "stop": float(stop),
                "target": float(target), "exit": close_j,
                "gross": float(gross), "why": why,
                "ret5d": float(ret5d.iloc[i]) if pd.notna(ret5d.iloc[i]) else 0.0,
            })
            break                       # no outcome inside the window -> dropped,
                                        # same as the reference function
    return _fill_supply(b, recs)


# ---------------------------------------------------------------------------
# cache assembly
# ---------------------------------------------------------------------------
def build_cache(workers: int = 10) -> list[dict]:
    paths = sorted(HK_BARS_DIR.glob("*.json"))
    if not paths:
        raise SystemExit(f"no bar files in {HK_BARS_DIR}")
    args = [(p.stem, str(p), HK_VOL_START) for p in paths]
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, out in enumerate(ex.map(hk_v1_records, args), 1):
            rows.extend(out)
            if i % 40 == 0:
                print(f"  {i}/{len(paths)} tickers, {len(rows):,} records", flush=True)
    return rows


def write_cache(rows: list[dict], out: Path) -> None:
    out.write_text(json.dumps(
        {"_src": HK_BARS_DIR.name, "_v": RECORD_VERSION,
         "_vol_start": HK_VOL_START, "records": rows},
        default=str))
    print(f"-> {out}  ({out.stat().st_size / 1e6:.1f} MB, {len(rows):,} records)")


def verify(fresh: Path, against: Path) -> int:
    """Compare a freshly built cache against the retained one, record by record.

    A rebuild that produces a plausible-looking but different record set is
    worse than no rebuild: it would let a stale conclusion be re-derived and
    re-published as current. So this compares the actual records, not the row
    count, and returns non-zero on any difference.
    """
    a = json.loads(fresh.read_text())
    b = json.loads(against.read_text())
    ra, rb = a["records"], b["records"]
    print(f"\nverify against {against.name}")
    print(f"  _vol_start  {a['_vol_start']}  vs  {b['_vol_start']}")
    print(f"  _v          {a['_v']}  vs  {b['_v']}")
    print(f"  records     {len(ra):,}  vs  {len(rb):,}")
    if (a["_vol_start"], a["_v"]) != (b["_vol_start"], b["_v"]):
        print("  MISMATCH: header differs, record comparison is meaningless")
        return 1
    if len(ra) != len(rb):
        print(f"  MISMATCH: {len(ra) - len(rb):+,} records")
        return 1
    ka = {(r["sym"], r["entry_date"], r["side"]): r for r in ra}
    kb = {(r["sym"], r["entry_date"], r["side"]): r for r in rb}
    only_a, only_b = set(ka) - set(kb), set(kb) - set(ka)
    if only_a or only_b:
        print(f"  MISMATCH: {len(only_a)} fresh-only, {len(only_b)} retained-only")
        for k in list(only_a)[:3]:
            print(f"    fresh only: {k}")
        for k in list(only_b)[:3]:
            print(f"    retained only: {k}")
        return 1
    diffs = 0
    for k, x in ka.items():
        y = kb[k]
        for f in ("entry", "stop", "target", "exit", "gross", "why",
                  "penetration_pct", "dist_to_entry_pct", "touched_on_bar"):
            if x.get(f) != y.get(f):
                diffs += 1
                if diffs <= 5:
                    print(f"  MISMATCH {k} field={f}: {x.get(f)!r} vs {y.get(f)!r}")
    if diffs:
        print(f"  MISMATCH: {diffs} field differences across {len(ka):,} matched records")
        return 1
    print(f"  IDENTICAL: {len(ka):,} records, all 9 payload fields match")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HK_SIGNAL_CACHE,
                    help=f"cache path to write (default {HK_SIGNAL_CACHE})")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--verify", action="store_true",
                    help="compare the freshly built cache against the retained one")
    ap.add_argument("--against", type=Path, default=HK_SIGNAL_CACHE,
                    help="cache to verify against when --verify and --out differ")
    args = ap.parse_args()

    print(f"HK v1 signal cache rebuild  start={HK_VOL_START}  workers={args.workers}")
    rows = build_cache(args.workers)
    if not rows:
        print("no records produced — refusing to write an empty cache", file=sys.stderr)
        return 2
    write_cache(rows, args.out)

    if args.verify:
        if args.out.resolve() == args.against.resolve():
            print("\n--verify needs --out to differ from --against")
            return 2
        return verify(args.out, args.against)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())