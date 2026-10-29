"""market_sessions.py — write down what each market has most recently closed.

WHY
    The site must be able to say "your data is stale" without anyone
    hand-maintaining a list of holidays. That needs one fact the site's own
    stock bars cannot supply: the market's latest CLOSED session, measured
    independently of the 200 names we publish.

    The index is that independent measure. Each market's benchmark index
    closes on the market's own calendar, so its last close date IS the
    market's last session. We already depend on the vendor that serves it;
    this adds no new data source, it reuses the one that is already in the
    pipeline.

    The output is data/market_sessions.json, read at build time by
    scripts/staleness.py. The build never touches the network.

WHEN IT FAILS
    A vendor that cannot be reached is recorded as status "source_down" with
    the error, NOT as an empty file and NOT as a stale-but-plausible date.
    The site shows a different message for "we could not check" than for "we
    checked and you are behind" — the two are different facts and the
    visitor is owed the difference.

USAGE
    python3 scripts/market_sessions.py            # write the record
    python3 scripts/market_sessions.py --print    # show it, do not write
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "market_sessions.json"

# market -> (index ticker, label, source, timezone, close hour)
MARKETS = {
    "hk200": ("^HSI", "Hang Seng Index", "yfinance", "Asia/Hong_Kong", 16),
    "us200": ("^GSPC", "S&P 500", "yfinance", "America/New_York", 16),
    "jp200": ("^N225", "Nikkei 225", "yfinance", "Asia/Tokyo", 15),
}


def _last_close(index_ticker: str, market: str) -> str:
    """The index's most recent CLOSED session, as YYYY-MM-DD.

    2026-10-29: this returned TODAY's date while the market was still open.
    The vendor publishes an in-progress daily bar for the session in
    progress, so "the last bar" is not "the last CLOSED session" — and the
    difference is a false STALE banner on the site every morning, for a
    market that has not opened yet.

    So: if the newest bar is dated today in the market's own timezone and
    the close has not happened yet, that bar is today's unfinished session
    and the last closed session is the one before it.
    """
    import yfinance as yf

    df = yf.Ticker(index_ticker).history(period="10d", auto_adjust=False)
    if df is None or len(df) == 0:
        raise RuntimeError(f"no bars returned for {index_ticker}")
    dates = [str(d.date()) for d in df.index]

    tzname, close_hour = MARKETS[market][3], MARKETS[market][4]
    try:
        from zoneinfo import ZoneInfo
        now_local = datetime.now(ZoneInfo(tzname))
    except Exception:
        now_local = datetime.now()
    today_local = now_local.date().isoformat()
    if dates and dates[-1] == today_local and now_local.hour < close_hour:
        dates = dates[:-1]
    if not dates:
        raise RuntimeError(f"no completed session found for {index_ticker}")
    return dates[-1]


def collect() -> dict:
    now = datetime.now().astimezone()
    markets: dict = {}
    for key, (ticker, label, source, _tz, _close) in MARKETS.items():
        try:
            sess = _last_close(ticker, key)
            markets[key] = {
                "latest_closed_session": sess,
                "status": "ok",
                "index": ticker,
                "index_label": label,
                "source": source,
                "checked_at": now.isoformat(timespec="seconds"),
                "error": "",
            }
        except Exception as e:  # noqa: BLE001 — the reason is recorded, not swallowed
            markets[key] = {
                "latest_closed_session": "",
                "status": "source_down",
                "index": ticker,
                "index_label": label,
                "source": source,
                "checked_at": now.isoformat(timespec="seconds"),
                "error": f"{type(e).__name__}: {e}",
            }
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "markets": markets,
    }


def main() -> int:
    rec = collect()
    if "--print" in sys.argv:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    for k, v in rec["markets"].items():
        print(f"{k}: {v['status']:12s} {v['latest_closed_session'] or '-'}")
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
