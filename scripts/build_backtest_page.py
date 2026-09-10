"""build_backtest_page.py — Render /backtest.html from data/monthly_backtest/latest.md.

Turns the first-party multi-horizon backtest into a public, AI-quotable page:
markdown tables → HTML tables, bullet lists → styled <ul>, plus Article + FAQPage JSON-LD.

Sections rendered (in order):
  1. Headline (T+1/T+3/T+5/T+10)
  2. HSI Bear regime impact
  3. Sig score buckets
  4. Live stop/target adoption (NEW 2026-09-09)
  5. Universe filter (NEW 2026-09-09)
  6. Risk metrics (NEW 2026-09-09 — portfolio sim)
  7. Walk-forward OOS (NEW 2026-09-09)

Regenerated every time monthly_swing_backtest.py runs (called from main build).
"""
from __future__ import annotations
import json
import re
import html as _html
from pathlib import Path

REPO = Path("/Users/kenken/Documents/dsa-hk")
MD_PATH = REPO / "data" / "monthly_backtest" / "latest.md"
OUT_PATH = REPO / "public" / "backtest.html"
JSON_PATH = REPO / "data" / "monthly_backtest" / "backtest_latest.json"  # canonical, for future per-page deep dives


# ===========================
# Markdown → HTML helpers
# ===========================

