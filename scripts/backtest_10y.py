#!/usr/bin/env python3
"""backtest_10y.py — honest 10-year backtest of the v2 rule set, vs buy-and-hold.

Re-implements the published v2 rules from the daily bars in data/bt10y/:

    Direction   long only
    Stock filter  T-1 close > 200d MA  AND  200d MA rising over 20 sessions
    Market filter index T-1 close > its 200d MA
    Guard rails   ATR% < 6%, 20d box width < 35%
    Entry         limit BUY at S1 (20-day low of the T-1 window), valid for the
                  next session only, and only if the session TRADES THROUGH S1
                  by 0.10% (a touch is not a fill)
    Stop          S1 x (1 - 3 x ATR%)
    Target        none — time exit at the close of the 10th session after entry
    Costs         fees + slippage per side; extra slippage on stop exits

EXECUTION MODEL — this is the part that decides the result, so it is explicit:

  * No look-ahead. Levels for session t use bars through t-1 only; the order is
    placed for t and can fill at t's open or later.
  * A limit at S1 fills only if the session trades 0.10% THROUGH S1. The
    published site assumes a mere touch fills, which overstates results.
  * Stop before target on the same bar. A stop needs only a touch.
  * An intraday gap through the stop fills at the open, not at the stop level.
  * Costs are charged on entry and on exit.

Every trade is recorded so the result can be audited, not just trusted.

Usage:
    python3 scripts/backtest_10y.py --out data/bt10y_result.json
"""
from __future__ import annotations
import argparse
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
DATA = REPO / "data" / "bt10y"

# v2 rule set (thresholds from the 10-year audit; every one chosen on
# 2016-2022 and tested once on 2023-2026)
P = dict(stop_atr=3.0, hold=10, atr_max=0.06, box_max=0.35, through=0.0010)

# round-trip cost model, split into fee and per-side slippage
FEE = {"us200": 0.0005, "hk200": 0.0025, "jp200": 0.0010}
SLIP = {"us200": 0.0003, "hk200": 0.0008, "jp200": 0.0005}
STOP_EXTRA = {"us200": 0.0005, "hk200": 0.0010, "jp200": 0.0008}

IDX = {"us200": "SPY", "jp200": "1306_T", "hk200": "IDX_HSI"}

MARKET_OF = {"hk200": "hk", "us200": "us", "jp200": "jp"}

MARKETS = {
    "hk": (REPO / "hk_universe_200.json", None),
    "us": (REPO / "charts/us200/us_top200_fresh.json", None),
    "jp": (None, REPO / "data/jp200"),
}


