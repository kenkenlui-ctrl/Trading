#!/usr/bin/env python3
"""
lot_size.py — the tradable order quantity (単元株数) for each ticker.

WHY THIS EXISTS
    build_v2_signals.plan() sizes a position as

        shares = 1% equity / (S1 - stop)          -> e.g. 3.41 shares
        shares = min(shares, 10% equity / S1)
        shares = int(shares)                       -> 3

    and publishes that integer as `shares_at_1pct_risk`. For Japan that number
    is not an orderable quantity at all: the exchange minimum is one 単元
    (unit), 100 shares for essentially every listed equity. A published "buy 3
    shares of 2503" is an instruction nobody can execute, and the same
    calculation produces 0 for names whose per-share risk exceeds the whole
    risk budget — which is what currently ships in public/v2-signals.json.

    The size is still a real number. It is just not a real ORDER.

WHAT THIS DOES
    Gives plan() a floor-to-unit sizing so the published quantity is one a
    broker will accept, and says out loud when one unit is too big for the
    stated risk budget instead of silently printing a fractional share count.

DATA PROVENANCE
    Three tiers, and the tier is reported with every answer so a caller can
    tell a measured unit from a market-wide assumption:

      "futu"   measured per ticker from Futu OpenD (scripts/lot_size.py
               --refresh writes data/lot_sizes.json)
      "cache"  read back from that file on a later run
      "market" market-wide default when neither is available. Defaults are the
               exchange norm, NOT a measurement of the individual stock: a few
               JP listings trade in units of 10 or 1, and HK board lots vary by
               price band. Anything returned as "market" is an assumption.

Usage:
    from lot_size import lot_for
    lot, src = lot_for("jp200", "2503_T")
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "data" / "lot_sizes.json"

# Exchange norms, used only when a ticker has no measured value. JP is 100 for
# virtually all listed equities; US has no unit concept; HK varies by price
# band and is not covered by this script's markets today.
MARKET_DEFAULT = {"us200": 1, "jp200": 100}

FUTU_PREFIX = {"us200": "US.", "jp200": "JP."}


def _load_cache() -> dict:
    try:
        d = json.loads(CACHE.read_text(encoding="utf-8"))
        return d.get("lots", {}) if isinstance(d, dict) else {}
    except Exception:
        return {}


def _bare(symbol: str) -> str:
    """1379_T -> 1379 ; the _T suffix is this project's own market tag."""
    return symbol[:-2] if symbol.endswith("_T") else symbol


def lot_for(market: str, symbol: str) -> tuple[int, str]:
    """(unit size, provenance) for one ticker. Never raises."""
    lots = _load_cache()
    v = lots.get(f"{market}:{symbol}")
    if isinstance(v, int) and v > 0:
        return v, "cache"
    return MARKET_DEFAULT.get(market, 1), "market"


def floor_to_lot(shares: float, lot: int) -> int:
    """Largest whole multiple of `lot` not exceeding `shares`."""
    if lot <= 1:
        return max(0, int(shares))
    return int(shares // lot) * lot


def refresh(markets=("jp200",), chunk: int = 60, verbose: bool = True) -> dict:
    """Measure unit sizes from Futu OpenD and write data/lot_sizes.json.

    Only markets with a real 単元株数 are measured. US tickers are written as
    lot=1 because that is the definition of the US market, not a measurement —
    recording them as "measured" would overstate what we actually checked, and
    asking Futu 220 times for a constant is a slow way to learn nothing.

    Returns (lots, missing). `missing` lists tickers no candidate answered for
    — those keep falling back to the market default, so the caller can see
    how much of the universe is assumed rather than measured.
    """
    import glob
    from futu import OpenQuoteContext, RET_OK

    codes: list[tuple[str, str, str]] = []          # (futu_code, symbol, market)
    for mkt in markets:
        pre = FUTU_PREFIX.get(mkt)
        if not pre:
            continue
        for path in sorted(glob.glob(str(REPO / "public" / mkt / "ohlc" / "*_ohlc.json"))):
            sym = Path(path).name[: -len("_ohlc.json")]
            codes.append((pre + _bare(sym), sym, mkt))

    lots: dict[str, int] = {}
    missing: list[str] = []
    if codes:
        ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
        try:
            for i in range(0, len(codes), chunk):
                part = codes[i:i + chunk]
                try:
                    ret, data = ctx.get_market_snapshot([c[0] for c in part])
                except Exception as e:
                    if verbose:
                        print(f"  chunk {i // chunk}: {type(e).__name__}: {e}")
                    missing.extend(f"{c[2]}:{c[1]}" for c in part)
                    continue
                got: dict[str, int] = {}
                if ret == RET_OK and data is not None and len(data):
                    for _, row in data.iterrows():
                        try:
                            got[str(row["code"])] = int(row["lot_size"])
                        except Exception:
                            pass
                elif verbose:
                    print(f"  chunk {i // chunk}: ret={ret} {str(data)[:160]}")
                for code, sym, mkt in part:
                    v = got.get(code)
                    if v and v > 0:
                        lots[f"{mkt}:{sym}"] = v
                    else:
                        missing.append(f"{mkt}:{sym}")
                if verbose and (i // chunk) % 5 == 0:
                    print(f"  ...{i + len(part)}/{len(codes)}")
        finally:
            ctx.close()

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({
        "source": "Futu OpenD get_market_snapshot().lot_size (JP); "
                  "us lot=1 written from the market definition, not measured",
        "markets": list(markets),
        "count": len(lots),
        "lots": dict(sorted(lots.items())),
    }, indent=1), encoding="utf-8")
    return lots, missing


if __name__ == "__main__":
    import sys
    if "--refresh" in sys.argv:
        got, miss = refresh()
        print(f"measured {len(got)} tickers -> {CACHE}")
        if miss:
            print(f"  {len(miss)} unmeasured (will fall back to market default):")
            print("  " + ", ".join(miss[:40]) + (" ..." if len(miss) > 40 else ""))
    else:
        for m, s in (("jp200", "2503_T"), ("us200", "JPM"), ("jp200", "9999_T")):
            print(f"{m:6} {s:9} lot={lot_for(m, s)}")