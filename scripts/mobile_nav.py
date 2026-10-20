MOBILE_BOTTOM_NAV = '''
<nav class="mobile-bottom-nav" aria-label="Mobile">
  <a href="/"{a_home}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>Home</a>
  <a href="/hk200/"{a_hk}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><rect x="7" y="12" width="3" height="6"/><rect x="12" y="8" width="3" height="10"/><rect x="17" y="4" width="3" height="14"/></svg>HK</a>
  <a href="/us200/"{a_us}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="M7 14l3-3 3 3 4-5"/></svg>US</a>
  <a href="/jp200/"{a_jp}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18"/></svg>JP</a>
  <details class="mob-more">
    <summary aria-label="更多頁面"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/></svg>More</summary>
    <div class="mob-more-panel">
      <a href="/backtest.html">Backtest · 回測</a>
      <a href="/insights.html">Insights · 研究</a>
      <a href="/compare/">Compare · 對比</a>
      <a href="/methodology">Methodology · 方法論</a>
      <a href="/methodology.html">Methodology</a>
      <a href="/track-record/">Track record</a>
      <a href="/research/negative-results/">Negative results</a>
      <a href="/about/">About</a>
      <a href="/disclaimer/">Disclaimer</a>
      <a href="/privacy/">Privacy</a>
      <a href="{langhref}" lang="{langattr}">{lang} · {langname}</a>
    </div>
  </details>
</nav>
'''

# 2026-10-17: the HK "not covered by v2" banner below is rendered on ~200 HK
# ticker pages, so its figures come from perf_figures (the retest summary) and
# never from a literal — a stale number there repeats hundreds of times.
import perf_figures  # noqa: E402


def mobile_bottom_nav(active: str = "", en: bool = False) -> str:
    """Bottom tab bar for <=768px.

    2026-10-02: leeks.css already carried the full .mobile-bottom-nav rules
    (fixed bar, icon sizing, .active state, body bottom padding) but no
    template ever emitted the element, so every phone-width page hid .nav
    (display:none !important) and had no navigation at all. This renders it.
    """
    a = lambda k: ' class="active"' if active == k else ""
    # The language switch used to be a bare "EN" glyph rendered INSIDE the
    # Method anchor, so tapping what looked like a language control navigated
    # to /methodology. It is now its own link at the bottom of the More panel.
    lang, langhref = ("中", "/") if not en else ("EN", "/en/")
    langattr = "zh-Hant-HK" if not en else "en"
    langname = "繁體中文" if not en else "English"
    return (
        MOBILE_BOTTOM_NAV
        .replace("{a_home}", a("home"))
        .replace("{a_hk}", a("hk"))
        .replace("{a_us}", a("us"))
        .replace("{a_jp}", a("jp"))
        .replace("{langhref}", langhref)
        .replace("{langattr}", langattr)
        .replace("{langname}", langname)
        .replace("{lang}", lang)
    )


def _size_cell(r: dict) -> str:
    """The tradeable order quantity, or an honest "not sizeable today".

    Before 2026-10-08 this column did not exist and the plan shipped only S1
    and a stop, so the reader had no way to know how many shares the sizing
    rule actually allowed — and the JSON's number was a fraction of a share
    on Japan. Now the count is floored to whole trading units, and a name that
    needs more risk than one unit costs says so in the row rather than
    printing a number nobody can send to a broker.
    """
    sh = int(r.get("shares_at_1pct_risk") or 0)
    if sh > 0:
        lot = int(r.get("lot_size") or 1)
        unit = f"{sh:,} 股" + (f"（{sh // lot} 手）" if lot > 1 else "")
        return f"{unit}<div style=\"font-size:.68rem;opacity:.6\">風險 {r.get('risk_pct_actual', 0):.2f}%</div>"
    return ('<span style="opacity:.75">唔落單</span>'
            f'<div style="font-size:.68rem;opacity:.6">{r.get("size_note", "")}</div>')


