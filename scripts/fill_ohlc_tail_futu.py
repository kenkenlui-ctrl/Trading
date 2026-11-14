#!/usr/bin/env python3
"""fill_ohlc_tail_futu.py — fill a MISSING tail session from Futu OpenD.

WHY THIS EXISTS (2026-11-14)
-----------------------------
Yahoo answered the 2026-11-13 session for 16 HK ETFs with a ROW that has a
volume but a NULL close. Checked on Yahoo's own v8 chart API (not yfinance):
2800.HK -> {date: 2026-11-13, close: null, volume: 392049779}, and the same
shape for 2802/3032/7747/... — the row is there, the settled price is not.

update_ohlc.py drops a bar with no price. That is correct (a bar without a
price is not a bar, and shipping a NaN candle is the 2026-10-07 bug), but
the consequence is that those names simply stayed one session behind: the
market closed, the site did not, and NOTHING failed. The site published a
"T-1 2026-11-12" label for a market that had already closed on the 13th,
and no error anywhere said so.

Futu has the settled close for the same session. Its 11-13 volume
(392,049,279) matches Yahoo's own 11-13 volume (392,049,779) to within
0.0001% — the only independent confirmation available when one vendor has
the price and the other has only the row.

WHY THE TAIL, NOT A RE-FETCH
-----------------------------
The stored series is Yahoo RAW (unadjusted close). Futu's default is qfq
(back-adjusted for dividends), and on a dividend payer the two bases
diverge by up to 6% over a year (00005 HSBC: -6.16%). Re-fetching the whole
history would replace months of raw prices with a different price basis,
and every S/R level computed from that series would stop matching the
candles drawn from the stored one.

Futu's autype=NONE is the SAME basis as the stored series. Measured on five
HK names across 255 sessions: the newest 30 bars match the stored closes
to 0.0000% and the whole window to within 0.3% (vendor rounding). So this
script appends ONLY the sessions the stored file is missing, and it
VERIFIES the basis before it writes anything: it re-reads the stored last
session from Futu and refuses to append unless the two agree.

NEVER SHORTEN. The stored history is kept and the vendor's newer bars are
added on top, exactly like update_ohlc.py's merge (vendor wins on a date
both carry).

Usage:
    python3 scripts/fill_ohlc_tail_futu.py --asof 2026-11-13 --markets hk
    python3 scripts/fill_ohlc_tail_futu.py --asof 2026-11-13 --markets hk \
        --tickers 02800.HK,03032.HK
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

SKILL_DIR = Path("/Users/kenken/.minimax/skills/daily-sr-chart/scripts")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

PRICE_COLS = ("open", "high", "low", "close")
COLS = ("date", "open", "high", "low", "close", "volume")
# The basis gate. Futu and Yahoo disagree by a few tenths of a percent on the
# same raw close (0.3% measured over 255 sessions); anything beyond this is a
# different price basis, and appending it would silently mix two series.
BASIS_TOL_PCT = 0.5
BASIS_TOL_ABS = 0.02
# A move this large between the stored last close and the vendor's first new
# close is not a session's move — it is a wrong series, a split, or a
# different basis. Refuse and let a human look.
MAX_JUMP_PCT = 35.0
WARN_JUMP_PCT = 10.0


# The universe files describe what the SITE publishes. update_ohlc.py
# refreshes whatever it finds in the directory (JP's source dir also holds
# leftovers from a September repair under a second naming scheme), but a
# ticker with no page is not a published surface and filling it would only
# pad the provenance record with names nobody can reach.
PUBLISHED_UNI = {
    "hk": REPO / "hk_universe_200.json",
    "us": REPO / "charts/us200/us_published.json",
    "jp": REPO / "jp_universe_200.json",
}


def published_stems(mkt: str) -> set | None:
    p = PUBLISHED_UNI.get(mkt)
    if not p or not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("symbols") or raw.get("tickers") or []
    out = set()
    for it in raw:
        tk = it if isinstance(it, str) else (
            it[1] if isinstance(it, (list, tuple)) and len(it) > 1
            else it.get("ticker", ""))
        if tk:
            out.add(str(tk).replace(".", "_"))
    return out


def markets() -> dict:
    """Reuse update_ohlc.py's market table — one definition of where a
    market's series lives and which universe file describes it."""
    import update_ohlc
    return update_ohlc.MARKETS


def universe_for(mkt: str) -> list[tuple[str, str]]:
    import update_ohlc
    upath, out_dir = markets()[mkt]
    return update_ohlc.universe(upath, out_dir)


def read_series(path: Path) -> list[dict]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and "date" in r]


def futu_daily(ctx, code: str, start: str, end: str) -> list[dict]:
    """Futu daily bars, raw prices (autype=NONE), fully paged."""
    from futu import KLType, RET_OK, AuType

    out: list[dict] = []
    page_key = None
    for _ in range(10):  # a window longer than max_count is paged, not looped
        ret, data, page_key = ctx.request_history_kline(
            code, start=start, end=end, ktype=KLType.K_DAY,
            autype=AuType.NONE, max_count=1000, page_req_key=page_key,
        )
        if ret != RET_OK:
            raise RuntimeError(f"futu error: {data}")
        for _, r in data.iterrows():
            ds = str(r["time_key"])[:10]
            out.append({
                "date": ds,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": int(float(r["volume"])),
            })
        if page_key is None:
            break
    out.sort(key=lambda b: b["date"])
    return out


