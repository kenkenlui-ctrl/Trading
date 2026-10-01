#!/usr/bin/env python3
"""build_v2_signals.py — generate today's v2 limit-buy plans from our own bars.

WHAT THIS IS
    The site publishes a daily action plan built by the 10-step framework
    (SELL_R1 / BUY_S1 / BREAK_LONG / BREAK_SHORT). A third-party 10-year
    backtest of those rules (Manus, 2026-10-01) found they have no positive
    edge over 2016-2026, and that the per-stock "60d win%" badge carries no
    information about the next trade (rank correlation -0.001 / -0.004).

    v2 is the replacement rule set from that same study: long-only, BUY at S1
    next session, 3xATR stop, no target, 10-session time exit, trend + market
    filters, US/JP only.

PROVENANCE (important — the numbers are NOT ours)
    The signal *rules* are implemented here against our own OHLC. The
    *performance* statistics attached to each signal are the study's published
    out-of-sample figures, reproduced verbatim with attribution. We have not
    re-run their backtest, so the site must not present these as our own
    verified track record.

DATA
    Reads the same per-ticker bars the rest of the pipeline uses
    (public/<market>/ohlc/<ticker>_ohlc.json, 400 daily bars) and converts
    them to the Yahoo-chart shape that engine/core.py::load_bars expects.
    Index bars for the market filter are fetched from Yahoo on the fly.

OUTPUT
    public/v2-signals.json  +  a CSV for auditing.
"""
from __future__ import annotations
import csv
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
from core import features  # noqa: E402

PARAMS = dict(stop_atr=3.0, hold=10, atr_max=0.06, box_max=0.35,
               risk=0.01, max_notional=0.10)

# Third-party 10-year backtest, out-of-sample 2023-2026, published by the study.
# Reproduced WITH attribution — not independently reproduced by us.
V2_OOS = {
    "us200": {"win_pct": 50.8, "win95": [49.4, 52.3], "avg_net_pct": 0.45, "n": 4656},
    "jp200": {"win_pct": 53.7, "win95": [None, None], "avg_net_pct": 0.70, "n": 3305},
    "hk200": {"win_pct": 48.0, "win95": [None, None], "avg_net_pct": 0.02, "n": 2447},
}
V2_SOURCE = ("第三方 10 年回測（2016–2026，2023–26 樣本外），"
             "規則見 Leeks Terminal 10 年訊號審計報告")

MARKETS = {
    "us200": {"index": "SPY", "tz": "America/New_York", "close": (16, 30)},
    "jp200": {"index": "1306.T", "tz": "Asia/Tokyo", "close": (15, 50)},
    "hk200": {"index": "2800.HK", "tz": "Asia/Hong_Kong", "close": (16, 40)},
}
INCLUDE_HK = False  # study found HK is ~0 edge after 0.45% round-trip


def bars_from_public(market: str) -> dict[str, pd.DataFrame]:
    """{safe_ticker: DataFrame(date, open, high, low, close, volume)}"""
    d = REPO / "public" / market / "ohlc"
    out: dict[str, pd.DataFrame] = {}
    for p in sorted(d.glob("*_ohlc.json")):
        try:
            rows = json.loads(p.read_text())
        except Exception:
            continue
        if not isinstance(rows, list) or len(rows) < 260:
            continue
        df = pd.DataFrame(rows)
        if not {"date", "open", "high", "low", "close"} <= set(df.columns):
            continue
        df["date"] = pd.to_datetime(df["date"])
        df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("date")
        # features() is shift()-based, so the DatetimeIndex is load-bearing.
        df = df.set_index("date")
        out[p.name[: -len("_ohlc.json")]] = df
    return out


def index_up(index_sym: str) -> tuple[bool | None, str | None, str | None]:
    """Market filter: index T-1 close above its own 200d MA.

    Returns (up, asof, error). `up` is None when the index could not be read.
    2026-10-02: this used to return False on any exception, which meant a
    Yahoo rate-limit looked exactly like a bearish market and silently zeroed
    every signal. A data failure must never be publishable as a view.
    """
    try:
        import yfinance as yf
        d = yf.Ticker(index_sym).history(period="2y", auto_adjust=True)
        if d is None or d.empty:
            return None, None, f"{index_sym}: no data returned"
        c = d["Close"]
        ma = c.rolling(200).mean()
        if ma.empty or pd.isna(ma.iloc[-1]):
            return None, None, f"{index_sym}: 200d MA unavailable"
        return bool(c.iloc[-1] > ma.iloc[-1]), str(d.index[-1].date()), None
    except Exception as e:
        return None, None, f"{index_sym}: {type(e).__name__}: {e}"


