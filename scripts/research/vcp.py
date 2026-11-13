#!/usr/bin/env python3
"""E1 / E2 / E3 — the Minervini VCP experiments. RESEARCH ONLY.

WHAT THIS IS
    Kenneth supplied a third-party XS (Taiwan XQ) alert script that
    implements Minervini's VCP (Volatility Contraction Pattern) entry, and
    asked three separate questions. This module answers all three with
    numbers, on the same frozen 10-year store the rest of this research
    uses, and changes nothing in production.

      E1  the script's own rule — a VCP breakout bought long — standalone.
      E2  the short side. The script has NO short version; Minervini's real
          short rule is a FAILED breakout or a breakdown, not "sell the top".
          Both short definitions here are OUR proposals, not Minervini's.
      E3  the highest-value question: used as a FILTER on the signals v2
          already publishes. No new strategy claim, only "does this label
          separate good trades from bad ones".

THE XS SCRIPT, TRANSLATED (Kenneth's A0 spec, reproduced verbatim)
    XS  Highest(X[k], n)  = the max over n bars starting k bars back,
                             EXCLUDING the current bar.
    XS  CurrentBar >= 260 = at least 260 bars of history.

      ma50   = Close.rolling(50).mean()
      ma150  = Close.rolling(150).mean()
      ma200  = Close.rolling(200).mean()
      low52  = Low.rolling(250).min()
      high52 = High.rolling(250).max()
      trend  = (Close > ma50) & (Close > ma150) & (Close > ma200)
            & (ma150 > ma200)
            & (ma200 > ma200.shift(20))
            & (ma50 > ma150) & (ma50 > ma200)
            & (Close > ma50)
            & (Close >= low52 * 1.3)
            & (Close >= high52 * (1 - 0.25))

      h3 = High.shift(1).rolling(25).max()      # Highest(High[1], 25)
      l3 = Low.shift(1).rolling(25).min()
      h2 = High.shift(26).rolling(25).max()
      l2 = Low.shift(26).rolling(25).min()
      h1 = High.shift(51).rolling(25).max()
      l1 = Low.shift(51).rolling(25).min()
      d1 = (h1-l1)/h1*100 ; d2 = (h2-l2)/h2*100 ; d3 = (h3-l3)/h3*100
      contract = (d1 >= 10) & (d1 > d2) & (d2 > d3) & (d3 <= 10)

      avgVol10 = Volume.shift(1).rolling(10).mean()
      avgVol50 = Volume.shift(1).rolling(50).mean()
      volDry   = (avgVol50 > 0) & (avgVol10 / avgVol50 < 0.7)

      pivot     = h3
      breakout  = (Close > pivot) & (Close.shift(1) <= pivot.shift(1))
      volume_ok = Volume > avgVol50 * 1.5

      stockROC250 = Close/Close.shift(250) - 1
      indexROC250 = the market index / index.shift(250) - 1
      rs_ok       = stockROC250 > indexROC250 + 0

    Index: US = IDX_GSPC.json (S&P 500), JP = IDX_N225.json. The original
    script used TSE.TW (Taiwan); the market index is swapped, nothing else.

    The three contraction parameterisations (Kenneth A2.2 — the script's own
    10%/10% is LOOSER than Minervini's canonical 25% -> 10% -> 5%):
      wide     the script as written: d1 >= 10, d1 > d2 > d3, d3 <= 10
      typical  the canonical shape:    d1 >= 20, d1 > d2 > d3,
                                       d2 >= 8,  d3 <= 8
      strict   the canonical shape, tight: d1 >= 25, d1 > d2 > d3,
                                       d2 >= 10, d3 <= 5
    "typical" and "strict" are PROPOSALS (未批) — the canonical percentages
    do not map onto a boolean rule without a choice, and every choice is
    reported rather than only the best one.

TWO THINGS THE XS SCRIPT DOES NOT HAVE, AND WHAT WAS DONE INSTEAD
    stop   the script has none (its own author says so). The site
           convention is used: 3 x ATR%, with core.features' atr_pct
           (T-1 by construction), measured from the entry price.
    borrow the script is long-only, so it has no borrow cost. The short
           legs (E2) are re-run at borrow = 0% / 10% / 30% annualised,
           charged as rate * held_sessions / 252.

WHAT IS DISCLOSED ABOUT THE SOURCE
    * "Three equal-length segments is an approximation" (the script's own
      author). Real VCP segments have different lengths.
    * "Breakouts can fail; always set your own stop." The script has no
      stop; a 3x ATR stop is added here and disclosed as an addition.
    * The J Law extra conditions could not be obtained, so they are NOT
      here. Only the Minervini original is implemented. Any claim about
      "the J Law edge" is therefore NOT supported by this module.
    * The script's settotalbar(400)/setbackbar(400) limit the XS scan to
      the last 400 bars. This module uses the full 10-year store.

THE DECISION BAR (why E3 is evaluated one bar before the entry)
    A v2 signal for session i is published at the T-1 close and filled
    during session i. The information available at publication is
    everything up to and including bar i-1. E3 therefore evaluates the
    whole VCP feature set at bar i-1, which is the same information set v2
    itself uses (core.features is shifted by one). Evaluating the VCP at
    bar i would use the entry session's own close — a look-ahead that no
    live filter could ever have. The self-test proves the E3 flags do not
    move when the entry bar's OHLC is mutated.

NOTHING HERE IS COMMITTED, PUSHED OR DEPLOYED.
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

from core import cost_rt, features            # noqa: E402
import short_side as SS                        # noqa: E402

BARS = SS.BARS
SPLIT = SS.SPLIT
HOLD = SS.HOLD
MARKETS = SS.MARKETS
INDEX_SYM = SS.INDEX_SYM
STOP_ATR = 3.0
SEG_LEN = 25
BARS_MIN = 260                 # XS CurrentBar >= 260

# The liquidity gate. The script's own MinVol = 500 is in TAIWANESE "lots"
# (張), a unit that does not exist for US/JP shares, so it is NOT copied.
# Two versions are reported: no floor at all, and a per-market floor that
# is a PROPOSAL (未批).
LIQUIDITY_PROPOSAL = {"us200": 1_000_000.0, "jp200": 500_000.0}

# The borrow fee for the short legs: 0 / 10% / 30% annualised.
BORROW_RATES = (0.0, 0.10, 0.30)

CONTRACT_SETS = ("wide", "typical", "strict")

# The "no Nov-2026 rout in the data" arm. The frozen store ends 2026-10-01,
# so this window is empty; the arm is implemented so the claim is testable
# rather than asserted, and the report states that it changes nothing.
NOV2026_START = "2026-11-01"
NOV2026_END = "2026-12-31"


# -------------------------------------------------------------------------- #
# A0 — the XS script, translated
# -------------------------------------------------------------------------- #
def vcp_features(df: pd.DataFrame, index_roc: dict | None = None,
                  atr_pct: pd.Series | None = None) -> pd.DataFrame:
    """Every feature the XS script needs, in pandas, exactly as A0 states.

    atr_pct may be passed in when core.features has already been computed
    for this symbol — it is the same series either way, and computing it
    twice per symbol is pure waste."""
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    lo = df["low"].astype(float)
    v = df["volume"].astype(float)

    f = pd.DataFrame(index=df.index)
    f["close"] = c
    f["ma50"] = c.rolling(50).mean()
    f["ma150"] = c.rolling(150).mean()
    f["ma200"] = c.rolling(200).mean()
    f["low52"] = lo.rolling(250).min()
    f["high52"] = h.rolling(250).max()

    # Trend Template. "trend_core" is Minervini's eight price/MA conditions
    # on their own; "at_top" and "above_low52" are the two range conditions
    # the script adds; "trend_full" is the script's complete template.
    f["trend_core"] = ((c > f["ma50"]) & (c > f["ma150"]) & (c > f["ma200"]) &
                       (f["ma150"] > f["ma200"]) &
                       (f["ma200"] > f["ma200"].shift(20)) &
                       (f["ma50"] > f["ma150"]) & (f["ma50"] > f["ma200"]))
    f["at_top"] = c >= f["high52"] * (1 - 0.25)
    f["above_low52"] = c >= f["low52"] * 1.3
    f["trend_full"] = f["trend_core"] & f["above_low52"] & f["at_top"]

    # three contraction segments
    f["h3"] = h.shift(1).rolling(SEG_LEN).max()
    f["l3"] = lo.shift(1).rolling(SEG_LEN).min()
    f["h2"] = h.shift(SEG_LEN + 1).rolling(SEG_LEN).max()
    f["l2"] = lo.shift(SEG_LEN + 1).rolling(SEG_LEN).min()
    f["h1"] = h.shift(2 * SEG_LEN + 1).rolling(SEG_LEN).max()
    f["l1"] = lo.shift(2 * SEG_LEN + 1).rolling(SEG_LEN).min()
    f["d1"] = (f["h1"] - f["l1"]) / f["h1"] * 100
    f["d2"] = (f["h2"] - f["l2"]) / f["h2"] * 100
    f["d3"] = (f["h3"] - f["l3"]) / f["h3"] * 100
    f["contract_wide"] = ((f["d1"] >= 10) & (f["d1"] > f["d2"]) &
                          (f["d2"] > f["d3"]) & (f["d3"] <= 10))
    f["contract_typical"] = ((f["d1"] >= 20) & (f["d1"] > f["d2"]) &
                             (f["d2"] > f["d3"]) & (f["d2"] >= 8) & (f["d3"] <= 8))
    f["contract_strict"] = ((f["d1"] >= 25) & (f["d1"] > f["d2"]) &
                            (f["d2"] > f["d3"]) & (f["d2"] >= 10) & (f["d3"] <= 5))
    f["pivot"] = f["h3"]
    f["pivot_low"] = lo.shift(1).rolling(SEG_LEN).min()      # p3 for E2b

    # volume dry-up (measured up to the day BEFORE the bar)
    f["avgVol10"] = v.shift(1).rolling(10).mean()
    f["avgVol50"] = v.shift(1).rolling(50).mean()
    f["volRatio"] = f["avgVol10"] / f["avgVol50"]
    f["volDry"] = (f["avgVol50"] > 0) & (f["volRatio"] < 0.7)

    # breakout
    f["breakout"] = (c > f["pivot"]) & (c.shift(1) <= f["pivot"].shift(1))
    f["volume_ok"] = v > f["avgVol50"] * 1.5
    f["cross_under_pivot_low"] = ((c < f["pivot_low"]) &
                                   (c.shift(1) >= f["pivot_low"].shift(1)))

    # relative strength
    f["stock_roc250"] = c / c.shift(250) - 1
    if index_roc:
        arr = np.array([index_roc.get(d, np.nan) for d in df.index.strftime("%Y-%m-%d")],
                       dtype=float)
        f["index_roc250"] = arr
    else:
        f["index_roc250"] = np.nan
    f["rs_ok"] = f["stock_roc250"] > f["index_roc250"]

    # the weak-stock mirror of the Trend Template, for E2b
    f["weak_trend"] = ((c < f["ma50"]) & (c < f["ma150"]) & (c < f["ma200"]) &
                       (f["ma150"] < f["ma200"]) &
                       (f["ma200"] < f["ma200"].shift(20)))

    f["bars_ok"] = np.arange(len(df)) >= BARS_MIN
    # ATR% from the v2 engine — already T-1 by construction
    f["atr_pct"] = features(df)["atr_pct"] if atr_pct is None else atr_pct
    return f


def index_roc_map(mkt: str) -> dict:
    """date string -> the market index's 250-session ROC on that date.

    Built on the index's OWN bars (so the 250-session lookback is the
    index's own), then aligned to each symbol's dates with a last-bar-at-
    or-before lookup.
    """
    df = SS.load_bars(BARS / f"{INDEX_SYM[mkt]}.json")
    if df is None:
        return {}
    c = df["close"].astype(float)
    roc = (c / c.shift(250) - 1)
    return {d.strftime("%Y-%m-%d"): float(v)
            for d, v in roc.items() if v == v}


def _asof_lookup(dates: list[str], table: dict) -> list[float]:
    """For each date, the value of the last table entry at or before it."""
    if not table:
        return [float("nan")] * len(dates)
    keys = sorted(table)
    idx = np.searchsorted(np.array(keys), np.array(dates), side="right") - 1
    out = []
    for k in idx:
        out.append(table[keys[k]] if k >= 0 else float("nan"))
    return out


# -------------------------------------------------------------------------- #
# the simulator
# -------------------------------------------------------------------------- #
def simulate(side: int, entry_i: int, entry_px: float, stop: float,
             O, H, L, C, horizon: int = HOLD,
             fill: str = "open_next") -> tuple | None:
    """A conservative daily-bar walk with the gap handled FIRST.

    fill = "open_next"  the fill is at Open[entry_i]; the entry bar's
                        intrabar stop is checked, and there is no gap check
                        on that bar (the fill already happened at the open).
    fill = "close_now"  the fill is at Close[entry_i - 1]; the exposure
                        starts at entry_i, which is therefore treated as a
                        LATER bar — a gap through the stop exits at the
                        open, not at the stop.

    Returns (exit_i, exit_px, reason) or None when the data ran out.
    """
    n = len(C)
    last = min(entry_i + horizon - 1, n - 1)
    for j in range(entry_i, last + 1):
        o, hi, lo_, cl = O[j], H[j], L[j], C[j]
        if j == entry_i and fill == "open_next":
            if side > 0:
                if lo_ <= stop:
                    return j, stop, "stop"
            else:
                if hi >= stop:
                    return j, stop, "stop"
            continue
        if side > 0:
            if o <= stop:
                return j, o, "stop"
            if lo_ <= stop:
                return j, stop, "stop"
        else:
            if o >= stop:
                return j, o, "stop"
            if hi >= stop:
                return j, stop, "stop"
    if last < entry_i + horizon - 1:
        return None
    return last, C[last], "time"


def borrow_cost(rate: float, held_sessions: int) -> float:
    """A borrow fee charged on the notional: rate * held/252.

    A short that is stopped out after 2 sessions pays 2/252 of the annual
    rate; a time exit at 10 sessions pays 10/252.
    """
    return rate * max(0, held_sessions) / 252.0


# -------------------------------------------------------------------------- #
# E1 — the script's own rule, standalone
# -------------------------------------------------------------------------- #
def e1_combos() -> list[dict]:
    """Every E1 parameter combination. All of them are reported."""
    out = []
    for contract in CONTRACT_SETS:
        for rs in (False, True):
            for fill in ("close_now", "open_next"):
                for liq in (None, "proposal"):
                    out.append({
                        "contract": contract, "rs": rs, "fill": fill,
                        "liquidity": liq,
                        "name": (f"E1_c{contract}_rs{'on' if rs else 'off'}_"
                                 f"{'close' if fill == 'close_now' else 'openn'}_"
                                 f"{'noLiq' if liq is None else 'Liq'}"),
                    })
    return out


def e1_run(mkt: str, mask_all: dict, combos: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {c["name"]: [] for c in combos}
    for sym in SS.symbols_for(mkt):
        df = SS.load_bars(BARS / f"{sym}.json")
        if df is None:
            continue
        vf = vcp_features(df, None)
        O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        atr = vf["atr_pct"].to_numpy()
        mask = mask_all.get(sym)
        base = (vf["trend_full"].to_numpy() & vf["volDry"].to_numpy() &
                vf["breakout"].to_numpy() & vf["volume_ok"].to_numpy() &
                vf["bars_ok"].to_numpy())
        for c in combos:
            sig = base & vf[f"contract_{c['contract']}"].to_numpy()
            if c["rs"]:
                sig = sig & vf["rs_ok"].to_numpy()
            if c["liquidity"] == "proposal":
                sig = sig & (df["volume"].to_numpy(float) >= LIQUIDITY_PROPOSAL[mkt])
            for i in np.where(sig)[0]:
                row = _trade_row(mkt, sym, df, vf, O, H, L, C, atr, mask, i, +1,
                                 c["fill"], contract=c["contract"])
                if row:
                    out[c["name"]].append(row)
    return out


# -------------------------------------------------------------------------- #
# E2 — the short side (OUR proposals; the XS script has no short version)
# -------------------------------------------------------------------------- #
def e2_combos() -> list[dict]:
    out = []
    for n_sessions in (5, 10):
        for antecedent in ("full_vcp", "pivot_cross"):
            out.append({
                "n_sessions": n_sessions, "antecedent": antecedent,
                "name": f"E2a_{antecedent}_N{n_sessions}",
            })
    out.append({"n_sessions": 0, "antecedent": "breakdown",
                "name": "E2b_breakdown"})
    return out


def _failed_breakout_signals(vf: pd.DataFrame, n_sessions: int,
                             antecedent: str) -> np.ndarray:
    """Bars where a prior VCP breakout has failed back below its pivot.

    antecedent = "full_vcp"    the prior breakout was a COMPLETE VCP
                               breakout (trend + contract + volDry +
                               breakout + volume spike)
                "pivot_cross"  the prior breakout was a plain cross above
                               the rolling pivot, with no other condition

    The failure itself must be a CROSS: the close falls back below the
    pivot it broke, having been at or above it the session before. One
    signal per breakout, not one per session for the next N sessions.
    """
    c = vf["close"].to_numpy(float)
    pivot = vf["pivot"].to_numpy(float)
    if antecedent == "full_vcp":
        brk = (vf["trend_full"].to_numpy() & vf["volDry"].to_numpy() &
               vf["breakout"].to_numpy() & vf["volume_ok"].to_numpy())
    else:
        brk = vf["breakout"].to_numpy()
    out = np.zeros(len(c), dtype=bool)
    n = len(c)
    last_brk = -999
    last_piv = np.nan
    for i in range(n):
        if brk[i]:
            last_brk = i
            last_piv = pivot[i]
        if i - last_brk <= n_sessions and i > last_brk and last_piv == last_piv:
            if c[i] < last_piv and c[i - 1] >= last_piv:
                out[i] = True
    return out


def e2_run(mkt: str, mask_all: dict, combos: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {c["name"]: [] for c in combos}
    for sym in SS.symbols_for(mkt):
        df = SS.load_bars(BARS / f"{sym}.json")
        if df is None:
            continue
        vf = vcp_features(df, None)
        O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        atr = vf["atr_pct"].to_numpy()
        mask = mask_all.get(sym)
        for c in combos:
            if c["antecedent"] == "breakdown":
                sig = (vf["cross_under_pivot_low"].to_numpy() &
                       vf["weak_trend"].to_numpy() & vf["bars_ok"].to_numpy())
            else:
                sig = _failed_breakout_signals(vf, c["n_sessions"], c["antecedent"])
            for i in np.where(sig)[0]:
                row = _trade_row(mkt, sym, df, vf, O, H, L, C, atr, mask, i, -1,
                                 "open_next", contract=c.get("contract", "wide"),
                                 rule=c["name"])
                if row:
                    out[c["name"]].append(row)
    return out


# -------------------------------------------------------------------------- #
# the shared trade builder
# -------------------------------------------------------------------------- #
def _trade_row(mkt, sym, df, vf, O, H, L, C, atr, mask, i, side, fill,
               contract=None, rule=None) -> dict | None:
    """Build one trade: entry, stop, exit, costs. Borrow fee is added by the
    caller for the short legs."""
    if np.isnan(atr[i]) or atr[i] <= 0:
        return None
    if fill == "open_next":
        entry_i = i + 1
        if entry_i >= len(C):
            return None
        entry_px = float(O[entry_i])
        held_bar = entry_i
    else:
        entry_i = i + 1
        if entry_i >= len(C):
            return None
        entry_px = float(C[i])          # the close of the signal bar
        held_bar = entry_i
    if entry_px <= 0:
        return None
    stop = entry_px * (1 - side * STOP_ATR * atr[i])
    if side > 0 and not (0 < stop < entry_px):
        return None
    if side < 0 and not (stop > entry_px):
        return None
    res = simulate(side, entry_i, entry_px, stop, O, H, L, C, HOLD, fill)
    if res is None:
        return None
    j, xp, why = res
    if mask:
        lo = df.index[i] - pd.Timedelta(days=SS.GLITCH_LOOKBACK_DAYS)
        if any(lo <= d <= df.index[j] for d in mask):
            return None
    gross = (xp / entry_px - 1) * side
    held = j - held_bar + 1
    # stop-free forward return: where the position would have closed on the
    # time exit, with no stop in the way. It tells a reader whether a
    # result is the stop doing the work or the direction doing the work.
    j_time = min(entry_i + HOLD - 1, len(C) - 1)
    fwd10 = (float(C[j_time] / entry_px - 1) * side) if j_time >= entry_i else float("nan")
    return {
        "mkt": mkt, "sym": sym, "date": df.index[i].strftime("%Y-%m-%d"),
        "entry_date": df.index[entry_i].strftime("%Y-%m-%d"),
        "exit_date": df.index[j].strftime("%Y-%m-%d"),
        "entry": entry_px, "stop": float(stop), "exit": float(xp),
        "gross": float(gross), "reason": why, "held": int(held),
        "fwd10": fwd10,
        "side": side, "fill": fill, "atr_pct": float(atr[i]),
        "signal_i": int(i), "entry_i": int(entry_i),
        "contract": contract, "rule": rule,
    }


def add_costs(rows: list[dict], mkt: str, side: int,
              borrow_rate: float = 0.0) -> list[dict]:
    """Attach both cost calibers and the borrow fee.

    "site"      = fees only, zero slippage (the published-v2 caliber)
    "realistic" = fees + slippage (NOT comparable with "site")
    borrow      = rate * held / 252, charged only on the short legs
    """
    out = []
    for r in rows:
        stop_exit = r["reason"] == "stop"
        r2 = dict(r)
        r2["net_site"] = r["gross"] - cost_rt(mkt, "site", stop_exit)
        r2["net_realistic"] = r["gross"] - cost_rt(mkt, "realistic", stop_exit)
        bc = borrow_cost(borrow_rate, r["held"]) if side < 0 else 0.0
        r2["borrow_cost"] = bc
        r2["net_site_borrow"] = r2["net_site"] - bc
        r2["net_realistic_borrow"] = r2["net_realistic"] - bc
        out.append(r2)
    return out


# -------------------------------------------------------------------------- #
# E3 — VCP as a FILTER on the signals v2 already publishes
# -------------------------------------------------------------------------- #
def e3_filters(vf: pd.DataFrame, i: int) -> dict:
    """The five filters, evaluated at the DECISION bar i (the bar before
    the entry session).

    NOTE ON "AT THE TOP". The XS script's own at_top condition is
    "close within 25% of the 52-week high" - that is a TREND-TEMPLATE
    REQUIREMENT, not a "do not chase" marker, and it is satisfied by
    almost every signal that passes the trend template. It cannot separate
    anything, so the tighter "actually extended" readings are computed
    here as well: within 1% / 3% of the 52-week high, and within 2% of the
    top of the 20-session box (R1), which is the Part-B definition.
    """
    r = vf.iloc[i]
    contract = "none"
    for name in CONTRACT_SETS:
        if bool(r[f"contract_{name}"]):
            contract = name
            break
    close = float(r["close"])
    r1 = float(r["r1"]) if "r1" in vf.columns else float("nan")
    dist52 = (close / float(r["high52"]) - 1) * 100 if r["high52"] == r["high52"] else float("nan")
    dist_r1 = (r1 / close - 1) * 100 if r1 == r1 else float("nan")
    return {
        "near_box_top_2pct": bool(dist_r1 == dist_r1 and dist_r1 < 2.0),
        "near_52w_high_1pct": bool(dist52 == dist52 and dist52 > -1.0),
        "near_52w_high_3pct": bool(dist52 == dist52 and dist52 > -3.0),
        "at_52w_high": bool(dist52 == dist52 and dist52 > 0.0),
        "dist_to_r1_pct": dist_r1,
        "F1_trend": bool(r["trend_core"]),
        "F2_at_top": bool(r["trend_core"]) and bool(r["at_top"]),
        "F3_contract": bool(r["trend_core"]) and contract != "none",
        "F4_vol_dry": bool(r["trend_core"]) and bool(r["volDry"]),
        "F5_rs": bool(r["trend_core"]) and bool(r["rs_ok"]),
        "at_top": bool(r["at_top"]),
        "trend_core": bool(r["trend_core"]),
        "contract_set": contract,
        "vol_dry": bool(r["volDry"]),
        "rs_ok": bool(r["rs_ok"]),
        "dist_to_52w_high_pct": (float(r["close"] / r["high52"] - 1) * 100
                                 if r["high52"] == r["high52"] else float("nan")),
        "atr_pct": float(r["atr_pct"]) if r["atr_pct"] == r["atr_pct"] else float("nan"),
    }


def e3_apply(long_rows: list[dict], index_rocs: dict) -> list[dict]:
    """Apply F1-F5 to every published v2 signal.

    long_rows: the v2 long trades (with sym and entry_i).
    """
    out = []
    for mkt in MARKETS:
        rows = [r for r in long_rows if r["mkt"] == mkt]
        by_sym = defaultdict(list)
        for r in rows:
            by_sym[r["sym"]].append(r)
        roc = index_rocs.get(mkt, {})
        for sym, rlist in by_sym.items():
            df = SS.load_bars(BARS / f"{sym}.json")
            if df is None:
                continue
            vf = vcp_features(df, None)
            cf = features(df)
            vf["r1"] = cf["R1"]
            vf["s1"] = cf["S1"]
            dates = list(df.index.strftime("%Y-%m-%d"))
            asof = _asof_lookup(dates, roc)
            vf["index_roc250"] = asof
            vf["rs_ok"] = vf["stock_roc250"] > vf["index_roc250"]
            for r in rlist:
                i = r["entry_i"] - 1          # the decision bar: T-1 close
                if i < 0:
                    continue
                flags = e3_filters(vf, i)
                row = {k: r.get(k) for k in ("mkt", "sym", "date", "exit_date",
                                             "gross", "net_site", "net_realistic",
                                             "reason", "held", "atr_pct",
                                             "entry", "fwd10")}
                row.update(flags)
                out.append(row)
    return out


# -------------------------------------------------------------------------- #
# self-test
# -------------------------------------------------------------------------- #
def _selftest() -> int:
    raise SystemExit(0 if _selftest_body() else 1)


def _selftest_body() -> bool:
    ok = True

    # 1. the XS translation, on a synthetic 400-bar series with a known
    #    shape: a wide range that contracts over three 25-bar segments
    n = 400
    idx = pd.bdate_range("2018-01-01", periods=n)
    close = np.full(n, 100.0)
    high = np.full(n, 101.0)
    low = np.full(n, 99.0)
    vol = np.full(n, 1_000_000.0)
    # The three 25-bar windows at bar 150 are:
    #   h1 -> bars  75.. 99   (oldest segment)
    #   h2 -> bars 100..124
    #   h3 -> bars 125..149   (the pivot segment)
    # so the contracting base is laid out on exactly those boundaries.
    for i in range(75, 100):
        high[i], low[i] = 115.0, 80.5        # 30% range
    for i in range(100, 125):
        high[i], low[i] = 107.5, 91.375      # 15% range
    for i in range(125, 150):
        high[i], low[i] = 103.0, 96.82       # 6% range
    for i in range(150, n):
        high[i], low[i] = 102.0, 99.5
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close,
                       "volume": vol}, index=idx)
    vf = vcp_features(df, None)
    d3 = float(vf["d3"].iloc[150])
    d2 = float(vf["d2"].iloc[150])
    d1 = float(vf["d1"].iloc[150])
    good = (abs(d3 - 6.0) < 0.05 and abs(d2 - 15.0) < 0.05 and abs(d1 - 30.0) < 0.05)
    print(f"  {'PASS' if good else 'FAIL'}  the three contraction segments are "
          f"read off the right bars (d1={d1:.2f}, d2={d2:.2f}, d3={d3:.2f})")
    ok &= good

    # the contract must be TRUE on that shape and FALSE when the segments
    # are not shrinking (the OLDEST segment narrower than the middle one)
    c_ok = bool(vf["contract_wide"].iloc[150])
    bad = df.copy()
    bad["high"] = bad["high"].to_numpy().copy()
    bad["low"] = bad["low"].to_numpy().copy()
    bad.loc[idx[75:100], "high"] = 103.0
    bad.loc[idx[75:100], "low"] = 96.82
    bad.loc[idx[100:125], "high"] = 115.0
    bad.loc[idx[100:125], "low"] = 80.5
    c_bad = bool(vcp_features(bad, None)["contract_wide"].iloc[150])
    good = c_ok and not c_bad
    print(f"  {'PASS' if good else 'FAIL'}  a contracting base passes and a "
          f"widening one fails ({c_ok} / {c_bad})")
    ok &= good

    # 2. no look-ahead in the VCP features: plant a late bar
    late = df.copy()
    extra = df.iloc[[300]].copy()
    extra.index = pd.DatetimeIndex([df.index[300] + pd.Timedelta(days=7)])
    for k, v2 in (("close", 9999.0), ("high", 10000.0), ("low", 1.0), ("open", 9999.0)):
        extra[k] = v2
    late = pd.concat([df, extra]).sort_index()
    vf2 = vcp_features(late, None)
    cols = ("trend_core", "at_top", "contract_wide", "volDry", "breakout",
            "volume_ok", "cross_under_pivot_low", "weak_trend")
    same = all(np.array_equal(vf[c].iloc[:301].to_numpy(), vf2[c].iloc[:301].to_numpy())
               for c in cols)
    print(f"  {'PASS' if same else 'FAIL'}  a late-arriving bar does not move "
          f"any VCP feature")
    ok &= same

    # 3. the simulator handles a gap through the stop at the OPEN
    O = np.array([100.0, 90.0, 90.0, 90.0])
    H = np.array([101.0, 91.0, 91.0, 91.0])
    L = np.array([99.0, 89.0, 89.0, 89.0])
    C = np.array([100.0, 90.0, 90.0, 90.0])
    res = simulate(+1, 1, 100.0, 97.0, O, H, L, C, horizon=3, fill="close_now")
    gap_ok = res is not None and res[1] == 90.0 and res[2] == "stop"
    res2 = simulate(+1, 1, 100.0, 97.0, O, H, L, C, horizon=3, fill="open_next")
    print(f"  {'PASS' if gap_ok else 'FAIL'}  a gap through the stop exits at "
          f"the open, not at the stop (got {res})")
    ok &= gap_ok

    # 4. the borrow fee is charged on the sessions actually held
    good = (abs(borrow_cost(0.10, 10) - 0.10 * 10 / 252) < 1e-12 and
            abs(borrow_cost(0.30, 2) - 0.30 * 2 / 252) < 1e-12 and
            borrow_cost(0.0, 10) == 0.0)
    print(f"  {'PASS' if good else 'FAIL'}  the borrow fee is rate x held/252")
    ok &= good

    # 5. E3 is evaluated on the DECISION bar: mutating the ENTRY bar's OHLC
    #    must not move any filter flag
    sym = "AAPL"
    bars = SS.load_bars(BARS / f"{sym}.json")
    if bars is None:
        print("  FAIL  could not load AAPL for the E3 decision-bar probe")
        return False
    vfb = vcp_features(bars, None)
    i = 1200
    before = e3_filters(vfb, i)
    mutated = bars.copy()
    for k in ("open", "high", "low", "close"):
        mutated.iloc[i + 1, mutated.columns.get_loc(k)] = 12345.0
    vfm = vcp_features(mutated, None)
    after = e3_filters(vfm, i)
    # NaN-safe: NaN != NaN would make the comparison fail on a value that
    # did NOT move, which would make the probe meaningless.
    def _same(a, b):
        if isinstance(a, float) and isinstance(b, float):
            if a != a and b != b:
                return True
            return abs(a - b) < 1e-12
        return a == b
    same = all(_same(before[k], after[k]) for k in before)
    print(f"  {'PASS' if same else 'FAIL'}  mutating the entry bar does not "
          f"move the E3 flags")
    ok &= same

    # 6. the contract parameter sets are ordered wide >= typical >= strict
    n_wide = int(vf["contract_wide"].sum())
    n_typ = int(vf["contract_typical"].sum())
    n_strict = int(vf["contract_strict"].sum())
    good = n_wide >= n_typ >= n_strict
    print(f"  {'PASS' if good else 'FAIL'}  the contraction sets nest "
          f"(wide {n_wide} >= typical {n_typ} >= strict {n_strict})")
    ok &= good

    return bool(ok)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        print("=" * 72)
        print("VCP SELF-TEST")
        print("=" * 72)
        raise SystemExit(_selftest())
    print("vcp.py is an engine. Run scripts/research/run_short_side.py for the "
          "full report, or --selftest for the checks.")
    raise SystemExit(0)
