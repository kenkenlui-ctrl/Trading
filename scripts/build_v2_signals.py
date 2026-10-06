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

# 2026-10-05: close-hold requirement, measured on our own bars.
#
# A/B (scripts/ab_close_hold.py, data/bt10y 10y bars, same fill rules, same
# costs, ONLY the entry qualification differs) — reject the fill when the
# session closes back below S1 after touching it:
#
#   US  train 2016-22  kept 51.0%  +0.888% -> +1.913%   t +5.96 -> +10.67
#   US  test  2023-26  kept 51.5%  +0.783% -> +1.777%   t +3.73 -> +7.67
#   JP  train 2016-22  kept 46.7%  +0.149% -> +1.034%   t +1.54 -> +5.08
#   JP  test  2023-26  kept 51.1%  +1.211% -> +2.414%   t +6.40 -> +9.31
#
# All four cells improve on both average and date-clustered t, so it is not a
# window artefact. Each cell has 2,193-5,812 trades, far above the >=1,000
# bar this project set after the 2026-10-01 pilot-sample lesson.
CLOSE_HOLD = True

# The shipped rule is now OUR measured rule, so the setup statistics attached
# to each signal must be ours too. The previously published figures came from
# the third-party 10-year audit and described a rule WITHOUT the close-hold
# requirement — quoting them next to a close-hold order would attribute our
# rule's performance to someone else's study.
#
# HONESTY NOTE 1 — our own engine did not reproduce the third-party baseline
# (ours was ~1.7x higher on the same rule), so the absolute levels here are not
# comparable to the audit's. The A/B delta is valid because both arms run through
# one engine; the levels are not.
#
# HONESTY NOTE 2 — these are PER-TRADE figures. They are NOT evidence that the
# strategy beats simply owning the same names. On the same bars and the same
# 10-slot book, v2 compounded at 27.2%/yr in the US against 27.4%/yr for an
# equal-weight monthly rebalance of the very same 201 names (portfolio_test.py).
# The per-trade edge is real; the excess return over holding the universe is
# approximately zero in the US. Japan showed +6.9pp/yr but with a deeper
# drawdown, and that one has not been confirmed against a point-in-time universe.
#
# Re-measured 2026-10-05 on data/bt10y2 (407 symbols re-fetched one at a time;
# 8 cached files were corrupt) with bars whose [entry-210d, exit] window touches
# a data glitch excluded. See bt_clean.py.
V2_OOS = {
    "us200": {"win_pct": 60.3, "win95": [None, None], "avg_net_pct": 1.77, "n": 2982},
    "jp200": {"win_pct": 65.5, "win95": [None, None], "avg_net_pct": 2.35, "n": 2187},
}
V2_SOURCE = ("本站自測（Yahoo 10 年日線，同一引擎 A/B，2023–26 樣本外）；"
             "含收市企穩 S1 條件。單筆數字，唔等於跑贏被動持有同一批股票，見分析方法論頁")
V2_IS_THIRD_PARTY = False

