"""v2 signal generator for Leeks Terminal (drop-in for the BUY/SELL/WAIT action plan).

Rule set (every threshold was chosen on 2016-2022 data only; 2023-2026 is out-of-sample):
  Universe      : US200 + JP200 (HK excluded by default: ~0 edge after 0.25% fees + slippage)
  Direction     : LONG ONLY (SELL_R1 / BREAK_* lost money in every market and period tested)
  Stock filter  : T-1 close > 200d MA  AND  200d MA rising over 20 sessions
  Market filter : market ETF (SPY / 1306.T / 2800.HK) T-1 close > its 200d MA
  Guard rails   : ATR% < 6%, 20d box width < 35%   (kept from the site)
  Entry         : limit BUY at S1 (20-day low of T-1 window), valid for the next session only,
                  and only if the session OPENS above S1 (if it gaps below S1 -> skip)
  Stop          : S1 x (1 - 3 x ATR%)                (wider than the site's; fewer noise stop-outs)
  Target        : none -- time exit at the close of the 10th session after entry
  Ranking       : most oversold first (lowest 5-day return) when you have fewer slots than signals
  Sizing        : risk 1% of equity per trade -> shares = 0.01 x equity / (S1 - stop); cap 10% notional

What to DISPLAY next to each signal (replaces the per-stock 60d win% badge):
  the pooled, out-of-sample expectancy of THIS SETUP in THIS MARKET, with a Wilson 95% interval.

Usage:
  python3 v2_signals.py --data ../data/yahoo --idx ../data/idx --symbols ../data/symbols.json \
                        --out ../report/v2_signals_sample.csv [--include-hk] [--equity 1000000]
"""
from __future__ import annotations
import argparse, json, math, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import load_bars, features

IDX = {"us200": "SPY", "hk200": "2800.HK", "jp200": "1306.T"}
TZ = {"us200": "America/New_York", "hk200": "Asia/Hong_Kong", "jp200": "Asia/Tokyo"}
CLOSE_HHMM = {"us200": (16, 30), "hk200": (16, 40), "jp200": (15, 50)}   # + buffer for final prints
PARAMS = dict(stop_atr=3.0, hold=10, atr_max=0.06, box_max=0.35, risk=0.01, max_notional=0.10)


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def complete_bars(b: pd.DataFrame, mkt: str, now_utc: datetime) -> pd.DataFrame:
    """Drop today's bar if that session has not closed yet (T-1 hard rule)."""
    loc = now_utc.astimezone(ZoneInfo(TZ[mkt]))
    today = pd.Timestamp(loc.date())
    hh, mm = CLOSE_HHMM[mkt]
    if (loc.hour, loc.minute) < (hh, mm):
        b = b[b.index < today]
    return b


def market_up(idx_dir: str, mkt: str, now_utc: datetime) -> tuple[bool, str]:
    b = load_bars(f"{idx_dir}/{IDX[mkt]}.json")
    b = complete_bars(b, mkt, now_utc)
    c = b.close
    return bool(c.iloc[-1] > c.rolling(200).mean().iloc[-1]), str(b.index[-1].date())


def plan_for(sym: str, mkt: str, b: pd.DataFrame, equity: float):
    # append a placeholder row for the NEXT session so features() (which is shifted) gives T-1 values
    nxt = b.index[-1] + pd.offsets.BDay(1)
    bb = pd.concat([b, b.iloc[[-1]].set_index(pd.DatetimeIndex([nxt]))])
    f = features(bb).iloc[-1]
    if any(pd.isna(f[k]) for k in ("S1", "R1", "R2", "ma200", "ma200_slope", "atr_pct")):
        return None, "insufficient history"
    if not (f.prev_close > f.ma200 and f.ma200_slope > 0):
        return None, "not in uptrend"
    if f.atr_pct >= PARAMS["atr_max"] or f.box_w >= PARAMS["box_max"]:
        return None, "guard rail (ATR/box)"
    s1 = float(f.S1)
    stop = s1 * (1 - PARAMS["stop_atr"] * f.atr_pct)
    risk_ps = s1 - stop
    shares = PARAMS["risk"] * equity / risk_ps
    shares = min(shares, PARAMS["max_notional"] * equity / s1)
    return {
        "market": mkt, "symbol": sym, "asof_T-1": str(b.index[-1].date()), "last_close": round(float(f.prev_close), 4),
        "action": "BUY LIMIT @S1 (next session only; skip if it opens <= S1)",
        "entry_S1": round(s1, 4), "dist_to_entry%": round((s1 / f.prev_close - 1) * 100, 2),
        "stop": round(stop, 4), "stop_dist%": round(-PARAMS["stop_atr"] * f.atr_pct * 100, 2),
        "exit_rule": f"close of session {PARAMS['hold']} after fill (no fixed target)",
        "R1": round(float(f.R1), 4), "ma200": round(float(f.ma200), 4), "atr%": round(float(f.atr_pct) * 100, 2),
        "ret_5d%": round(float(f.ret_5d) * 100, 2), "shares_at_1pct_risk": int(shares),
    }, "ok"


