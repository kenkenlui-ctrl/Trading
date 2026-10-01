#!/usr/bin/env python3
"""Generate public/sitemap.xml from current public/ contents.
Run: python3 scripts/build_sitemap.py
"""
import datetime
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "public"
BASE = "https://www.win9you.com"

EXCLUDE_DIRS = {"charts", "dashboard", "_archive", "pair-trades/old"}
EXT = {".html"}

# 2026-10-02 SEO audit: the sitemap listed pages that should never be indexed
# (the 404 page, noindexed drafts, noindexed equity curves) and stamped every
# URL with today's date, so Search Console saw 626 pages all "changed" on every
# rebuild. Only canonical, indexable, 200-returning pages belong here, and
# lastmod must be the file's real mtime.
EXCLUDE_URLS = {
    "/404/",            # the 404 page itself
    "/wikipedia-draft/",  # noindex, nofollow draft
    "/compare/",         # noindex
    "/equity_curve_t_1/", "/equity_curve_t_3/", "/equity_curve_t_10/",
    "/equity_curve_t10/", "/equity_curve_t1/", "/equity_curve_t3/",
    "/live-monitor/",    # duplicate of /hk200/ with canonical -> /hk200/
    "/faq/",             # duplicate of /methodology/
    "/full-results/",    # 301 -> /methodology
}
# Keep the slash-less form only; the .html / trailing-slash variants 308 away.
CANONICAL_ALIASES = ("/equity_curve_t_10/",)  # duplicate of equity_curve_t10


def indexable(html_path: Path) -> bool:
    """Skip pages that carry a noindex robots meta."""
    try:
        head = html_path.read_text(encoding="utf-8", errors="ignore")[:4000]
    except Exception:
        return True
    return not re.search(r'name=["\']robots["\'][^>]*noindex', head, re.I)


def walk():
    out = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]):
            continue
        if p.suffix == "" and p.name in {"_redirects", "_headers", "robots.txt", "llms.txt", "security.txt"}:
            continue
        if p.suffix not in EXT:
            continue
        if not indexable(p):
            continue
        # URL form
        parts = list(rel.parts)
        if parts[-1] in {"index.html"}:
            parts = parts[:-1]
        else:
            parts[-1] = re.sub(r"\.html$", "", parts[-1])
        url = "/" + "/".join(parts)
        if not url.endswith("/"):
            url += "/"
        if url in EXCLUDE_URLS or url in CANONICAL_ALIASES:
            continue
        out.append((url, p))
    return out

def main():
    rows = walk()
    urls = sorted({u for u, _ in rows})
    lastmod = {}
    for u, p in rows:
        d = datetime.date.fromtimestamp(p.stat().st_mtime).isoformat()
        if u not in lastmod or d > lastmod[u]:
            lastmod[u] = d
    # 2026-10-02: changefreq/priority removed — Google ignores both, and they
    # only made the file bigger. The exclusion list above is the real fix.
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{BASE}{u}</loc>")
        lines.append(f"    <lastmod>{lastmod[u]}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    out_path = ROOT / "sitemap.xml"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(urls)} URLs to {out_path} "
          f"(dropped {len(set(walk.__defaults__ or [])) if False else 0} excluded, "
          f"{len(set(lastmod.values()))} distinct lastmod dates)")

if __name__ == "__main__":
    main()
