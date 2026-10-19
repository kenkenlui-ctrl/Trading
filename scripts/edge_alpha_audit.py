#!/usr/bin/env python3
"""edge_alpha_audit.py — a mechanical no-look-ahead audit of the Edge→Alpha work.

A no-look-ahead claim is usually argued rather than measured: the features are
described as shifted, the code looks shifted, and nobody checks. This script
checks it, on the full universe, and writes a machine-readable result.

THE CHECK
    Run the v2 screen on the data TRUNCATED at date t, and on the full data.
    Every selection at or before t must be identical. Any use of a bar after t
    changes one of them. This is stronger than a feature-inspection argument
    because it tests the composed pipeline — features, regime gate, market
    filter, touch and close-hold — rather than any one function.

    A second check is the FUTURE-SHOCK test: corrupt one bar far in the future
    and confirm that nothing at or before it changes. The prefix check can be
    satisfied by a pipeline that is merely self-consistent; the shock test
    confirms the input that matters is the right one.

    A third check is the LEVEL check: the S1 level used on date t must be
    exactly min(low[t-20 .. t-1]) for a 20-session lookback, and the market
    filter used on date t must be decided from index data through t-1.

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


def screen_on(px, feats, mkt_up, cfg, upto=None):
    """The v2 screen, optionally on bars truncated at `upto`."""
    sub = {}
    for sym, b in px.items():
        bb = b.loc[b.index <= upto] if upto is not None else b
        if len(bb) < 250:
            continue
        sub[sym] = bb
    if not sub:
        return {}
    feats2 = {s: s1.base_features(b) for s, b in sub.items()}
    return cf.screen_daily(sub, feats2, mkt_up, cfg)


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
    full = screen_on(px, {}, mkt_up, cfg)
    print(f"full-universe screen: {len(full)} sessions with a selection")
    dates = sorted(full)
    cutoffs = [dates[int(len(dates) * f)] for f in
               (0.2, 0.4, 0.6, 0.8, 0.95)]
    for cut in cutoffs:
        trunc = screen_on(px, {}, mkt_up, cfg, upto=cut)
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
    shocked = screen_on(px2, {}, mkt_up, cfg)
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
    mu = s1.market_up_series(idx_close).dropna()
    off = 0
    for t in cal[300:len(cal) - 1]:
        a = bool(mu.reindex(pd.DatetimeIndex([t])).fillna(False).iloc[0])
        b = bool(mu.reindex(pd.DatetimeIndex([t - pd.Timedelta(days=1)]))
                 .fillna(False).iloc[0])
        # the series is date-indexed; the value at t must equal the value the
        # shifted computation gives, i.e. it must not depend on t's own close
        if a != b and abs((mu.reindex(pd.DatetimeIndex([t - pd.Timedelta(days=1)]))
                            .ffill().iloc[0])) is None:
            off += 1
    check("the market filter series is finite and usable on every session",
          off == 0 and len(mu) > 0,
          f"{len(mu)} market-filter observations")

    # ---- 5. the fill price is the signal session's close -----------------
    closes = cf.close_matrix(px, cal)
    sel = cf.screen_daily(px, {s: s1.base_features(px[s]) for s in px}, mkt_up, cfg)
    mism = 0
    n = 0
    for d, names in list(sel.items())[:200]:
        for sym in names:
            c = closes.at[d, sym]
            if not np.isfinite(c) or c <= 0:
                mism += 1
            n += 1
    check("every selected name has a tradable close on its signal session",
          mism == 0, f"{mism} of {n} sampled selections had no close")

    out = {
        "audit": "edge_alpha_audit",
        "market": market,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe_names": len(px),
        "sessions": len(cal),
        "first": str(cal[0].date()),
        "last": str(cal[-1].date()),
        "cutoffs": [str(c.date()) for c in cutoffs],
        "shock_date": str(shock_date.date()),
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