def render_v2_block(market: str) -> str:
    """Primary v2 limit-buy plan block for a hub page.

    Reads public/v2-signals.json (written by scripts/build_v2_signals.py from
    our own OHLC). The *rules* are ours to state; the *performance* figures
    attached to each setup are the third-party study's out-of-sample numbers
    and are labelled as such on the page — they are not our verified record.
    """
    import json
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / "public" / "v2-signals.json"
    if not p.exists():
        return ""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ""

    key = market.lower() + "200"
    rows = [r for r in d.get("signals", []) if r.get("market") == key]
    if not rows:
        # 2026-10-06: an empty result used to mean "render nothing", which made
        # the HK hub indistinguishable from a v2 page that simply found no
        # signals. HK has no v2 signals *by design* (INCLUDE_HK=False), and
        # saying nothing there is the one case a reader most needs told.
        if key == "hk200":
            return (
                '<div class="container" style="margin:22px 0 6px">'
                '<div class="info-card" style="border-left:3px solid var(--amber)">'
                '<h2 style="margin-top:0">呢一版未涵蓋港股</h2>'
                '<p style="font-size:0.85rem;opacity:0.75;margin:6px 0 0">'
                '港股<b>未納入 v2</b>，所以呢頁冇港股入場計劃。'
                '舊版 (v1) 引擎嘅行動建議（BUY/SELL/WAIT）已於 2026-10-18 全站移除。'
                '我哋自己嘅研究發現港股喺 '
                + perf_figures.hk_note('zh') +
                '下面只列價格行為分析（Phase / S-R ladder / 情境），唔構成 v2 訊號。</p></div></div>'
            )
        return ""

    rules = d.get("rules", {})
    oos = d.get("setup_oos", {}).get(key, {})
    src = d.get("performance_source", "")
    asof = rows[0].get("asof", "")

    # 2026-10-05: this used to be a hard-coded "third-party, not verified
    # by us". That was true while the numbers came from the 2026-10-01
    # audit, but the shipped rule now carries our own close-hold
    # requirement and the attached statistics are measured by us, so the
    # label has to follow the payload instead of asserting something the
    # data no longer says.
    third_party = bool(d.get("performance_is_third_party"))
    prov = ("第三方回測數字，非本站自行驗證" if third_party
            else "本站自測數字 · 含收市企穩 S1 條件 · 見方法論頁")
    stat = ""
    if oos:
        w95 = oos.get("win95") or [None, None]
        ci = f"（95% CI {w95[0]}–{w95[1]}%）" if w95[0] else ""
        stat = (
            f'<div class="idx-card" style="border:1px solid var(--border);'
            f'border-radius:8px;padding:10px 12px">'
            f'<div style="font-size:0.7rem;opacity:0.6;letter-spacing:0.06em">'
            f'THIS SETUP · 樣本外</div>'
            f'<div style="font-size:1.15rem;margin:2px 0">勝率 {oos.get("win_pct")}%{ci}</div>'
            f'<div style="font-size:0.75rem;opacity:0.7">平均淨 {oos.get("avg_net_pct")}%／單 '
            f'· n = {oos.get("n"):,}（2023–26）</div>'
            f'<div style="font-size:0.68rem;opacity:0.5;margin-top:4px">'
            f'{prov}</div></div>'
        )

    rule_lines = " · ".join([
        "只做多",
        "下一個交易日以 S1 限價買入（開市低過 S1 則跳過，收市企唔穩 S1 唔當成交）",
        f"止損 S1 ×(1 − 3×ATR%)",
        f"無目標價，第 {rules.get('exit','').split()[-3] if rules.get('exit') else 10} 個交易日收市離場",
    ])

    trs = "".join(
        f"<tr><td class=\"mono\"><a href=\"/{key}/ticker/{r['symbol']}\" "
        f"style=\"color:var(--fg)\">{r['symbol']}</a></td>"
        f"<td class=\"cell-right mono\">{r['last_close']:.2f}</td>"
        f"<td class=\"cell-right mono text-accent\">{r['entry_s1']:.2f}</td>"
        f"<td class=\"cell-right mono\">{r['dist_to_entry_pct']:.2f}%</td>"
        f"<td class=\"cell-right mono text-bear\">{r['stop']:.2f}</td>"
        f"<td class=\"cell-right mono\">{r['stop_dist_pct']:.2f}%</td>"
        f"<td class=\"cell-right mono\">{r['ret_5d_pct']:.2f}%</td>"
        f"<td class=\"cell-right mono\">{_size_cell(r)}</td></tr>"
        for r in rows[:40]
    )
    more = (f'<p style="font-size:0.75rem;opacity:0.6;margin-top:8px">'
            f'另有 {len(rows) - 40} 張限價單未列出，按 5 日跌幅由大到小排序（最超賣優先）。</p>'
            if len(rows) > 40 else "")

    return f"""
<div class="container" style="margin:22px 0 6px">
  <div class="info-card" style="border-left:3px solid var(--accent)">
    <h2 style="margin-top:0">v2 交易計劃 · T-1 {asof} 收市</h2>
    <p style="font-size:0.85rem;opacity:0.75;margin:6px 0 12px">
      10 年回測顯示原有四個策略（SELL_R1 / BUY_S1 / BREAK_LONG / BREAK_SHORT）於
      2016–2026 並無正期望值，故改用以下經樣本外驗證的規則。
    </p>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-bottom:12px">
      <div class="idx-card" style="border:1px solid var(--border);border-radius:8px;padding:10px 12px">
        <div style="font-size:0.7rem;opacity:0.6">今日限價單</div>
        <div style="font-size:1.15rem">{len(rows)}</div>
        <div style="font-size:0.72rem;opacity:0.65">以 S1 掛單，僅下一個交易日有效</div>
      </div>
      {stat}
    </div>
    <div style="font-size:0.8rem;opacity:0.8;margin-bottom:10px">
      <b>規則：</b>{rule_lines}<br>
      <b>股票篩選：</b>T-1 收市價 &gt; 200 天線且 200 天線 20 日內向上 ·
      <b>大市過濾：</b>指數 T-1 收市價 &gt; 其 200 天線 ·
      <b>倉位：</b>每單 1% 風險，單名上限 10% 名義；股數按交易所最小交易單位
      （単元株数）向下取整，買唔到 1 個單位嘅標示為當日不落單 ——
      {rules.get('sizing_equity') and ' · '.join(
          f'{"US$" if m == "us200" else "¥"}{v:,.0f}'
          for m, v in rules['sizing_equity'].items()) or ''}
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;font-size:0.82rem">
      <thead><tr>
        <th>代號</th><th style="text-align:right">T-1 收市</th>
        <th style="text-align:right">入場 S1</th><th style="text-align:right">距離</th>
        <th style="text-align:right">止損</th><th style="text-align:right">止損幅度</th>
        <th style="text-align:right">5 日</th>
        <th style="text-align:right">股數</th>
      </tr></thead>
      <tbody>{trs}</tbody>
    </table>
    </div>
    {more}
    <p style="font-size:0.72rem;opacity:0.55;margin-top:10px">
      績效數字來源：{src}（{prov}）。
      過往表現不代表未來結果，非投資建議。每單優勢不等於跑贏大市 —— 限制見方法論頁。
    </p>
  </div>
</div>
"""


