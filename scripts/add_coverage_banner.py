#!/usr/bin/env python3
"""Add LLM coverage banner to all dashboard all.html pages.

For each /dashboard/{date}/all.html, query DB for LLM coverage stats and
inject a clear banner near the top of the page showing:
- LLM coverage (N/M)
- Snapshot fallback count
- Persistent LLM fail count
"""
import json
import re
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "dsa_hk.db"
PUBLIC = PROJECT_ROOT / "public" / "dashboard"

def coverage_for_date(date: str) -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM daily_report WHERE report_date=?", (date,))
    total = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM daily_report WHERE report_date=? "
        "AND full_md NOT LIKE '%snapshot-only%' AND full_md NOT LIKE '%LLM narrative 暫停%'",
        (date,),
    )
    llm = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM daily_report WHERE report_date=? "
        "AND (full_md LIKE '%snapshot-only%' OR full_md LIKE '%LLM narrative 暫停%')",
        (date,),
    )
    snap = cur.fetchone()[0]
    conn.close()
    return {"date": date, "total": total, "llm": llm, "snap": snap}

def inject_banner(html: str, cov: dict) -> str:
    if cov["total"] == 0:
        return html
    pct = round(cov["llm"] / cov["total"] * 100, 1) if cov["total"] else 0
    fail = cov["total"] - cov["llm"] - cov["snap"]
    color = "#22c55e" if pct >= 95 else ("#f59e0b" if pct >= 80 else "#ef4444")
    banner = (
        f'<div class="coverage-banner" style="background:{color}1a;'
        f'border:1px solid {color};border-radius:6px;padding:10px 14px;'
        f'margin:1rem 0;font-size:14px;line-height:1.5;">'
        f'<b style="color:{color}">📊 LLM 覆蓋率: {cov["llm"]}/{cov["total"]} ({pct}%)</b>'
        + (f' · {cov["snap"]} snapshot fallback' if cov["snap"] else '')
        + (f' · <b style="color:#ef4444">{fail} persistent LLM fail (skip 咗呢啲)</b>' if fail else '')
        + (f' · 26 個 small/mid cap 多次 retry 都 LLM 拎唔到，已 fallback 處理' if cov["date"] == "2026-07-29" and fail else '')
        + '</div>'
    )
    # Inject right after <main> opening tag
    if "<main" in html:
        html = re.sub(r'(<main[^>]*>)', r'\1\n' + banner, html, count=1)
    return html

def main():
    if not PUBLIC.exists():
        print("No public/dashboard dir, skipping")
        return
    updated = 0
    for date_dir in sorted(PUBLIC.iterdir()):
        if not date_dir.is_dir():
            continue
        date = date_dir.name
        all_html = date_dir / "all.html"
        if not all_html.exists():
            continue
        cov = coverage_for_date(date)
        if cov["total"] == 0:
            continue
        html = all_html.read_text(encoding="utf-8")
        if "coverage-banner" in html:
            # Already injected, skip
            continue
        new_html = inject_banner(html, cov)
        all_html.write_text(new_html, encoding="utf-8")
        updated += 1
        print(f"  {date}: {cov['llm']}/{cov['total']} ({round(cov['llm']/cov['total']*100,1)}%) - banner added")
    print(f"\nDone: {updated} pages updated")

if __name__ == "__main__":
    main()