# 2026-10-02: yfinance started returning a NaN last bar for every US ETF
# (SPY/VOO/IVV/QQQ/DIA all NaN) while ^GSPC resolved fine. Each market
# therefore lists fallback symbols; the index is also the more direct proxy
# for "is this market up" than a tracker ETF.
MARKETS = {
    "us200": {"index": "SPY", "fallback": ["^GSPC", "^NDX", "VOO"],
              "tz": "America/New_York", "close": (16, 30)},
    "jp200": {"index": "1306.T", "fallback": ["^N225", "1306.T"],
              "tz": "Asia/Tokyo", "close": (15, 50)},
    "hk200": {"index": "2800.HK", "fallback": ["^HSI", "2800.HK"],
              "tz": "Asia/Hong_Kong", "close": (16, 40)},
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


INDEX_CACHE = REPO / "data" / "index_state.json"


def _cache_read() -> dict:
    try:
        return json.loads(INDEX_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_write(sym: str, up: bool, asof: str) -> None:
    d = _cache_read()
    d[sym] = {"up": up, "asof": asof}
    INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_CACHE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def index_up(index_sym: str, fallback: list[str] | None = None
             ) -> tuple[bool | None, str | None, str | None, bool]:
    """Market filter: index T-1 close above its own 200d MA.

    Returns (up, asof, error, from_cache).

    2026-10-02: this used to return False on ANY exception, so a Yahoo
    rate-limit was indistinguishable from a bearish market and silently zeroed
    every signal. A data failure must never be publishable as a view.

    Yahoo rate-limits aggressively (429) when a full 600-ticker refresh is
    running, which is exactly when this runs. On failure we fall back to the
    last good reading in data/index_state.json and flag from_cache=True so the
    page can say the filter is based on a stale reading rather than showing
    either a wrong verdict or a silent zero.
    """
    errors = []
    for sym in [index_sym] + list(fallback or []):
        try:
            return _read_index(sym)
        except Exception as e:
            errors.append(f"{sym}: {type(e).__name__}")
    c = _cache_read().get(index_sym)
    if c:
        return bool(c["up"]), c["asof"], \
            f"全部候選取數失敗（{', '.join(errors)}）；用 {c['asof']} 快取", True
    return None, None, "全部候選取數失敗：" + ", ".join(errors), False


def _read_index(index_sym: str) -> tuple[bool, str, None, bool]:
    """Read one symbol's 200d-MA verdict. Raises on any unusable data."""
    import yfinance as yf
    d = yf.Ticker(index_sym).history(period="2y", auto_adjust=True)
    if d is None or d.empty:
        raise ValueError("no data returned")
    c = d["Close"]
    ma = c.rolling(200).mean()
    last_c, last_ma = c.iloc[-1], ma.iloc[-1]
    # NaN compares False in Python, so a missing last bar would read as
    # "index below its 200d MA" and publish a bearish verdict built on nothing.
    if pd.isna(last_c) or pd.isna(last_ma):
        raise ValueError(f"NaN in series (close={last_c}, 200dMA={last_ma})")
    up, asof = bool(last_c > last_ma), str(d.index[-1].date())
    _cache_write(index_sym, up, asof)
    return up, asof, None, False


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


def bars_freshness(bars: dict[str, pd.DataFrame]) -> tuple[str, int, int]:
    """(modal last-bar date, count at that date, total count) for a market's bars.

    2026-10-05: the daily pipeline refreshes the per-ticker snapshot JSON
    (which drives the hub tables) but nothing has been writing
    public/<mkt>/ohlc/*_ohlc.json for HK/US since 2026-10-02. v2 reads the
    ohlc, so the "今日限價單" block was being computed from bars 2-6 sessions
    old while the page header showed the current T-1. Two data dates on one
    page, one badge, no error. The per-symbol modal count is the same
    trick data_asof() uses: a handful of suspended tickers cannot drag the
    verdict off the real session.
    """
    from collections import Counter
    c = Counter()
    for df in bars.values():
        if len(df):
            c[str(df.index[-1].date())] += 1
    if not c:
        return "", 0, 0
    d, n = c.most_common(1)[0]
    return d, n, sum(c.values())


def main() -> None:
    equity = 100_000.0
    mkts = [m for m in ("us200", "jp200")] + (["hk200"] if INCLUDE_HK else [])
    out_rows, screen = [], {}
    market_state = {}

    for mkt in mkts:
        cfg = MARKETS[mkt]
        up, idx_date, err, cached = index_up(cfg["index"], cfg.get("fallback"))
        market_state[mkt] = {"index": cfg["index"], "index_asof": idx_date,
                             "up": up, "error": err, "from_cache": cached}
        if err:
            # Do not publish "no signals" when the cause is our own data failure.
            screen[f"{mkt}:INDEX_UNAVAILABLE"] = 1
            print(f"  !! {mkt}: market filter unknown — {err}")
            continue
        if not up:
            screen[f"{mkt}:market-filter-off"] = 1
            continue
        bars = bars_from_public(mkt)

        # Fail closed on stale bars. A limit price computed from an old bar is
        # not a conservative estimate, it is a wrong number wearing a fresh
        # timestamp — the exact "静默过期" failure. Skip the market and say so
        # rather than publishing it.
        bar_date, at_modal, total = bars_freshness(bars)
        market_state[mkt]["bars_asof"] = bar_date
        market_state[mkt]["bars_at_modal"] = f"{at_modal}/{total}"
        try:
            stale_days = (date.fromisoformat(idx_date[:10]) - date.fromisoformat(bar_date)).days
        except Exception:
            stale_days = None
        # Markets have different calendars; allow 4 days so a long weekend
        # (Golden Week, Easter) does not silently suppress a market.
        if not bar_date or (stale_days is not None and stale_days > 4):
            screen[f"{mkt}:BARS_STALE"] = 1
            print(f"  !! {mkt}: bars end {bar_date} ({at_modal}/{total}) but index is "
                  f"{idx_date} — {stale_days}d behind, refusing to publish stale signals")
            continue

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
            "entry": ("limit BUY at S1, next session only; skip if the session opens at or "
                      "below S1. 2026-10-05: additionally REJECT the fill if that session "
                      "closes back below S1 — a touch that fails to hold is a broken level, "
                      "not a buy. Measured: halves trade count, ~doubles per-trade return on "
                      "both markets and both windows."),
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
        "performance_is_third_party": V2_IS_THIRD_PARTY,
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
