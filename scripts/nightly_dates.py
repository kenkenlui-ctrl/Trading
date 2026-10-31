#!/usr/bin/env python3
"""nightly_dates.py — one line: what to fetch, and whether to fetch it.

Prints exactly one line, pipe-separated:

    BEHIND|ASOF_HK|ASOF_US|ASOF_JP|DATA_DATE

BEHIND      the markets whose latest CLOSED session the vendor has and our
            published bars do not have (comma-separated; empty if none)
ASOF_HK     the session to fetch for each market — the vendor's own latest
            closed session, one per market, because the three do not share a
            trading calendar
DATA_DATE   the generic cap: the latest of the three

WHY THIS IS A SEPARATE FILE
    The shell that reads it used to embed the Python in a heredoc inside
    `eval "$( ... )"`. Bash does not treat a heredoc body as opaque when it
    is scanning for the closing `)` of a command substitution: it stopped at
    the first UNBALANCED `)` in the Python source, so `max([d for d in (hk,
    us, jp) if d] or [""])` ended the substitution half-way and DATA_DATE
    was set to the string " jp) if d] or [])". The build then fetched with a
    garbage cap and nobody noticed, because the failure looked like a data
    problem rather than a shell problem.

    A separate file has no parentheses in the command substitution, so there
    is nothing for bash to misread. The lesson generalises: inside
    `$( ... )`, a heredoc is NOT a safe way to embed a script that contains
    unbalanced brackets.

USAGE
    python3 scripts/nightly_dates.py
"""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import staleness as S  # noqa: E402


def main() -> int:
    ref = (S.reference().get("markets") or {})

    def sess(market: str) -> str:
        entry = ref.get(S.MARKET_DIR[market]) or {}
        return str(entry.get("latest_closed_session") or "")[:10]

    behind = []
    for market in ("HK", "US", "JP"):
        latest = sess(market)
        ours = S.data_session(market)
        if latest and ours and ours < latest:
            behind.append(market)

    dates = [d for d in (sess("HK"), sess("US"), sess("JP")) if d]
    print("|".join([
        ",".join(behind),
        sess("HK"),
        sess("US"),
        sess("JP"),
        max(dates) if dates else "",
    ]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