def calibration(trades_parquet: str | None):
    """Setup-level expectancy per market from the v2 backtest (OOS 2023+ and full)."""
    if not trades_parquet or not os.path.exists(trades_parquet):
        return {}
    V = pd.read_parquet(trades_parquet)
    V = V[(V.stop_rule == "atr3") & (V.tgt_rule == "none") & (V.hz == 10) & (V.mkt_up == 1)]
    out = {}
    for mkt, g in V.groupby("mkt"):
        for lab, x in (("oos_2023plus", g[g.date >= "2023-01-01"]), ("full_2016plus", g)):
            k, n = int((x.net > 0).sum()), len(x)
            lo, hi = wilson(k, n)
            out.setdefault(mkt, {})[lab] = {"n": n, "win%": round(100 * k / n, 1), "win95": [round(lo, 1), round(hi, 1)],
                                            "avg_net%": round(100 * x.net.mean(), 2), "median_net%": round(100 * x.net.median(), 2),
                                            "p10_net%": round(100 * x.net.quantile(0.1), 2)}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__)); root = os.path.dirname(here)
    ap.add_argument("--data", default=f"{root}/data/yahoo"); ap.add_argument("--idx", default=f"{root}/data/idx")
    ap.add_argument("--symbols", default=f"{root}/data/symbols.json")
    ap.add_argument("--trades", default=f"{root}/data/trades_v2grid.parquet")
    ap.add_argument("--out", default=f"{root}/report/v2_signals_sample.csv")
    ap.add_argument("--include-hk", action="store_true"); ap.add_argument("--equity", type=float, default=1_000_000)
    ap.add_argument("--now", default=None, help="override current UTC time, ISO format")
    a = ap.parse_args()
    now = datetime.fromisoformat(a.now) if a.now else datetime.now(ZoneInfo("UTC"))
    mkts = ["us200", "jp200"] + (["hk200"] if a.include_hk else [])
    cal = calibration(a.trades)
    syms = json.load(open(a.symbols))["symbols"]
    rows, reasons = [], {}
    for mkt in mkts:
        up, asof = market_up(a.idx, mkt, now)
        print(f"{mkt}: market filter {'ON (index > 200d MA)' if up else 'OFF -> no longs today'}  (index T-1 = {asof})")
        if not up:
            continue
        for m, _, y in syms:
            if m != mkt or not os.path.exists(f"{a.data}/{y}.json"):
                continue
            b = load_bars(f"{a.data}/{y}.json")
            if b is None or len(b) < 260:
                continue
            b = complete_bars(b, mkt, now)
            p, why = plan_for(y, mkt, b, a.equity)
            reasons[why] = reasons.get(why, 0) + 1
            if p:
                c = cal.get(mkt, {}).get("oos_2023plus", {})
                p["setup_OOS_win%"] = c.get("win%"); p["setup_OOS_win95"] = str(c.get("win95"))
                p["setup_OOS_avg_net%"] = c.get("avg_net%"); p["setup_OOS_n"] = c.get("n")
                rows.append(p)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["market", "ret_5d%"]).reset_index(drop=True)
    df.to_csv(a.out, index=False)
    json.dump(cal, open(os.path.splitext(a.out)[0] + "_calibration.json", "w"), indent=2)
    print("screen:", reasons); print(f"{len(df)} candidate limit orders written to {a.out}")
    if not df.empty:
        print(df[["market", "symbol", "asof_T-1", "last_close", "entry_S1", "dist_to_entry%", "stop", "stop_dist%", "ret_5d%"]].head(25).to_string(index=False))
    print(json.dumps(cal, indent=1))
