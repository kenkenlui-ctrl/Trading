#!/usr/bin/env python3
"""build_insights_index.py — stamp insights.html with real index levels.

2026-10-02 SEO/GEO audit: /insights.html carried "HSI 18,420 / USD-JPY 142.8"
as static text written on 2026-09-03. Real HSI closed 24,613 and USD/JPY
157.95, so the page contradicted the site's own "zero LLM hallucination"
claim by ~25%.

This does NOT rewrite the analysis. The 2026-09-03 narrative is dated in
place, and a generated index strip is injected above it with the actual
closes, their as-of date, and the source. If yfinance is unavailable the
strip says so rather than showing a stale or invented number.
"""
from __future__ import annotations
import re
from datetime import date
from pathlib import Path

PUB = Path(__file__).resolve().parent.parent / "public"
PAGE = PUB / "insights.html"
MARK_START = "<!-- INDEX_SNAPSHOT:START -->"
MARK_END = "<!-- INDEX_SNAPSHOT:END -->"
NARRATIVE_OLD = "September 3rd"
NARRATIVE_NEW = "2026-09-03"

# yfinance Ticker -> display label
SERIES = [
    ("^HSI", "HSI 恒生指數"),
    ("^GSPC", "S&P 500"),
    ("^N225", "Nikkei 日經"),
    ("JPY=X", "USD/JPY"),
]

# 2026-10-05: FX is not an exchange series. Yahoo returns a JPY=X bar dated
# 2026-10-04 — a Sunday, because the FX week opens Sunday evening UTC. Three
# equity indices all closed 2026-10-02 while this one bar said 10-04, and
# because `asof` was reassigned every loop iteration the strip footer printed
# "最後更新 2026-10-04" on a day no market traded. Drop weekend FX bars so the
# page quotes a real session, and derive the strip date from the equity
# indices rather than from whichever series happened to be fetched last.
FX_SYMBOLS = {"JPY=X"}


def fetch() -> tuple[list[tuple[str, float, str]], str | None]:
    import yfinance as yf
    import pandas as pd

    out: list[tuple[str, float, str]] = []
    equity_days: list[str] = []
    for sym, label in SERIES:
        try:
            d = yf.Ticker(sym).history(period="1mo", auto_adjust=True)
            if d is None or d.empty:
                continue
            d.index = pd.to_datetime(d.index).tz_localize(None)
            if sym in FX_SYMBOLS:
                d = d[[ts.weekday() < 5 for ts in d.index]]
                if d.empty:
                    continue
            row = d.iloc[-1]
            day = d.index[-1].strftime("%Y-%m-%d")
            out.append((label, float(row["Close"]), day))
            if sym not in FX_SYMBOLS:
                equity_days.append(day)
        except Exception:
            continue
    # Strip-level date = the session the equity indices actually closed on.
    asof = max(set(equity_days), key=equity_days.count) if equity_days else None
    return out, asof


def build_strip(rows, asof) -> str:
    if not rows:
        return (
            f'{MARK_START}\n<div class="index-strip">'
            "<strong>指數水平未能取得</strong>（數據源暫時不可用，"
            "為免顯示過時或臆造數字，此處留空）。</div>\n" + MARK_END
        )
    cards = []
    for label, val, day in rows:
        if label == "USD/JPY":
            shown = f"{val:,.1f}"
        else:
            shown = f"{val:,.0f}"
        cards.append(
            f'<div class="idx-card"><div class="idx-name">{label}</div>'
            f'<div class="idx-val">{shown}</div>'
            f'<div class="idx-asof">{day} 收市</div></div>'
        )
    return (
        f"{MARK_START}\n"
        '<div class="index-strip" style="margin:16px 0;padding:14px 16px;'
        'border:1px solid var(--border);border-radius:8px">'
        '<div style="font-size:0.8rem;letter-spacing:0.08em;text-transform:uppercase;'
        'opacity:0.6;margin-bottom:10px">指數收市（自動生成）</div>'
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px">'
        + "".join(cards)
        + "</div>"
        f'<div style="font-size:0.72rem;opacity:0.55;margin-top:10px">'
        f"數據源 Yahoo Finance · 最後更新 {asof} · 每次 build 自動重取</div>"
        "</div>\n" + MARK_END
    )


def main() -> None:
    if not PAGE.exists():
        print("insights.html not found, skip")
        return
    html = PAGE.read_text(encoding="utf-8", errors="ignore")

    rows, asof = fetch()
    # 2026-10-05: fail loudly rather than publish a non-trading day. A weekend
    # asof means an upstream series leaked an FX/Future-session bar into an
    # equity label again — the exact failure this strip was built to prevent.
    if asof:
        _d = date.fromisoformat(asof)
        if _d.weekday() >= 5:
            print(f"! refusing to stamp a non-trading day: asof={asof} "
                  f"({_d.strftime('%A')}). Strip written without a date.")
            asof = None
    strip = build_strip(rows, asof)

    if MARK_START in html:
        html = re.sub(
            re.escape(MARK_START) + r".*?" + re.escape(MARK_END),
            lambda _m: strip,
            html,
            flags=re.S,
        )
    else:
        # inject right after the first <h1
        m = re.search(r"</h1>", html)
        if m:
            html = html[: m.end()] + "\n" + strip + "\n" + html[m.end():]

    # date-stamp the narrative so it cannot be read as current
    if NARRATIVE_OLD in html:
        html = html.replace(NARRATIVE_OLD, NARRATIVE_NEW, 1)
        html = html.replace(
            "directional move is likely within 3–5 sessions",
            "directional move was likely within 3–5 sessions (以下為 "
            + NARRATIVE_NEW
            + " 收市時點之記錄，非當前市況)",
            1,
        )

    PAGE.write_text(html, encoding="utf-8")
    n = len(rows)
    print(f"insights.html: index strip with {n} series, asof={asof}" if n
          else "insights.html: index strip written (no data available)")


if __name__ == "__main__":
    main()
