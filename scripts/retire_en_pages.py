#!/usr/bin/env python3
"""
retire_en_pages.py — take /en/ out of service without losing it.

WHY
    On 2026-10-08 the English tree turned out to be a frozen snapshot of the v1
    engine. It was live (HTTP 200), all seven pages were listed in
    sitemap.xml, and it advertised:

      - a data as-of of 2026-08-28, six weeks behind the Chinese site
      - SELL_R1 / BUY_S1, strategies switched off in October 2026
      - the per-stock 60-day win-rate badge, audited out (rank correlation
        -0.001 with the next trade)
      - and, in its structured data, an "AI-driven engine" claim the Chinese
        homepage had already retracted on 2026-10-07

    So the English reader — and any crawler or LLM reading the site — was being
    shown a strategy set the operator had publicly disowned. That is a worse
    failure than having no English pages at all: it is confidently wrong
    information in the operator's own voice.

WHAT IT DOES
    Retires the pages without destroying them:

      1. stamps <meta name="robots" content="noindex,nofollow"> into each head,
         so a crawler that reaches a file directly is told not to index it
         (build_sitemap.py already skips noindex pages, so this also removes
         them from sitemap.xml on the next build)
      2. appends 301s in public/_redirects from every /en/* form to its
         Chinese counterpart, so links and bookmarks still land somewhere real

    The files stay on disk. If the English pages are ever rebuilt against v2,
    deleting the two block markers below and re-running this script is the
    whole reversal.

NOT DONE HERE, DELIBERATELY
    No hreflang. A hreflang pair must point at two indexable, equivalent
    pages; declaring /en/ as the English alternate of a noindexed, redirected
    path would be a declaration that cannot be true.

Usage:
    python3 scripts/retire_en_pages.py            # apply (idempotent)
    python3 scripts/retire_en_pages.py --revert   # remove the stamp + rules
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EN = REPO / "public" / "en"
REDIRECTS = REPO / "public" / "_redirects"

ROBOTS = '<meta name="robots" content="noindex,nofollow">'

BEGIN = "# >>> retire_en_pages.py — /en/ retired 2026-10-08 (frozen v1 snapshot)"
END = "# <<< retire_en_pages.py"

# The 2026-08-29/30 i18n rules that mapped /en/*.html -> /en/<page>/ and
# /en/ -> /en/index/. They are superseded, not merely out of order: Cloudflare
# Pages applies the FIRST matching rule, so leaving them would make
# /en/methodology.html -> /en/methodology/ -> /methodology.html, two hops, and
# the project already decided redirect hops cost something (see
# build_static.repoint_faq_links). Removed and replaced by the block below.
# At this point our own block has already been stripped, so ANY remaining
# /en 301 line is a superseded legacy rule. Matching the shape rather than a
# list of paths means a rule added later is retired too, instead of quietly
# outranking the block below it — which is what happened on the first attempt:
# Cloudflare Pages takes the first match, so the 2026-08-30 `/en/ -> /en/index/`
# rule kept winning and every /en/ URL took two hops to reach a Chinese page.
LEGACY_EN_RE = re.compile(r"^/en\S*\s+\S+\s+301[^\n]*\n", re.M)
LEGACY_NOTE = ("# 2026-08-29/30 i18n rules (/en/*.html -> /en/<page>/, /en/ ->\n"
               "# /en/index/) were removed 2026-10-08: superseded by the\n"
               "# retire_en_pages.py block below, which sends /en/ to the Chinese\n"
               "# pages in one hop instead of chaining through /en/ first.\n")

# Every /en/ URL form a visitor or crawler can actually request, mapped to the
# Chinese page that now carries the same subject. The .html and slash-less
# variants are listed separately because Cloudflare Pages matches redirects
# literally; missing one just means a redirect hop is skipped, never a 404.
TARGETS: dict[str, str] = {
    "/en/index/": "/",
    "/en/index.html": "/",
    "/en": "/",
    "/en/": "/",
    "/en/hk200/": "/hk200/",
    "/en/hk200.html": "/hk200/",
    "/en/hk200/index.html": "/hk200/",
    "/en/methodology/": "/methodology.html",
    "/en/methodology.html": "/methodology.html",
    "/en/methodology/index.html": "/methodology.html",
    "/en/backtest/": "/backtest.html",
    "/en/backtest.html": "/backtest.html",
    "/en/backtest/index.html": "/backtest.html",
    "/en/insights/": "/insights.html",
    "/en/insights.html": "/insights.html",
    "/en/insights/index.html": "/insights.html",
    "/en/privacy/": "/privacy.html",
    "/en/privacy.html": "/privacy.html",
    "/en/privacy/index.html": "/privacy.html",
    "/en/disclaimer/": "/disclaimer.html",
    "/en/disclaimer.html": "/disclaimer.html",
    "/en/disclaimer/index.html": "/disclaimer.html",
    "/en/about/": "/about/",
    "/en/about.html": "/about/",
    "/en/about/index.html": "/about/",
    "/en/faq/": "/methodology.html",
    "/en/faq.html": "/methodology.html",
}


def stamp_robots(revert: bool) -> list[str]:
    changed = []
    for p in sorted(EN.rglob("*.html")):
        h = p.read_text(encoding="utf-8")
        new = h.replace(ROBOTS + "\n", "").replace(ROBOTS, "") if revert else h
        if not revert and ROBOTS not in new:
            if "<head>" not in new:
                print(f"  !! {p.relative_to(REPO)} has no <head>; skipped")
                continue
            new = new.replace("<head>", "<head>\n" + ROBOTS, 1)
        if new != h:
            p.write_text(new, encoding="utf-8")
            changed.append(str(p.relative_to(REPO)))
    return changed


def rewrite_redirects(revert: bool) -> bool:
    text = REDIRECTS.read_text(encoding="utf-8")
    body = re.sub(
        re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S
    ).rstrip("\n")
    if not revert:
        body = LEGACY_EN_RE.sub("", body)
        if LEGACY_NOTE not in body:
            body = body.replace("# 2026-08-29 i18n", LEGACY_NOTE + "# 2026-08-29 i18n", 1)
        # Derive the slash-less form of every rule instead of listing it. The
        # first deployment shipped /en/backtest/ but not /en/backtest, and the
        # only reason it was caught was that someone happened to curl it —
        # a hand-maintained list of URL variants is a list someone forgets.
        full = dict(TARGETS)
        for src in list(TARGETS):
            if src.endswith("/") and src != "/en/":
                full.setdefault(src.rstrip("/"), TARGETS[src])
        block = [BEGIN,
                 "# The English tree is a frozen v1 snapshot: retired strategies,",
                 "# a six-week-old data as-of, and an AI claim already retracted on",
                 "# the Chinese site. Redirect rather than delete so links survive.",
                 ""]
        for src, dst in sorted(full.items()):
            block.append(f"{src:<28} {dst:<20} 301")
        block += [END, ""]
        body = body + "\n" + "\n".join(block)
    REDIRECTS.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    return BEGIN not in body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()

    if not EN.exists():
        print(f"no {EN} — nothing to retire")
        return 0

    pages = stamp_robots(a.revert)
    changed = rewrite_redirects(a.revert)
    n_rules = sum(1 for ln in REDIRECTS.read_text(encoding="utf-8").splitlines()
                  if ln.startswith("/en") and " 301" in ln)
    print(f"retire_en_pages: {'reverted' if a.revert else 'applied'}")
    print(f"  robots meta: {'removed from' if a.revert else 'added to'} {len(pages)} page(s)")
    for q in pages:
        print(f"    {q}")
    verb = "block removed" if a.revert else f"{n_rules} /en/ rules in place"
    print(f"  redirects: {verb} (changed={changed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())