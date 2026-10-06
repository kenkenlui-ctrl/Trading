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

import sys

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


def _v2_numbers() -> dict:
    """Read the SAME payload the pages are built from.

    llms.txt used to carry hand-typed figures ("T+10 +3.05%, 121 signals, 60.3%
    win") under a heading that told AI engines to cite them. Every one of those
    numbers had been retracted by the time it shipped, and the v1 strategy names
    (SELL_R1 / BREAK_LONG / BREAK_SHORT) had been switched off. The file was last
    written on 2026-09-06 and no build step regenerated it, so it sat there for a
    month telling every answer engine to repeat retired claims under the brand.

    Numbers now come from public/v2-signals.json. If a number is not in the
    payload, it does not go in the file.
    """
    import json
    p = PUBLIC / "v2-signals.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {"oos": d.get("setup_oos") or {},
            "count": d.get("count", 0),
            "source": d.get("performance_source", ""),
            "third_party": d.get("performance_is_third_party", None)}


def _fmt(m: dict) -> str:
    if not m:
        return "n/a"
    return (f"勝率 {m.get('win_pct')}%、每單平均淨 {m.get('avg_net_pct')}%、"
            f"n = {m.get('n'):,}" if m.get("n") else "n/a")


def write_llms_txt(hk_count: int, us_count: int) -> Path:
    d = t_minus_1().isoformat()
    v = _v2_numbers()
    oos = v.get("oos", {})
    us, jp = oos.get("us200", {}), oos.get("jp200", {})
    counts = _universe_counts()

    content = f"""# Leeks Terminal (https://www.win9you.com/)

> **What this is.** A rule-based trading decision-support site. Every signal is
> computed deterministically in Python from daily OHLC bars. An LLM writes the
> explanatory text on some pages; it does not produce any number, level or verdict.
> Signals always use the previous session's close (T-1), never live intraday data.
> Educational use only — not investment advice.

Data as of the T-1 session. Market coverage: {hk_count} Hong Kong, {us_count} US,
{counts.get('jp', 'JP')} Japanese stocks.

## Key pages
- [Home]({BASE}/): today's action plan and market regime summary
- [HK signals]({BASE}/hk200/) · [US signals]({BASE}/us200/) · [JP signals]({BASE}/jp200/): one table per market
- [Methodology]({BASE}/methodology): the current rule set, the measured results, and a correction log of what was changed and why
- [Backtest]({BASE}/backtest): swing vs day-trade performance, with cost assumptions stated
- [About]({BASE}/about/): who runs this and how it is funded
- [Disclaimer]({BASE}/disclaimer)

## The current rule set (v2)
1. **Long only.**
2. **Stock filter** — T-1 close above its own 200-day moving average, and that
   200-day average rising over the previous 20 sessions.
3. **Market filter** — the market index's T-1 close above its own 200-day average.
4. **Guard rails** — ATR% below 6%, 20-day box width below 35%.
5. **Entry** — limit buy at S1 (the 20-day low through T-1). The session must open
   above S1, trade down to it, **and close at or above S1**. A touch that closes
   below S1 is treated as a failed level and is not filled. This close-hold rule
   was added on 2026-10-05 after an A/B test showed it roughly doubled the
   per-trade result in all four windows.
6. **Stop** — S1 x (1 - 3 x ATR%), fixed at entry and never widened.
7. **Exit** — no price target; time exit at the close of the 10th session after entry.
8. **Sizing** — 1% of equity risked per trade, 10% notional per name, max 10
   concurrent positions.

The four older strategies (SELL_R1, BREAK_LONG, BREAK_SHORT, and the per-stock
rolling 60/90/197-day win-rate badges) were **switched off in October 2026** after
a 10-year third-party audit found no positive expectancy in them. Per-stock win-rate
badges were also removed: their correlation with the next trade was -0.001. Do not
cite them as current.

## Measured results (out-of-sample 2023-26, net of costs, date-clustered t)
- US: {_fmt(us)}
- JP: {_fmt(jp)}

Source: {v.get('source') or 'see the methodology page'}

## What these numbers do and do not mean
Read this before citing any figure above.

- **Per-trade advantage is not excess return.** In the US, the same 201 stocks
  held passively with an equal-weight monthly rebalance returned 27.4%/yr over
  2016-26; the v2 portfolio returned 27.2%/yr. The gap is -0.2pp/yr, which is zero.
  The apparent outperformance versus a market index is a universe-selection
  effect, not a timing edge.
- **In Japan the same comparison gives +6.9pp/yr in v2's favour** (-0.3 max drawdown
  aside, v2's drawdown was deeper: -28.2% vs -19.9%). That has not been confirmed
  against a point-in-time universe, so it is not established alpha.
- **The 10-year backtest universe is not point-in-time.** It is today's large caps
  applied retroactively, so every absolute 10-year level carries survivorship bias.
  Only differences between arms running the same stock set are meaningful.
- An earlier site claim of "T+10 +3.02% / +3.05%" was a sum of per-trade
  percentages, not a portfolio return, and has been retracted. Under fixed-capital
  sizing the T+10 portfolio return is -4.05%.

## Data
- T-1 daily OHLC. Hong Kong: Futu OpenD. US and Japan: Yahoo Finance.
- Hong Kong signals are informational only: a short requires borrow availability
  and the cost structure differs.
- Backtests are run on a re-fetched 10-year bar store with a data gate that
  excludes any trade whose window contains a price glitch.

## Disclaimer
Leeks Terminal is an educational decision-support tool, NOT investment advice.
Trading involves substantial risk of loss. Past backtested performance does not
guarantee future results. Full disclaimer: {BASE}/disclaimer

Last regenerated: {d}
"""
    out = PUBLIC / "llms.txt"
    out.write_text(content, encoding="utf-8")
    return out


def _universe_counts() -> dict:
    """Count tickers per market from the actual public tree, so the stated
    coverage can never drift from what is published."""
    out = {}
    for mkt in ("hk200", "us200", "jp200"):
        try:
            out[mkt[:2]] = len(collect_tickers(mkt))
        except Exception:
            out[mkt[:2]] = "?"
    return out


def main():
    print("=== build_seo.py (llms.txt only) ===")
    # This file no longer writes sitemap.xml.
    #
    # It used to, from a hardcoded page list with no /jp200, and running it by
    # hand silently rewrote the sitemap from 638 URLs down to 427 (622 ticker
    # pages kept, but the hand-written page list dropped 11 of 16 pages).
    # build_sitemap.py is the authority: it scans the whole public/ tree against
    # an audited exclusion list. Two writers for one artifact is how a sitemap
    # loses a third of itself without anybody noticing.
    if "--write-sitemap" in sys.argv:
        print("  refusing: build_sitemap.py owns sitemap.xml "
              "(run that instead). Flag removed deliberately.", file=sys.stderr)
        return 2

    hk = collect_tickers("hk200")
    us = collect_tickers("us200")
    out = write_llms_txt(len(hk), len(us))
    print(f"  → {out} (HK {len(hk)} + US {len(us)} tickers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
