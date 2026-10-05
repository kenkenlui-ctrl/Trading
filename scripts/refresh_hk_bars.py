"""refresh_hk_bars.py — Bring HK bars to the current session, end to end.

WHY
    2026-10-05: the daily pipeline refreshes the per-ticker snapshot for HK but
    nothing refreshes charts/hk200/*_ohlc.json, which is what build_v2_signals
    reads. So the v2 limit-buy plan was computed from bars ending 2026-09-30
    while the page header read T-1 2026-10-02. JP got the same fix inside
    fetch_jp_charts.py; this is the HK equivalent, which never existed because
    HK is produced by batch_hk200_fresh.py + the daily-sr-chart skill rather
    than by a single script.

    Note the honest limitation: 2026-10-02 an independent check found yfinance
    does not serve .HK single names (the 10-year store in data/bt10y has 1 HK
    file out of 407). If that still holds, this script will fail loudly per
    ticker rather than write empty bars — the failure is the signal.

WHAT IT DOES
    For each ticker in the current HK universe:
      1. fetch 400 daily bars (yfinance)
      2. run daily-sr-chart to rebuild phase / S-R / action_plan
      3. write charts/hk200/<T>_ohlc.json  (raw series — what v2 reads)
      4. write charts/hk200/<T>.json       (snapshot + last_bar, canonical shape)

    400 bars, not 200: build_v2_signals rejects any series under 260 rows, and
    writing 200 silently took JP v2 signals from 91 to 0.

Usage:
    python3 scripts/refresh_hk_bars.py                 # whole universe
    python3 scripts/refresh_hk_bars.py --limit 5       # pilot first
    python3 scripts/refresh_hk_bars.py --tickers 00700.HK,00005.HK
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
UNIVERSE = REPO / "hk_universe_200.json"
JSON_DIR = REPO / "charts" / "hk200"
CHART_DIR = REPO / "charts" / "hk200"
ASOF = ""

sys.path.insert(0, "/Users/kenken/.minimax/skills/daily-sr-chart/scripts")
import daily_sr  # type: ignore  # noqa: E402


def fetch(code: str, days: int = 400) -> pd.DataFrame | None:
    """Tencent daily kline.

    2026-10-05: yfinance stopped serving .HK single names (404 for every
    ticker tried, including 09988.HK). Tencent's kline endpoint still returns
    them and is what the HK pipeline already used for the universe ranking, so
    this is the surviving source. Format is
    [date, open, close, high, low, volume, {...}] — note close comes BEFORE
    high/low, which is the opposite of OHLC ordering.
    """
    import urllib.request
    try:
        bare = code.split(".")[0].zfill(5)
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
               f"?param=hk{bare},day,,,420,qfq")
        with urllib.request.urlopen(url, timeout=15) as r:
            d = json.loads(r.read())
        node = (d.get("data") or {}).get(f"hk{bare}") or {}
        rows = node.get("qfqday") or node.get("day") or []
        if len(rows) < 60:
            return None
        recs = [{"date": x[0], "open": float(x[1]), "close": float(x[2]),
                 "high": float(x[3]), "low": float(x[4]),
                 "volume": float(x[5]) if len(x) > 5 else 0.0} for x in rows]
        df = pd.DataFrame(recs).set_index("date")
        df.index = pd.to_datetime(df.index)
        return df.tail(days)
    except Exception as e:
        print(f"  {code} fetch err: {type(e).__name__}: {e}")
        return None


def bars_of(df: pd.DataFrame) -> list[dict]:
    return [
        {
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": float(r["volume"]) if "volume" in r else 0.0,
            "date": pd.Timestamp(idx).strftime("%Y-%m-%d"),
        }
        for idx, r in df.iterrows()
    ]


def process(code: str) -> tuple[str, bool, str]:
    safe = code.replace(".", "_")
    out_png = CHART_DIR / f"{safe}.png"
    out_json = JSON_DIR / f"{safe}.json"
    try:
        df = fetch(code, days=400)
        if df is None or len(df) < 60:
            return (code, False, "no bars")

        try:
            snapshot = daily_sr.run(code, str(out_png), days=200,
                                     source="futu", asof=ASOF)
        except Exception as e:
            return (code, False, f"daily_sr: {type(e).__name__}")
        if not snapshot:
            return (code, False, "empty snapshot")

        bars = bars_of(df)
        last, prev = bars[-1], bars[-2]
        chg = round((last["close"] - prev["close"]) / prev["close"] * 100, 2) if prev["close"] else 0.0

        # Keep whatever daily_sr computed; overwrite the quote fields with
        # values derived from the same bars we are publishing, so the table
        # price and the v2 input can never disagree again.
        snapshot["ticker"] = code
        snapshot["ticker_user"] = code
        snapshot["last_bar"] = {
            "date": last["date"], "O": last["open"], "H": last["high"],
            "L": last["low"], "C": last["close"],
            "prev_close": prev["close"], "chg_pct": chg,
        }
        snapshot.setdefault("asof", last["date"])
        snapshot.setdefault("name", "")
        (JSON_DIR / f"{safe}_ohlc.json").write_text(
            json.dumps(bars, ensure_ascii=False), encoding="utf-8")
        out_json.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        return (code, True, last["date"])
    except Exception as e:
        return (code, False, f"{type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--pause", type=float, default=0.3)
    ap.add_argument("--asof", default="", help="YYYY-MM-DD session to trim to")
    a = ap.parse_args()
    global ASOF
    ASOF = a.asof or ""

    if a.tickers:
        uni = [t.strip() for t in a.tickers.split(",") if t.strip()]
    else:
        uni = json.load(open(UNIVERSE))
        if a.limit:
            uni = uni[: a.limit]

    JSON_DIR.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    asofs: dict[str, int] = {}
    for i, code in enumerate(uni, 1):
        c, good, info = process(code)
        if good:
            ok += 1
            asofs[info] = asofs.get(info, 0) + 1
        else:
            fail += 1
            print(f"  [{i}/{len(uni)}] {c} FAIL: {info}")
        if i % 25 == 0 or i == len(uni):
            print(f"  [{i}/{len(uni)}] ok={ok} fail={fail} asof={asofs}")
        time.sleep(a.pause)

    print(f"\ndone: ok={ok} fail={fail}")
    print("asof distribution:", asofs)
    if asofs:
        top = max(asofs, key=asofs.get)
        print(f"modal session: {top} ({asofs[top]}/{ok})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
