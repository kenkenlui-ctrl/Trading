#!/usr/bin/env python3
"""breakout_test.py — does a BREAKOUT entry beat equal-weight, and beat live v2?

*** READ THIS BEFORE QUOTING ANY NUMBER FROM THIS SCRIPT ***

Three arms, identical in every respect except the entry:

  breakout     BUY STOP at R1 (20-day high of the T-1 window) x 0.999. Valid for
               the next session only. A session must trade 0.10% THROUGH that
               level to fill (the "through" convention backtest_10y.py uses for
               S1). Filled at max(open, through-level) — backtest_10y.py's fill
               logic, which fills at the through level rather than the stop level.
  v2_pullback  The live v2 rule: the session must open above S1, trade DOWN to
               S1, and CLOSE back at or above S1 (close-hold, added 2026-10-05).
               Filled at S1. Candidates come from portfolio_test.candidates_v2
               verbatim; only the shared market filter is added here.
  ew_hold      Equal-weight hold of the SAME symbol set, same window, same costs.

SHARED (unchanged from backtest_10y.py / portfolio_test.py)
    Direction   long only
    Stock       T-1 close > 200d MA  AND  200d MA rising over 20 sessions
    Market      index T-1 close > its own 200d MA
    Guard rails ATR% < 6%, 20d box width < 35%
    Stop        3 x ATR below the fill
    Target      none — time exit at the close of the 10th session after entry
    Costs       backtest_10y.FEE / SLIP / STOP_EXTRA per market for per-trade
                stats; portfolio_test.cost_rt(mkt, "realistic", ...) at book level
    Book        portfolio_test.walk: 1% equity at risk, 10 slots, 10% notional
                per NAME, gross exposure <= 100% of equity

EXECUTION MODEL
  * No look-ahead. Levels for session t use bars through t-1 only.
  * The stop is scanned from the ENTRY bar forward, including the entry bar.
    backtest_10y.py starts its stop scan at entry_i+1, which would let a
    breakout fill survive a same-session collapse to its own stop; the live v2
    comparator (core._simulate, via candidates_v2) does check the entry bar.
    Checking it here is the conservative choice and keeps the two arms
    like-for-like. Documented deviation, applied to the arm under test only.
  * A trade that cannot complete its 10-session hold before the bars run out is
    dropped, matching _simulate's behaviour in the comparator arm.
  * Costs charged on entry and on exit; extra slippage on stop exits.

DATA
  * Bars: data/bt10y2 for us200 + jp200 + all three index series.
  * HK stocks are NOT in data/bt10y2 — the HK half of the same clean re-fetch
    is data/bt10y2_hk (215 files, 207 with >= 260 bars). backtest_10y.py's
    bars_dir() only sees bt10y2, so HK would have scored zero trades purely for
    lack of an input. This script reads both dirs. That is a real data source,
    not a manufactured result — see "coverage" in the output JSON.
  * The cached glitch mask (data/bt10y_glitch_mask.json) covers bt10y2 only, so
    the HK mask is rebuilt in memory via bt_clean.build_mask (read-only; no
    cache file is written). Trades whose [entry-210d, exit] window touches a
    masked bar are dropped fail-closed, exactly as bt_clean.filter_candidates
    does for the existing studies.
  * The market filter is aligned BY DATE, not by position. backtest_10y.market_up
    indexes the index series positionally (mup.iloc[t]); with symbols whose first
    bar is years after the index's, that is a look-ahead. This script reindexes
    the index filter onto each symbol's own calendar.
  * Index per market: IDX_GSPC / IDX_N225 / IDX_HSI. NOT 1306_T — that ETF has an
    unadjusted 10:1 glitch in March 2026 (portfolio_test.py:49-52).

PORTFOLIO STATS
  Equity curves are forward-filled onto the market's index calendar before
  CAGR / maxDD / Calmar. portfolio_test.stats() differences the sparse event grid
  instead, which understates drawdown between events. Every arm gets the same
  treatment here so the comparison is like-for-like; absolute numbers therefore
  will not tie exactly to portfolio_test.csv.

HEADLINE
  breakout CAGR minus ew_hold CAGR in pp/yr, with a moving-block bootstrap CI on
  daily log returns. Block indices are shared across arms so the cross-arm
  correlation (both books are long the same universe) survives the resample.

BENCHMARK — READ THIS
  The equal-weight benchmark is a TRUE monthly rebalance. portfolio_test.py's B1
  is NOT reused, because it has a defect: `transform('mean')` broadcasts each
  month's mean daily return onto the month's first day, whose pct_change() is NaN,
  handing the benchmark one extra full day of return per month (+2 to +3.4pp/yr of
  CAGR). Reusing it would understate every arm here. portfolio_test.py is owned by
  another process and is NOT modified; its figure is carried alongside as
  ew_hold.portfolio_repoB1_asimplemented so the gap is auditable.

Usage:
    python3 scripts/breakout_test.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))

import backtest_10y as B10          # noqa: E402  features(), FEE/SLIP/STOP_EXTRA, P, wilson, universe()
import portfolio_test as PT         # noqa: E402  walk(), candidates_v2(), cost_rt(), COST, slot params
from bt_clean import (               # noqa: E402
    bars_dir, filter_candidates, build_mask, _load_mask_cache,
)

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------
HK_BARS = REPO / "data" / "bt10y2_hk"
BAR_DIR = {"us200": bars_dir(), "jp200": bars_dir(), "hk200": HK_BARS}
# Index, not ETF. 1306_T has an unadjusted 10:1 glitch (portfolio_test.py:49-52).
MARKET_IDX = {"us200": "IDX_GSPC", "jp200": "IDX_N225", "hk200": "IDX_HSI"}
MARKET_OF = {"hk200": "hk", "us200": "us", "jp200": "jp"}
MIN_BARS = 260                      # backtest_10y.load()'s floor
LEVEL_K = 0.999                     # breakout level = R1 x 0.999
THROUGH = B10.P["through"]          # 0.0010, same as backtest_10y
HOLD = B10.P["hold"]                # 10 sessions
STOP_ATR = B10.P["stop_atr"]        # 3.0 x ATR below the fill
ARMS = ("breakout", "v2_pullback", "ew_hold")
TRADE_ARMS = ("breakout", "v2_pullback")

WINDOWS = {
    "full": (None, None),
    "2016_2022": (None, pd.Timestamp("2023-01-01")),
    "2023_2026": (pd.Timestamp("2023-01-01"), None),
}

BOOT_B, BOOT_BLOCK, BOOT_SEED = 2000, 21, 20261014


# --------------------------------------------------------------------------------------
# Loaders / filters
# --------------------------------------------------------------------------------------
def load_safe(d: Path, safe: str) -> pd.DataFrame | None:
    """backtest_10y.py:load() with the bar dir as a parameter, returned date-INDEXED
    so portfolio_test.candidates_v2() can consume it directly."""
    p = d / f"{safe}.json"
    if not p.exists():
        return None
    try:
        df = pd.DataFrame(json.loads(p.read_text()))
    except Exception:
        return None
    if len(df) < MIN_BARS or "date" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("date")
    if len(df) < MIN_BARS:
        return None
    return df.set_index("date")


def market_up(mkt: str) -> pd.Series:
    """Date-indexed bool: index close at t-1 above its 200d MA through t-1. Same
    arithmetic as backtest_10y.market_up / portfolio_test.market_up_series, but
    reindexable per symbol instead of positional. All three index series live in
    bars_dir() (bt10y2), including IDX_HSI — the HK stock dir has no index bar."""
    b = load_safe(bars_dir(), MARKET_IDX[mkt])
    if b is None:
        return pd.Series(dtype=bool)
    c = b.close
    return (c > c.rolling(200).mean()).shift(1).fillna(False).rename("mkt_up")


def market_mask(mkt: str) -> dict[str, set]:
    """Cached mask for bt10y2, plus a freshly built (read-only) mask for the HK dir."""
    m = dict(_load_mask_cache())
    if BAR_DIR[mkt] != bars_dir():
        m.update(build_mask(BAR_DIR[mkt]))
    return m


# --------------------------------------------------------------------------------------
# Candidate generators
# --------------------------------------------------------------------------------------
def _exit_from(entry_i: int, n: int, stop: float,
               O: np.ndarray, L: np.ndarray, C: np.ndarray):
    """backtest_10y.py's exit loop, scanned from the ENTRY bar. Returns
    (exit_i, exit_px, why), or None if the 10-session hold cannot complete."""
    for j in range(entry_i, min(entry_i + HOLD + 1, n)):
        if j > entry_i and O[j] < stop:
            return j, float(O[j]), "stop_gap"
        if L[j] <= stop:
            return j, float(stop), "stop"
    last = entry_i + HOLD
    if last >= n:                      # ran out of data -> drop, as _simulate does
        return None
    return last, float(C[last]), "time"


def candidates_breakout(b: pd.DataFrame, up: pd.Series) -> list[dict]:
    f = B10.features(b)
    O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(C)
    r1, atr = f.R1.to_numpy(), f.atr_pct.to_numpy()
    ma200, slope = f.ma200.to_numpy(), f.ma200_slope.to_numpy()
    boxw, r5 = f.box_w.to_numpy(), f.ret_5d.to_numpy()
    prevc = f.prev_close.to_numpy()
    ok = (prevc > ma200) & (slope > 0) & (atr < B10.P["atr_max"]) & (boxw < B10.P["box_max"])
    upa = up.reindex(b.index).fillna(False).to_numpy(bool)
    out = []
    for i in np.where(ok & upa)[0]:
        if np.isnan(r1[i]) or np.isnan(atr[i]) or atr[i] <= 0 or r1[i] <= 0:
            continue
        lvl = r1[i] * LEVEL_K                    # BUY STOP level: 20d high x 0.999
        need = lvl * (1 + THROUGH)              # must trade THROUGH, not touch
        if H[i] < need:
            continue
        fill = max(float(O[i]), need)           # backtest_10y fill logic
        stop = fill * (1 - STOP_ATR * atr[i])   # 3 x ATR below the FILL
        if not (0 < stop < fill):
            continue
        res = _exit_from(i, n, stop, O, L, C)
        if res is None:
            continue
        j, xp, why = res
        out.append({"entry_date": b.index[i], "exit_date": b.index[j], "entry": fill,
                    "stop": stop, "exit": xp, "why": why, "ret5d": r5[i],
                    "atr": float(atr[i]), "level": lvl})
    return out


def candidates_v2(b: pd.DataFrame, up: pd.Series, sym: str) -> list[dict]:
    """portfolio_test.candidates_v2 verbatim, plus the shared market filter."""
    atr = B10.features(b).atr_pct
    out = []
    for c in PT.candidates_v2(b):
        if not bool(up.get(c["entry_date"], False)):
            continue
        out.append(dict(c, sym=sym, atr=float(atr.get(c["entry_date"], np.nan)),
                        level=float(c["entry"])))
    return out


def net_trade(mkt: str, entry_px: float, stop: float, exit_px: float, why: str) -> float:
    """backtest_10y.py's cost model: slippage on both legs, extra slippage on a
    stop exit, fee charged on both sides."""
    fee, slip, sx = B10.FEE[mkt], B10.SLIP[mkt], B10.STOP_EXTRA[mkt]
    e = entry_px * (1 + slip)
    x = exit_px * (1 - slip - (sx if str(why).startswith("stop") else 0.0))
    return (x / e - 1) - 2 * fee


# --------------------------------------------------------------------------------------
# Per-trade stats
# --------------------------------------------------------------------------------------
def per_trade(trades: list[dict]) -> dict:
    """n, win%, Wilson 95% CI on win rate, avg/median net %, expectancy in R."""
    if not trades:
        return {"n": 0}
    net = np.array([t["net"] for t in trades], float)
    risk = np.array([max(t["entry"] - t["stop"], 1e-12) / t["entry"] for t in trades])
    k, n = int((net > 0).sum()), len(net)
    lo, hi = B10.wilson(k, n)
    r = net / risk
    return {
        "n": n,
        "symbols": len({t["sym"] for t in trades}),
        "win_pct": round(100 * k / n, 2),
        "win_ci95": [round(lo, 2), round(hi, 2)],
        "avg_net_pct": round(100 * float(net.mean()), 4),
        "median_net_pct": round(100 * float(np.median(net)), 4),
        "expectancy_R": round(float(r.mean()), 4),
        "avg_risk_pct": round(100 * float(risk.mean()), 3),
        "stop_exits": sum(1 for t in trades if str(t["why"]).startswith("stop")),
        "avg_net_win_pct": round(100 * float(net[net > 0].mean()), 3) if k else None,
        "avg_net_loss_pct": round(100 * float(net[net < 0].mean()), 3) if n - k else None,
    }


# --------------------------------------------------------------------------------------
# Portfolio layer
# --------------------------------------------------------------------------------------
def align(curve: pd.Series | None, cal: pd.DatetimeIndex) -> pd.Series:
    """Forward-fill onto the market calendar, back-fill to the window start, rebase
    to 100,000. The back-fill means an arm sits in CASH (0% return) until its first
    trade, so every arm is measured over the identical window — without it the
    arms start on different dates and a CAGR comparison is not like-for-like."""
    if curve is None or len(curve) == 0 or len(cal) == 0:
        return pd.Series(dtype=float)
    s = curve.reindex(cal).ffill().bfill().dropna()
    return s / s.iloc[0] * 100_000.0


def portfolio_stats(eq: pd.Series | None, curve: pd.DataFrame | None = None) -> dict | None:
    if eq is None or len(eq) < 2:
        return None
    s = PT.stats(eq)                                  # total% / cagr% / maxDD%
    cal = abs(s["maxDD%"]) / 100.0
    return {"start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
            "sessions": int(len(eq)),
            "total_pct": round(s["total%"], 2),
            "cagr_pct": round(s["cagr%"], 3),
            "max_dd_pct": round(s["maxDD%"], 2),
            "calmar": round((s["cagr%"] / 100.0) / cal, 3) if cal > 1e-9 else None,
            "avg_slots": round(float(curve.open.mean()), 2) if curve is not None else None,
            "max_slots_used": int(curve.open.max()) if curve is not None else None}


def ew_curve(px: dict[str, pd.Series], cal: pd.DatetimeIndex, mkt: str) -> pd.Series:
    """TRUE monthly equal-weight rebalance of the same names, then one round-trip
    cost haircut so the benchmark pays the friction the arms pay. Monthly turnover
    cost is not charged — same limitation the repo's B1 has.

    DELIBERATE DEVIATION FROM portfolio_test.py's B1. B1 builds the benchmark as
        src.pct_change().groupby([year,month]).transform('mean').mean(axis=1)
    `transform('mean')` computes each ticker's mean daily return over the month and
    broadcasts it back over EVERY day of that month — including the first, whose
    pct_change() is NaN. The benchmark therefore earns one extra day of full monthly
    return per month, ~126 free days over the window. Measured effect on the
    benchmark alone (see ew_hold_repoB1_asimplemented in the output JSON):
        us200  B1 +28.88%/yr  vs  true monthly EW +26.11%/yr   (+2.8pp/yr)
        hk200  B1 +18.94%/yr  vs  true monthly EW +15.54%/yr   (+3.4pp/yr)
        jp200  B1 +18.01%/yr  vs  true monthly EW +15.98%/yr   (+2.0pp/yr)
    That inflates the benchmark, so copying B1 verbatim would understate every arm
    here. portfolio_test.py is owned by another process and is NOT modified; both
    figures are reported so the discrepancy is auditable."""
    src = pd.DataFrame(px).reindex(cal)
    src = src.loc[src.notna().any(axis=1)]
    if src.empty:
        return pd.Series(dtype=float)
    mr = src.resample("ME").last().pct_change()
    mm = mr.mean(axis=1)
    eq = (100_000 * (1 + mm).cumprod()).ffill()
    return eq * (1 - PT.cost_rt(mkt, PT.COST, False))


def ew_curve_repoB1(px: dict[str, pd.Series], cal: pd.DatetimeIndex, mkt: str) -> pd.Series:
    """portfolio_test.py's B1 exactly as written, kept only as an audit reference.
    See ew_curve() for why it is not used as the headline benchmark."""
    src = pd.DataFrame(px).reindex(cal)
    src = src.loc[src.notna().any(axis=1)]
    if src.empty:
        return pd.Series(dtype=float)
    c = src.index
    mm = src.pct_change().groupby([c.year, c.month]).transform("mean").mean(axis=1)
    return (100_000 * (1 + mm).cumprod()).ffill() * (1 - PT.cost_rt(mkt, PT.COST, False))


def daily_log_rets(eq: pd.Series) -> pd.Series:
    return np.log(eq / eq.shift(1)).iloc[1:]


def cagr_gap_ci(rets: dict[str, pd.Series], B=BOOT_B, block=BOOT_BLOCK,
                seed=BOOT_SEED) -> dict:
    """Moving-block bootstrap on daily log returns. Block indices are shared
    across arms so the cross-arm correlation survives the resample."""
    arms = [a for a in ("breakout", "v2_pullback", "ew_hold") if a in rets]
    if len(arms) < 2 or "ew_hold" not in arms:
        return {}
    common = None
    for a in arms:
        ix = rets[a].dropna().index
        common = ix if common is None else common.intersection(ix)
    if common is None or len(common) < block * 2:
        return {}
    R = {a: rets[a].reindex(common).to_numpy(float) for a in arms}
    T = len(common)
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(T / block))
    offs = np.arange(block)
    acc: dict[str, list] = {a: [] for a in arms if a != "ew_hold"}
    yrs = T / 252.0
    for s in range(0, B, 250):
        n = min(250, B - s)
        starts = rng.integers(0, T, size=(n, nb))
        sel = ((starts[:, :, None] + offs[None, None, :]) % T).reshape(n, -1)[:, :T]
        cg = {a: np.expm1(R[a][sel].sum(axis=1) / yrs) * 100.0 for a in arms}
        for a in acc:
            acc[a].extend((cg[a] - cg["ew_hold"]).tolist())
    res: dict = {}
    for a, v in acc.items():
        v = np.array(v)
        res[f"{a}_minus_ew_hold_ppyr"] = round(float(v.mean()), 3)
        res[f"{a}_minus_ew_hold_ppyr_ci95"] = [
            round(float(np.percentile(v, 2.5)), 3), round(float(np.percentile(v, 97.5)), 3)]
    res["_bootstrap"] = (f"{B} resamples, {block}-session moving blocks, shared block "
                         f"indices across arms, seed {seed}, on the common "
                         f"{T}-session overlap of all arms")
    return res


def combine_daily(rets_by_mkt: dict[str, pd.Series]) -> pd.Series:
    """Equal capital per market, daily rebalanced: mean of the per-market daily
    log returns on the union calendar (0 where a market is closed)."""
    idx = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for s in rets_by_mkt.values()])))
    tot = np.zeros(len(idx))
    for s in rets_by_mkt.values():
        tot += s.reindex(idx).fillna(0.0).to_numpy(float)
    return pd.Series(tot / len(rets_by_mkt), index=idx)


def count_admitted(cands: list[dict], mkt: str) -> int:
    """AUDIT ONLY — counts how many candidates portfolio_test.walk actually
    admits into the book. It does not produce any reported number; it exists so
    the reader can see that the portfolio result rests on the ~1-2k trades that
    clear the 10-slot / 10%-per-name gates, not on the full candidate pool.
    Mirrors PT.walk's admission tests exactly. PT.walk itself is untouched."""
    if not cands:
        return 0
    d = pd.DataFrame(cands).sort_values(["entry_date", "ret5d"])
    d["cost"] = [PT.cost_rt(mkt, PT.COST, w == "stop") for w in d.why]
    equity, pos, taken = 100_000.0, [], 0
    for dt in sorted(set(d.entry_date) | set(d.exit_date)):
        keep = []
        for p in pos:
            if p["exit_date"] <= dt:
                equity += p["shares"] * (p["exit"] * (1 - p["cost"]) - p["entry"])
            else:
                keep.append(p)
        pos = keep
        for _, t in d[d.entry_date == dt].iterrows():
            if len(pos) >= PT.MAX_SLOTS:
                break
            rps = t.entry - t.stop
            if rps <= 0:
                continue
            room = PT.MAX_NOTIONAL * equity - sum(
                p["shares"] * p["entry"] for p in pos if p["sym"] == t.sym)
            if room <= 0:
                continue
            sh = min(PT.RISK * equity / rps, room / t.entry)
            if sh <= 0:
                continue
            if sum(p["shares"] * p["entry"] for p in pos) + sh * t.entry > equity:
                continue
            pos.append({"sym": t.sym, "entry": float(t.entry), "shares": sh,
                        "exit_date": t.exit_date, "exit": float(t.exit), "cost": t.cost})
            taken += 1
    return taken


