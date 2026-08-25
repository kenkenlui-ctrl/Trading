#!/usr/bin/env python3
"""
build_seo.py — Regenerate sitemap.xml + llms.txt from the actual public/ tree.

Why this exists (SEO audit 2026-08-25):
  - The old sitemap.xml was hand-written once (2026-08-03) and still listed
    pages deleted in the v2.0 redesign (faq.html, learn/*, dashboard/<date>/*),
    while NONE of the ~400 hk200/us200 ticker pages were included.
  - llms.txt had the same problem: ~15 dead links that now 301.
  - Universe refreshes every 5 trading days, so sitemap MUST be regenerated
    on every build, not maintained by hand.

Usage:
    python3 scripts/build_seo.py

Writes:
    public/sitemap.xml
    public/llms.txt

Run AFTER build_home.py / build_dashboard.py so the ticker scan reflects
the latest build.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PUBLIC = REPO / "public"
BASE = "https://www.win9you.com"


def t_minus_1() -> date:
    """Return T-1 = last trading day (skip Sat/Sun). Same rule as build_dashboard."""
    today = date.today()
    if today.weekday() == 0:   # Monday -> Friday
        return today - timedelta(days=3)
    elif today.weekday() == 6: # Sunday -> Friday
        return today - timedelta(days=2)
    elif today.weekday() == 5: # Saturday -> Friday
        return today - timedelta(days=1)
    return today - timedelta(days=1)


def mtime_date(p: Path) -> date:
    """Content-change date for a hand-maintained file = its mtime date."""
    return datetime.fromtimestamp(p.stat().st_mtime).date()


def collect_tickers(market_dir: str) -> list[str]:
    td = PUBLIC / market_dir / "ticker"
    if not td.exists():
        return []
    return sorted(f.stem for f in td.glob("*.html"))


def collect_urls():
    """Return list of (loc, lastmod_iso, changefreq, priority).

    Canonical URL form = extensionless (Cloudflare Pages pretty URLs).
    Verified live 2026-08-25:
      /hk200/ticker/00700_HK.html -> 308 -> /hk200/ticker/00700_HK
      /hk200/ticker/00700_HK/     -> 308 -> /hk200/ticker/00700_HK
    """
    data_date = t_minus_1().isoformat()

    urls = [
        ("/", data_date, "daily", "1.0"),
        ("/hk200/", data_date, "daily", "0.9"),
        ("/us200/", data_date, "daily", "0.9"),
    ]

    pt = PUBLIC / "pair-trades" / "index.html"
    if pt.exists():
        urls.append(("/pair-trades/", mtime_date(pt.parent / "index.html").isoformat(), "weekly", "0.7"))

    for name, freq, pri in (("methodology", "monthly", "0.8"), ("disclaimer", "yearly", "0.3")):
        f = PUBLIC / f"{name}.html"
        if f.exists():
            urls.append((f"/{name}", mtime_date(f).isoformat(), freq, pri))

    for market in ("hk200", "us200"):
        for safe in collect_tickers(market):
            urls.append((f"/{market}/ticker/{safe}", data_date, "daily", "0.6"))

    return urls


def write_sitemap(urls) -> Path:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, lastmod, changefreq, priority in urls:
        lines += [
            "  <url>",
            f"    <loc>{BASE}{loc}</loc>",
            f"    <lastmod>{lastmod}</lastmod>",
            f"    <changefreq>{changefreq}</changefreq>",
            f"    <priority>{priority}</priority>",
            "  </url>",
        ]
    lines.append("</urlset>")
    out = PUBLIC / "sitemap.xml"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def write_llms_txt(hk_count: int, us_count: int) -> Path:
    d = t_minus_1().isoformat()
    content = f"""# Leeks Terminal

> HK + US day-trade decision dashboard. {hk_count} HK stocks + {us_count} US stocks ranked by rolling turnover.
> Every stock gets a daily 10-step price-action analysis (phase → S/R ladder → action plan with trigger/target/stop)
> computed from T-1 OHLC only — no LLM-generated numbers. Educational use; not investment advice.

## Pages
- [Home]({BASE}/): Today's actionable signals + market regime distribution
- [HK Signals]({BASE}/hk200/): All {hk_count} HK stocks, one sortable table (phase/action/trigger/target/stop)
- [US Signals]({BASE}/us200/): All {us_count} US stocks, same table format
- [Pair Trades]({BASE}/pair-trades/): Correlated-pair relative value screen
- [Methodology]({BASE}/methodology): The full 10-step framework, backtest windows, reliability grading
- [Disclaimer]({BASE}/disclaimer): Educational-use disclaimer and risk notice

## Per-stock pages
Format: `{BASE}/{{hk200|us200}}/ticker/<SAFE>` where SAFE is the ticker symbol
(dot replaced by underscore), e.g. {BASE}/hk200/ticker/00700_HK or {BASE}/us200/ticker/AAPL.
Each page contains: current phase, S/R ladder (S1-S4/R1-R3), verdict (BUY/SELL/WAIT),
trigger/target/stop prices, risk-reward ratio, 30/60/90/197-day backtest edge,
and four if-then scenarios.

## Method summary
1. Phase classification (5 regimes) from 20-day rolling box + 60-day trend
2. S/R ladder: S1/R1 = 20-day low/high, S2/R2 = 60-day, S3/R3 = 120-day, S4 = 200-day crash low
3. Strategies: SELL_R1, BUY_S1, BREAK_LONG, BREAK_SHORT — all deterministic Python on raw OHLC
4. Backtest each strategy over 30/60/90/197-day windows per stock
5. Verdict + trigger/target/stop chosen from the strongest recent-regime edge

## Data sources
- HK daily bars: Futu OpenD (T-1 close)
- US daily bars: YFinance / Alpaca (T-1 close)
- Backtest engine: same OHLC inputs, no survivorship adjustment within window

## Operating rules
- T-1 hard rule: signals always use yesterday's close, never live intraday data
- Day-trade only: flat by 16:00 HKT / 16:00 ET, no overnight holds
- Reliability grades: HIGH = n>=10 & win%>=60 · MED = n>=5 & win%>=50 · LOW = skip

## Disclaimer
Leeks Terminal is an educational decision-support tool, NOT investment advice.
Day trading involves substantial risk of loss. Past backtest performance does not
guarantee future results. Full disclaimer: {BASE}/disclaimer

Updated: {d}
"""
    out = PUBLIC / "llms.txt"
    out.write_text(content, encoding="utf-8")
    return out


def main():
    print("=== build_seo.py ===")
    urls = collect_urls()
    out = write_sitemap(urls)
    print(f"  → {out} ({len(urls)} URLs)")

    hk = collect_tickers("hk200")
    us = collect_tickers("us200")
    out = write_llms_txt(len(hk), len(us))
    print(f"  → {out} (HK {len(hk)} + US {len(us)} tickers)")


if __name__ == "__main__":
    main()
