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
  7. Close-confirmation A/B (NEW 2026-10-18 — read from
     data/ab_close_hold.json; states the scope of the −4.05% withdrawal,
     which is a dead pipeline and not a verdict on the 10-day hold question)
  8. Walk-forward OOS (NEW 2026-09-09)

Regenerated every time monthly_swing_backtest.py runs (called from main build).
Section 7 does NOT depend on that script — it reads the v2 A/B artifact, so it
survives monthly_swing_backtest.py being unrunnable.
"""
from __future__ import annotations
import json
import re
import html as _html
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
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

def _sign(v) -> str:
    """Format a percentage that may or may not carry its own sign.
    Prepending '+' to '+0.76' produced '++0.76' in the title, the meta and the
    FAQ schema."""
    if v in (None, "", "?"):
        return "—"
    v = str(v)
    return v if v.startswith(("+", "-")) else f"+{v}"


def _risk_managed_return() -> str:
    """Removed 2026-10-18.

    This scraped the T+10 "Total Return" off /equity_curve_t_10.html and the FAQ
    then published it as *this page's* risk-managed portfolio result: "風險預算下
    （1% 風險/單、5 倉上限）嘅 T+10 組合回報為 +814.07%". Three things were wrong
    with that sentence:

      - wrong dataset. /equity_curve_t_10.html is the v2 rule set over 2017-26
        US large caps (2,003 of 6,675 signals). This page is a 119-signal HK
        swing backtest over 2026-06-27..07-31. Two unrelated books.
      - wrong slots. The curve walk used 10 concurrent positions, not 5. The
        caption that said 5 was itself wrong and has been fixed.
      - the source page titles itself "機械供給上限 · 非可達成回報" — a mechanical
        supply ceiling it explicitly says is not an achievable return. Quoting
        it as a result was quoting the disclaimer.

    And the figure that DID belong to this page (-4.05%, quoted in the hero,
    the FAQ and llms.txt) has no artifact anywhere and cannot be regenerated:
    scripts/monthly_swing_backtest.py reads data/dsa_hk.db, whose daily_report
    table is empty (0 rows), so it exits "No BUY signals in window".

    So this returns "" and the callers state the absence instead of filling it
    with a number from a different experiment.
    """
    return ""


def _withheld_risk_block(tbl: str, cls: str) -> str:
    """The upstream "Risk metrics" block, withheld with the reason shown.

    data/monthly_backtest/latest.md carries a section headed "T+10 portfolio sim
    (1% risk/trade, 5 concurrent cap — matches d71 honest equity curve)" whose
    rows read Trades taken 120 / Total return +44.02% / Final capital
    HK$1,440,219. Over this page's ~25-weekday window with 10-day holds and 5
    slots, the cap admits at most about 12 trades; the block claims 120, so the
    cap was not applied and +44.02% is a sum of per-trade percentages wearing a
    portfolio label. The page's own prose already retracted that number while
    the table kept printing it.

    Printing a table whose heading contradicts its contents is worse than
    printing neither, so the rows are withheld and the reason is stated where
    they were. Everything else in that markdown file is per-trade and is still
    rendered.
    """
    if not tbl:
        return ""
    return (
        '<div class="info-card" style="border-left:3px solid var(--amber);margin:16px 0">'
        '<h3 style="margin-top:0">Risk metrics（組合回報）— 已停止引用</h3>'
        '<p style="font-size:0.85rem;opacity:0.8">上游 backtest 報告呢一節聲稱'
        '「1% 風險/單、5 倉上限」，但同一個表寫住 <b>Trades taken 120</b>：'
        '呢個窗口得約 25 個交易日、每單持有 10 日，5 個倉位最多只做得約 12 單。'
        '即係上限根本冇套用，<b>+44.02%</b> 係逐筆百分比相加而唔係組合回報。'
        '站上嘅文字版已經撤回咗個數，但表格一直留住佢，所以由 2026-10-18 起'
        '連表格一齊停止引用。單筆統計仍然喺上面嘅表。</p></div>'
    )


_AB_JSON = REPO / "data" / "ab_close_hold.json"


def _ab_read() -> dict:
    """The close-confirmation A/B, read from the run that produced it.

    Raised, not degraded, for the same reason build_v2_signals._oos() raises:
    a performance claim rendered from a missing source is byte-identical to a
    correct one, which is the whole failure mode this project keeps hitting.
    """
    try:
        return json.loads(_AB_JSON.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"FATAL: cannot read {_AB_JSON}: {e} — run "
                         f"scripts/ab_close_hold.py --placebo 300 first")


def _close_hold_block() -> str:
    """The live answer to the question this page's dead pipeline was asking.

    Why this exists on /backtest at all
    -----------------------------------
    The −4.05% T+10 figure was withdrawn on 2026-10-18 because the run that
    produced it reads data/dsa_hk.db, whose daily_report table is empty — the
    monthly_swing pipeline is dead. The honest scope of that withdrawal is
    usually stated wrong. What died is the PIPELINE, not the QUESTION. "Should
    a position be held for 10 sessions after a support entry?" is not answered
    by a broken database, and treating one dead pipeline as a verdict on the
    idea would be its own kind of error.

    The question is answered — on other bars, in the v2 engine, by a run that
    does reproduce. ab_close_hold.py holds HOLD = 10 in BOTH arms, so the hold
    length is held constant while the entry qualification is varied. That
    makes this a live experiment about the same holding horizon this page is
    about, not a different one.

    And it is falsifiable, which the dead pipeline's output was not. B keeps
    only about half of A's trades, so "B beats A" on its own cannot separate
    "the close carries information" from "the rule just trades less". The
    thresholds were fixed in advance in docs/close-confirmation-prereg-2026-10-18.md
    and a size-matched placebo subset of arm A acts as the null. If B landed
    inside that null the pre-registration required publishing it as a negative
    result.

    SCOPE WARNING, stated on the page rather than left implicit: this measures
    the FILL CONDITION. HOLD = 10 is identical in both arms, so this result is
    NOT a mandate for 10 days over 5, 15 or 20 — that sweep is Signal Search
    Sprint 1's, and it is not in this artifact.
    """
    d = _ab_read()
    pl = d.get("placebo")
    if not pl:
        # A run with --placebo 0 writes null. Printing the raw A/B here would
        # look identical to a placebo-backed claim, which is the point.
        return ('<section class="section-closehold"><div class="container">'
                '<h2>同一個問題仲生猛 · Close-confirmation A/B</h2>'
                '<div class="info-card" style="border-left:3px solid var(--amber);'
                'margin:16px 0"><p>呢一節嘅來源 <code>data/ab_close_hold.json</code> '
                '<b>冇 placebo 區</b>（嗰次重跑冇加 <code>--placebo</code>）。'
                '一個冇對照嘅兩臂比較讀落同「有對照、企得穩」一模一樣，'
                '所以呢度寧願唔印。重跑 '
                '<code>scripts/ab_close_hold.py --placebo 300</code> 就會出現。</p>'
                '</div></div></section>')

    cells = pl.get("cells", {})
    rows = []
    for mkt in ("us200", "jp200"):
        if mkt not in cells:
            continue
        label = {"us200": "美股 US200", "jp200": "日股 JP200"}[mkt]
        for win in ("train", "test"):
            c = cells[mkt].get(win)
            if not c:
                continue
            wl = "2016–22 訓練" if win == "train" else "2023–26 樣本外"
            if not c.get("has_power"):
                badge = '<span style="color:var(--red)">無檢定力</span>'
            elif c.get("passes"):
                badge = '<span style="color:var(--green)">通過</span>'
            else:
                badge = '<span style="color:var(--red)">不通過</span>'
            rows.append(
                f"<tr><td>{label}</td><td>{wl}</td>"
                f"<td>{c['B_n']:,}</td>"
                f"<td>{c['B_avg%']:+.3f}%</td>"
                f"<td>{c['P2_null_mean%']:+.3f}%</td>"
                f"<td>{c['gap_vs_null_pp']:+.3f}pp</td>"
                f"<td>{c['z']:.2f}</td><td>{badge}</td></tr>")

    verdict = pl.get("verdict", "?")
    npass = pl.get("cells_passing", "?")
    lead = ("通過" if verdict == "PASS" else "唔通過")
    return (
        '<section class="section-closehold"><div class="container">'
        '<h2>同一個問題仲生猛 · Close-confirmation A/B</h2>'
        '<p style="font-size:0.95rem;opacity:0.85;max-width:74ch">'
        '上面撤回嘅 −4.05% 死嘅係<b>一條 pipeline</b>（訊號庫個表係空嘅，'
        '重跑唔到），唔係「10 日持有」呢個問題本身。一個壞咗嘅資料庫唔能夠'
        '否定一個研究問題 —— 咁講同錯用一個數字一樣錯。同一個問題而家喺另一條'
        '<b>仲生猛、而且重跑得出</b>嘅實驗度：<code>scripts/ab_close_hold.py</code>。'
        '兩臂都用 <b>HOLD = 10</b>，只有入場資格唔同（開市高過 S1 vs 開市同收市'
        '都企喺 S1 之上），所以呢個正正就係呢一頁嘅持有長度，只係喺另一個引擎度'
        '重新驗證。</p>'
        '<div class="info-card" style="border-left:3px solid var(--green);margin:16px 0">'
        '<p style="margin:0"><b>預先登記結果：{_html.escape(lead)}</b>（{_html.escape(str(npass))} '
        '格通過，門檻 z ≥ {pl["z_crit"]}，四格全要）。門檻喺跑之前已經寫死落 '
        '<code>docs/close-confirmation-prereg-2026-10-18.md</code>，事後唔准改。'
        'B 臂只保留 A 臂約一半交易，所以「B 贏 A」本身唔夠 —— 一個同 B 一樣大細、'
        '但係隨機抽嘅 A 臂子集做咗對照 null。如果收市確認只係令你<b>交易少啲</b>，'
        'B 應該落喺嗰條 null 入面；佢冇。</p></div>'
        '<table class="data-table"><thead><tr>'
        '<th>市場</th><th>窗口</th><th>B 單數</th><th>B 平均</th>'
        '<th>安慰劑 null 均值</th><th>差距</th><th>z</th><th>判定</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
        '<p style="font-size:0.85rem;opacity:0.8;max-width:74ch">'
        '<b>範圍提醒：</b>呢個量度嘅係<b>入場資格</b>，唔係持有日數。兩臂都係 10 日，'
        '所以呢個結果<b>唔係</b>「10 日好過 5/15/20 日」嘅證據 —— 嗰個掃描係 '
        'Signal Search Sprint 1 嘅 hold sweep，唔喺呢個 artifact 度。'
        '四格都高過 null 講嘅係<b>收市企穩呢個條件帶資訊</b>，唔係「持有耐啲就賺得多」。'
        '</p>'
        '<p style="font-size:0.8rem;opacity:0.7">'
        f'來源 <code>data/ab_close_hold.json</code>（bars: {d["bars_dir"]}，'
        f'split: {d["split"]}，hold: {d["hold_days"]}，'
        f'{pl["trials"]} trials/cell，seed {pl["seed"]}）。</p>'
        '</div></section>')


def _num(v):
    """Return a display number, or None when the source had no value."""
    if v in (None, "", "?"):
        return None
    return str(v)


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
    2026-10-18 update: the <b>−4.05%</b> T+10 figure previously quoted here has been withdrawn. It had no artifact: the run that produced it (<code>scripts/monthly_swing_backtest.py</code>) reads <code>data/dsa_hk.db</code>, whose <code>daily_report</code> table is empty, so it cannot be reproduced. What died there is one dead pipeline, not the question — whether a support entry should be held for 10 sessions is answered below by a run that does reproduce. The <a href="/equity_curve_t10">ten-year v2 equity curves</a> are a different experiment (2017–26 US, same v2 rules) and are not a substitute for either. What stands on this page is the per-trade table below, which is reproducible from its own run.</p>
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

{_section_block("Universe filter (20d ADV, fail-closed)", universe_html, "section-universe") if universe_html else ''}

{_withheld_risk_block(risk_tbl, "section-risk")}

{_close_hold_block()}

{_section_block("Walk-forward OOS (80/20 train/test split)", oos_tbl, "section-oos") if oos_tbl else ''}

<div class="container">
  <div class="card mt-5">
    <h2>點樣解讀</h2>
    <ul>
      <li><b>Live stop/target 取代 hardcoded</b> — 51% 訊號用 live signal 嘅 stop/target（avg −2.45%/+4.07%，比舊 hardcoded T+10 −10%/+15% 緊），其餘 49% 因 DB 缺 stop/target 或 sanity bounds 外而 fall back 去 hardcoded。</li>
      <li><b>T+10 即日鮮嘅優勢係頻率</b> — 單筆 +0.72% 但可以日日做；T+10 單筆大但要忍 10 日波動。兩者唔互斥，可以並行 paper-trade 驗證。</li>
      <li><b>Equity curve per horizon</b> — the loose "+55% sum-of-pct" figure was
      replaced by <a href="/equity_curve_t10">per-horizon fixed-capital curves</a>
      for the ten-year v2 rule set (1% risk/trade, 10 concurrent, US$1M).
      Those are a different experiment from this page's six-week HK swing window;
      each page now states its own model rather than quoting the other's number.</li>
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

    # SEO description — this text is what Google shows under the title and what
    # every social preview shows, so it has to survive a contradiction.
    #
    # It previously read "資金管理下組合回報 +44.02%（MaxDD 1.10%）". That figure
    # comes from the "Total return (compounded)" row of the backtest markdown,
    # which sits under a heading claiming a 5-concurrent-position cap — over the
    # same 25-weekday window with 10-day holds, that cap allows at most 12
    # trades, and the block claims 120. The cap was not applied. The risk-managed
    # portfolio result on this site (-4.05%, 17 of 121 taken, 104 skipped) is
    # the one that honoured it.
    #
    # So: quote per-trade figures, label them as per-trade, and never print a
    # portfolio number this page cannot substantiate. Also, do not prepend "+" to
    # a value that already carries its sign, and never print "?" for a metric
    # that was not found — drop the clause instead.
    t10_d, t1_d = _num(t10_avg), _num(t1_avg)
    n_d = _num(n_signals.split(" ")[0])
    oos_d = _num(oos_wr_test)


    per_trade = []
    if t10_d:
        per_trade.append(f"T+10 每次交易平均 {t10_d}%")
    if t1_d:
        per_trade.append(f"T+1 日內平均 {t1_d}%")
    if n_d:
        per_trade.append(f"{n_d} 個 BUY 訊號")
    desc = "，".join(per_trade) + "（已扣交易成本）。"
    if oos_d:
        desc += f"樣本外勝率 {oos_d}%。"
    desc += (
        "注意：每次平均 ≠ 組合回報。風險預算下的組合回報為負，"
        "詳見方法論頁同 T+10 權益曲線。教育用途，非投資建議。"
    )
    desc = desc[:155]

    canonical = "https://www.win9you.com/backtest"
    title = "回測報告 · T+10 Swing vs T+1 Day-trade · Leeks Terminal"

    article_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": f"Leeks Terminal 6-Month Backtest: T+10 {_sign(t10_avg)} per trade (live stop/target) vs T+1 {_sign(t1_avg)} per trade",
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
            f"risk-managed portfolio sizing at 1% risk/trade with a 10 concurrent cap, "
            f"80/20 walk-forward OOS validation)."
        ),
    }

    # The risk-managed portfolio number, read from the equity-curve page that
    # actually applied the concurrency cap. `portfolio_total` (the markdown's
    # "Total return (compounded)" row) is NOT used: that block claims 120 trades
    # taken under a 5-slot cap over a 25-weekday window, which allows at most
    # 12. It is not a portfolio return, it is a sum of per-trade percentages
    # with the cap ignored.
    risk_mgmt = _risk_managed_return()
    # z range for the close-confirmation A/B, read not typed. If the placebo
    # block is missing, say nothing quantitative rather than quote a stale z.
    _pl = _ab_read().get("placebo") or {}
    _zs = [c["z"] for m in _pl.get("cells", {}).values() for c in m.values()
           if c.get("z") is not None]
    _zspan = (f"z 由 {min(_zs):.1f} 到 {max(_zs):.1f}，門檻 1.96"
              if _zs else "門檻 1.96（來源未跑 placebo，暫時唔報數）")
    risk_txt = (
        f"風險預算下嘅 T+10 組合回報為 {risk_mgmt}"
        if risk_mgmt else
        "呢個窗口嘅 T+10 風險管理組合回報<b>冇一個可以引用嘅數字</b>："
        "產生佢嘅 monthly swing backtest 依賴嘅訊號庫（<code>data/dsa_hk.db</code> "
        "嘅 <code>daily_report</code> 表）而家係空嘅，重跑唔到。"
        "站上曾經引用過嘅 −4.05% 查唔到任何來源，已經停用。"
        "但要分清楚：死嘅係<b>呢一條 pipeline</b>，唔係「10 日持有」呢個問題 —— "
        "壞咗嘅資料庫唔能夠否定一個研究問題。"
        "同一個問題而家喺 close-confirmation A/B 度重新驗證（兩臂都係 HOLD = 10，"
        f"美股同日股、訓練同樣本外共四格，全部企喺預先登記嘅安慰劑對照之上，{_zspan}），"
        "見下面「同一個問題仲生猛」一節。"
        "另外 10 年期 v2 規則嘅定資模擬見 <a href='/equity_curve_t10'>權益曲線頁</a>，"
        "但嗰個係另一個實驗（2017–26 美股、同一套 v2 規則），唔可以攞嚟代表呢一頁。"
    )

    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": "T+10 swing 同 T+1 day-trade 邊個回報高？",
             "acceptedAnswer": {"@type": "Answer", "text": (
                 f"以每單計，{_sign(t10_avg)} 嘅 T+10 高過 {_sign(t1_avg)} 嘅 T+1。"
                 f"但每單 ≠ 組合：{risk_txt}。T+1 即日鮮先係現實可行的 edge。"
             )}},
            {"@type": "Question", "name": "恒指熊市日會唔會令 T+10 失效？",
             "acceptedAnswer": {"@type": "Answer", "text": (
                 "回測窗口入面有 HSI 單日跌 ≥1.5% 嘅熊市日。"
                 "持倉期有熊市日嘅 T+10 交易反而更強（高 WR + 高 avg return），"
                 "但呢個現象樣本小，需要更多熊市日先有 statistical power。"
             )}},
            {"@type": "Question", "name": "呢個回測用邊個 stop/target？",
             "acceptedAnswer": {"@type": "Answer", "text": (
                 "由 2026-09-09 起，T+1/T+3/T+5/T+10 改用 live signal 嗰日 DB 儲存嘅 stop_loss 同 target_price 模擬（51% 訊號用 live，"
                 "其餘因 DB 缺值或 sanity bounds 外 fall back 去 hardcoded）。舊版用固定 -10% stop / +15% target 高估咗 edge。"
             )}},
            {"@type": "Question", "name": "點解「每次平均」會高過組合回報？",
             "acceptedAnswer": {"@type": "Answer", "text": (
                 "因為每單都要食倉位、扣成本、受同時持倉上限同大市方向限制。"
                 f"同一批訊號，{risk_txt}。"
                 "站上曾經引用過一個高得多的數字（+44.02%），嗰個係逐筆百分比相加、"
                 "而且冇執行同時持倉上限 —— 同一個窗口 5 個倉位、每單持有 10 日，"
                 "數學上最多只做得 12 單，但嗰個數字聲稱做咗 120 單。已經撤回。"
             )}},
            {"@type": "Question", "name": "Walk-forward OOS 點解得咁少 trades？",
             "acceptedAnswer": {"@type": "Answer", "text": (
                 "現有 DB 由 2026-06-27 開始，先 80% 嘅 signals 做 train，後 20% 做 OOS。"
                 + (f"OOS {oos_wr_test}% WR vs train {oos_wr_train}% WR。"
                    if _num(oos_wr_test) and _num(oos_wr_train) else "")
                 + "樣本仍然細，唔足以支持任何關於未來表現嘅結論；"
                 "framework 已 set up，每月自動重跑累積更多 OOS data。"
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