def plan(market: str, symbol: str, df: pd.DataFrame, equity: float):
    nxt = df.index[-1] + pd.offsets.BDay(1)
    bb = pd.concat([df, df.iloc[[-1]].set_index(pd.DatetimeIndex([nxt]))])
    f = features(bb).iloc[-1]
    need = ("S1", "R1", "R2", "ma200", "ma200_slope", "atr_pct")
    if any(pd.isna(f[k]) for k in need):
        return None, "insufficient history"
    if not (f.prev_close > f.ma200 and f.ma200_slope > 0):
        return None, "not in uptrend"
    if f.atr_pct >= PARAMS["atr_max"] or f.box_w >= PARAMS["box_max"]:
        return None, "guard rail (ATR/box)"
    s1 = float(f.S1)
    stop = s1 * (1 - PARAMS["stop_atr"] * f.atr_pct)
    risk_ps = s1 - stop
    if risk_ps <= 0:
        return None, "degenerate risk"
    shares = PARAMS["risk"] * equity / risk_ps
    shares = min(shares, PARAMS["max_notional"] * equity / s1)
    return {
        "market": market,
        "symbol": symbol,
        "asof": str(df.index[-1].date()),
        "last_close": round(float(f.prev_close), 2),
        "entry_s1": round(s1, 2),
        "dist_to_entry_pct": round((s1 / f.prev_close - 1) * 100, 2),
        "stop": round(stop, 2),
        "stop_dist_pct": round(-PARAMS["stop_atr"] * f.atr_pct * 100, 2),
        "r1": round(float(f.R1), 2),
        "atr_pct": round(float(f.atr_pct) * 100, 2),
        "ret_5d_pct": round(float(f.ret_5d) * 100, 2),
        "shares_at_1pct_risk": int(shares),
        "exit_rule": f"buy fill 後第 {PARAMS['hold']} 個交易日收市離場（無固定目標）",
    }, "ok"


def main() -> None:
    equity = 100_000.0
    mkts = [m for m in ("us200", "jp200")] + (["hk200"] if INCLUDE_HK else [])
    out_rows, screen = [], {}
    market_state = {}

    for mkt in mkts:
        cfg = MARKETS[mkt]
        up, idx_date, err = index_up(cfg["index"])
        market_state[mkt] = {"index": cfg["index"], "index_asof": idx_date,
                             "up": up, "error": err}
        if err:
            # Do not publish "no signals" when the cause is our own data failure.
            screen[f"{mkt}:INDEX_UNAVAILABLE"] = 1
            print(f"  !! {mkt}: market filter unknown — {err}")
            continue
        if not up:
            screen[f"{mkt}:market-filter-off"] = 1
            continue
        bars = bars_from_public(mkt)
        for sym, df in bars.items():
            p, why = plan(mkt, sym, df, equity)
            screen[why] = screen.get(why, 0) + 1
            if p:
                out_rows.append(p)

    out_rows.sort(key=lambda r: (r["market"], r["ret_5d_pct"]))  # most oversold first

    payload = {
        "generated_for": "T-1 close",
        "rules": {
            "direction": "long only",
            "entry": "limit BUY at S1, next session only; skip if the session opens at or below S1",
            "stop": "S1 x (1 - 3 x ATR%)",
            "target": "none",
            "exit": f"close of session {PARAMS['hold']} after fill",
            "stock_filter": "T-1 close > 200d MA and 200d MA rising over 20 sessions",
            "market_filter": "index T-1 close > its 200d MA",
            "guard_rails": "ATR% < 6%, 20d box width < 35%",
            "sizing": "1% equity risk per trade, capped at 10% notional per name",
            "markets": [m for m in ("us200", "jp200") if m in mkts],
        },
        "performance_source": V2_SOURCE,
        "performance_is_third_party": True,
        "setup_oos": V2_OOS,
        "market_state": market_state,
        "screen": screen,
        "count": len(out_rows),
        "signals": out_rows,
    }

    (REPO / "public" / "v2-signals.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    csv_p = REPO / "data" / "v2_signals.csv"
    csv_p.parent.mkdir(exist_ok=True)
    if out_rows:
        with open(csv_p, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)

    print(f"v2 signals: {len(out_rows)} limit orders")
    print(f"  market filter: { {k: v['up'] for k, v in market_state.items()} }")
    print(f"  screen: {screen}")
    for m in ("us200", "jp200"):
        n = sum(1 for r in out_rows if r["market"] == m)
        print(f"  {m}: {n}")


if __name__ == "__main__":
    main()
