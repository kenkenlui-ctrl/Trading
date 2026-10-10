#!/usr/bin/env python3
"""
retire_dropped_tickers.py — remove ticker pages for symbols no longer tracked.

WHY
    A universe regen swaps names in and out. The swapped-OUT tickers kept a
    published page forever, because pages were only ever removed by hand. That
    produced two separate lies:

      * their nav badge was re-stamped with the CURRENT market T-1 by
        finalize.restamp_t1_badges(), so the page headed "T-1 · 2026-10-09"
        while its bars stopped at 2026-10-08 (fixed 2026-10-10 by
        build_static.stamp_stale_ticker_banners(), which is the safety net, not
        the fix);
      * and — the actual root cause — build_v2_signals.py scanned every
        public/<mkt>/ohlc/*.json rather than the current universe, so seven
        swapped-out JP names were still being published as "today's limit
        orders" two days after they left the universe.

    Once the signal generator honours the universe, the remaining question is
    what to do with pages nothing will ever rebuild. Answer: retire them the
    way /en/ was retired — noindex, 301 to the market hub, delete. Not 404, and
    not left to rot with a date they do not have.

SCOPE IS COMPUTED, NOT LISTED
    A ticker page is retired iff its symbol is absent from that market's own
    filter file — the same file the signal generator and the hub table use.
    That means the next regen is handled by re-running this, and it cannot
    drift into deleting a page for a ticker we still track.

Usage:
    python3 scripts/retire_dropped_tickers.py            # apply
    python3 scripts/retire_dropped_tickers.py --dry      # report only
    python3 scripts/retire_dropped_tickers.py --revert <sym> [sym ...]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PUBLIC = REPO / "public"
REDIRECTS = PUBLIC / "_redirects"

# The same per-market sources build_v2_signals.py and build_dashboard.py use.
# HK has no v2 signals but keeps pages for the whole universe.
FILTERS = {
    "hk200": REPO / "hk_universe_200.json",
    "us200": REPO / "charts" / "us200" / "us_published.json",
    "jp200": REPO / "jp_universe_200.json",
}
HUB = {"hk200": "/hk200/", "us200": "/us200/", "jp200": "/jp200/"}

BEGIN = "# >>> retire_dropped_tickers.py — pages for symbols dropped from the universe"
END = "# <<< retire_dropped_tickers.py"
ROBOTS = '<meta name="robots" content="noindex,nofollow">'


def tracked(mkt: str) -> set[str]:
    f = FILTERS[mkt]
    if not f.exists():
        return set()
    return {str(t).replace(".", "_") for t in json.load(open(f))}


def dropped_pages() -> list[tuple[str, str, Path]]:
    out = []
    for mkt, f in FILTERS.items():
        keep = tracked(mkt)
        if not keep:
            continue
        for path in sorted((PUBLIC / mkt / "ticker").glob("*.html")):
            sym = path.stem
            if sym not in keep:
                out.append((mkt, sym, path))
    return out


def rewrite_redirects(pairs: list[tuple[str, str]]) -> int:
    text = REDIRECTS.read_text(encoding="utf-8")
    body = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", text,
                  flags=re.S).rstrip("\n")
    if pairs:
        block = [BEGIN,
                 "# These tickers left the tracked universe on a regen. Nothing",
                 "# rebuilds them, so they would otherwise keep a published URL",
                 "# carrying a date their bars do not have. Redirect to the hub.",
                 ""]
        for src, dst in sorted(pairs):
            block.append(f"{src:<34} {dst:<10} 301")
        block += [END, ""]
        body = body + "\n" + "\n".join(block)
    REDIRECTS.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    return len(pairs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--revert", nargs="*", metavar="SYM",
                    help="restore pages for these symbols instead of retiring")
    a = ap.parse_args()

    if a.revert:
        for sym in a.revert:
            mkt = next((m for m in FILTERS if re.match(
                r"^\d{4}", sym) and (sym.endswith("_T")) == (m == "jp200")), None)
            if not mkt:
                print(f"  !! cannot infer market for {sym}")
                continue
            print(f"  {sym}: re-run scripts/build_dashboard.py to regenerate "
                  f"(the generator rebuilds from the universe, so a retired "
                  f"name only returns if it is tracked again)")
        rewrite_redirects([])
        print("redirect block cleared")
        return 0

    drops = dropped_pages()
    if not drops:
        print("no ticker pages outside their market filter — nothing to retire")
        return 0

    print(f"{len(drops)} ticker page(s) no longer tracked:")
    for mkt, sym, _ in drops:
        print(f"    {mkt}/{sym}")
    if a.dry:
        print("\n(dry run — nothing written)")
        return 0

    # 1. noindex first, so a crawler that reaches the file between now and the
    #    delete is told not to index it rather than seeing it as current.
    for _, _, p in drops:
        h = p.read_text(encoding="utf-8")
        if ROBOTS not in h and "<head>" in h:
            p.write_text(h.replace("<head>", "<head>\n" + ROBOTS, 1), encoding="utf-8")

    # 2. redirect every URL form, then delete the file.
    pairs = []
    for mkt, sym, p in drops:
        base = f"/{mkt}/ticker/{sym}"
        hub = HUB[mkt]
        pairs += [(base, hub), (base + "/", hub), (base + ".html", hub)]
    n = rewrite_redirects(pairs)

    for _, _, p in drops:
        p.unlink()

    print(f"\nretired: {len(drops)} page(s) deleted, {n} redirect rules written")
    return 0


if __name__ == "__main__":
    sys.exit(main())