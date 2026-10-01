"""fetch_capital_anomaly.py — Batch-fetch Futu get_financial_unusual for the universe.

Stores qualifying anomalies into data/dsa_hk.db table `capital_anomaly`
(code, report_date, anomaly_type, content). Rows with no anomaly are NOT
stored — absence of rows = "no anomaly" (the page simply doesn't render
the section).

Usage:
  python3 scripts/fetch_capital_anomaly.py                # both markets, today
  python3 scripts/fetch_capital_anomaly.py --date 2026-08-28
  python3 scripts/fetch_capital_anomaly.py --market HK

Requires Futu OpenD running on 127.0.0.1:11111 (user opens it manually —
script checks the port first and exits with a clear message if not reachable).

~400 stocks ≈ 3.5 min measured (0.9s/stock on one shared OpenQuoteContext).
"""
from __future__ import annotations
import argparse
import json
import socket
import sqlite3
import sys
import re
import time
from datetime import date, datetime
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
DB_PATH = REPO / "data" / "dsa_hk.db"
FUTU_HOST = "127.0.0.1"
FUTU_PORT = 11111

# Map ticker → futu code
def to_futu_code(ticker: str) -> str:
    if ticker.endswith(".HK"):
        return f"HK.{ticker[:-3]}"
    return f"US.{ticker}"


def check_opend() -> None:
    try:
        s = socket.create_connection((FUTU_HOST, FUTU_PORT), timeout=3)
        s.close()
    except OSError:
        print("❌ Futu OpenD 冇開（127.0.0.1:11111 連唔上）。")
        print("   開咗 OpenD 之後再跑呢個 script。")
        sys.exit(2)


def load_universe(market: str | None) -> list[str]:
    codes = []
    if market in (None, "HK"):
        hk = json.loads((REPO / "hk_universe_200.json").read_text())
        codes += [f"{c}.HK" if not c.endswith(".HK") else c for c in hk]
    if market in (None, "US"):
        us_path = Path("/Users/kenken/dev/dsa-hk/charts/us200/us_top200_fresh.json")
        if us_path.exists():
            us = json.loads(us_path.read_text())
            codes += us
        else:
            us = json.loads((REPO / "us_universe_200.json").read_text())
            codes += [c for c in us if not c.endswith(".HK")]
    return codes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat(), help="report_date label (YYYY-MM-DD)")
    ap.add_argument("--market", choices=["HK", "US"], default=None)
    ap.add_argument("--time-range", type=int, default=7, help="days window for unusual scan")
    args = ap.parse_args()

    check_opend()
    from futu import OpenQuoteContext, RET_OK
    import logging
    logging.getLogger("futu").setLevel(logging.WARNING)

    codes = load_universe(args.market)
    print(f"=== fetch_capital_anomaly: {len(codes)} tickers, window {args.time_range}d, date={args.date} ===")

    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")

    ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    ok = err = no_data = 0
    stored = 0
    t0 = time.time()

    try:
        for i, code in enumerate(codes, 1):
            futu_code = to_futu_code(code)
            try:
                ret, df = ctx.get_financial_unusual(futu_code)
            except Exception as e:
                err += 1
                continue
            if ret != RET_OK:
                err += 1
                continue
            # get_financial_unusual returns a dict: {err_code, retMsg, time_range, content}
            if not isinstance(df, dict):
                err += 1
                continue
            if df.get("err_code") != 0:
                err += 1
                continue
            content = (df.get("content") or "").strip()
            if not content or content in ("无异常", "nan"):
                no_data += 1
                continue

            # Classify the free-text content by the three anomaly classes the
            # skill defines: 資金分布/經紀商, 資金流向, 賣空.
            def classify(c: str) -> str:
                if "卖空" in c or "沽空" in c:
                    return "short_selling"
                if "主力资金" in c and ("净流入" in c or "净流出" in c):
                    return "funds_flow"
                return "funds_distribution_broker"

            # The API may return multiple paragraphs; store per paragraph so the
            # page can render one <li> each.
            paragraphs = [p.strip() for p in re.split(r"\n(?=\[timestamp)", content) if p.strip()]
            if not paragraphs:
                paragraphs = [content]
            rows = [(code, args.date, classify(p), p) for p in paragraphs]
            con.executemany(
                "INSERT OR REPLACE INTO capital_anomaly (code, report_date, anomaly_type, content) VALUES (?,?,?,?)",
                rows,
            )
            stored += len(rows)
            ok += 1

            if i % 50 == 0:
                con.commit()
                rate = i / (time.time() - t0)
                eta = (len(codes) - i) / rate if rate > 0 else 0
                print(f"  {i}/{len(codes)} ok={ok} err={err} nodata={no_data} stored={stored} {rate:.1f}/s ETA {eta:.0f}s", flush=True)
    finally:
        con.commit()
        con.close()
        ctx.close()

    dt = time.time() - t0
    print(f"Done in {dt:.0f}s: ok={ok} err={err} nodata={no_data} anomalies_stored={stored}")


if __name__ == "__main__":
    main()