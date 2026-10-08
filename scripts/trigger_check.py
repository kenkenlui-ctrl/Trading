#!/usr/bin/env python3
"""
trigger_check.py — for each v2 signal generated on D, did the resting S1
limit order actually fire on the next session, and did that session close
back above S1?

WHAT IS BEING TESTED — the production fill rule, transcribed from
scripts/portfolio_test.py::candidates_v2 (lines 81-84), which is the code the
site's published performance numbers came from:

    if not (O[i] > s1[i] and L[i] <= s1[i]):   continue   # open above S1, dip to it
    if C[i] < s1[i]:                           continue   # and hold it at the close

So "VALID FILL" needs all three legs. Two traps this script would otherwise
fall into, both of which would report trades the live rule never takes:

  1. OPEN > S1 matters. A session that gaps down and opens BELOW the limit is
     skipped by the rule, not bought. Checking only "low <= S1" would count a
     gap-down through S1 as a clean fill.
  2. THE MARKET FILTER matters. build_v2_signals.py::main() skips an entire
     market when its index is below its 200d MA. Ignoring it reports signals
     the site never published.

Both gates are re-implemented here only because neither is exposed by a
callable in production code; the per-stock S1/ATR/box/uptrend math is IMPORTED
from scripts/build_v2_signals.py rather than re-derived, so a signal here is
by construction the same signal the site published.

Dates with no data are reported as NOT OBSERVABLE and never guessed: on
2026-10-08 evening the US session of 2026-10-08 has not happened yet (it opens
21:30 HKT), so a US signal from 10-07 cannot be checked.

Usage:
    python3 scripts/trigger_check.py                       # 10-06, 10-07, JP+US
    python3 scripts/trigger_check.py --dates 2026-10-07
    python3 scripts/trigger_check.py --markets us200
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts"))

import build_v2_signals as BVS                       # noqa: E402  (production rule)

EQUITY = 100_000.0     # only feeds shares_at_1pct_risk; S1/stop do not depend on it
DEFAULT_DATES = ["2026-10-06", "2026-10-07"]
DEFAULT_MARKETS = ["us200", "jp200"]
MIN_HISTORY = 210     # 200d MA + 20d slope need 220; 210 keeps near-listings in


# --------------------------------------------------------------------------
# Market filter, point-in-time.
#
# production index_up() asks "is TODAY's index close above its 200d MA". For a
# historical D that would be peeking: it would apply today's regime to a signal
# we claim was generated a week ago. Here the index series is truncated at D
# first, so the filter is the one that was actually true on that date.
# --------------------------------------------------------------------------
def index_up_on(mkt: str, D: pd.Timestamp) -> tuple[bool | None, str]:
    cfg = BVS.MARKETS[mkt]
    try:
        import yfinance as yf
    except Exception as e:                       # pragma: no cover
        return None, f"yfinance unavailable ({type(e).__name__})"

    errs = []
    for sym in [cfg["index"]] + list(cfg.get("fallback") or []):
        try:
            d = yf.Ticker(sym).history(period="2y", auto_adjust=True)
            if d is None or d.empty:
                raise ValueError("no data returned")
            c = d["Close"]
            if isinstance(c, pd.DataFrame):        # some yfinance builds return
                c = c.iloc[:, 0]                  # a MultiIndex column frame
            # yfinance hands back a tz-aware index (America/New_York); D is
            # tz-naive, and slicing one with the other is a TypeError that
            # reads like "no data" if the message is swallowed.
            idx = c.index
            c.index = idx.tz_localize(None) if idx.tz is not None else idx
            c = c.loc[:D]                          # <- point-in-time truncation
            ma = c.rolling(200).mean()
            lc, lm = c.iloc[-1], ma.iloc[-1]
            if pd.isna(lc) or pd.isna(lm):
                raise ValueError("index close or 200d MA is NaN")
            return bool(lc > lm), f"{sym} close {lc:.2f} vs MA200 {lm:.2f}"
        except Exception as e:
            # Print the message, not just the class: a bare TypeError cost a
            # full debugging round-trip once already.
            errs.append(f"{sym}: {type(e).__name__}: {e}")
    return None, "index filter unresolvable — " + " | ".join(errs)


def market_calendar(bars: dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
    """Session calendar = every date at least a third of the universe traded on.

    Per-symbol indices differ (suspensions, half days), so the union would
    invent sessions that most names did not trade; the intersection would drop
    real ones. A third-of-universe cutoff tolerates a handful of stragglers.
    """
    per_day = Counter()
    for df in bars.values():
        for d in df.index:
            per_day[d] += 1
    cutoff = max(1, len(bars) // 3)
    return sorted(d for d, n in per_day.items() if n >= cutoff)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="*", default=DEFAULT_DATES)
    ap.add_argument("--markets", nargs="*", default=DEFAULT_MARKETS)
    ap.add_argument("--json-out", default=str(REPO / "data" / "trigger_check.json"))
    a = ap.parse_args()

    report: dict = {
        "rule": "VALID FILL requires open>S1 AND low<=S1 AND close>=S1 "
                "(portfolio_test.py::candidates_v2)",
        "source": "S1/stop/guards imported from scripts/build_v2_signals.py; "
                  "market filter = index close > own 200d MA as of D (point-in-time)",
        "results": {},
    }
    print(f"markets={a.markets}  dates={a.dates}\n")

    for mkt in a.markets:
        bars = BVS.bars_from_public(mkt)
        if not bars:
            print(f"{mkt}: no bars")
            continue
        cal = market_calendar(bars)
        print(f"=== {mkt} — {len(bars)} symbols, sessions {cal[0].date()} → {cal[-1].date()}")

        for ds in a.dates:
            D = pd.Timestamp(ds)
            key = f"{mkt}@{ds}"
            nxt = next((d for d in cal if d > D), None)

            # --- gate 0: market filter, evaluated AS OF D ---
            up, idx_note = index_up_on(mkt, D)
            idx = {
                "up": up, "note": idx_note, "asof": str(D.date()),
                "index": BVS.MARKETS[mkt]["index"],
            }
            if up is False:
                report["results"][key] = {
                    "market_filter": idx, "n_signals": 0,
                    "reason": "market filter OFF on this date — production publishes "
                              "zero signals for the whole market, so there is nothing "
                              "to trigger",
                }
                print(f"  {ds}: market filter OFF ({idx_note}) → 0 signals published")
                continue
            if up is None:
                print(f"  !! {ds}: {idx_note} — cannot confirm the gate, reporting UNKNOWN")
            idx_caution = "" if up else "  !! market filter unconfirmed\n"

            # --- gate 1..n: per-stock screen (production plan()) ---
            sigs, skips = [], Counter()
            for sym, df in bars.items():
                sub = df[df.index <= D]
                if len(sub) < MIN_HISTORY:
                    skips["insufficient history"] += 1
                    continue
                p, why = BVS.plan(mkt, sym, sub, EQUITY)
                if p is None:
                    skips[why] += 1
                    continue
                # The order is placed after THIS ticker's own last bar, so its
                # eligible session is the ticker's own next bar — not the market
                # calendar's. A name that did not trade on D (suspended, or its
                # upstream feed is truncated) would otherwise be checked one
                # session too late: QRVO's bars stop at 10-05, so its entry
                # session is 10-06, and testing its 10-07 bar would report a
                # miss for a session that was never the entry session.
                sig_date = sub.index[-1]
                own_next = next((d for d in df.index if d > sig_date), None)
                p["_sig_date"] = sig_date
                p["_own_next"] = own_next
                sigs.append(p)

            # --- gate N: did the resting order actually fire on that session? ---
            rows = []
            for p in sigs:
                s1 = float(p["entry_s1"])
                sym = p["symbol"]
                df = bars[sym]
                sig_date = p["_sig_date"]
                nxt = p["_own_next"]
                rec = {k: v for k, v in p.items() if not k.startswith("_")}
                rec.update({
                    "signal_date": ds, "generated_from_bar": str(sig_date.date()),
                    "next_session": str(nxt.date()) if nxt is not None else None,
                })
                if sig_date.date() != D.date():
                    rec["late_note"] = (f"last bar is {sig_date.date()}, not {ds} — the "
                                        f"order was placed after that close instead")

                if nxt is None:
                    rec.update(status="NOT OBSERVABLE", reason=(
                        "no bar after this ticker's signal session — that market "
                        "has not traded the entry session yet"))
                    rows.append(rec)
                    continue

                bar = df.loc[nxt]
                op, lo, cl = float(bar["open"]), float(bar["low"]), float(bar["close"])
                opened_above = op > s1
                touched = lo <= s1
                held = cl >= s1
                valid = opened_above and touched and held

                if not opened_above:
                    status = "GAPPED BELOW S1 — skipped by rule"
                elif not touched:
                    status = "no trigger"
                elif not held:
                    status = "touched but closed below S1 — rejected"
                else:
                    status = "VALID FILL"

                rec.update({
                    "next_open": round(op, 2), "next_low": round(lo, 2),
                    "next_close": round(cl, 2),
                    "open_vs_s1_pct": round((op / s1 - 1) * 100, 3),
                    "low_vs_s1_pct": round((lo / s1 - 1) * 100, 3),
                    "close_vs_s1_pct": round((cl / s1 - 1) * 100, 3),
                    "opened_above_s1": opened_above, "touched_s1": touched,
                    "close_held_s1": held, "valid_fill": valid, "status": status,
                })
                rows.append(rec)

            n_touch = sum(1 for r in rows if r.get("touched_s1"))
            n_fill = sum(1 for r in rows if r.get("valid_fill"))
            n_gap = sum(1 for r in rows if r.get("status", "").startswith("GAPPED"))
            n_fail_hold = sum(1 for r in rows if r.get("status", "").startswith("touched but"))
            n_noobs = sum(1 for r in rows if r.get("status") == "NOT OBSERVABLE")
            report["results"][key] = {
                "market_filter": idx,
                "next_session": str(nxt.date()) if nxt is not None else None,
                "n_signals": len(rows), "n_touched": n_touch, "n_valid_fill": n_fill,
                "n_gapped_below": n_gap, "n_failed_close_hold": n_fail_hold,
                "n_not_observable": n_noobs,
                "skip_reasons": dict(skips),
                "rows": rows,
            }

            print(f"{idx_caution}  {ds}: {len(rows)} signals | next session "
                  f"{nxt.date() if nxt is not None else 'NONE'} | touched {n_touch} "
                  f"| VALID FILL {n_fill} | gapped-below {n_gap} | failed hold {n_fail_hold} "
                  f"| not observable {n_noobs}")

            interesting = [r for r in rows
                           if r.get("touched_s1") or r.get("status") == "NOT OBSERVABLE"]
            for r in sorted(interesting, key=lambda x: (not x.get("valid_fill"),
                                                        x.get("low_vs_s1_pct", 0))):
                print(f"      {r['symbol']:<11} S1={r['entry_s1']:<9} "
                      f"next O/L/C={r.get('next_open')}/{r.get('next_low')}/"
                      f"{r.get('next_close')}  {r['status']}")

    Path(a.json_out).write_text(json.dumps(report, indent=2, ensure_ascii=False,
                                           default=str), encoding="utf-8")
    print(f"\n→ {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())