def basis_ok(stored_close: float, vendor_close: float) -> tuple[bool, str]:
    diff = abs(vendor_close - stored_close)
    pct = diff / stored_close * 100 if stored_close else 999.0
    ok = diff <= BASIS_TOL_ABS or pct <= BASIS_TOL_PCT
    return ok, f"stored={stored_close} futu={vendor_close} diff={diff:.4f} ({pct:.3f}%)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True, help="target session YYYY-MM-DD")
    # The three markets do not share a calendar, and one date across all three
    # is the same defect update_ohlc.py documents: it stamps a session that
    # may not have happened on a market that has not opened yet.
    ap.add_argument("--asof-hk")
    ap.add_argument("--asof-us")
    ap.add_argument("--asof-jp")
    ap.add_argument("--markets", default="hk")
    ap.add_argument("--tickers", help="comma list to limit the run")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    only = {t.strip() for t in a.tickers.split(",")} if a.tickers else None

    # OpenD not running does not raise — OpenQuoteContext() BLOCKS, and a
    # blocked refresh is a site that never updates again. Check the port
    # first and skip the step out loud.
    import socket
    probe = socket.socket()
    probe.settimeout(2)
    try:
        probe.connect(("127.0.0.1", 11111))
    except OSError:
        print("Futu OpenD is not listening on 127.0.0.1:11111 — "
              "skipping the tail fill (nothing is blocked, no file is touched)")
        return 0
    finally:
        probe.close()

    from futu import OpenQuoteContext
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    # config/, not data/: data/ is gitignored, and a provenance record that
    # only exists on one machine cannot be checked by the next one. The
    # published payload carries a data_source label, and that label has to be
    # reproducible from the repo.
    manifest_path = REPO / "config" / "ohlc_tail_futu.json"
    try:
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}

        patched, skipped, refused = [], [], []
        for mkt in [m.strip() for m in a.markets.split(",") if m.strip()]:
            mkt_asof = getattr(a, f"asof_{mkt}", None) or a.asof
            upath, out_dir = markets()[mkt]
            pub = published_stems(mkt)
            for tk, safe in universe_for(mkt):
                if only and tk not in only:
                    continue
                if pub is not None and safe not in pub:
                    continue
                path = out_dir / f"{safe}_ohlc.json"
                rows = read_series(path)
                if not rows:
                    continue
                stored_last = rows[-1]["date"]
                if stored_last >= mkt_asof:
                    skipped.append((tk, f"already at {stored_last}"))
                    continue

                # daily_sr.normalize_ticker is the one place that knows how
                # a published ticker maps to a Futu code: 02800.HK -> HK.02800
                # (5 digits, padded) and AAPL -> US.AAPL.
                try:
                    from daily_sr import normalize_ticker
                    futu_code = normalize_ticker(tk)[0]
                except Exception as e:
                    refused.append((tk, f"cannot map to a Futu code: {e}"))
                    continue
                try:
                    bars = futu_daily(ctx, futu_code, stored_last, mkt_asof)
                except Exception as e:
                    refused.append((tk, f"fetch failed: {type(e).__name__} {e}"))
                    continue

                by_date = {b["date"]: b for b in bars}
                overlap = by_date.get(stored_last)
                if overlap is None:
                    refused.append((tk, f"futu has no bar for the stored session "
                                        f"{stored_last}; cannot prove the basis"))
                    continue
                ok, why = basis_ok(float(rows[-1]["close"]), overlap["close"])
                if not ok:
                    refused.append((tk, f"basis mismatch on {stored_last}: {why}"))
                    continue

                new_bars = [b for b in bars if stored_last < b["date"] <= mkt_asof]
                if not new_bars:
                    refused.append((tk, f"futu has no bar after {stored_last} up to {a.asof}"))
                    continue

                prev_close = float(rows[-1]["close"])
                jump = (new_bars[0]["close"] - prev_close) / prev_close * 100
                if abs(jump) > MAX_JUMP_PCT:
                    refused.append((tk, f"first new bar moves {jump:+.1f}% from the stored "
                                        f"close {prev_close} — refusing (wrong series?)"))
                    continue
                if abs(jump) > WARN_JUMP_PCT:
                    print(f"  ! {tk}: first new bar moves {jump:+.1f}% (check)")

                merged = {b["date"]: b for b in rows}
                merged.update({b["date"]: b for b in new_bars})
                out_rows = [merged[d] for d in sorted(merged)]
                out_rows = [{**r, **{c: (round(float(r[c]), 4) if c in PRICE_COLS
                                        else int(float(r[c])))
                                     for c in PRICE_COLS + ("volume",) if c in r}}
                             for r in out_rows]
                if not a.dry_run:
                    path.write_text(json.dumps(out_rows, separators=(",", ":")),
                                    encoding="utf-8")
                    manifest[f"{mkt}:{safe}"] = {
                        "ticker": tk, "market": mkt, "session": new_bars[-1]["date"],
                        "source": "futu", "autype": "NONE",
                        "basis_check": why, "bars_added": len(new_bars),
                        "stored_last_before": stored_last,
                    }
                patched.append((tk, stored_last, new_bars[-1]["date"],
                                len(new_bars), len(out_rows)))

        if not a.dry_run:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
    finally:
        ctx.close()

    print(f"\nfilled={len(patched)} already_current={len(skipped)} refused={len(refused)}")
    for tk, old, new, nb, total in patched:
        print(f"  ✓ {tk}: {old} -> {new} (+{nb} bar(s), {total} bars total)")
    for tk, why in refused:
        print(f"  ✗ {tk}: {why}")
    if a.dry_run:
        print("\n(dry run — nothing written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
