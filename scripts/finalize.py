#!/usr/bin/env python3
"""
finalize.py — the LAST step of every build. Normalises public/** in place.

2026-10-07: build_static.py already owns four normalising passes (T-1 badge,
CSS cache-buster, FAQ link repoint, mobile nav), and they were correct — but
refresh.sh runs build_backtest_page.py, build_insights_radar.py and
build_v2_signals.py AFTER build_static.py. Those builders write HTML of their
own, so they re-introduce whatever build_static had just fixed. It surfaced as
backtest.html reading "T-1 · 2026-10-06" after the badge pass had already run,
while the rest of the tree read the per-market label.

A post-pass only holds while it is genuinely last. Rather than re-order the
chain (which the next new builder would break again), this runs the same
passes on demand so the caller has ONE obvious final step:

    python3 scripts/finalize.py

Safe to run twice; every pass is idempotent. Does not regenerate pages — it
only rewrites the badge string, the stylesheet query string and a duplicated
anchor, so it never destroys hand-written bodies.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_static as bs  # noqa: E402


def main() -> int:
    from build_dashboard import site_label

    total = 0

    restamped = bs.restamp_t1_badges()
    if restamped:
        print(f"✅ T-1 badge re-stamped on {len(restamped)} page(s) "
              f"(market pages use their own date, cross-market → {site_label()})")
        total += len(restamped)

    duped = bs.collapse_duplicate_nav_links()
    if duped:
        print(f"🧭 Collapsed a repeated nav link on {len(duped)} page(s)")
        total += len(duped)

    branded = bs.sync_en_brand_line()
    if branded:
        print(f"🏷  Synced the product subtitle on {len(branded)} page(s)")
        total += len(branded)

    faqd = bs.repoint_faq_links()
    if faqd:
        print(f"🔗 Re-pointed {len(faqd)} /faq link(s) → /methodology.html")
        total += len(faqd)

    cssed = bs.restamp_css_version()
    if cssed:
        print(f"🎨 leeks.css cache-buster → {bs._CSS_VERSION()} on {len(cssed)} page(s)")
        total += len(cssed)

    if not total:
        print("✅ Already normalised — nothing to rewrite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())