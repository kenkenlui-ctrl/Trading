#!/usr/bin/env python3
"""market_filter.py — the market filter, in ONE place.

THE RULE (as the live product applies it)
-----------------------------------------
build_v2_signals._read_index reads the market index and asks: is its LAST
close above its own 200-day MA, where the MA INCLUDES that close? If the
answer is no, that market publishes NOTHING for the session - the filter is a
market-level switch on publication, not a per-stock tilt.

THE SAME RULE IN THE FROZEN ENGINE'S FRAME
-------------------------------------------
core.features() computes prev_close = close.shift(1) and ma200 = the 200-day
mean of those same shifted closes. So

    prev_close > ma200   at session X   ==   live_verdict at session X-1

i.e. the features frame expresses the same rule one session earlier. The
frozen engine's entry session i IS the live plan's synthetic entry row
(features at row i use closes through i-1, exactly like the live frame's row
D+1 where D is the plan session), so the gate has to be read ON THE ENTRY
SESSION. That is why session_verdicts() below is keyed by the entry session
and not by the plan session.

WHY THIS IS A SEPARATE MODULE
-----------------------------
Three places need the rule and they must not drift apart:
  - scripts/ab_close_hold.py         the frozen baseline the site quotes
  - scripts/research/executability.py the per-trade mirror the research
                                      re-derives it from
  - scripts/research/short_side.py    the short-side study, whose long path
                                      must equal the frozen engine row for row
Before this module existed, the live path had the rule and the frozen engine
did not - so the number the site published was measured on a superset of the
trades the published rule would ever take (2026-11-14).

Usage:
    python3 scripts/v2engine/market_filter.py --selftest
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

MA_WINDOW = 200

# A scale glitch is a bar served at 1/n of its true price. Yahoo served
# 1306.T with two sessions (2026-03-30 and 2026-03-31) at one tenth of the
# true price, and still serves it that way today:
#     https://finance.yahoo.com/quote/1306.T/history
# It is not a crash - the series returns to the same level on the next
# session. Left in, it puts a 10x error inside the 200-day MA window and
# makes the market verdict wrong in a way no reader could see.
GLITCH_DROP = 0.25      # a session that falls to under a quarter of the last
GLITCH_RECOVER = 4.0    # a session that jumps back by more than 4x
GLITCH_MAX_RUN = 12     # a glitch is a short run, not a drawdown
GLITCH_LEVEL_TOL = 0.25  # the level before and after the run must agree

# The market proxy, and it is the LIVE path's proxy.
#
# build_v2_signals.MARKETS reads SPY for US and 1306.T for JP; the fallback
# list only comes into play when the primary cannot be fetched. The frozen
# engines must gate on the SAME series, otherwise "the measured rule" and
# "the published rule" are two different rules that happen to share a name.
#
# The proxies are not interchangeable: on the frozen store's own history the
# ETF proxy and the index proxy disagree on 1.4% of US sessions and 11.9% of
# JP ones, so picking a different one is a measurement change, not a detail.
MARKET_PROXY = {"us200": "SPY", "jp200": "1306_T"}


def _date_str(d) -> str:
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


def repair_scale_glitch(close: pd.Series) -> tuple[pd.Series, list[dict]]:
    """Repair a bar served at 1/n of its true price.

    The signature is narrow on purpose: a session collapses to under a
    quarter of the last one, a short run later a session jumps back by more
    than 4x, and the level before the run and the level after it agree. A
    real -90% move that never comes back is NOT a glitch and is left exactly
    as it is; so is a drop whose recovery does not restore the level.

    Returns the repaired series and a ledger of what was repaired, so the
    caller can report it instead of silently changing a number.
    """
    if close is None or len(close) < 5:
        return close, []
    c = close.astype(float)
    r = c.pct_change()
    out = c.copy()
    repairs: list[dict] = []
    n = len(c)
    i = 1
    while i < n - 1:
        ri = r.iloc[i]
        if ri != ri or (1.0 + ri) >= GLITCH_DROP:
            i += 1
            continue
        k = None
        for j in range(i + 1, min(n, i + GLITCH_MAX_RUN + 1)):
            rj = r.iloc[j]
            if rj == rj and rj >= GLITCH_RECOVER:
                k = j
                break
        if k is None:
            i += 1
            continue
        f = int(round(1.0 / (1.0 + ri)))
        if f < 2 or abs(r.iloc[k] - f) > max(1.0, 0.4 * f):
            i += 1
            continue
        before, after = float(c.iloc[i - 1]), float(c.iloc[k])
        if before <= 0 or after <= 0:
            i += 1
            continue
        if abs(after / before - 1.0) > GLITCH_LEVEL_TOL:
            i += 1
            continue
        seg = out.iloc[i:k] * f
        lo, hi = min(before, after) * 0.9, max(before, after) * 1.1
        if float(seg.min()) < lo or float(seg.max()) > hi:
            i += 1
            continue
        out.iloc[i:k] = seg
        repairs.append({
            "from": str(c.index[i].date()), "to": str(c.index[k - 1].date()),
            "sessions": int(k - i), "factor": f,
            "old": [round(float(x), 6) for x in c.iloc[i:k]],
            "new": [round(float(x), 6) for x in seg],
        })
        i = k
    return out, repairs


def live_verdict(close: pd.Series) -> bool:
    """The live form: the LAST close above its own 200d MA (MA includes it).

    This is what build_v2_signals._read_index asks, on a series whose last
    row is the index's most recent session.

    The series is scale-glitch repaired first: a bar served at 1/n of its
    true price inside the MA window would otherwise make the verdict a
    measurement of Yahoo's typo rather than of the market.
    """
    if close is None or len(close) == 0:
        return False
    close = repair_scale_glitch(close)[0]
    ma = close.rolling(MA_WINDOW).mean()
    last_c, last_ma = close.iloc[-1], ma.iloc[-1]
    # NaN compares False, so a missing bar would read as "market down" and
    # gate every session off. A missing bar is not a verdict.
    if pd.isna(last_c) or pd.isna(last_ma):
        return False
    return bool(last_c > last_ma)


def session_verdicts(close: pd.Series) -> dict[str, bool]:
    """One verdict per session, keyed by the session's date.

    The features-frame form: prev_close > ma200, where prev_close is
    close.shift(1) and ma200 is the 200-day mean of those same shifted
    closes - core.features()'s own definition, not a re-derivation of it.

    A session with no verdict (NaN, i.e. not enough history yet) is simply
    absent: the live product refuses to publish when the index cannot be
    read, so an absent verdict must gate the session OFF, never on.

    The series is scale-glitch repaired first, for the same reason
    live_verdict() repairs it.
    """
    if close is None or len(close) == 0:
        return {}
    close = repair_scale_glitch(close)[0]
    cs = close.shift(1)
    ma = cs.rolling(MA_WINDOW).mean()
    out: dict[str, bool] = {}
    for i in range(len(close)):
        pc, m = cs.iloc[i], ma.iloc[i]
        if pd.isna(pc) or pd.isna(m):
            continue
        out[_date_str(close.index[i])] = bool(pc > m)
    return out


def verdicts_for(mkt: str, bars_dir) -> dict[str, bool]:
    """The per-session verdicts for one market, read off the market proxy.

    One loader for every consumer, so the frozen baseline, the research
    mirrors and the published rule cannot end up gating on different series.
    An unreadable proxy returns NO verdicts, and a session with no verdict is
    gated OFF (fail-closed): the live product refuses to publish when the
    index cannot be read, so "no verdict" must never mean "market up".
    """
    from bt_clean import load as load_clean

    sym = MARKET_PROXY[mkt]
    try:
        df = load_clean(Path(bars_dir) / (sym + ".json"))
    except Exception:
        return {}
    if df is None or len(df) < 260:
        return {}
    return session_verdicts(df["close"].astype(float))


def verdicts_series(mkt: str, bars_dir) -> "pd.Series | None":
    """The same verdicts as a boolean Series indexed by session date.

    For the modules that already align a market filter onto the calendar of
    each symbol with .reindex().fillna(False). A session with no verdict
    reindexes to False, which is the fail-closed direction.
    """
    v = verdicts_for(mkt, bars_dir)
    if not v:
        return None
    return pd.Series({pd.Timestamp(d): bool(x) for d, x in v.items()}, dtype=bool)


def _selftest() -> int:
    """The mirror has to be provable, not asserted.

    The identity under test: session_verdicts(X) == live_verdict of the same
    series truncated one session earlier. If that ever stops holding, the
    frozen engine is gating on a different session than the live rule and
    every number it produces is off by one session.
    """
    ok = True
    rng = np.random.default_rng(7)
    n = 400
    idx = pd.bdate_range("2020-01-01", periods=n)
    # a series that trends up, dips, and trends up again, so both verdicts
    # appear and the boundary between them is exercised
    close = pd.Series(100 + np.cumsum(rng.normal(0.12, 1.1, n)), index=idx)
    close.iloc[120:260] *= 0.86

    v = session_verdicts(close)
    checked = 0
    for i in range(MA_WINDOW + 2, n):
        d = _date_str(idx[i])
        if d not in v:
            continue
        trunc = close.iloc[:i]          # everything up to, but not including, i
        expect = live_verdict(trunc)
        checked += 1
        if v[d] != expect:
            print(f"  FAIL  {d}: session_verdict={v[d]} live_verdict={expect}")
            ok = False
            break
    print(f"  {'PASS' if ok else 'FAIL'}  the frozen-frame verdict equals the "
          f"live verdict one session earlier ({checked} sessions checked)")

    # a session with no history has NO verdict at all, and must not be
    # silently treated as "market up"
    short = session_verdicts(close.iloc[:10])
    print(f"  {'PASS' if not short else 'FAIL'}  a series shorter than the MA "
          f"window yields no verdict ({len(short)} found)")

    # the live form must read the last close against an MA that INCLUDES it
    up = pd.Series(np.arange(1.0, 301.0), index=pd.bdate_range("2020-01-01", periods=300))
    print(f"  {'PASS' if live_verdict(up) else 'FAIL'}  a rising series is "
          f"'market up' in the live form")
    down = up.iloc[::-1].reset_index(drop=True)
    print(f"  {'PASS' if not live_verdict(down) else 'FAIL'}  a falling series "
          f"is 'market down' in the live form")
    print(f"  {'PASS' if not live_verdict(pd.Series([1.0, 2.0])) else 'FAIL'}  "
          f"a series with no MA is not a verdict")
    # the scale-glitch repair: a bar served at 1/10 of its true price is
    # repaired, and a real crash that never comes back is NOT.
    n2 = 300
    idx2 = pd.bdate_range("2023-01-02", periods=n2)
    good = pd.Series(np.linspace(300.0, 380.0, n2), index=idx2)
    glitched = good.copy()
    glitched.iloc[150] = good.iloc[150] / 10.0
    glitched.iloc[151] = good.iloc[151] / 10.0
    fixed, repairs = repair_scale_glitch(glitched)
    fac = repairs[0]["factor"] if repairs else None
    ok_rep = (len(repairs) == 1 and fac == 10
              and abs(float(fixed.iloc[150]) - float(good.iloc[150])) < 1e-6
              and abs(float(fixed.iloc[151]) - float(good.iloc[151])) < 1e-6)
    print(f"  {'PASS' if ok_rep else 'FAIL'}  a 1/10-scale run is repaired "
          f"({len(repairs)} repair(s), factor={fac})")
    ok &= ok_rep
    crash = good.copy()
    crash.iloc[150:] = good.iloc[150] * 0.1
    fixed2, repairs2 = repair_scale_glitch(crash)
    ok_crash = (len(repairs2) == 0
                and float(fixed2.iloc[200]) == float(crash.iloc[200]))
    print(f"  {'PASS' if ok_crash else 'FAIL'}  a real 90% drawdown is NOT "
          "repaired (it never comes back)")
    ok &= ok_crash
    clean, repairs3 = repair_scale_glitch(good)
    ok_clean = (len(repairs3) == 0 and clean.equals(good))
    print(f"  {'PASS' if ok_clean else 'FAIL'}  a clean series is returned "
          "untouched")
    ok &= ok_clean
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        print("MARKET FILTER — selftest")
        return _selftest()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
