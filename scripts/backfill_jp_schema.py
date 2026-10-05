"""backfill_jp_schema.py — Give JP200 snapshots the same shape as HK/US.

2026-10-05: /jp200/ rendered 200/200 rows with an empty Name, Last = 0.00 and
Chg% = +0.00% while HK and US were perfect. Cause was a schema split, not a
data outage:

  HK/US snapshot (batch_*_200_fresh.py)  -> { name, last_bar: {date,O,H,L,C,
                                             prev_close, chg_pct}, ... }
  JP snapshot    (fetch_jp_charts.py)    -> { asof, close, chg_pct, ... }

Every reader (build_dashboard.py, build_jp_index_minimal.py, build_home.py,
build_insights_radar.py) does `tj["last_bar"]["C"]`, which is {} for JP, so it
silently fell back to the literal 0. build_dashboard.data_asof() had already
been taught the JP `asof` fallback; the price columns never were.

This script normalises the *data* layer so the flat readers get real values,
and adds the missing Japanese company names (JP had no name source at all —
jp_universe_200.json is a bare ticker list).

  - name       <- data/jp_names.json (yfinance longName, cached)
  - last_bar   <- data/jp200/<t>_ohlc.json last two daily bars (full OHLC)

Run:  python3 scripts/backfill_jp_schema.py            # apply
      python3 scripts/backfill_jp_schema.py --check    # report only, no writes
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
JP_DIR = REPO / "data" / "jp200"
JP_UNIVERSE = REPO / "jp_universe_200.json"
JP_NAMES = REPO / "data" / "jp_names.json"


def fetch_names(tickers: list[str], out_path: Path) -> dict[str, str]:
    """Populate data/jp_names.json from yfinance. ~0.4s throttle per ticker."""
    import yfinance as yf  # type: ignore

    names: dict[str, str] = {}
    if out_path.exists():
        try:
            names = json.load(open(out_path))
        except Exception:
            names = {}
    todo = [t for t in tickers if not names.get(t)]
    print(f"fetching {len(todo)} JP names (have {len(tickers) - len(todo)})")
    for i, t in enumerate(todo, 1):
        try:
            info = yf.Ticker(t).info
            nm = (info.get("longName") or info.get("shortName") or "").strip()
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {t} ERR {type(e).__name__}")
            nm = ""
        if nm:
            names[t] = nm
        if i % 25 == 0:
            print(f"  [{i}/{len(todo)}] …")
        time.sleep(0.4)
    out_path.write_text(json.dumps(names, ensure_ascii=False, indent=1))
    return names


def last_bar_from_ohlc(ohlc: list[dict]) -> dict | None:
    """Build a HK/US-shaped last_bar from the JP ohlc bar list."""
    rows = [r for r in (ohlc or []) if isinstance(r, dict) and r.get("date")]
    if len(rows) < 2:
        return None
    cur, prev = rows[-1], rows[-2]
    c = cur.get("close")
    pc = prev.get("close")
    chg = 0.0
    if c and pc:
        chg = round((c - pc) / pc * 100, 2)
    return {
        "date": cur["date"],
        "O": cur.get("open"),
        "H": cur.get("high"),
        "L": cur.get("low"),
        "C": c,
        "prev_close": pc,
        "chg_pct": chg,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    tickers = json.load(open(JP_UNIVERSE))
    names: dict[str, str] = {}
    if not args.check and not JP_NAMES.exists():
        names = fetch_names(tickers, JP_NAMES)
    elif JP_NAMES.exists():
        names = json.load(open(JP_NAMES))
    print(f"jp_names.json: {len(names)}/{len(tickers)} names available")

    fixed_lb = fixed_nm = already = no_ohlc = 0
    for tk in tickers:
        safe = tk.replace(".", "_")
        js = JP_DIR / f"{safe}.json"
        oh = JP_DIR / f"{safe}_ohlc.json"
        if not js.exists():
            continue
        d = json.load(open(js))
        lb = d.get("last_bar") or {}
        ohlc = json.load(open(oh)) if oh.exists() else None
        if not ohlc:
            no_ohlc += 1

        want_lb = last_bar_from_ohlc(ohlc)
        if want_lb and lb.get("C") == want_lb["C"] and lb.get("date") == want_lb["date"]:
            already += 1
        else:
            fixed_lb += 1
            if not args.check:
                d["last_bar"] = want_lb
                # keep the flat fields the JP reader still uses, in sync
                if want_lb:
                    d["asof"] = want_lb["date"]
                    d["close"] = want_lb["C"]
                    d["chg_pct"] = want_lb["chg_pct"]

        if not d.get("name") and names.get(tk):
            fixed_nm += 1
            if not args.check:
                d["name"] = names[tk]
        elif d.get("name"):
            already += 1

        if not args.check:
            js.write_text(json.dumps(d, ensure_ascii=False))

    verb = "WOULD FIX" if args.check else "FIXED"
    print(f"{verb}: last_bar={fixed_lb} name={fixed_nm} | already ok/seen={already} | missing ohlc={no_ohlc}")


if __name__ == "__main__":
    main()