def _md_table_to_html(md: str) -> str:
    """Convert a markdown table block to a styled HTML table."""
    lines = [l.strip() for l in md.strip().splitlines() if l.strip()]
    if len(lines) < 2 or not lines[0].startswith("|"):
        return ""
    rows = []
    for l in lines:
        cells = [c.strip() for c in l.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue  # separator row
        rows.append(cells)
    if not rows:
        return ""
    th = "".join(f"<th>{_html.escape(c)}</th>" for c in rows[0])
    trs = []
    for r in rows[1:]:
        tds = "".join(f"<td>{_html.escape(c)}</td>" for c in r)
        trs.append(f"<tr>{tds}</tr>")
    return f'<table class="data-table"><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table>'


def _md_bullets_to_html(md: str) -> str:
    """Convert a bullet list block to styled HTML <ul>.
    Supports `**bold**` inline markdown. Skips blank lines, H2/H3 headers,
    and lines that look like leftover H3 trailers (e.g. "(62 signals):**").
    """
    items = []
    for line in md.strip().splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("## ") or s.startswith("### "):
            continue
        # Skip lines that are the tail of a stripped H3 marker like "(62 signals):**"
        if s.endswith(":**") or re.fullmatch(r"\([0-9]+ signals[^)]*\):\*\*", s):
            continue
        if s.startswith("- "):
            content = s[2:].strip()
        elif s.startswith("* "):
            content = s[2:].strip()
        else:
            # Non-bullet line — include as paragraph for context
            content = s
        # Convert **bold** → <strong>
        content = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", content)
        items.append(f"<li>{content}</li>")
    if not items:
        return ""
    return f'<ul class="md-bullets">{"".join(items)}</ul>'


def _section_block(title: str, body_html: str, css_class: str = "") -> str:
    if not body_html:
        return ""
    cls = f' class="{css_class}"' if css_class else ""
    return f"<section{cls}><div class=\"container\"><h2>{_html.escape(title)}</h2>{body_html}</div></section>"


# ===========================
# Render
# ===========================

def render() -> str:
    md = MD_PATH.read_text(encoding="utf-8")

    # Parse metadata
    def grab(pattern: str) -> str:
        m = re.search(pattern, md)
        return m.group(1).strip() if m else ""

    window = grab(r"\*\*Window[^:]*:\*\* ([^\n]+)")
    generated = grab(r"\*\*Generated:\*\* ([^\n]+)")
    n_signals = grab(r"\*\*BUY signals[^:]*:\*\* ([^\n]+)")
    bear_days = grab(r"\*\*HSI bear days[^:]*:\*\* ([^\n]+)")

    # Pull headline numbers for meta description
    def parse_headline_avg(horizon_name: str) -> str:
        m = re.search(rf"\|\s*{re.escape(horizon_name)}\s*\|[^|]*\|[^|]*\|\s*([+-]?[0-9.]+)%\s*\|", md)
        return m.group(1) if m else "?"

    t10_avg = parse_headline_avg("T+10 (10-day swing)")
    t1_avg = parse_headline_avg("T+1 (day-trade)")

    # Parse out the new portfolio section for meta description
    portfolio_total = "?"
    portfolio_mdd = "?"
    oos_wr_train = "?"
    oos_wr_test = "?"
    m = re.search(r"Total return \(compounded\)\s*\|\s*([+-]?[0-9.]+)%", md)
    if m: portfolio_total = m.group(1)
    m = re.search(r"Max drawdown\s*\|\s*([0-9.]+)%", md)
    if m: portfolio_mdd = m.group(1)
    m = re.search(r"\| Train \(in-sample\) \| [0-9]+ \| ([0-9.]+)% \|", md)
    if m: oos_wr_train = m.group(1)
    m = re.search(r"\| OOS \(out-of-sample\) \| [0-9]+ \| ([0-9.]+)% \|", md)
    if m: oos_wr_test = m.group(1)

    # Split into sections by H2
    sections = re.split(r"\n## ", md)

    # Index by title
    section_map = {}
    for sec in sections:
        title = sec.splitlines()[0].strip() if sec else ""
        section_map[title] = sec

    def section_table(title_match: str) -> str:
        for k, v in section_map.items():
            if title_match.lower() in k.lower():
                tbl_match = re.search(r"(\|[^\n]+\|(?:\n\|[^\n]+)*)", v)
                if tbl_match:
                    return _md_table_to_html(tbl_match.group(1))
        return ""

    def section_bullets(title_match: str) -> str:
        for k, v in section_map.items():
            if title_match.lower() in k.lower():
                return _md_bullets_to_html(v)
        return ""

    headline_tbl = section_table("Headline")
    bear_tbl = section_table("Bear regime")
    sig_tbl = section_table("Sig score")
    risk_tbl = section_table("Risk metrics")
    oos_tbl = section_table("Walk-forward OOS")

    # Live stop/target — mixed: bullet + sub-bullet blocks
    # The section key in section_map is the full H2 title (e.g. "Live stop/target adoption"),
    # not the substring we use to look it up.
    live_stops_key = next((k for k in section_map if k.startswith("Live stop/target")), None)
    live_stops_html = ""
    if live_stops_key:
        v = section_map[live_stops_key]
        sub_groups = re.split(r"\*\*(Live group|Fallback group)", v)
        # First chunk is the main bullets — drop the H2 title line (first line)
        main = sub_groups[0] if sub_groups else ""
        main_lines = main.splitlines()[1:] if main else []
        live_stops_html += _md_bullets_to_html("\n".join(main_lines))
        for i in range(1, len(sub_groups) - 1, 2):
            label = sub_groups[i]
            content = sub_groups[i + 1]
            # Drop the H3 trailer line (e.g. " (62 signals):**")
            content_lines = content.splitlines()
            while content_lines and (
                content_lines[0].strip().endswith(":**")
                or "(signals)" in content_lines[0]
                or not content_lines[0].strip()
            ):
                content_lines = content_lines[1:]
            content = "\n".join(content_lines)
            live_stops_html += f'<h3 class="sub-h3">{_html.escape(label)}</h3>'
            live_stops_html += _md_bullets_to_html(content)

    # Universe filter — bullet list (drop H2 title)
    universe_key = next((k for k in section_map if k.startswith("Universe filter")), None)
    if universe_key:
        v = section_map[universe_key]
        v_lines = v.splitlines()[1:]  # drop H2 title
        universe_html = _md_bullets_to_html("\n".join(v_lines))
    else:
        universe_html = ""

    # Build page body
    body = f"""
<div class="page-head">
  <div class="container">
    <div class="kicker">First-party data</div>
    <h1>回測報告 · <em class="italic">T+10 Swing vs T+1 Day-trade</em></h1>
    <p class="lede">{n_signals} 個 BUY 訊號嘅 6 個月回測，全部<b>淨回報</b>（扣交易成本）。
    同一批訊號，T+10 持有平均 <b>+{t10_avg}%</b>，係 T+1 即日鮮（+{t1_avg}%）嘅 {float(t10_avg)/max(float(t1_avg), 0.01):.0f}× 倍。
    Portfolio sim（1.5% risk/trade, 8 concurrent）總回報 <b>+{portfolio_total}%</b>，MaxDD {portfolio_mdd}%。</p>
    <div class="hero-meta">
      <span>Window: {window}</span>
      <span>Signals: {n_signals}</span>
      <span>HSI bear days: {bear_days}</span>
      <span>Generated: {generated}</span>
    </div>
  </div>
</div>

<div class="container">
  {('<h2>Headline（淨回報 · Live stop/target）</h2>' + headline_tbl) if headline_tbl else ''}
  {('<h2 style="margin-top: var(--sp-5);">HSI 熊市日影響</h2>' + bear_tbl) if bear_tbl else ''}
  {('<h2 style="margin-top: var(--sp-5);">Sig score 分桶</h2>' + sig_tbl) if sig_tbl else ''}
</div>

{_section_block("Live stop/target adoption", live_stops_html, "section-live-stops") if live_stops_html else ''}

{_section_block("Universe filter (data availability + 20d ADV)", universe_html, "section-universe") if universe_html else ''}

{_section_block("Risk metrics — T+10 portfolio sim (1.5% risk/trade, 8 concurrent cap)", risk_tbl, "section-risk") if risk_tbl else ''}

{_section_block("Walk-forward OOS (80/20 train/test split)", oos_tbl, "section-oos") if oos_tbl else ''}

<div class="container">
  <div class="card mt-5">
    <h2>點樣解讀</h2>
    <ul>
      <li><b>Live stop/target 取代 hardcoded</b> — 51% 訊號用 live signal 嘅 stop/target（avg −2.45%/+4.07%，比舊 hardcoded T+10 −10%/+15% 緊），其餘 49% 因 DB 缺 stop/target 或 sanity bounds 外而 fall back 去 hardcoded。</li>
      <li><b>T+10 即日鮮嘅優勢係頻率</b> — 單筆 +0.72% 但可以日日做；T+10 單筆大但要忍 10 日波動。兩者唔互斥，可以並行 paper-trade 驗證。</li>
      <li><b>Portfolio sim 顯示真實 sizing 下 edge 仲存</b> — 1.5% risk/trade + 8 concurrent cap 嘅 T+10 sim：+55% 6mo return，MaxDD 只 1.96%，Calmar 28。</li>
      <li><b>Walk-forward OOS 初步 positive</b> — 80/20 split：train 60.4% WR vs OOS 56.0% WR（−4.4pp degradation），OOS 25 trades 樣本仍細，要至少 3 cycle OOS 先有 statistical evidence。</li>
      <li><b>高 signal score 唔保證 T+10 更好</b> — 分桶顯示 high-score 組 T+1 反而 -0.24%（過熱回歸），low-score 組 T+10 仲有 +1.63%。分數係方向信心，唔係持貨期指引。</li>
      <li><b>樣本限制</b> — 121 個訊號來自 yfinance 有數據嘅 regular stocks（ETP/warrant 缺數據）；6 個月窗口未涵蓋多日熊市。每月 1 號自動重跑滾動更新。</li>
    </ul>
  </div>

  <div class="disclaimer mt-5">
    <b>⚠ Disclaimer</b> · 回測唔等於 live 表現。過往表現不代表將來回報。即日鮮交易高風險。
  </div>
</div>
"""

    # SEO description — keep under 160 chars
    desc = (
        f"T+10 swing {t10_avg}% / T+1 day-trade +{t1_avg}%, 6mo {n_signals.split(' ')[0]} BUY signals, "
        f"portfolio +{portfolio_total}% / MaxDD {portfolio_mdd}%, OOS {oos_wr_test}% WR (n=25)."
    )[:158]

    canonical = "https://www.win9you.com/backtest"
    title = "回測報告 · T+10 Swing vs T+1 Day-trade · Leeks Terminal"

    article_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": f"Leeks Terminal 6-Month Backtest: T+10 {t10_avg}% (live stop/target) vs T+1 +{t1_avg}%",
        "url": canonical,
        "mainEntityOfPage": canonical,
        "inLanguage": "zh-Hant-HK",
        "dateModified": generated,
        "author": {"@type": "Organization", "name": "Leeks Terminal",
                   "url": "https://www.win9you.com/"},
        "publisher": {"@type": "Organization", "name": "Leeks Terminal",
                      "url": "https://www.win9you.com/"},
        "about": (
            f"Multi-horizon backtest of a deterministic price-action signal engine over "
            f"{n_signals.split(' ')[0]} BUY signals (net of HK/US friction costs, LIVE stop/target, "
            f"portfolio-level position sizing with 1.5% risk/trade and 8 concurrent cap, "
            f"80/20 walk-forward OOS validation)."
        ),
    }
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": "T+10 swing 同 T+1 day-trade 邊個回報高？",
             "acceptedAnswer": {"@type": "Answer", "text": (
                 f"同一批 BUY 訊號，T+10 持有平均 +{t10_avg}%，T+1 即日鮮 +{t1_avg}%。"
                 f"T+10 嘅 paper-trade 模式 + portfolio sim 顯示 +{portfolio_total}% 6 個月總回報，"
                 f"但需要 1.5% risk/trade + 8 concurrent cap 嘅紀律。"
             )}},
            {"@type": "Question", "name": "恒指熊市日會唔會令 T+10 失效？",
             "acceptedAnswer": {"@type": "Answer", "text": (
                 "回測窗口入面有 HSI 單日跌 ≥1.5% 嘅熊市日。"
                 "持倉期有熊市日嘅 T+10 交易反而更強（高 WR + 高 avg return），"
                 "但呢個現象樣本細，需要更多熊市日先有 statistical power。"
             )}},
            {"@type": "Question", "name": "呢個回測用邊個 stop/target？",
             "acceptedAnswer": {"@type": "Answer", "text": (
                 "由 2026-09-09 起，T+1/T+3/T+5/T+10 改用 live signal 嗰日 DB 儲存嘅 stop_loss 同 target_price 模擬（51% 訊號用 live，"
                 "其餘因 DB 缺值或 sanity bounds 外 fall back 去 hardcoded）。舊版用固定 -10% stop / +15% target 高估咗 edge。"
             )}},
            {"@type": "Question", "name": "點解 portfolio sim 嘅總回報高過 avg × n？",
             "acceptedAnswer": {"@type": "Answer", "text": (
                 "因 compounding — 每筆 trade 嘅 size = 1.5% × capital / |stop_distance|，"
                 "贏錢 trade 加 capital 後，下一筆 size 都大啲，所以 final return 係幾何級數。"
                 f"呢個 +{portfolio_total}% 已經 cap 喺 8 concurrent 同 30% deploy 之內。"
             )}},
            {"@type": "Question", "name": "Walk-forward OOS 點解得 25 trades？",
             "acceptedAnswer": {"@type": "Answer", "text": (
                 "現有 DB 由 2026-06-27 開始，先 80% train 76 個 calendar days 嘅 signals，後 20% OOS 5 個 days。"
                 f"OOS {oos_wr_test}% WR vs train {oos_wr_train}% WR，−4.4pp degradation，"
                 "但 25 trades 樣本仍細；framework 已 set up，每月 1 號自動重跑累積更多 OOS data。"
             )}},
            {"@type": "Question", "name": "呢個回測有冇計成本？",
             "acceptedAnswer": {"@type": "Answer", "text": (
                 "有。每筆交易扣除 HK 0.30% 或 US 0.20% round-trip 成本（佣金+費用+滑點）。"
                 "同日 target 同 stop 都觸及時，悲觀假設 stop 先觸發。"
             )}},
        ]
    }

    def ld_scripts() -> str:
        return ('<script type="application/ld+json">' + json.dumps(article_ld, ensure_ascii=False) + "</script>"
                + '<script type="application/ld+json">' + json.dumps(faq_ld, ensure_ascii=False) + "</script>")

    from build_static import shell
    page = shell(
        title=title,
        body_html=body,
        active_path="/backtest.html",
        description=desc,
        json_ld=None,
        canonical=canonical,
    )
    # inject JSON-LD blocks before </head>
    page = page.replace("</head>", ld_scripts() + "\n</head>", 1)
    return page


if __name__ == "__main__":
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(render(), encoding="utf-8")
    print(f"→ {OUT_PATH} ({OUT_PATH.stat().st_size:,} bytes)")
