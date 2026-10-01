MOBILE_BOTTOM_NAV = '''
<nav class="mobile-bottom-nav" aria-label="Mobile">
  <a href="/"{a_home}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>Home</a>
  <a href="/hk200/"{a_hk}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><rect x="7" y="12" width="3" height="6"/><rect x="12" y="8" width="3" height="10"/><rect x="17" y="4" width="3" height="14"/></svg>HK</a>
  <a href="/us200/"{a_us}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="M7 14l3-3 3 3 4-5"/></svg>US</a>
  <a href="/jp200/"{a_jp}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18"/></svg>JP</a>
  <a href="/methodology"{a_me}>{lang}<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/></svg>Method</a>
</nav>
'''


def mobile_bottom_nav(active: str = "", en: bool = False) -> str:
    """Bottom tab bar for <=768px.

    2026-10-02: leeks.css already carried the full .mobile-bottom-nav rules
    (fixed bar, icon sizing, .active state, body bottom padding) but no
    template ever emitted the element, so every phone-width page hid .nav
    (display:none !important) and had no navigation at all. This renders it.
    """
    a = lambda k: ' class="active"' if active == k else ""
    lang = "EN" if not en else "中"
    return (
        MOBILE_BOTTOM_NAV
        .replace("{a_home}", a("home"))
        .replace("{a_hk}", a("hk"))
        .replace("{a_us}", a("us"))
        .replace("{a_jp}", a("jp"))
        .replace("{a_me}", a("me"))
        .replace("{lang}", lang)
    )


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
        return ""

    rules = d.get("rules", {})
    oos = d.get("setup_oos", {}).get(key, {})
    src = d.get("performance_source", "")
    asof = rows[0].get("asof", "")

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
            f'第三方回測數字，非本站自行驗證</div></div>'
        )

    rule_lines = " · ".join([
        "只做多",
        f"下一個交易日以 S1 限價買入（開市低過 S1 則跳過）",
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
        f"<td class=\"cell-right mono\">{r['ret_5d_pct']:.2f}%</td></tr>"
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
      <b>倉位：</b>每單 1% 風險，單名上限 10% 名義
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;font-size:0.82rem">
      <thead><tr>
        <th>代號</th><th style="text-align:right">T-1 收市</th>
        <th style="text-align:right">入場 S1</th><th style="text-align:right">距離</th>
        <th style="text-align:right">止損</th><th style="text-align:right">止損幅度</th>
        <th style="text-align:right">5 日</th>
      </tr></thead>
      <tbody>{trs}</tbody>
    </table>
    </div>
    {more}
    <p style="font-size:0.72rem;opacity:0.55;margin-top:10px">
      績效數字來源：{src}。本站未自行重跑該回測，故此處標示為第三方數字。
      過往表現不代表未來結果，非投資建議。
    </p>
  </div>
</div>
"""