def universe(upath, out_dir: Path | None):
    """[(yahoo_symbol, safe_name)] for one market's published universe."""
    if upath:
        raw = json.loads(upath.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("symbols") or raw.get("tickers") or []
        out = []
        for it in raw:
            tk = it if isinstance(it, str) else (it[1] if isinstance(it, (list, tuple)) and len(it) > 1 else it.get("ticker", ""))
            if tk:
                out.append((tk, tk.replace(".", "_").replace("/", "_")))
        return out
    out = []
    for q in sorted((out_dir or REPO).glob("*.json")):
        if q.name.endswith("_ohlc.json"):
            continue
        st = q.stem
        if st.endswith("_T"):
            out.append((st[:-2] + ".T", st))
        elif st.endswith("_HK"):
            out.append((st[:-3] + ".HK", st))
        else:
            out.append((st, st))
    return out


def load(safe: str) -> pd.DataFrame | None:
    p = DATA / f"{safe}.json"
    if not p.exists():
        return None
    try:
        df = pd.DataFrame(json.loads(p.read_text()))
    except Exception:
        return None
    if len(df) < 260:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def features(df: pd.DataFrame) -> pd.DataFrame:
    """T-1 features: every column is shifted one bar, so index t uses data < t."""
    f = pd.DataFrame(index=df.index)
    h, l, c = df.high, df.low, df.close
    hs, ls, cs = h.shift(1), l.shift(1), c.shift(1)
    f["S1"] = ls.rolling(20).min()
    f["R1"] = hs.rolling(20).max()
    f["ma200"] = cs.rolling(200).mean()
    f["ma200_slope"] = f.ma200 / f.ma200.shift(20) - 1
    tr = pd.concat([h - l, (h - cs).abs(), (l - cs).abs()], axis=1).max(axis=1)
    f["atr_pct"] = (tr.rolling(14).mean() / c).shift(1)
    f["prev_close"] = cs
    f["ret_5d"] = cs / cs.shift(5) - 1
    f["box_w"] = f.R1 / f.S1 - 1
    return f


def market_up(mkt: str) -> pd.Series:
    """Per-session boolean: index close > its 200d MA (index bar t, used for t+1)."""
    df = load(IDX[mkt])
    if df is None:
        return pd.Series(dtype=bool)
    c = df.close
    return (c > c.rolling(200).mean()).shift(1).fillna(False).rename("mkt_up")


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def backtest_symbol(sym: str, mkt: str, df: pd.DataFrame, mup: pd.Series) -> list[dict]:
    """Walk sessions. At t we know levels from t-1 and may fill during t."""
    f = features(df)
    fee, slip, sx = FEE[mkt], SLIP[mkt], STOP_EXTRA[mkt]
    trades = []
    i = 0
    n = len(df)
    while i < n - 1:
        t = i + 1                      # the session the order is live for
        if t >= n:
            break
        row = f.iloc[t]
        if any(pd.isna(row[k]) for k in ("S1", "R1", "ma200", "ma200_slope", "atr_pct")):
            i += 1
            continue
        # market filter is read at the order session, index bar t-1
        mok = bool(mup.iloc[t]) if t < len(mup) else False
        long_ok = mok and row.prev_close > row.ma200 and row.ma200_slope > 0
        long_ok = long_ok and row.atr_pct < P["atr_max"] and row.box_w < P["box_max"]
        if not long_ok:
            i += 1
            continue

        s1 = float(row.S1)
        atr_pct = float(row.atr_pct)
        stop = s1 * (1 - P["stop_atr"] * atr_pct)
        if s1 <= stop:
            i += 1
            continue
        need = s1 * (1 + P["through"])       # must trade THROUGH, not touch

        entry_i = None
        # The published rule is "valid for the next session only". An earlier
        # draft used range(t, t+3), i.e. a three-session resting order, which
        # roughly doubled the trade count and inflated the average.
        for j in range(t, min(t + 1, n)):
            if df.high.iloc[j] >= need:
                entry_i = j
                break
        if entry_i is None:
            i += 1
            continue

        # fill at max(open, need) — never better than the limit, never below it
        entry_px = max(float(df.open.iloc[entry_i]), need)
        entry_px *= (1 + slip)

        exit_i = entry_i + P["hold"]
        exit_px, reason, exit_bar = None, None, None
        for j in range(entry_i + 1, min(entry_i + P["hold"] + 1, n)):
            gap_down = float(df.open.iloc[j]) < stop
            if gap_down:
                exit_px, exit_i, exit_bar, reason = float(df.open.iloc[j]), j, j, "stop_gap"
                break
            if float(df.low.iloc[j]) <= stop:
                exit_px, exit_i, exit_bar, reason = stop, j, j, "stop"
                break
        if exit_px is None:
            j = min(entry_i + P["hold"], n - 1)
            exit_px, exit_i, exit_bar, reason = float(df.close.iloc[j]), j, j, "time"
        exit_px *= (1 - slip - (sx if reason and reason.startswith("stop") else 0.0))

        net = (exit_px / entry_px - 1) - 2 * fee
        trades.append({
            "mkt": mkt, "sym": sym,
            "entry_date": str(df.date.iloc[entry_i].date()),
            "exit_date": str(df.date.iloc[exit_i].date()),
            "s1": round(s1, 4), "entry": round(entry_px, 4), "stop": round(stop, 4),
            "exit": round(exit_px, 4), "net": net, "reason": reason,
            "held": int(exit_i - entry_i), "atr_pct": round(atr_pct * 100, 2),
            "ret_5d": round(float(row.ret_5d) * 100, 2),
        })
        i = exit_i + 1
    return trades


def buy_hold(safe: str) -> dict | None:
    df = load(safe)
    if df is None:
        return None
    a, b = float(df.close.iloc[0]), float(df.close.iloc[-1])
    return {"symbol": safe, "start": str(df.date.iloc[0].date()),
            "end": str(df.date.iloc[-1].date()),
            "ret": b / a - 1, "start_px": a, "end_px": b}


def summarise(trades: list[dict], label: str) -> dict:
    if not trades:
        return {"label": label, "n": 0}
    net = np.array([t["net"] for t in trades])
    k, n = int((net > 0).sum()), len(net)
    lo, hi = wilson(k, n)
    wins = net[net > 0].sum()
    loss = -net[net < 0].sum()
    return {
        "label": label, "n": n,
        "win_pct": round(100 * k / n, 1), "win_ci95": [round(lo, 1), round(hi, 1)],
        "avg_net_pct": round(100 * float(net.mean()), 3),
        "median_net_pct": round(100 * float(np.median(net)), 3),
        "profit_factor": round(float(wins / loss), 2) if loss > 0 else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "data/bt10y_result.json"))
    ap.add_argument("--markets", default="us200,jp200,hk200")
    a = ap.parse_args()

    res: dict = {"generated": date.today().isoformat(), "rules": P, "markets": {}}
    all_trades: list[dict] = []

    for mkt in [m.strip() for m in a.markets.split(",")]:
        mup = market_up(mkt)
        if mup.empty:
            print(f"[{mkt}] 指數缺失，跳過")
            continue
        # Only this market's own symbols. The first version walked every cached
        # file for every market, so US names were charged JP fees and vice versa
        # and both markets printed the same 399.
        members = {safe for _, safe in universe(*MARKETS[MARKET_OF[mkt]])}
        mkt_trades: list[dict] = []
        syms = 0
        for p in sorted(DATA.glob("*.json")):
            safe = p.stem
            if safe not in members:
                continue
            df = load(safe)
            if df is None:
                continue
            syms += 1
            mkt_trades.extend(backtest_symbol(safe, mkt, df, mup))
        all_trades.extend(mkt_trades)
        print(f"[{mkt}] {syms} 隻（universe {len(members)}），{len(mkt_trades)} 筆交易", flush=True)

    # index availability is a data-quality note, not a verdict
    for mkt in ("us200", "jp200", "hk200"):
        res["markets"][mkt] = {
            "all": summarise([t for t in all_trades if t["mkt"] == mkt], "2016-2026"),
            "train_2016_2022": summarise([t for t in all_trades if t["mkt"] == mkt and t["entry_date"] < "2023-01-01"], "train"),
            "test_2023_2026": summarise([t for t in all_trades if t["mkt"] == mkt and t["entry_date"] >= "2023-01-01"], "test"),
        }
    res["combined"] = {
        "all": summarise(all_trades, "2016-2026"),
        "train_2016_2022": summarise([t for t in all_trades if t["entry_date"] < "2023-01-01"], "train"),
        "test_2023_2026": summarise([t for t in all_trades if t["entry_date"] >= "2023-01-01"], "test"),
    }
    res["buy_hold"] = {k: buy_hold(s) for k, s in
                       (("SPY", "SPY"), ("QQQ", "QQQ"), ("1306.T", "1306_T"), ("HSI", "IDX_HSI"))}
    res["buy_hold"] = {k: v for k, v in res["buy_hold"].items() if v}
    res["n_trades"] = len(all_trades)
    res["caveats"] = [
        "Universe = today's 600 names, so delisted names are missing; this "
        "flatters long results and hurts short results.",
        "Daily bars cannot resolve intraday order; stops assume a touch fills.",
        "JP fees are assumed, not documented by the site.",
        "The index market filter is the raw index close vs its own 200d MA; "
        "if the index series is stale the filter reads stale, which is why "
        "build_v2_signals.py reports index availability separately.",
    ]
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    (REPO / "data/bt10y_trades.json").write_text(json.dumps(all_trades), encoding="utf-8")

    print("\n=== v2 結果 ===")
    for mkt, d in res["markets"].items():
        t = d["test_2023_2026"]
        al = d["all"]
        if al.get("n"):
            print(f"  {mkt:6s} 全期 n={al['n']:5d} 勝率 {al['win_pct']:5.1f}% 平均 {al['avg_net_pct']:+.3f}% | "
                  f"樣本外 n={t.get('n',0):5d} 勝率 {t.get('win_pct',0):5.1f}% 平均 {t.get('avg_net_pct',0):+.3f}%")
    c = res["combined"]
    print(f"  合計   全期 n={c['all']['n']} 勝率 {c['all']['win_pct']}% 平均 {c['all']['avg_net_pct']:+.3f}%")
    print("\n=== 買入持有 ===")
    for k, v in res["buy_hold"].items():
        print(f"  {k:8s} {v['start']} → {v['end']}   {v['ret']*100:+.1f}%")


if __name__ == "__main__":
    main()
