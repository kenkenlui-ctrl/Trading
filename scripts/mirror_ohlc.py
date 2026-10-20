#!/usr/bin/env python3
"""Mirror the refreshed OHLC series from the source dirs into public/<mkt>/ohlc/.

WHY THIS IS ITS OWN STEP (2026-10-20). build_dashboard.py used to do this
copy inside its own render loop, so the mirror happened at the same moment as
the render — and the render draws each ticker page's v2 block from
public/v2-signals.json, which is generated FROM these very files. The order
was therefore: mirror + render with the PREVIOUS run's signals, then
regenerate the signals. On any run where the signal set actually changed,
the 674 ticker pages and the three hub tables shipped a v2 block one session
stale, and the hub's SIGNAL-row count disagreed with the file it claims to
render.

It was never caught before because the signal set only changes when the bars
change — and the bars had not been changing: the daily refresh had not run
since 2026-10-16. The first run that actually moved the data caught it.

This step copies the OHLC series only. The chart JSON stays where it is:
build_dashboard copies that as it renders, and nothing reads it before then.
build_dashboard's own copy of the series is left in place — it is idempotent,
and removing it would only move the same trap somewhere else.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# market -> (the directory update_ohlc.py writes into, the universe file
# build_dashboard.py publishes from). Same mapping build_dashboard reads from.
SRC = {
    "hk200": (REPO / "charts" / "hk200", REPO / "hk_universe_200.json"),
    "us200": (REPO / "charts" / "us200", REPO / "charts" / "us200" / "us_published.json"),
    "jp200": (REPO / "data" / "jp200", REPO / "jp_universe_200.json"),
}


def _published_stems(universe_path: Path) -> list[str]:
    """The file stems the site actually publishes for this market.

    The universe, not a glob. data/jp200/ still holds 192 files left by a
    September repair script under a second naming scheme (JP_1332_ohlc.json
    beside 1332_T_ohlc.json), each holding a truncated 5-bar fetch. A glob
    copied them into public/jp200/ohlc/, and the self-test caught the
    directory carrying two schemes again — the same defect that script was
    written to fix, re-introduced from the other side.
    """
    if not universe_path.exists():
        return []
    raw = json.loads(universe_path.read_text(encoding="utf-8"))
    return [str(t).replace(".", "_") for t in raw]


def main() -> int:
    total = 0
    for mkt, (src, universe) in SRC.items():
        if not src.is_dir():
            print(f"  !! {mkt}: source dir {src} is missing")
            return 1
        stems = _published_stems(universe)
        if not stems:
            print(f"  !! {mkt}: universe {universe} is missing or empty")
            return 1
        dst_dir = REPO / "public" / mkt / "ohlc"
        dst_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for stem in stems:
            p = src / f"{stem}_ohlc.json"
            if not p.is_file():
                continue
            # copy2 keeps the mtime, which matters because other parts of the
            # pipeline reason about "is this file newer than the artefact".
            shutil.copy2(p, dst_dir / p.name)
            n += 1
        print(f"  {mkt}: mirrored {n}/{len(stems)} ohlc series -> public/{mkt}/ohlc/")
        total += n
    print(f"  mirrored {total} ohlc series in total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
