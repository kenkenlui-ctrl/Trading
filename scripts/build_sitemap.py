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
TODAY = datetime.date.today().isoformat()

EXCLUDE_DIRS = {"charts", "dashboard", "_archive", "pair-trades/old"}
EXT = {".html"}

def walk():
    out = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]):
            continue
        if p.suffix not in EXT and p.suffix == "" and not p.is_dir():
            continue
        if p.suffix == "" and p.name in {"_redirects", "_headers", "robots.txt", "llms.txt", "security.txt"}:
            continue
        if p.suffix not in EXT:
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
        out.append(url)
    return out

def main():
    urls = walk()
    # dedupe, sort
    urls = sorted(set(urls))
    # priority: hub pages 1.0, dashboard 0.9, ticker 0.6, others 0.5
    def priority(u):
        if u in ("/", "/hk200/", "/us200/"):
            return "1.0"
        if "/ticker/" in u:
            return "0.6"
        if u in ("/methodology/", "/disclaimer/", "/pair-trades/"):
            return "0.8"
        return "0.5"

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{BASE}{u}</loc>")
        lines.append(f"    <lastmod>{TODAY}</lastmod>")
        lines.append(f"    <changefreq>daily</changefreq>")
        lines.append(f"    <priority>{priority(u)}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    out_path = ROOT / "sitemap.xml"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(urls)} URLs to {out_path}")

if __name__ == "__main__":
    main()