def render_v2_status_for_ticker(market: str, safe: str,
                                old_verdict: str | None = None,
                                old_phase: str | None = None) -> str:
    """Per-ticker v2 verdict for a detail page, injected above the v1 content.

    2026-10-06: only build_dashboard_page (the hub) called render_v2_block, so
    the 422 US/JP detail pages carried no v2 state at all — a visitor landing
    on /us200/ticker/AAPL saw only the retired v1 action plan and its v1
    strategy name, with nothing on the page saying the live engine had
    replaced it. The hub already stated that the four v1 strategies had no
    positive expectancy over 2016-2026; the detail pages did not.

    Additive on purpose: the v1 block below is still what HK (and any
    not-yet-screened name) is served, and deleting it would hide which engine
    produced a number. This states the v2 answer first and labels the rest.

    Returns "" for markets the live engine does not scan (HK), so the page
    says nothing it cannot support.
    """
    import json
    from pathlib import Path

    key = (market or "").lower() + "200"
    if key == "hk200":
        # 2026-10-18 (2-into-1). This banner used to say HK "is running the old
        # v1 engine" and that the content below came from v1. Both are now
        # false: the v1 action output was removed from every market including
        # HK, so there is no engine left that produces a HK action. HK is
        # simply not screened, and what remains on the page is price-behaviour
        # description with no action attached to it.
        return (
            '<div class="container" style="margin:22px 0 6px">'
            '<div class="info-card" style="border-left:3px solid var(--amber)">'
            '<h2 style="margin-top:0">呢一頁未涵蓋於現行引擎</h2>'
            '<p style="font-size:0.85rem;opacity:0.75;margin:6px 0 0">'
            '港股<b>未納入 v2</b>，所以本站<b>冇</b>任何港股行動計劃 —— '
            '舊版引擎嘅行動建議亦已全站移除。我哋自己嘅研究發現港股喺 '
            + perf_figures.hk_note('zh') +
            '下面只係價格行為分析（phase / S-R ladder / 情境），唔構成 v2 訊號，'
            '亦唔應該當成入場建議。</p></div></div>'
        )

    p = Path(__file__).resolve().parent.parent / "public" / "v2-signals.json"
    if not p.exists():
        return ""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ""

    rows = [r for r in d.get("signals", []) if r.get("market") == key]
    hit = next((r for r in rows if r["symbol"] == safe), None)
    asof = rows[0].get("asof", "") if rows else ""

    if hit:
        # 2026-10-18 (2-into-1). This card used to repeat 距入場位 / 入場 S1 /
        # 止損 above the Action Plan card, which now owns those numbers as the
        # page's single action authority. Two cards printing the same entry on
        # one page is the same class of problem as two cards printing
        # different verdicts, so the levels now live in exactly one place and
        # this card keeps only what the Action Plan does not say: the risk
        # caveat and the withdrawal record.
        # No entry / stop / distance here on purpose — the Action Plan card
        # below is where those numbers live now, and printing them twice puts
        # the page back to two places that can disagree.
        body = ('<div style="font-size:1.15rem;margin:2px 0">'
                '本頁行動計劃由 v2 提供 ↓</div>')
        tag = "v2 訊號"
        col = "var(--bull)"
        # 2026-10-18 (2-into-1). This used to be a "the two engines disagree"
        # warning that quoted the RETIRED v1 verdict (WAIT/SELL/BUY) beside the
        # v2 entry — which kept the retired engine alive as a second opinion.
        # The retired engine no longer renders an action anywhere on the site,
        # so there is nothing left to disagree with. What survives is the part
        # that was always true about v2 itself: it has no downtrend filter, so
        # it can fire mid-decline with a stop that is genuinely wide.
        risk_note = ""
        if old_phase and ("down" in str(old_phase).lower()
                          or "bear" in str(old_phase).lower()):
            risk_note = (
                '<p style="font-size:0.78rem;margin:8px 0 0;color:var(--amber)">'
                '<b>技術面注意</b>：v2 設計上冇跌勢過濾（跌到 20 日低位就接），'
                f'所以喺技術面 {old_phase} 狀態一樣會出單 —— '
                '留意下面止損距離，風險由你自行判斷。</p>'
            )
        body += risk_note
    else:
        body = (
            '<div style="font-size:1.15rem;margin:2px 0">今日無 v2 訊號</div>'
            f'<div style="font-size:0.78rem;opacity:0.75">'
            f'該股今日未通過 v2 篩選（{key.upper()} 今日共 {len(rows)} 隻通過）。'
            '頁面下半部嘅價格行為分析仍然有效，但唔構成入場計劃。</div>'
        )
        tag = "v2"
        col = "var(--fg-2)"

    return f"""
<div class="container" style="margin:22px 0 6px">
  <div class="info-card" style="border-left:3px solid {col}">
    <div style="font-size:0.7rem;letter-spacing:0.08em;opacity:0.6">{tag} · T-1 {asof} 收市</div>
    {body}
    <p style="font-size:0.75rem;opacity:0.6;margin:8px 0 0">
      v2 規則：只做多 · 股價喺向上嘅 200 天線之上 · 大市過濾 · 跌到 20 日低位 S1 掛限價單，
      收市企穩 S1 先算成交 · 止損 S1 下方 3 倍 ATR · 冇目標價，第 10 個交易日收市離場。
    </p>
    <p style="font-size:0.75rem;opacity:0.6;margin:6px 0 0">
      本頁嘅行動計劃<b>只有 v2 一個來源</b>：舊版四個策略（SELL_R1 / BUY_S1 /
      BREAK_LONG / BREAK_SHORT）於 2016–2026 回測並無正期望值，已於 2026-10-18
      由頁面移除。價格行為分析（phase / S/R ladder / 情境）仍然保留，
      但佢哋係描述，唔係行動指示。<a href="/methodology">修正記錄 →</a>
    </p>
  </div>
</div>
"""