def in_window(trades: list[dict], lo, hi) -> list[dict]:
    return [t for t in trades
            if (lo is None or t["entry_date"] >= lo) and (hi is None or t["entry_date"] < hi)]


def fm(v, fmt="{:+.2f}"):
    return "—" if v is None else fmt.format(v)


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "data" / "breakout_test.json"))
    ap.add_argument("--trades", default=str(REPO / "data" / "breakout_test_trades.json"))
    ap.add_argument("--markets", default="us200,jp200,hk200")
    a = ap.parse_args()

    res = {
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M HKT"),
        "rules": {
            "direction": "long only",
            "stock_filter": "T-1 close > ma200 AND ma200_slope_20 > 0",
            "market_filter": "index T-1 close > index ma200 (date-aligned, not positional)",
            "guards": "ATR% < 6%, 20d box width < 35%",
            "stop": "3 x ATR below the fill, scanned from the entry bar",
            "target": "none; time exit at the close of the 10th session after entry",
            "breakout_entry": f"BUY STOP at R1 x {LEVEL_K}, valid 1 session, must trade "
                              f"{THROUGH:.4%} THROUGH, fill at max(open, through-level)",
            "v2_entry": "limit BUY at S1: open > S1, low touches S1, close >= S1",
            "book": f"{PT.RISK:.0%} equity at risk, {PT.MAX_SLOTS} slots, "
                    f"{PT.MAX_NOTIONAL:.0%} notional per NAME, gross <= 100%",
            "fee_per_side_charged_twice": dict(B10.FEE),
            "slip_per_side": dict(B10.SLIP),
            "stop_extra_slip": dict(B10.STOP_EXTRA),
            "portfolio_cost_scenario": PT.COST,
            "portfolio_cost_rt": {m: round(PT.cost_rt(m, PT.COST, False), 5)
                                  for m in ("us200", "jp200", "hk200")},
            "portfolio_cost_rt_stop": {m: round(PT.cost_rt(m, PT.COST, True), 5)
                                       for m in ("us200", "jp200", "hk200")},
            "benchmark": "true monthly equal-weight rebalance of the same names, less "
                         "one round-trip cost. NOT portfolio_test.py's B1 as written — "
                         "see ew_curve() for the transform('mean') defect; the B1 figure "
                         "is kept per window as ew_hold.portfolio_repoB1_asimplemented.",
        },
        "markets": {},
    }
    all_trades: list[dict] = []
    curves: dict[str, dict[str, dict[str, pd.Series]]] = {}

    for mkt in [m.strip() for m in a.markets.split(",")]:
        up = market_up(mkt)
        ib = load_safe(bars_dir(), MARKET_IDX[mkt])
        if up.empty or ib is None:
            print(f"[{mkt}] 指數缺失，跳過")
            continue
        mask = market_mask(mkt)
        members = {safe for _, safe in B10.universe(*B10.MARKETS[MARKET_OF[mkt]])}
        px: dict[str, pd.Series] = {}
        acc: dict[str, list[dict]] = {arm: [] for arm in TRADE_ARMS}
        syms = 0

        for safe in sorted(members):
            b = load_safe(BAR_DIR[mkt], safe)
            if b is None:
                continue
            syms += 1
            px[safe] = b.close
            for arm, cs in (("breakout", candidates_breakout(b, up)),
                            ("v2_pullback", candidates_v2(b, up, safe))):
                tagged = [dict(c, sym=safe, arm=arm, mkt=mkt) for c in cs]
                kept, _ = filter_candidates(tagged, mask)
                for c in kept:
                    c["net"] = net_trade(mkt, float(c["entry"]), float(c["stop"]),
                                         float(c["exit"]), str(c["why"]))
                acc[arm].extend(kept)

        ibc = ib.close.copy()                       # benchmark calendar, glitch-masked
        for d in mask.get(MARKET_IDX[mkt], set()):
            ibc = ibc[ibc.index != d]
        cal = ibc.dropna().index
        ew_full = align(ew_curve(px, cal, mkt), cal) if px else pd.Series(dtype=float)
        ew_b1 = align(ew_curve_repoB1(px, cal, mkt), cal) if px else pd.Series(dtype=float)
        curves[mkt] = {}

        span = (str(min(s.index[0] for s in px.values()).date()),
                str(max(s.index[-1] for s in px.values()).date())) if px else [None, None]
        per_mkt: dict = {"coverage": {
            "bar_dir": str(BAR_DIR[mkt]),
            "index": MARKET_IDX[mkt],
            "universe_members": len(members),
            "symbols_with_usable_bars": syms,
            "index_span": [str(cal[0].date()), str(cal[-1].date())],
            "symbol_bar_span": span,
            "glitch_masked_symbols_in_bar_dir": len(
                [s for s in mask if s in members or s in px]),
            "glitch_masked_bars_in_bar_dir": sum(
                len(v) for s, v in mask.items() if s in members or s in px),
        }, "windows": {}}

        for wname, (lo, hi) in WINDOWS.items():
            wcal = cal[cal >= lo] if lo is not None else cal
            if hi is not None:
                wcal = wcal[wcal < hi]
            arms_out: dict = {}
            daily: dict[str, pd.Series] = {}

            for arm in TRADE_ARMS:
                tl = in_window(acc[arm], lo, hi)
                curve, _ = PT.walk(tl, mkt) if tl else (None, 0)
                eq = align(curve.set_index("date").equity if curve is not None else None, wcal)
                arms_out[arm] = {"per_trade": per_trade(tl),
                                 "portfolio": portfolio_stats(eq, curve)}
                if arms_out[arm]["portfolio"]:
                    arms_out[arm]["portfolio"]["candidates_in_window"] = len(tl)
                    arms_out[arm]["portfolio"]["trades_admitted_by_book"] = count_admitted(tl, mkt)
                if eq is not None and len(eq) >= 2:
                    curves[mkt].setdefault(arm, {})[wname] = eq
                    daily[arm] = daily_log_rets(eq)

            ew_w = ew_full.reindex(wcal).dropna() if len(wcal) else pd.Series(dtype=float)
            arms_out["ew_hold"] = {
                "per_trade": None,
                "portfolio": portfolio_stats(ew_w),
                # audit reference only — portfolio_test.py's B1 as written, which
                # overstates the benchmark. Not a reporting arm.
                "portfolio_repoB1_asimplemented": portfolio_stats(
                    ew_b1.reindex(wcal).dropna() if len(wcal) else pd.Series(dtype=float))}
            if len(ew_w) >= 2:
                curves[mkt].setdefault("ew_hold", {})[wname] = ew_w
                daily["ew_hold"] = daily_log_rets(ew_w)

            head: dict = {}
            base = arms_out["ew_hold"]["portfolio"]
            for arm in TRADE_ARMS:
                ps = arms_out[arm]["portfolio"]
                if ps and base:
                    head[f"{arm}_minus_ew_hold_ppyr"] = round(ps["cagr_pct"] - base["cagr_pct"], 3)
            head.update(cagr_gap_ci(daily))
            per_mkt["windows"][wname] = {
                "window": [str(wcal[0].date()) if len(wcal) else None,
                           str(wcal[-1].date()) if len(wcal) else None],
                "sessions": int(len(wcal)),
                "arms": arms_out, "headline": head,
            }

        all_trades.extend(t for arm in TRADE_ARMS for t in acc[arm])
        traded = {arm: {t["sym"] for t in acc[arm]} for arm in TRADE_ARMS}
        per_mkt["coverage"]["zero_trade_symbols"] = {
            arm: sorted(set(px) - traded[arm]) for arm in TRADE_ARMS}
        per_mkt["coverage"]["zero_trade_symbols_count"] = {
            arm: len(set(px) - traded[arm]) for arm in TRADE_ARMS}
        res["markets"][mkt] = per_mkt
        n = per_mkt["windows"]["full"]["arms"]
        print(f"[{mkt}] {syms} 隻 / universe {len(members)} | "
              f"breakout {n['breakout']['per_trade']['n']} 筆, "
              f"v2 {n['v2_pullback']['per_trade']['n']} 筆", flush=True)

    # ---- combined: pooled per-trade + equal-capital-per-market portfolio blend
    if res["markets"]:
        by_arm = {arm: [t for t in all_trades if t["arm"] == arm] for arm in TRADE_ARMS}
        res["combined"] = {"note": (
            "Combined portfolio = equal capital per market, daily rebalanced from the same "
            "curves. Per-trade stats are pooled counts only — costs are per-market, so a "
            "single pooled net-% figure would not be meaningful; win rate and n are."),
            "windows": {}}
        for wname, (lo, hi) in WINDOWS.items():
            arms_out, daily = {}, {}
            for arm in ARMS:
                dm = {m: daily_log_rets(c[arm][wname]) for m, c in curves.items()
                      if arm in c and wname in c[arm] and len(c[arm][wname]) >= 2}
                if not dm:
                    arms_out[arm] = {"per_trade": None, "portfolio": None}
                    continue
                d = combine_daily(dm)
                daily[arm] = d
                eq = 100_000 * np.exp(d.cumsum())
                arms_out[arm] = {
                    "per_trade": ({k: v for k, v in per_trade(in_window(by_arm[arm], lo, hi)).items()
                                   if k in ("n", "symbols", "win_pct", "win_ci95")}
                                  if arm in by_arm else None),
                    "portfolio": portfolio_stats(eq)}
            head: dict = {}
            base = arms_out["ew_hold"]["portfolio"]
            for arm in TRADE_ARMS:
                ps = arms_out[arm]["portfolio"]
                if ps and base:
                    head[f"{arm}_minus_ew_hold_ppyr"] = round(ps["cagr_pct"] - base["cagr_pct"], 3)
            head.update(cagr_gap_ci(daily))
            res["combined"]["windows"][wname] = {"arms": arms_out, "headline": head}

    Path(a.out).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    Path(a.trades).write_text(json.dumps(all_trades, default=str), encoding="utf-8")

    # ---------------------------------------------------------------- report
    print("\n" + "=" * 112)
    print(f"{'market':7} {'window':10} {'arm':12} {'n':>7} {'win%':>7} {'avgNet%':>9} {'expR':>7} "
          f"{'CAGR%':>8} {'maxDD%':>8} {'Calmar':>7} {'slots':>6}")
    print("-" * 112)
    for mkt, d in res["markets"].items():
        for wname, w in d["windows"].items():
            for arm in ARMS:
                t = w["arms"][arm]["per_trade"]
                p = w["arms"][arm]["portfolio"]
                print(f"{mkt:7} {wname:10} {arm:12} {fm(t and t['n'], '{:>7}')} "
                      f"{fm(t and t.get('win_pct'), '{:>7.2f}')} "
                      f"{fm(t and t.get('avg_net_pct'), '{:>9.3f}')} "
                      f"{fm(t and t.get('expectancy_R'), '{:>7.3f}')} "
                      f"{fm(p and p['cagr_pct'])} {fm(p and p['max_dd_pct'], '{:+8.1f}')} "
                      f"{fm(p and p['calmar'], '{:>7.2f}')} {fm(p and p.get('avg_slots'), '{:>6.2f}')}")
            for arm in TRADE_ARMS:
                k, ci = f"{arm}_minus_ew_hold_ppyr", f"{arm}_minus_ew_hold_ppyr_ci95"
                if k in w["headline"]:
                    print(f"{'':7} {'':10} -> {arm} - ew_hold = {w['headline'][k]:+.2f} pp/yr "
                          f"95% CI [{w['headline'][ci][0]:+.2f}, {w['headline'][ci][1]:+.2f}]")
        print("-" * 112)
    print("\n=== 全期結論 (full window) ===")
    for mkt, d in res["markets"].items():
        h = d["windows"]["full"]["headline"]
        z = d["coverage"]["zero_trade_symbols_count"]
        print(f"  {mkt:6} breakout - ew_hold = {fm(h.get('breakout_minus_ew_hold_ppyr'))} pp/yr "
              f"| v2 - ew_hold = {fm(h.get('v2_pullback_minus_ew_hold_ppyr'))} pp/yr "
              f"| 0 交易 symbols {z}")
        for arm in TRADE_ARMS:
            p = d["windows"]["full"]["arms"][arm]["portfolio"] or {}
            if p:
                print(f"         {arm:12} {p.get('candidates_in_window')} candidates -> "
                      f"{p.get('trades_admitted_by_book')} actually traded "
                      f"(10-slot / 10%-per-name book)")
    h = res.get("combined", {}).get("windows", {}).get("full", {}).get("headline", {})
    print(f"  COMB  breakout - ew_hold = {fm(h.get('breakout_minus_ew_hold_ppyr'))} pp/yr "
          f"| v2 - ew_hold = {fm(h.get('v2_pullback_minus_ew_hold_ppyr'))} pp/yr")
    print("\n" + "=" * 112)
    print(f"provenance | bars: us200={BAR_DIR['us200']}  jp200={BAR_DIR['jp200']}  "
          f"hk200={BAR_DIR['hk200']} | per-trade cost model FEE={B10.FEE} SLIP={B10.SLIP} "
          f"STOP_EXTRA={B10.STOP_EXTRA} | book cost_rt(COST={PT.COST!r}) = "
          f"us {PT.cost_rt('us200', PT.COST, False):.4f} / jp {PT.cost_rt('jp200', PT.COST, False):.4f} "
          f"/ hk {PT.cost_rt('hk200', PT.COST, False):.4f} round trip | index: {MARKET_IDX} "
          f"| trades: {len(all_trades)} | generated {res['generated']}")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())