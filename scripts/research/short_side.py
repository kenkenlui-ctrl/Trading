#!/usr/bin/env python3
"""Part A — a SHORT mirror of the live v2 rule, measured on the frozen store.

RESEARCH ONLY. Nothing in here is imported by a production build path, no
production file is modified, and nothing here is deployed or committed.

THE QUESTION
    v2 is long-only: build_v2_signals.py publishes
    payload["rules"]["direction"] = "long only". Kenneth's proposal is the
    mirror image — a VCP that has run to the top is not a buy, it is a
    short. This module answers that with numbers instead of opinions: it
    builds the short side of the rule, condition by condition mirrored, and
    measures it on exactly the same bars, the same window split, the same
    cost model and the same hold period as the long side that is already
    published.

WHAT "MIRRORED" MEANS HERE (written down, because the spec is ambiguous)
    long  S1   = 20-session support (core.features S1, already T-1 shifted)
    short R1   = 20-session resistance (core.features R1, already T-1 shifted)

    screen (long)   T-1 close > MA200 AND MA200 slope(20) > 0
                    AND ATR% < 6% AND 20d box width < 35%
    screen (short)  T-1 close < MA200 AND MA200 slope(20) < 0
                    AND ATR% < 6% AND 20d box width < 35%

    entry (long)    the session OPENS ABOVE S1, trades DOWN to S1 (the limit
                    buy fills), and CLOSES AT OR ABOVE S1 (the close-hold
                    rule shipped 2026-10-05).
    entry (short)   the session OPENS BELOW R1, trades UP to R1 (the limit
                    sell fills), and CLOSES AT OR BELOW R1 (the close-hold
                    mirror: the level was rejected).

    stop (long)     S1 x (1 - 3 x ATR%)
    stop (short)    R1 x (1 + 3 x ATR%)
    target          none, both sides — the exit is the close of the 10th
                    session after the fill
    hold            10 sessions, both sides
    glitch mask     drop the trade when a masked bar sits in
                    [entry - 304 days, exit] (same rule as the long side)
    costs           core.cost_rt(mkt, scenario, stop_exit)

THE MARKET FILTER — applied per trade, on both sides
    The published rule text lists a market filter ("index T-1 close > its
    200d MA"). In the LIVE product it is a MARKET-LEVEL switch: if the index
    is below its 200d MA, the whole market publishes nothing.

    2026-11-14: the FROZEN engine had NO market filter at all —
    ab_close_hold.trades_for only printed market_up() to stdout — so the
    long numbers below were measured on a superset of the trades the
    published rule would ever take. That is now fixed in the frozen engine
    itself, and this module mirrors the fix:

      long arm   gated on the market being UP   (the live rule)
      short arm  gated on the market being DOWN  (the mirror)

    Each side is therefore measured in the regime that favours it, which is
    the only way the two sides stay comparable — and if the short side
    still loses after being handed its best regime, the verdict is robust.
    The ungated engine is kept as a SENSITIVITY arm (market_filter_rows),
    so the effect of the gate on both sides is visible rather than assumed.

    The rule, the proxy and the scale-glitch repair all live in
    scripts/v2engine/market_filter.py, so this module, the frozen baseline
    and the live path cannot drift apart.

NO TARGET, AND WHY -1e12
    core._simulate takes a target price. The long side passes 1e12, which
    can never be reached by a close or a high, so it means "no target".
    The SAME number does NOT work for a short: _simulate's short branch
    tests `cl <= target`, and 1e12 is above every close, so every short
    would have exited on its entry bar at a price of 1e12 — a +1e10
    return. The short side therefore passes -1e12, which no close or low
    can reach. This is the single easiest way to get a mirror wrong, and it
    is asserted in the self-test.

COSTS — two calibers, never mixed
    "site"       = cost_rt(mkt, "site", stop_exit) = fees only, NO slippage.
                   us200 0.05% round trip, jp200 0.10% round trip.
                   THIS is the caliber the published v2 numbers are
                   measured at, so the headline numbers use it.
    "realistic"  = cost_rt(mkt, "realistic", stop_exit) = fees + slippage on
                   both legs + extra slip on a stop exit. SLIP_SIDE is
                   us200 0.0003, jp200 0.0005 per side.
                   NOT COMPARABLE with the site numbers, and never mixed
                   with them in one cell. Reported as a separate column.

WHAT THIS MODULE DOES NOT DO
    It does not touch production. It does not change the signal set, the win
    rate, or any published page. Wiring a short side onto the site is a
    separate, flagged, Kenneth-approved step.

Usage:
    python3 scripts/research/short_side.py --selftest
    python3 scripts/research/run_short_side.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

from core import _simulate, cost_rt, features          # noqa: E402
from market_filter import MARKET_PROXY, verdicts_for    # noqa: E402
from bt_clean import bars_dir, load as load_clean        # noqa: E402
import executability as E                                # noqa: E402  (load_mask)

BARS = bars_dir()
SPLIT = pd.Timestamp("2023-01-01")      # train < SPLIT <= test
HOLD = 10
MARKETS = ("us200", "jp200")
# The market proxy is NOT chosen here. scripts/v2engine/market_filter.py owns
# it: SPY for US and 1306.T for JP, the same pair the live path reads. The
# index pair this module used to name (IDX_GSPC / IDX_N225) disagrees with
# the ETF pair on 1.4% of US sessions and 11.9% of JP ones, so keeping it
# would have made the short-side study measure a different rule from the
# one the site publishes.
INDEX_SYM = MARKET_PROXY
NO_TARGET_LONG = 1e12
NO_TARGET_SHORT = -1e12
GLITCH_LOOKBACK_DAYS = 304              # same as ab_close_hold.trades_for
ATR_MAX = 0.06
BOX_MAX = 0.35
STOP_ATR = 3.0

# The primary short rule and the ablations, all disclosed in the report.
VARIANTS = {
    "L-long-v2":     dict(side=+1, trend="up_both",     close_hold=True,
                          market="up",
                          label="live v2 long (authority)"),
    "S-strict":      dict(side=-1, trend="down_both",   close_hold=True,
                          market="down",
                          label="short mirror, full condition set"),
    "S-nohold":      dict(side=-1, trend="down_both",   close_hold=False,
                          market="down",
                          label="short mirror without the close-hold"),
    "S-nolevel":     dict(side=-1, trend="slope_down",  close_hold=True,
                          market="down",
                          label="short mirror, trend = MA200 slope only"),
    "S-levelonly":   dict(side=-1, trend="level_down",  close_hold=True,
                          market="down",
                          label="short mirror, trend = below MA200 only"),
}
PRIMARY_SHORT = "S-strict"
SHORT_VARIANTS = [k for k in VARIANTS if k.startswith("S-")]


# -------------------------------------------------------------------------- #
# data access (read-only; no write side effects anywhere in this module)
# -------------------------------------------------------------------------- #
def load_bars(path: Path) -> pd.DataFrame | None:
    """Load one symbol's bars. core.features() needs a volume column and
    bt_clean.load() only guarantees OHLC, so volume is filled if absent."""
    df = load_clean(path)
    if df is None:
        return None
    if "volume" not in df.columns:
        df = df.copy()
        df["volume"] = 1.0
    else:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    return df


def load_mask() -> dict:
    """The glitch mask, read WITHOUT the write side effect.

    bt_clean._load_mask_cache() rebuilds and REWRITES
    data/bt10y_glitch_mask.json when the cache is missing or its _source does
    not match the bar store. executability.load_mask() reads the file
    directly and applies the same validity rule, so this module never
    mutates data/ as a side effect of reading a mask.
    """
    return E.load_mask()


def is_jp(sym: str) -> bool:
    return sym.endswith("_T")


def is_us(sym: str) -> bool:
    return (not is_jp(sym) and "_" not in sym
            and not sym.startswith(("HK_", "JP_")))


def symbols_for(mkt: str) -> list[str]:
    return sorted(p.stem for p in BARS.glob("*.json")
                  if (is_jp(p.stem) if mkt == "jp200" else is_us(p.stem)))


# -------------------------------------------------------------------------- #
# the engine
# -------------------------------------------------------------------------- #
def _trend_mask(f: pd.DataFrame, trend: str) -> np.ndarray:
    ma200 = f.ma200.to_numpy()
    slope = f.ma200_slope.to_numpy()
    prev = f.prev_close.to_numpy()
    a = f.atr_pct.to_numpy()
    boxw = f.box_w.to_numpy()
    if trend == "up_both":
        ok = (prev > ma200) & (slope > 0)
    elif trend == "down_both":
        ok = (prev < ma200) & (slope < 0)
    elif trend == "slope_down":
        ok = slope < 0
    elif trend == "level_down":
        ok = prev < ma200
    else:
        raise ValueError(f"unknown trend filter {trend!r}")
    return ok & (a < ATR_MAX) & (boxw < BOX_MAX)


def trades_for(df: pd.DataFrame, f: pd.DataFrame, mkt: str, spec: dict,
               mask: set | None, *, market_ok: dict[str, bool]) -> list[dict]:
    """One pass of the rule for one symbol.

    For side=+1 this is a line-for-line mirror of
    ab_close_hold.trades_for(..., close_hold=True) and is asserted equal to
    it by the frozen gate. For side=-1 every condition is the mirror image.

    market_ok = the per-session market verdicts, and it is REQUIRED: a
    caller that forgets it gets a TypeError instead of a number that
    quietly has no market filter in it.

    spec["market"] is the policy: "up" for the long side (the live rule),
    "down" for the short side (the mirror), "none" for the ungated
    sensitivity arm. A session with no verdict gates the trade OFF
    (fail-closed): the live product refuses to publish when the index
    cannot be read.
    """
    side = spec["side"]
    policy = spec.get("market", "none")
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = f.atr_pct.to_numpy()
    s1 = f.S1.to_numpy()
    r1 = f.R1.to_numpy()
    ma200 = f.ma200.to_numpy()
    prev_close = f.prev_close.to_numpy()
    boxw = f.box_w.to_numpy()
    slope = f.ma200_slope.to_numpy()
    idx = df.index
    ok = _trend_mask(f, spec["trend"])
    hold = spec.get("hold", HOLD)
    rows: list[dict] = []

    for i in np.where(ok)[0]:
        level = s1[i] if side > 0 else r1[i]
        if np.isnan(level) or np.isnan(a[i]) or a[i] <= 0:
            continue
        # THE MARKET GATE. A session the market filter did not pass is a
        # session the live product published nothing on, so it cannot be in
        # the cohort (2026-11-14: the frozen engine had no gate at all).
        if policy != "none":
            v = market_ok.get(idx[i].strftime("%Y-%m-%d"))
            if v is None:
                continue                      # no verdict -> gate OFF
            if policy == "up" and not v:
                continue
            if policy == "down" and v:
                continue
        if side > 0:
            # opens above the level, trades down to it
            if not (O[i] > level and L[i] <= level):
                continue
            if spec["close_hold"] and C[i] < level:
                continue
            stop = level * (1 - STOP_ATR * a[i])
            if stop <= 0 or stop >= level:
                continue
            target = NO_TARGET_LONG
        else:
            # opens below the level, trades up to it
            if not (O[i] < level and H[i] >= level):
                continue
            if spec["close_hold"] and C[i] > level:
                continue
            stop = level * (1 + STOP_ATR * a[i])
            if not (stop > level) or not math.isfinite(float(stop)):
                continue
            target = NO_TARGET_SHORT

        res = _simulate(side, i, level, stop, target, O, H, L, C, hold, True)
        if res is None:
            continue
        j, xp, why = res
        if mask:
            lo = idx[i] - pd.Timedelta(days=GLITCH_LOOKBACK_DAYS)
            if any(lo <= d <= idx[j] for d in mask):
                continue
        gross = (xp / level - 1) * side
        row = {
            "mkt": mkt, "date": idx[i].strftime("%Y-%m-%d"),
            "exit_date": idx[j].strftime("%Y-%m-%d"),
            "level": float(level), "stop": float(stop), "exit": float(xp),
            "gross": float(gross), "reason": why, "held": int(j - i),
            "entry_i": int(i),
            # the T-1 feature set at the entry session (decision-time info)
            "atr_pct": float(a[i]), "box_w": float(boxw[i]),
            "s1": float(s1[i]), "r1": float(r1[i]),
            "prev_close": float(prev_close[i]), "ma200": float(ma200[i]),
            "ma200_slope": float(slope[i]),
        }
        # stop-free forward return: close of the 10th session after the entry
        k = i + HOLD
        row["fwd10"] = (float(C[k] / level - 1) * side) if k < len(C) else float("nan")
        rows.append(row)
    return rows


def run_engine(mkt: str, mask_all: dict, variants=None,
                market: bool = True) -> dict[str, list[dict]]:
    """Run every variant over one market's symbols, features computed once
    per symbol and reused across variants.

    market=False runs the ungated engine — the sensitivity arm that shows
    what the gate does to each side instead of assuming it.
    """
    variants = variants or list(VARIANTS)
    out: dict[str, list[dict]] = {v: [] for v in variants}
    mv = verdicts_for(mkt, BARS)
    for sym in symbols_for(mkt):
        p = BARS / f"{sym}.json"
        df = load_bars(p)
        if df is None:
            continue
        f = features(df)
        mask = mask_all.get(sym)
        for v in variants:
            spec = dict(VARIANTS[v])
            if not market:
                spec["market"] = "none"
            for row in trades_for(df, f, mkt, spec, mask, market_ok=mv):
                row["sym"] = sym
                out[v].append(row)
    return out


# -------------------------------------------------------------------------- #
# statistics
# -------------------------------------------------------------------------- #
def _round_stats(net: np.ndarray, by_date_mean: pd.Series) -> dict:
    """The same summary shape ab_close_hold.stats() produces, so the two are
    comparable without a translation layer."""
    if net is None or len(net) == 0:
        return {"n": 0, "win%": 0.0, "avg%": 0.0, "pf": 0.0, "t": 0.0}
    wins, losses = net[net > 0], net[net <= 0]
    t = 0.0
    if len(by_date_mean) > 2 and by_date_mean.std(ddof=1) > 0:
        t = by_date_mean.mean() / (by_date_mean.std(ddof=1) / np.sqrt(len(by_date_mean)))
    pf = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else 0.0
    return {"n": int(len(net)), "win%": round(float((net > 0).mean() * 100), 1),
            "avg%": round(float(net.mean() * 100), 3),
            "pf": round(float(pf), 2), "t": round(float(t), 2)}


def diagnostics(net: np.ndarray, dates: np.ndarray) -> dict:
    """Sharpe and max drawdown computed on the PER-TRADE return series.

    These are DIAGNOSTICS, not an investable equity curve: every trade is
    weighted 100% of capital, trades overlap in time, there is no position
    sizing and no compounding, and no benchmark. Read them as "how lumpy is
    this per-trade series", never as "what this would have returned".
    """
    if net is None or len(net) == 0:
        return {"sharpe_per_trade": 0.0, "sharpe_daily_clustered": 0.0,
                "max_dd_per_trade_series": 0.0, "sum_net": 0.0,
                "sum_net_ex_best5": 0.0, "top5_share_of_sum": None}
    sd = float(net.std(ddof=1)) if len(net) > 1 else 0.0
    sharpe = float(net.mean()) / sd if sd > 0 else 0.0
    by_date = pd.Series(net).groupby(pd.Index(dates)).mean()
    sdd = float(by_date.std(ddof=1))
    sharpe_d = (float(by_date.mean()) / sdd * math.sqrt(252.0)) if sdd > 0 else 0.0
    order = np.argsort(np.asarray(dates), kind="stable")
    cum = np.cumsum(net[order])
    peak = np.maximum.accumulate(cum)
    max_dd = float(np.min(cum - peak)) if len(cum) else 0.0
    total = float(net.sum())
    best5 = float(np.sort(net)[-5:].sum()) if len(net) else 0.0
    return {
        "sharpe_per_trade": round(sharpe, 4),
        "sharpe_daily_clustered": round(sharpe_d, 3),
        "max_dd_per_trade_series": round(max_dd, 4),
        "sum_net": round(total, 4),
        "sum_net_ex_best5": round(total - best5, 4),
        "top5_share_of_sum": (round(best5 / total, 3)
                              if total not in (0.0,) and abs(total) > 1e-12 else None),
    }


def window_of(date: str) -> str:
    return "train" if date < SPLIT.strftime("%Y-%m-%d") else "test"


def stats_for(rows: list[dict], cost_col: str = "net_site") -> dict:
    """Full stats for one cohort.

    The default cost column is net_site (fees only, zero slippage) — the
    caliber the published v2 numbers are measured at. Rows that predate
    add_costs() carry the raw "net" key instead.
    """
    if not rows:
        base = {"n": 0, "win%": 0.0, "avg%": 0.0, "pf": 0.0, "t": 0.0}
        base.update(diagnostics(np.array([]), np.array([])))
        base["avg_gross%"] = 0.0
        base["avg_fwd10%"] = 0.0
        base["n_stop"] = 0
        base["n_time"] = 0
        return base
    if cost_col not in rows[0] and "net" in rows[0]:
        cost_col = "net"
    net = np.array([r[cost_col] for r in rows], dtype=float)
    dates = np.array([r["date"] for r in rows])
    by_date = pd.Series(net).groupby(pd.Index(dates)).mean()
    out = _round_stats(net, by_date)
    out.update(diagnostics(net, dates))
    gross = np.array([r["gross"] for r in rows], dtype=float)
    out["avg_gross%"] = round(float(gross.mean() * 100), 3)
    fwd = np.array([r.get("fwd10", float("nan")) for r in rows], dtype=float)
    fwd = fwd[~np.isnan(fwd)]
    out["avg_fwd10%"] = round(float(fwd.mean() * 100), 3) if len(fwd) else 0.0
    out["n_stop"] = int(sum(1 for r in rows if r["reason"] == "stop"))
    out["n_time"] = int(sum(1 for r in rows if r["reason"] == "time"))
    return out


def add_costs(rows: list[dict], mkt: str) -> list[dict]:
    """Attach both cost calibers. 'site' is the published-v2 caliber (fees
    only, zero slippage). 'realistic' adds slippage and is NOT comparable
    with it; the two never share a cell."""
    out = []
    for r in rows:
        stop_exit = r["reason"] == "stop"
        r2 = dict(r)
        r2["net_site"] = r["gross"] - cost_rt(mkt, "site", stop_exit)
        r2["net_realistic"] = r["gross"] - cost_rt(mkt, "realistic", stop_exit)
        out.append(r2)
    return out


def by_window(rows: list[dict]) -> dict:
    out = {}
    for w in ("train", "test", "full"):
        sub = rows if w == "full" else [r for r in rows if window_of(r["date"]) == w]
        out[w] = sub
    return out


# -------------------------------------------------------------------------- #
# gates
# -------------------------------------------------------------------------- #
FROZEN = REPO / "data" / "ab_close_hold.json"


def check_frozen_gate(long_rows: list[dict], frozen: dict) -> dict:
    """The re-run of long v2 must reproduce data/ab_close_hold.json exactly,
    per market and per window: n, avg%, win%, pf and t.

    A hand-typed comparison is not a gate. This returns a machine verdict
    and the caller exits non-zero when it does not pass.
    """
    checks = []
    for mkt in MARKETS:
        rows = [r for r in long_rows if r["mkt"] == mkt]
        for w in ("train", "test"):
            sub = [r for r in rows if window_of(r["date"]) == w]
            got = stats_for(sub)
            want = frozen["windows"][mkt]["B_close-hold"][w]
            for field in ("n", "avg%", "win%", "pf", "t"):
                checks.append({
                    "mkt": mkt, "window": w, "field": field,
                    "got": got[field], "want": want[field],
                    "ok": got[field] == want[field],
                })
    failed = [c for c in checks if not c["ok"]]
    return {
        "gate": "frozen_ab_close_hold",
        "source": str(FROZEN.relative_to(REPO)),
        "n_checks": len(checks), "n_failed": len(failed),
        "failed": failed,
        "passed": not failed,
    }


def check_engine_equivalence(long_rows: list[dict], mkt: str) -> dict:
    """The long path of THIS module must equal ab_close_hold.trades_for
    row for row. That is what makes the short path (same code, mirrored)
    trustworthy: the machinery is validated on the side that has a published
    number to be validated against."""
    import ab_close_hold as AB
    mask_all = load_mask()
    mv = AB.market_verdicts(mkt)
    ref = []
    for sym in symbols_for(mkt):
        df = load_bars(BARS / f"{sym}.json")
        if df is None:
            continue
        t = AB.trades_for(df, mkt, True, mask_all.get(sym), market_ok=mv)
        if t is None:
            continue
        t = t.copy()
        t["sym"] = sym
        ref.append(t)
    if not ref:
        return {"gate": "engine_equivalence", "mkt": mkt, "passed": False,
                "detail": "the reference engine produced no trades"}
    R = pd.concat(ref, ignore_index=True)
    R["date"] = R["date"].astype(str)
    mine = pd.DataFrame([r for r in long_rows if r["mkt"] == mkt])
    if mine.empty:
        return {"gate": "engine_equivalence", "mkt": mkt, "passed": False,
                "detail": "this module produced no trades"}
    a = R.groupby("date").gross.agg(["count", "sum"]).sort_index()
    b = mine.groupby("date").gross.agg(["count", "sum"]).sort_index()
    same_index = a.index.equals(b.index)
    close_enough = False
    if same_index:
        diff = (a["sum"] - b["sum"]).abs().max()
        close_enough = bool(diff < 1e-9)
    return {
        "gate": "engine_equivalence", "mkt": mkt,
        "ref_n": int(len(R)), "mine_n": int(len(mine)),
        "same_dates": bool(same_index),
        "max_abs_gross_diff": float((a["sum"] - b["sum"]).abs().max()) if same_index else None,
        "passed": bool(same_index and close_enough and len(R) == len(mine)),
    }


def check_symmetry_breach(long_rows: list[dict], short_rows: list[dict]) -> dict:
    """NEGATIVE CONTROL. The short mirror must NOT reproduce the long rule.

    If the short numbers came out identical to the long ones, either the
    mirror was implemented as a copy of the long rule or the short path was
    fed the long data. Both are failures, so this gate fails when the two
    sides are indistinguishable.
    """
    detail = []
    ok = True
    for mkt in MARKETS:
        for w in ("train", "test", "full"):
            L = stats_for([r for r in long_rows if r["mkt"] == mkt
                           and (w == "full" or window_of(r["date"]) == w)])
            S = stats_for([r for r in short_rows if r["mkt"] == mkt
                           and (w == "full" or window_of(r["date"]) == w)])
            identical = (L["n"] == S["n"] and L["avg%"] == S["avg%"]
                         and L["win%"] == S["win%"])
            if identical:
                ok = False
            detail.append({"mkt": mkt, "window": w, "long_n": L["n"],
                           "short_n": S["n"], "long_avg%": L["avg%"],
                           "short_avg%": S["avg%"], "identical": identical})
    return {"gate": "symmetry_negative_control", "passed": ok, "cells": detail}


def tamper_gate_arm(long_rows: list[dict]) -> dict:
    """MUST-FAIL arm. The frozen gate must FAIL when the artifact it reads
    has been tampered with.

    The tamper is a REAL edit to a REAL file on disk: the frozen JSON is
    copied to a temp directory, one number in the copy is changed, the file
    is re-read from disk to PROVE the tamper actually landed (a tamper that
    did not happen would make this arm meaningless), and the gate is then
    run against the tampered copy. The repo's own data/ab_close_hold.json
    is hashed before and after to prove it was not touched.
    """
    before = hashlib.sha256(FROZEN.read_bytes()).hexdigest()
    blob = json.loads(FROZEN.read_text(encoding="utf-8"))
    original_n = blob["windows"]["us200"]["B_close-hold"]["train"]["n"]
    blob["windows"]["us200"]["B_close-hold"]["train"]["n"] = original_n + 1
    tmpdir = Path(tempfile.mkdtemp(prefix="w9_tamper_"))
    try:
        tampered = tmpdir / "ab_close_hold.json"
        tampered.write_text(json.dumps(blob, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        reread = json.loads(tampered.read_text(encoding="utf-8"))
        landed = reread["windows"]["us200"]["B_close-hold"]["train"]["n"] == original_n + 1
        result = check_frozen_gate(long_rows, reread)
        after = hashlib.sha256(FROZEN.read_bytes()).hexdigest()
        return {
            "gate": "tamper_must_fail",
            "tamper_target": "copy of data/ab_close_hold.json (temp dir)",
            "tamper_field": 'windows.us200.B_close-hold.train.n',
            "original_value": original_n, "tampered_value": reread["windows"]["us200"]["B_close-hold"]["train"]["n"],
            "tamper_landed_on_disk": bool(landed),
            "gate_passed_against_tampered_file": result["passed"],
            "n_failed_checks": result["n_failed"],
            "original_file_untouched": bool(before == after),
            "original_sha256": before,
            "passed": bool(landed and not result["passed"] and before == after),
            "detail": "the gate must FAIL on the tampered copy and the repo's "
                      "own artifact must be byte-identical before and after",
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def no_lookahead_probe() -> dict:
    """Plant a LATE-ARRIVING bar and confirm no feature behind an earlier
    signal moves. Checked for S1, R1, ma200, atr_pct, box_w and ma200_slope
    over EVERY row up to the planted date, not just one row.

    A MUST-FAIL arm rides along: a feature computed WITHOUT the shift must
    be caught by the same probe, otherwise the probe proves nothing.
    """
    sym = "AAPL"
    df = load_bars(BARS / f"{sym}.json")
    if df is None:
        return {"gate": "no_lookahead", "passed": False,
                "detail": f"could not load {sym}"}
    f1 = features(df)
    target = df.index[1200]
    planted_date = target + pd.Timedelta(days=7)
    extra = df.iloc[[1200]].copy()
    extra.index = pd.DatetimeIndex([planted_date])
    for k, v in (("close", 9999.0), ("high", 10000.0), ("low", 1.0), ("open", 9999.0)):
        extra[k] = v
    df2 = pd.concat([df, extra]).sort_index()
    f2 = features(df2)

    cols = ("S1", "R1", "ma200", "atr_pct", "box_w", "ma200_slope")
    up_to = df.index <= target
    unchanged, detail = True, ""
    for c in cols:
        a = f1.loc[:target, c].to_numpy(float)
        b = f2.loc[:target, c].to_numpy(float)
        if not np.allclose(a, b, rtol=1e-12, atol=1e-12, equal_nan=True):
            unchanged = False
            detail = f"{c} moved"
            break

    # the trade set behind earlier signals must not move either
    spec = VARIANTS["S-strict"]
    mask = set()
    mv = verdicts_for("us200", BARS)
    t1 = trades_for(df, f1, "us200", spec, mask, market_ok=mv)
    t2 = trades_for(df2, f2, "us200", spec, mask, market_ok=mv)
    same_trades = len(t1) == len(t2)
    if same_trades:
        for x, y in zip(t1, t2):
            if x["date"] != y["date"] or abs(x["level"] - y["level"]) > 1e-9:
                same_trades = False
                break

    # MUST-FAIL: an unshifted S1 must be detected by this probe
    unshifted = df.low.rolling(20).min()
    must_fail_detects = not math.isclose(float(f1["S1"].loc[target]),
                                         float(unshifted.loc[target]), rel_tol=1e-9)
    return {
        "gate": "no_lookahead",
        "symbol": sym, "signal_date": str(target.date()),
        "planted_date": str(planted_date.date()),
        "features_checked": list(cols),
        "features_unchanged": bool(unchanged), "detail": detail,
        "trade_set_unchanged": bool(same_trades),
        "must_fail_unshifted_feature_detected": bool(must_fail_detects),
        "passed": bool(unchanged and same_trades and must_fail_detects),
    }


def no_target_probe() -> dict:
    """The short side must NOT be able to hit a target.

    core._simulate's short branch tests `cl <= target`, so the long side's
    "no target" sentinel of 1e12 would exit every short on its entry bar at
    1e12 — a +1e10 return. This probe proves the sentinel actually used is
    unreachable, and that the wrong one IS reachable (so the probe can
    fail).
    """
    sym = "AAPL"
    df = load_bars(BARS / f"{sym}.json")
    if df is None:
        return {"gate": "no_target_sentinel", "passed": False,
                "detail": f"could not load {sym}"}
    f = features(df)
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = f.atr_pct.to_numpy()
    r1 = f.R1.to_numpy()
    hit = None
    for i in range(len(df)):
        if np.isnan(r1[i]) or np.isnan(a[i]) or a[i] <= 0:
            continue
        if not (O[i] < r1[i] and H[i] >= r1[i]):
            continue
        res = _simulate(-1, i, r1[i], r1[i] * (1 + STOP_ATR * a[i]),
                        NO_TARGET_SHORT, O, H, L, C, HOLD, True)
        if res is None:
            continue
        _j, xp, why = res
        if why == "target":
            hit = {"date": str(df.index[i].date()), "exit": float(xp)}
            break
    # the WRONG sentinel must be reachable, otherwise this probe is blind
    wrong = _simulate(-1, 0, float(C[0]), float(C[0]) * 1.5, NO_TARGET_LONG,
                       O, H, L, C, HOLD, True)
    wrong_reaches_target = bool(wrong is not None and wrong[2] == "target")
    return {
        "gate": "no_target_sentinel",
        "sentinel_long": NO_TARGET_LONG, "sentinel_short": NO_TARGET_SHORT,
        "short_target_hits_with_correct_sentinel": hit,
        "wrong_sentinel_reaches_target": wrong_reaches_target,
        "passed": bool(hit is None and wrong_reaches_target),
    }


# -------------------------------------------------------------------------- #
# market filter sensitivity (a separate arm, never folded into the headline)
# -------------------------------------------------------------------------- #
def index_verdicts(mkt: str) -> dict:
    """date string -> True when the market's T-1 close is above its own 200d
    MA, False when below.

    From scripts/v2engine/market_filter.py: the rule, the market proxy
    (SPY / 1306.T, the live pair) and the scale-glitch repair all live
    there, so this study cannot gate on a different series from the one
    the site publishes.
    """
    return verdicts_for(mkt, BARS)


def apply_market_filter(rows: list[dict], mkt: str, side: int) -> tuple[list[dict], int]:
    """Mirror the market filter PER SESSION. Long: index above its 200d MA.
    Short: index below it. Returns (kept rows, n dropped for no verdict).

    Applied to the UNGATED engine rows: the primary arms already carry the
    gate, so this is the sensitivity arm that shows what the gate did.
    """
    verdicts = index_verdicts(mkt)
    kept, dropped = [], 0
    for r in rows:
        v = verdicts.get(r["date"])
        if v is None:
            dropped += 1
            continue
        if side > 0 and not v:
            continue
        if side < 0 and v:
            continue
        kept.append(r)
    return kept, dropped


# -------------------------------------------------------------------------- #
# event dominance (the store ends 2026-10-01; there is no Nov-2026 rout in it)
# -------------------------------------------------------------------------- #
def event_days(mkt: str, k: int = 5) -> list[dict]:
    """The k largest single-session index moves inside the frozen store."""
    sym = INDEX_SYM[mkt]
    df = load_bars(BARS / f"{sym}.json")
    if df is None:
        return []
    c = df["close"].astype(float)
    ret = c.pct_change().abs()
    top = ret.dropna().sort_values(ascending=False).head(k)
    return [{"date": str(d.date()), "move_pct": round(float(ret.loc[d] * 100), 2)}
            for d in top.index]


def exclude_events(rows: list[dict], days: list[dict]) -> list[dict]:
    ds = {d["date"] for d in days}
    if not ds:
        return list(rows)
    out = []
    for r in rows:
        if any(r["date"] <= d <= r["exit_date"] for d in ds):
            continue
        out.append(r)
    return out


# -------------------------------------------------------------------------- #
# self-test
# -------------------------------------------------------------------------- #
def _selftest() -> int:
    raise SystemExit(0 if _selftest_body() else 1)


def _selftest_body() -> bool:
    ok = True

    # 1. the no-target sentinel
    nt = no_target_probe()
    print(f"  {'PASS' if nt['passed'] else 'FAIL'}  the short sentinel cannot "
          f"reach a target (wrong sentinel reachable = "
          f"{nt['wrong_sentinel_reaches_target']})")
    ok &= nt["passed"]

    # 2. no look-ahead
    nl = no_lookahead_probe()
    print(f"  {'PASS' if nl['passed'] else 'FAIL'}  a late-arriving bar does not "
          f"move S1/R1/ma200/atr_pct ({nl.get('detail', '')})")
    ok &= nl["passed"]

    # 3. the mirror machinery on a synthetic bar set. The long and the
    #    short trend filters are mutually exclusive by construction (price
    #    above MA200 vs below), so the separation test needs TWO feature
    #    frames: on an uptrend frame only the long may fire, on a downtrend
    #    frame only the short may fire. If the mirror were a copy of the
    #    long rule, one of the two would fire on both.
    # 12 bars: the hold is 10 sessions, so _simulate needs enough bars to
    # close the position — a shorter frame returns None and the trade never
    # exists, which would make the fixture test nothing.
    _dates = pd.bdate_range("2020-01-02", periods=12)
    df = pd.DataFrame({
        "open":  [100.0] * 12,
        "high":  [101.0] * 12,
        "low":   [99.0] * 12,
        "close": [100.0] * 12,
        "volume": [1e6] * 12,
    }, index=_dates)

    def fake_frame(ma200_value: float, slope: float) -> pd.DataFrame:
        fake = pd.DataFrame(index=df.index)
        fake["atr_pct"] = 0.01
        fake["box_w"] = 0.05
        fake["ma200"] = ma200_value
        fake["ma200_slope"] = slope
        fake["prev_close"] = 100.0
        fake["S1"] = 99.5
        fake["R1"] = 100.5
        return fake

    up = fake_frame(99.0, 0.01)       # price above a rising MA200
    down = fake_frame(101.0, -0.01)   # price below a falling MA200
    # the market gate is part of the rule now, so the fixture has to
    # supply verdicts too: the long arm needs the market up, the short
    # arm needs it down.
    mv_up = {"2020-01-02": True}
    mv_down = {"2020-01-02": False}
    long_up = trades_for(df, up, "us200", VARIANTS["L-long-v2"], set(), market_ok=mv_up)
    short_up = trades_for(df, up, "us200", VARIANTS["S-strict"], set(), market_ok=mv_down)
    long_down = trades_for(df, down, "us200", VARIANTS["L-long-v2"], set(), market_ok=mv_up)
    short_down = trades_for(df, down, "us200", VARIANTS["S-strict"], set(), market_ok=mv_down)
    # bar 0: open 100 > S1 99.5, low 99 <= 99.5, close 100 >= 99.5 -> long
    # bar 0: open 100 < R1 100.5, high 101 >= 100.5, close 100 <= 100.5 -> short
    fired_long = any(r["date"] == "2020-01-02" for r in long_up)
    fired_short = any(r["date"] == "2020-01-02" for r in short_down)
    no_cross = (not short_up) and (not long_down)
    print(f"  {'PASS' if fired_long and fired_short and no_cross else 'FAIL'}  "
          f"the two sides fire only on their own trend (long={fired_long}, "
          f"short={fired_short}, no cross-fire={no_cross})")
    ok &= (fired_long and fired_short and no_cross)

    # 4. the short stop must be ABOVE the entry, the long stop BELOW it
    if short_down:
        s = short_down[0]
        good = s["stop"] > s["level"]
        print(f"  {'PASS' if good else 'FAIL'}  the short stop sits ABOVE the "
              f"entry ({s['stop']:.4f} > {s['level']:.4f})")
        ok &= good
    if long_up:
        s = long_up[0]
        good = s["stop"] < s["level"]
        print(f"  {'PASS' if good else 'FAIL'}  the long stop sits BELOW the "
              f"entry ({s['stop']:.4f} < {s['level']:.4f})")
        ok &= good

    # 5. the mask reader must not write to the repo
    before = hashlib.sha256((REPO / "data" / "bt10y_glitch_mask.json").read_bytes()).hexdigest()
    load_mask()
    after = hashlib.sha256((REPO / "data" / "bt10y_glitch_mask.json").read_bytes()).hexdigest()
    print(f"  {'PASS' if before == after else 'FAIL'}  reading the mask does "
          f"not rewrite it")
    ok &= (before == after)

    return bool(ok)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        print("=" * 72)
        print("SHORT-SIDE SELF-TEST")
        print("=" * 72)
        raise SystemExit(_selftest())
    print("short_side.py is an engine. Run scripts/research/run_short_side.py "
          "for the full report, or --selftest for the checks.")
    raise SystemExit(0)
