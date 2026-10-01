"""
Leeks Terminal signal engine -- independent re-implementation + honest backtester.

Everything here is computed from daily OHLC only, with a strict T-1 rule:
features for trading day t use bars <= t-1; entries/exits use day t.. bars.

Two layers:
  1. `features()`      -> S/R ladder, ATR, MAs, phase (as documented on /methodology)
  2. `gen_trades()`    -> candidate trades for the 4 documented strategies,
                          simulated with conservative daily-bar fill rules.
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd

# --------------------------------------------------------------------------------------
# Cost model (per round trip, fraction of notional)
#   site      = exactly what the site assumes (HK 0.25%, US 0.05%)
#   realistic = site fees + slippage on both legs (bigger ticks/spreads in HK, JP)
#   stress    = realistic x2 slippage
# JP is not documented on the site; we assume 0.10% fees round trip.
# --------------------------------------------------------------------------------------
FEES = {"us200": 0.0005, "hk200": 0.0025, "jp200": 0.0010}
SLIP_SIDE = {"us200": 0.0003, "hk200": 0.0008, "jp200": 0.0005}   # per side, limit/open fills
STOP_EXTRA = {"us200": 0.0005, "hk200": 0.0010, "jp200": 0.0008}  # extra slip on stop (market) exits


def cost_rt(mkt: str, scenario: str, stop_exit: bool) -> float:
    if scenario == "site":
        return FEES[mkt]
    k = 1.0 if scenario == "realistic" else 2.0
    c = FEES[mkt] + k * 2 * SLIP_SIDE[mkt]
    if stop_exit:
        c += k * STOP_EXTRA[mkt]
    return c


# --------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------
def load_bars(path: str, end_date: str | None = None) -> pd.DataFrame | None:
    d = json.load(open(path))
    if d.get("status") not in ("ok", "partial") or not d.get("data"):
        return None
    b = pd.DataFrame(d["data"]["bars"])
    if b.empty:
        return None
    tz = d["data"]["meta"].get("exchangeTimezoneName", "UTC")
    b["date"] = pd.to_datetime(b["time"], utc=True).dt.tz_convert(tz).dt.tz_localize(None).dt.normalize()
    b = b[["date", "open", "high", "low", "close", "adjusted_close", "volume"]].dropna(subset=["open", "high", "low", "close"])
    b = b[(b.high > 0) & (b.low > 0) & (b.open > 0) & (b.close > 0)]
    # repair bars whose open/close sit outside [low, high] (bad prints)
    b["high"] = b[["high", "open", "close"]].max(axis=1)
    b["low"] = b[["low", "open", "close"]].min(axis=1)
    # zero-volume flat bars = non-trading days in Yahoo HK data -> drop
    flat = (b.high == b.low) & (b.volume.fillna(0) == 0)
    b = b[~flat].drop_duplicates("date", keep="last").sort_values("date")
    if end_date:
        b = b[b.date <= pd.Timestamp(end_date)]
    return b.set_index("date")


# --------------------------------------------------------------------------------------
# Features (all shifted: value at row t uses information up to t-1)
# --------------------------------------------------------------------------------------
def features(b: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=b.index)
    h, l, c = b.high, b.low, b.close
    hs, ls, cs = h.shift(1), l.shift(1), c.shift(1)
    # S/R ladder exactly as documented: non-overlapping tiers
    f["R1"] = hs.rolling(20).max()
    f["S1"] = ls.rolling(20).min()
    f["R2"] = hs.shift(20).rolling(40).max()      # days 21-60
    f["S2"] = ls.shift(20).rolling(40).min()
    f["R3"] = hs.shift(60).rolling(60).max()      # days 61-120
    f["S3"] = ls.shift(60).rolling(60).min()
    f["S4"] = ls.shift(120).rolling(130, min_periods=20).min()  # days 121-250 (52-week crash low)
    f["mid"] = (f.R1 + f.S1) / 2
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    f["atr_pct"] = (tr.rolling(14).mean() / c).shift(1)
    f["prev_close"] = cs
    f["ma20"] = cs.rolling(20).mean()
    f["ma50"] = cs.rolling(50).mean()
    f["ma200"] = cs.rolling(200).mean()
    f["ma50_slope"] = f.ma50 / f.ma50.shift(10) - 1
    f["ma200_slope"] = f.ma200 / f.ma200.shift(20) - 1
    f["box_w"] = f.R1 / f.S1 - 1
    f["pos_in_box"] = (cs - f.S1) / (f.R1 - f.S1)
    f["ret_5d"] = cs / cs.shift(5) - 1
    f["dollar_vol20"] = (c * b.volume).rolling(20).mean().shift(1)
    # Phase (approximation of the documented rules; the exact code is not public)
    up = (cs > f.ma20) & (f.S1 > f.S2) & (f.R1 > f.R2)
    down = (cs < f.ma20) & (f.S1 < f.S2) & (f.ma50_slope < 0)
    recov = (f.ma50_slope < 0) & (cs > f.mid) & (cs < f.R2) & ~up
    base = f.pos_in_box.between(0.3, 0.7) & (f.S1 >= f.S2 * 0.98)
    f["phase"] = np.select([up, down, recov, base], ["uptrend", "downtrend_active", "downtrend_recovery", "base_building"], "range")
    return f


# --------------------------------------------------------------------------------------
# Trade simulation
# --------------------------------------------------------------------------------------
STRATS = ("SELL_R1", "BUY_S1", "BREAK_LONG", "BREAK_SHORT")
HORIZONS = (0, 1, 3, 5, 10)   # 0 = exit at entry-day close (true day trade)


def _simulate(side, entry_i, entry_px, stop, target, O, H, L, C, horizon, limit_entry):
    """Walk forward bar by bar. Conservative rules:
    - entry bar, limit entry (fade at R1/S1): stop checked first; target credited only if
      the CLOSE is beyond target (that proves the target traded after our fill).
    - entry bar, open entry (breakout): stop first if both stop and target inside the bar.
    - later bars: gap through stop -> exit at open; gap through target -> exit at open;
      if both inside bar -> stop first.
    Returns (exit_i, exit_px, reason)."""
    n = len(C)
    last = min(entry_i + horizon, n - 1)
    for j in range(entry_i, last + 1):
        o, hi, lo, cl = O[j], H[j], L[j], C[j]
        if side > 0:  # long
            if j == entry_i:
                if lo <= stop:
                    return j, stop, "stop"
                if (limit_entry and cl >= target) or (not limit_entry and hi >= target):
                    return j, target, "target"
            else:
                if o <= stop:
                    return j, o, "stop"
                if o >= target:
                    return j, o, "target"
                if lo <= stop:
                    return j, stop, "stop"
                if hi >= target:
                    return j, target, "target"
        else:  # short
            if j == entry_i:
                if hi >= stop:
                    return j, stop, "stop"
                if (limit_entry and cl <= target) or (not limit_entry and lo <= target):
                    return j, target, "target"
            else:
                if o >= stop:
                    return j, o, "stop"
                if o <= target:
                    return j, o, "target"
                if hi >= stop:
                    return j, stop, "stop"
                if lo <= target:
                    return j, target, "target"
    if last < entry_i + horizon:           # ran out of data -> open trade, drop
        return None
    return last, C[last], "time"


def gen_trades(b: pd.DataFrame, f: pd.DataFrame, mkt: str, sym: str,
               horizons=HORIZONS, through: float = 0.0,
               stop_mode: str = "site", guard: bool = True) -> pd.DataFrame:
    """Generate every trade the 4 documented strategies would have taken.

    through   : require price to trade THROUGH the level by this fraction to count a
                limit fill (0 = touch fill, the site's implicit assumption).
    stop_mode : 'site' = documented stops (R1*(1+max(2%,1.5*ATR%)) etc.)
                'atr1' = level +/- 1.0 ATR (tighter, volatility-scaled)
    """
    O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    idx = b.index
    F = {k: f[k].to_numpy() for k in ("R1", "S1", "R2", "S2", "mid", "atr_pct", "box_w")}
    rows = []
    valid = ~np.isnan(F["R2"]) & ~np.isnan(F["atr_pct"]) & ~np.isnan(F["R1"])
    for i in np.where(valid)[0]:
        r1, s1, r2, s2, mid, atr, bw = (F[k][i] for k in ("R1", "S1", "R2", "S2", "mid", "atr_pct", "box_w"))
        if guard and (atr >= 0.06 or bw >= 0.35):
            continue
        o = O[i]
        cands = []
        in_box = s1 < o < r1
        if in_box and H[i] >= r1 * (1 + through):
            stop = r1 * (1 + max(0.02, 1.5 * atr)) if stop_mode == "site" else r1 * (1 + atr)
            cands.append(("SELL_R1", -1, r1, stop, mid, True))
        if in_box and L[i] <= s1 * (1 - through):
            stop = s1 * (1 - max(0.02, 1.5 * atr)) if stop_mode == "site" else s1 * (1 - atr)
            cands.append(("BUY_S1", 1, s1, stop, mid, True))
        if o > r1:
            stop = r1 * 0.99 if stop_mode == "site" else o * (1 - atr)
            cands.append(("BREAK_LONG", 1, o, stop, r2, False))
        if o < s1:
            stop = s1 * 1.01 if stop_mode == "site" else o * (1 + atr)
            cands.append(("BREAK_SHORT", -1, o, stop, s2, False))
        for strat, side, px, stop, tgt, lim in cands:
            risk = (px - stop) / px * side
            reward = (tgt - px) / px * side
            if risk <= 0 or reward <= 0:          # target on wrong side -> WAIT (guard rail)
                continue
            if guard and reward / risk < 1.2:     # R:R guard rail
                continue
            for hz in horizons:
                res = _simulate(side, i, px, stop, tgt, O, H, L, C, hz, lim)
                if res is None:
                    continue
                j, xp, why = res
                gross = (xp / px - 1) * side
                rows.append((sym, mkt, idx[i], strat, side, hz, px, stop, tgt, risk, reward,
                             reward / risk, idx[j], xp, why, gross, j - i))
    cols = ["sym", "mkt", "date", "strat", "side", "hz", "entry", "stop", "target", "risk", "reward",
            "rr", "exit_date", "exit", "reason", "gross", "bars"]
    return pd.DataFrame(rows, columns=cols)


def apply_costs(t: pd.DataFrame, scenario: str) -> pd.Series:
    out = np.empty(len(t))
    for mkt in t.mkt.unique():
        m = (t.mkt == mkt).to_numpy()
        stop_exit = (t.reason.to_numpy() == "stop")
        c = np.where(stop_exit[m], cost_rt(mkt, scenario, True), cost_rt(mkt, scenario, False))
        out[m] = t.gross.to_numpy()[m] - c
    return pd.Series(out, index=t.index)
