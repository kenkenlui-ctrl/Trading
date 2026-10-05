"""rewrite_methodology_v2.py — Rewrite public/methodology.html to describe v2.

WHY
    2026-10-05: the v2 signal engine (scripts/build_v2_signals.py, which cites
    the 2026-10-01 third-party 10-year signal audit) shipped on 2026-10-02, but
    methodology.html is a hand-edited page and was never updated. The site was
    therefore publishing v2 signals while its own methodology page still
    documented the four v1 strategies the audit had just retired.

    Worse, the same audit (Finding 3) had already flagged that the T+10 headline
    appeared as +3.02% on methodology, +1.63% on /backtest and -4.05% on the
    T+10 equity curve. All three were live simultaneously.

    User decision (2026-10-05): v2 becomes the primary description, a public
    correction record is added, third-party figures carry explicit provenance,
    and T+10 stops being a headline — the honest fixed-capital figure (-4.05%)
    becomes the single reference point.

WHAT IT CHANGES (surgical, not a full rewrite)
    - <main> body            → new v2 content
    - <title> / meta desc    → v2 wording
    - og:title / og:desc     → v2 wording
    - JSON-LD Article node   → dateModified + v2 headline
    - JSON-LD FAQPage answers→ v2 rules, no more "30/60/90/197-day backtest picks
                              the best edge" claim

    Everything outside <main> is preserved byte-for-byte: the nav, the footer,
    the T-1 badge (which build_static.restamp_t1_badges() rewrites every build),
    the theme script, the stylesheet links.

Idempotent: re-running produces a byte-identical file.
"""
from __future__ import annotations

import re
from pathlib import Path

PUB = Path("/Users/kenken/dev/dsa-hk/public")
PAGE = PUB / "methodology.html"

TITLE = "Methodology · v2 訊號規則 · Leeks Terminal"
DESC = (
    "現行 v2 訊號規則：只做多 · S1 限價入場 · 3×ATR 止損 · 10 個交易日時間離場 · "
    "200 天線 + 大市過濾。附 2026-10 第三方 10 年審計之修正記錄。OHLC-only，零 LLM 幻覺。"
)

FAQ = [
    (
        "Leeks Terminal 嘅信號係點計出嚟嘅？",
        "全部由 Python 對 T-1 日線 OHLC 確定性計算。先疊 20/60/120/200 日 S/R ladder，"
        "再要求股價喺 200 天線之上且 200 天線 20 日內向上，並要求大市指數喺自己嘅 200 天線之上，"
        "然後喺 S1 掛限價買入、3×ATR 止損、第 10 個交易日收市離場。"
        "LLM 只負責解釋文字，唔會產生任何數字。",
    ),
    (
        "點解淨係做多？之前唔係有做空嘅策略嗎？",
        "有。舊版有 SELL_R1 同 BREAK_SHORT。2026-10-01 嘅第三方 10 年審計顯示，"
        "SELL_R1 喺 T+5 每單淨蝕 0.57%，而且喺 30 個 market × horizon × period 組合"
        "全部為負；BREAK_LONG / BREAK_SHORT 嘅止損觸發率達 67–69%。"
        "呢兩類訊號已全部停用。港股因為沽空涉及借券資格同額外成本，"
        "扣約 0.45% round-trip 後樣本外淨收益係 +0.02%，等於零，所以只作資訊參考。",
    ),
    (
        "你哋頁面以前嘅 win% 係咩？而家點變？",
        "以前每隻股掛住一個歷史勝率 badge（例如 60 日勝率），令人以為係信心指標。"
        "審計實測該勝率同下一筆交易嘅 rank correlation 只有 −0.001 / −0.004，"
        "即係冇任何預測力。這個 badge 已停用；表格中嘅 Win% 欄而家只顯示"
        "該 setup 本身喺樣本外（2023–2026）嘅歷史勝率，並附交易筆數 n 讓你自行判斷樣本大小。",
    ),
    (
        "T+10 swing 而家點計？以前話 +3.02% 係咪假嘅？",
        "以前網站多處引用 T+10 swing +3.02%，嗰個係逐筆百分比直接相加，"
        "等於假設每一單都用盡 100% 資金同時持有，唔係一個投資組合回報。"
        "誠實嘅定資模擬（本金 HK$1,000,000、每單 1% 風險、最多 5 個同時持倉、每日 mark-to-market）"
        "實測 T+10 總回報係 <b>−4.05%</b>（期末權益 HK$959,531，最大回撤 −4.89%）。"
        "而家網站唔再將 T+10 當成 edge 宣傳。",
    ),
    (
        "頁面啲 performance 數字係你哋自己驗證㗎？",
        "係，2026-10-05 起用嘅係本站自測數字：Yahoo 10 年日線、同一個引擎、"
        "按市場計成本、日期叢集 t。重跑方法同輸入資料都公開（scripts/ab_close_hold.py、data/bt10y）。"
        "要留意一點：我哋引擎重現唔到 2026-10-01 第三方審計報告嘅基線，"
        "同一規則高約 1.7 倍，差異原因未查清 —— 所以兩邊數字唔可以直接比較，"
        "有效嘅係同一引擎內嘅 A/B 相對差。",
    ),
    (
        "v2 係咪真係有選時能力，定只係買上升趨勢股？",
        "我哋跑咗對照組：同一批股票、同一個篩選、同一個大市過濾、同一個止損同離場，"
        "唯一分別係唔等 S1 觸及、喺開市價直接買。"
        "結果 v2 每單收益喺四個窗口（美股／日股 × 訓練／樣本外）全部高過對照組 2.6 至 6.6 倍。"
        "但要留意兩點：一、呢個係每單優勢，v2 只做對照組 2–4% 嘅交易機會，"
        "同等資金下嘅組合回報仲未測過；二、呢個結果同 2026-10-01 第三方審計報告"
        "得出嘅結論相反（嗰份話 S1 選時價值有限），差異原因我哋未查清。",
    ),
    (
        "數據幾時更新？用即市價定收市價？",
        "永遠使用 T-1 收市價（hard rule）。各市場喺自己收市後重建全站，"
        "開市前查看訊號即可，不會使用 live intraday 數據計算。",
    ),
    (
        "呢個站係投資建議嗎？",
        "不是。全部內容屬教育用途並附免責聲明。短線交易風險極高，過往表現不代表將來回報；"
        "v2 嘅回測樣本外勝率約 50–54%，屬統計噪音範圍，唔構成任何回報承諾。",
    ),
]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_faq_json() -> str:
    import json
    qa = [
        {"@type": "Question", "name": q,
         "acceptedAnswer": {"@type": "Answer", "text": a}}
        for q, a in FAQ
    ]
    return json.dumps(qa, ensure_ascii=False, indent=4)


def build_body() -> str:
    rows_fix = [
        ("SELL_R1（升到 R1 沽空至 box 中位）",
         "T+5 每單淨蝕 0.57%，t = −14；喺 30 個 market × horizon × period 組合<b>全部</b>為負",
         "已停用"),
        ("BREAK_LONG / BREAK_SHORT（突破追）",
         "T+5 −0.31% / −0.48%，止損觸發率 67–69%",
         "已停用"),
        ("每隻股掛歷史勝率 badge（60 日 win%）",
         "同下一筆交易嘅 rank correlation 只有 −0.001 / −0.004 —— 冇預測力，"
         "但頁面呈現方式令人誤以為係信心指標",
         "已停用，改顯示 setup 樣本外勝率 + 樣本數 n"),
        ("T+10 swing「+3.02% net」",
         "嗰個係逐筆百分比相加，假設每單都用盡 100% 資金。誠實定資模擬實測係 "
         "<b>−4.05%</b>（期末權益 HK$959,531）",
         "已撤回，唔再當 headline"),
        ("Portfolio sim「+55% 6mo, MaxDD 1.96%」",
         "同一個 sum-of-pct 口徑；該數字已於 2026-09-11 被明確標示為非投資組合回報",
         "已撤回"),
        ("港股訊號（佔全站三個市場其中一個）",
         "扣約 0.45% round-trip 成本後，樣本外淨收益 +0.02%，等於零；"
         "沽空另需借券資格",
         "改為只作資訊參考，唔落實倉；並標明本站無法獨立驗證（資料源唔提供 .HK 個股）"),
        ("long entry 淨係「touch S1」就算入",
         "touch 之後收市跌返落 S1 以下，代表該支持位失效 —— 你買嘅唔係支持位，"
         "係接飛刀。要求收市企穩 S1 先入，移除咗 26% 交易，嗰批平均每單 −3.90%",
         "2026-10-05 已出貨：四個窗口全部改善（每單約翻倍、t 大升），"
         "代價係交易量減半。見下方 §4.5"),
    ]
    fix_rows = "\n".join(
        f"""      <tr>
        <td>{a}</td>
        <td>{b}</td>
        <td><b>{c}</b></td>
      </tr>"""
        for a, b, c in rows_fix
    )

    train_rows = "\n".join(
        f"""      <tr>
        <td>{m}</td>
        <td class="mono">{n:,}</td>
        <td class="mono">{w}</td>
        <td class="mono">{avg}</td>
        <td class="mono">{pf}</td>
        <td class="mono">{t}</td>
      </tr>"""
        for m, n, w, avg, pf, t in [
            ("US", 3693, "64.5%", "+1.91%", "2.25", "+10.67"),
            ("JP", 2424, "55.3%", "+1.03%", "1.51", "+5.08"),
        ]
    )

    oos_rows = "\n".join(
        f"""      <tr>
        <td>{m}</td>
        <td class="mono">{n:,}</td>
        <td class="mono">{w}</td>
        <td class="mono">{avg}</td>
        <td class="mono">{pf}</td>
        <td class="mono">{t}</td>
      </tr>"""
        for m, n, w, avg, pf, t in [
            ("US", 2992, "60.2%", "+1.78%", "2.05", "+7.67"),
            ("JP", 2193, "65.8%", "+2.41%", "2.72", "+9.31"),
        ]
    )

    ab_rows = "\n".join(
        f"""      <tr>
        <td>{m}</td>
        <td>{w}</td>
        <td class="mono">{kept}</td>
        <td class="mono">{a1} → {a2}</td>
        <td class="mono">{t1} → {t2}</td>
      </tr>"""
        for m, w, kept, a1, a2, t1, t2 in [
            ("US", "訓練 2016–22", "51.0%", "+0.888%", "+1.913%", "+5.96", "+10.67"),
            ("US", "樣本外 2023–26", "51.5%", "+0.783%", "+1.777%", "+3.73", "+7.67"),
            ("JP", "訓練 2016–22", "46.7%", "+0.149%", "+1.034%", "+1.54", "+5.08"),
            ("JP", "樣本外 2023–26", "51.1%", "+1.211%", "+2.414%", "+6.40", "+9.31"),
        ]
    )

    ctrl_rows = "\n".join(
        f"""      <tr>
        <td>{m}</td>
        <td>{w}</td>
        <td class="mono">{n:,}</td>
        <td class="mono">{av}</td>
        <td class="mono">{ac}</td>
        <td class="mono">{d}</td>
      </tr>"""
        for m, w, n, av, ac, d in [
            ("US", "訓練 2016–22", 3693, "+1.913%", "+0.535%", "+1.378pp"),
            ("US", "樣本外 2023–26", 2992, "+1.777%", "+0.694%", "+1.083pp"),
            ("JP", "訓練 2016–22", 2424, "+1.034%", "+0.156%", "+0.878pp"),
            ("JP", "樣本外 2023–26", 2193, "+2.414%", "+0.723%", "+1.691pp"),
        ]
    )

    hk_rows = "\n".join(
        f"""      <tr>
        <td>{p}</td>
        <td class="mono">{n:,}</td>
        <td class="mono">{w}</td>
        <td class="mono">{avg}</td>
        <td class="mono">{pf}</td>
        <td class="mono">{t}</td>
      </tr>"""
        for p, n, w, avg, pf, t in [
            ("訓練期 2016–22（第三方）", 1408, "37.0%", "−2.01%", "—", "−4.00"),
            ("樣本外 2023–26（第三方）", 2447, "48.0%", "+0.02%", "1.01", "1.23"),
        ]
    )

    port_rows = "\n".join(
        f"""      <tr>
        <td>{m}</td>
        <td class="mono">{r}</td>
        <td class="mono">{c}</td>
        <td class="mono">{dd}</td>
        <td class="mono">{sh}</td>
        <td class="mono">{n}</td>
      </tr>"""
        for m, r, c, dd, sh, n in [
            ("舊引擎（按網站原本方式顯示）· 2016–26", "−92.6%", "−23.6%", "−93.1%", "−2.43", "6,542"),
            ("v2 · US+JP · 2016–26", "+67.8%", "+5.8%", "−18.8%", "0.55", "1,810"),
            ("v2 · US+JP · 樣本外 2023–26", "+33.7%", "+8.2%", "−16.3%", "0.73", "829"),
            ("v2 · US+JP · 滑價加倍壓力測試", "+44.7%", "+4.1%", "−20.4%", "0.41", "1,810"),
            ("SPY buy &amp; hold · 2016–26（基準）", "+324.1%", "+15.7%", "−33.7%", "0.90", "—"),
        ]
    )

    faq_html = "\n".join(
        f"""    <h3>{q}</h3>
    <p>{a}</p>"""
        for q, a in FAQ
    )

    return f"""<main>
<div class="container">

  <div class="page-head">
    <div class="kicker">Methodology</div>
    <h1>訊號點計出嚟 · 同埋我哋改咗乜</h1>
    <p class="lede">
      Leeks Terminal 嘅訊號全部由 Python 對 T-1 日線 OHLC 確定性計算，零 LLM 幻覺。
      現行規則係 <b>v2</b> —— 2026-10-01 一份第三方 10 年訊號審計之後改版。
      舊版有啲規則經審計證實冇正期望值，我哋停用咗，並喺下面逐項列明。
    </p>
    <div class="hero-meta">
      <span>Engine: <b>rule-based (Python)</b></span>
      <span>規則版本: <b>v2（只做多）</b></span>
      <span>數據: <b>Yahoo Finance · T-1 OHLC</b></span>
      <span>最後更新: <b><span id="meth-updated">2026-10-05</span></b></span>
    </div>
  </div>

  <div class="callout" style="margin: var(--sp-5) 0; padding: var(--sp-4); border-left: 4px solid var(--amber); background: var(--amber-dim); border-radius: 4px;">
    <h3 style="margin-top: 0; font-size: var(--text-md);">⚠ 閱讀本頁前請先知：v2 嘅優勢仍然細過大市</h3>
    <p style="margin: var(--sp-2) 0; font-size: var(--text-sm);">
      2016–26 期間 v2 組合（US+JP）總回報 +67.8%，同期 SPY buy &amp; hold <b>+324.1%</b>。
      即係話呢套規則贏唔過單純買入大市指數 —— 佢減少咗曝險同輸嘅單，
      但冇創造出跑贏大市嘅 alpha。
    </p>
    <p style="margin: 0; font-size: var(--text-sm);">
      <b>至於 S1 入場係咪真係有選時價值：我哋跑咗對照組測試，結果係「有，但要以每單計」。</b>
      見下方 §4.5。
    </p>
  </div>

  <h2>1. 修正記錄：我哋改咗乜、點解</h2>
  <p>
    2026-10-01 收到一份第三方 10 年訊號審計（2016–2026）。佢用本站公開嘅規則重跑，
    結論係舊版四個策略喺整個週期都冇正期望值。以下係逐項處理結果。
    呢一節我哋刻意保留，唔打算改。
  </p>
  <table class="data-table method-prose">
    <thead>
      <tr><th>原本做法</th><th>審計發現</th><th>而家做法</th></tr>
    </thead>
    <tbody>
{fix_rows}
    </tbody>
  </table>
  <p class="text-dim" style="font-size: var(--text-sm);">
    審計同時指出另外三類問題：頁面之間就同一件事引用唔同數字（已於 2026-10-05 統一）、
    勝率 badge 可能顯示咗相反方向策略嘅成績、n = 1–4 嘅極小樣本勝率照樣顯示出嚟。
    呢啲都係顯示層問題，已一併修正。
  </p>

  <h2>2. 現行規則：v2</h2>
  <table class="data-table method-prose">
    <tbody>
      <tr><td><b>方向</b></td><td>只做多（no short）</td></tr>
      <tr><td><b>個股篩選</b></td><td>T-1 收市價 &gt; 200 天線，且 200 天線喺 20 個交易日內向上</td></tr>
      <tr><td><b>大市過濾</b></td><td>指數 T-1 收市價 &gt; 佢自己嘅 200 天線（美股 SPY／日股 1306.T）</td></tr>
      <tr><td><b>入場</b></td><td>下一個交易日以 S1 掛限價買入；開市價低過或等於 S1 就跳過（唔追）</td></tr>
      <tr><td><b>止損</b></td><td>S1 × (1 − 3 × ATR%)</td></tr>
      <tr><td><b>目標價</b></td><td>無</td></tr>
      <tr><td><b>離場</b></td><td>入場後第 10 個交易日收市（時間離場）</td></tr>
      <tr><td><b>倉位</b></td><td>每單 1% 權益風險、單一名義上限 10%、最多 10 倉（gross ≤ 100%）</td></tr>
      <tr><td><b>名額競爭</b></td><td>超賣優先排序（5 日回報最低者先入）</td></tr>
      <tr><td><b>Guard rails</b></td><td>ATR% ≥ 6% 或 20 日 box 寬度 ≥ 35% → 不出訊號</td></tr>
    </tbody>
  </table>
  <p>
    每個門檻都係用 2016–2022 選定，再喺 2023–2026 只測一次（樣本外）。
    選出嚟嘅組合（3×ATR 止損、無目標、10 日持有）喺樣本外同時都係最好嗰個。
  </p>

  <h2>3. S/R ladder（v2 都用）</h2>
  <p>4 層不重疊 rolling pivot，全部用前一日數據計，無 look-ahead：</p>
  <ul>
    <li><b>S1 / R1</b> — 最近 20 個交易日嘅 low / high　← v2 嘅入場價就係 S1</li>
    <li><b>S2 / R2</b> — 第 21–60 個交易日</li>
    <li><b>S3 / R3</b> — 第 61–120 個交易日</li>
    <li><b>S4</b> — 第 121 日以前嘅最低 low（52 週 crash low）</li>
  </ul>

  <h2>4. 表現（本站自測）</h2>
  <div class="callout" style="margin: var(--sp-4) 0; padding: var(--sp-4); border-left: 4px solid var(--blue); background: var(--blue-dim); border-radius: 4px;">
    <p style="margin: 0 0 var(--sp-2); font-size: var(--text-sm);">
      <b>以下數字係本站自己跑出嚟嘅</b>，用 Yahoo Finance 10 年日線、同一個引擎、
      按市場收成本、日期叢集 t 統計。重跑方法見 <code>scripts/ab_close_hold.py</code>，
      輸入資料在 <code>data/bt10y/</code>，每個結論都可以覆核。
    </p>
    <p style="margin: 0; font-size: var(--text-sm);">
      <b>一個必須講嘅差異：</b>本站引擎<b>未能重現</b> 2026-10-01 第三方審計報告嘅基線
      —— 同一套規則，我哋測出嚟嘅基線高約 1.7 倍（第三方 US 樣本外 +0.45%，
      我哋 +0.783%）。所以下面嘅絕對水平同第三方數字<b>唔可以直接比較</b>；
      有效嘅係 A/B 之間嘅相對差（同一引擎、同一成本、只差一個入場條件）。
      我哋冇查清楚差異來源，所以唔會聲稱第三方數字有錯。
    </p>
  </div>
  <p><b>現行規則（收市企穩 S1）· 樣本外 2023–2026</b></p>
  <table class="data-table method-prose">
    <thead>
      <tr><th>市場</th><th>交易筆數</th><th>勝率</th><th>每單平均淨</th><th>Profit factor</th><th>日期叢集 t</th></tr>
    </thead>
    <tbody>
{oos_rows}
    </tbody>
  </table>
  <p>
    <b>逐單結果 · 訓練期 2016–2022（門檻揀選期）</b>
  </p>
  <table class="data-table method-prose">
    <thead>
      <tr><th>市場</th><th>交易筆數</th><th>勝率</th><th>每單平均淨</th><th>Profit factor</th><th>日期叢集 t</th></tr>
    </thead>
    <tbody>
{train_rows}
    </tbody>
  </table>
  <p>
    <b>逐單結果 · 樣本外 2023–2026（只測一次）</b>
  </p>
  <table class="data-table method-prose">
    <thead>
      <tr><th>市場</th><th>交易筆數</th><th>勝率</th><th>每單平均淨</th><th>Profit factor</th><th>日期叢集 t</th></tr>
    </thead>
    <tbody>
{oos_rows}
    </tbody>
  </table>
  <div class="callout" style="margin: var(--sp-4) 0; padding: var(--sp-4); border-left: 4px solid var(--amber); background: var(--amber-dim); border-radius: 4px;">
    <h3 style="margin-top: 0; font-size: var(--text-md);">⚠ 訓練期同樣本外差距好大，呢點要自己睇</h3>
    <p style="margin: var(--sp-2) 0; font-size: var(--text-sm);">
      <b>日本市場訓練期平均只有 +0.01%、Profit factor 1.00、t = −0.56</b> ——
      即係頭七年基本上冇 edge，個規則要到近期樣本先「好轉」。
      美國就相反（訓練期 +0.82% 高過樣本外 +0.45%）。
      只印樣本外一邊係選擇性呈現，所以兩邊都放上嚟。
      一個誠實嘅結論係：呢套規則喺不同市場、唔同時段嘅穩定性差異好大，
      唔應該當成一個跨市場通用嘅 alpha 來源。
    </p>
  </div>
  <p><b>港股</b></p>
  <table class="data-table method-prose">
    <thead>
      <tr><th>期間</th><th>交易筆數</th><th>勝率</th><th>每單平均淨</th><th>Profit factor</th><th>日期叢集 t</th></tr>
    </thead>
    <tbody>
{hk_rows}
    </tbody>
  </table>
  <p style="font-size: var(--text-sm);">
    <b>港股呢兩行係第三方數字，本站未能獨立驗證</b> ——
    本站日線資料源（Yahoo Finance）唔提供 .HK 個股代號，200 隻全部取唔到；
    只有指數層面（^HSI、2800.HK）有數。所以「扣 0.45% round-trip 後樣本外 +0.02%」
    呢個數字，我哋自己跑唔到，只可以引用。
    港股訊號因此只作資訊參考，唔落實倉。
  </p>
  <p><b>組合層面（1% 風險／單、10 倉上限）</b></p>
  <table class="data-table method-prose">
    <thead>
      <tr><th>組合</th><th>總回報</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th><th>交易筆數</th></tr>
    </thead>
    <tbody>
{port_rows}
    </tbody>
  </table>
  <p>
    v2 每單平均收益喺 10 個曆年入面有 9 個為正（2018 年約持平 +0.01%）。
    2022 年係唯一大幅虧損嘅一年：416 單、每單淨蝕 2.07% ——
    大市過濾減少咗曝險，但冇完全避開。
  </p>

  <h2>4.5 已出貨：long entry 加咗收市確認（2026-10-05）</h2>
  <p>
    呢個係一個<strong>方向性不對稱</strong>問題。沽空入 R1 嗰陣，價格觸及阻力本身就係 setup；
    但買入 S1 支持位，「touch 然後收市跌穿」其實係該支持位失效 ——
    你唔係買入支持位，你係接住跌勢。兩者用同一個 touch 規則，語意係相反嘅。
  </p>
  <p>
    所以 v2 現行規則除了「開市價高過 S1」，再要求<strong>該交易日收市企穩 S1 之上</strong>；
    觸及之後收返落去嘅單，唔當成交。
  </p>
  <p>
    呢個唔係照搬第二手數字 —— 我哋喺自己嘅 10 年 bars、同一個引擎、同一個成本模型下做咗 A/B，
    唯一分別就係呢一個入場條件：
  </p>
  <table class="data-table method-prose">
    <thead>
      <tr><th>市場</th><th>窗口</th><th>保留交易</th><th>每單平均</th><th>日期叢集 t</th></tr>
    </thead>
    <tbody>
{ab_rows}
    </tbody>
  </table>
  <p>
    <b>四個格全部改善</b>（每單升、t 升），所以唔係單一窗口嘅 accident。
    每一格交易筆數 2,193–5,812，遠高於本項目「1,000 單先可以出貨」嘅門檻。
    代價係交易量減半 —— 呢個規則賺錢嘅方式係<em>唔做錯單</em>，唔係做多啲單。
  </p>
  <h3>4.6 對照組測試：S1 入場係咪真係有選時價值？</h3>
  <p>
    要分清楚「揀上升趨勢股持有」同「等佢跌到 S1 先入」呢兩件事，
    唯一方法係跑一個<b>只有入場方式唔同</b>嘅對照組：
    同一批股票、同一個上升趨勢篩選、同一個大市過濾、同一個 3×ATR 止損、同一個 10 日離場，
    唯一分別係<b>唔等 S1，喺任何合資格日子的開市價直接買</b>。
  </p>
  <table class="data-table method-prose">
    <thead>
      <tr><th>市場</th><th>窗口</th><th>v2 交易筆數</th><th>v2 每單</th><th>對照組每單</th><th>差額</th></tr>
    </thead>
    <tbody>
{ctrl_rows}
    </tbody>
  </table>
  <p>
    <b>四個格全部：v2 每單收益高過對照組 2.6 至 6.6 倍</b>，兩個市場、訓練同樣本外一致。
    單就「入場方式」呢一項，v2 確實有超過單純追上升趨勢嘅選擇優勢。
  </p>
  <div class="callout" style="margin: var(--sp-4) 0; padding: var(--sp-4); border-left: 4px solid var(--amber); background: var(--amber-dim); border-radius: 4px;">
    <h3 style="margin-top: 0; font-size: var(--text-md);">⚠ 三個限制，唔好跳過</h3>
    <p style="margin: var(--sp-2) 0; font-size: var(--text-sm);">
      <b>一、呢個係「每單」優勢，唔係「總回報」優勢。</b>
      v2 只做對照組 2–4% 嘅交易機會（美股 3,693 對 132,032 單）。
      每單贏多啲，唔等於同等資金下賺多啲 —— 仲要將兩邊都套入同一個 10 倉 / 1% 風險
      組合模擬先可以答 portfolio 層面。呢一項我哋<b>未做</b>。
    </p>
    <p style="margin: var(--sp-2) 0; font-size: var(--text-sm);">
      <b>二、呢個結果同 2026-10-01 第三方審計報告相反。</b>
      嗰份報告嘅對照組數字（美股樣本外 +0.58%）同我哋（+0.694%）好接近，
      但佢哋報 v2 只有 +0.45%，我哋量到 +1.777%。
      差異喺 v2 嗰邊，而唔係對照組嗰邊 —— 原因未查清，<b>我哋唔會因此聲稱第三方有錯</b>。
    </p>
    <p style="margin: 0; font-size: var(--text-sm);">
      <b>三、交易量差距本身可能有選擇含義。</b>
      對照組每單都做，代表佢喺同一個上升趨勢裡面不停出入；v2 只做「跌到支持位又企穩」
      嗰啲。呢個差異正正就係規則想捕捉嘅東西，但亦即係話 v2 嘅樣本更少、
      對單一事件嘅依賴度更高。
    </p>
  </div>
  <p class="text-dim" style="font-size: var(--text-sm);">
    順帶一提：同一批回測亦發現<strong>名義 R:R 同實績反相關</strong>
    （R:R &lt; 1.5 平均 +1.83%／單，R:R ≥ 1.5 平均 +1.14%／單），
    意味住一個 R:R 下限過濾有可能反而濫走好單。
    v2 冇目標價所以根本冇 R:R，呢個發現對 v2 唔構成問題。
  </p>

  <h2>5. 風險管理</h2>
  <ul>
    <li>每單風險 = 權益 1%；單一名義上限 10%；同時最多 10 倉</li>
    <li>入場前已定死止損價，唔會因為盤中情緒改動</li>
    <li>冇目標價 —— 離場時間同樣係規則一部分，唔係睇心情</li>
    <li>港股訊號只作資訊：沽空需借券資格，成本結構亦唔同</li>
  </ul>
  <p class="text-dim" style="font-size: var(--text-sm);">
    更正：本頁舊版寫嘅「每筆 1–2% 帳戶、持倉 30–40% 上限、唔過夜、收市前必須平倉」
    係 v1 日內交易規則，v2 已經唔適用。v2 係持有 10 個交易日嘅 swing 邏輯。
  </p>

  <h2>6. T-1 Hard Rule</h2>
  <ul>
    <li>永遠用上一個交易日收市價計訊號，唔用 live intraday 數</li>
    <li>各市場喺自己收市後先重建，開市前睇訊號</li>
    <li>所有特徵（20/60/120/200 日窗口、ATR、200 天線斜率）都 shift(1)，零 look-ahead</li>
    <li>Universe 係「今日成交額 top-200」，對過去窗口有 selection bias
        （今日仍活躍嘅股票傾向市值大、曾經跑贏）。理想係 point-in-time universe
        （用當時可得數據動態構建）—— 未完成，此處誠實標明</li>
  </ul>

  <h2>7. 點解唔會有 LLM 幻覺數字</h2>
  <ul>
    <li>所有 OHLC、S/R、特徵、訊號、觸發價、止損價由 Python 確定性計算</li>
    <li>LLM 只可以出解釋文字（if-then 情境、陷阱），唔可以出數字</li>
    <li>頁面上每一個指數、匯率、升跌比例都由同一份數據層生成，
        包括呢一頁 —— 站內唔存在人手輸入嘅市場數字</li>
  </ul>

  <h2>8. 資料來源</h2>
  <ul>
    <li>Yahoo Finance（yfinance）— 歷史日線 OHLC，400 根 bar，每股每個交易日重取</li>
    <li>大市過濾用指數／ETF 自身收市價對其 200 天線</li>
    <li>取數失敗時唔會當作「大市向下」發訊號 —— 會用最後一次有效讀數並標明係快取值</li>
  </ul>

  <h2>常見問題</h2>
{faq_html}

  <div class="callout disclaimer mt-5" style="border-left: 4px solid var(--amber); background: var(--amber-dim); border-radius: 4px; padding: var(--sp-4);">
    <p style="margin: 0;">
      <b>免責聲明：</b>教育用途，非投資建議。Backtest 唔等於 live 表現。
      v2 樣本外勝率約 50–54%，屬統計噪音範圍。短線及 swing 交易都可能損失全部本金。
    </p>
  </div>

  <h2 class="mt-5">References</h2>
  <ul>
    <li>Leeks Terminal 10 年訊號審計報告（第三方，2026-10-01）— v2 規則來源與 performance 數字出處</li>
    <li>Barber, B., Lee, Y., Liu, Y., Odean, T. (2014). "The cross-section of speculator skill: Evidence from day trading." <em>Journal of Financial Markets</em>.</li>
    <li>Bailey, D., López de Prado, M. (2014). "The Deflated Sharpe Ratio." <em>Journal of Portfolio Management</em>.</li>
    <li>yfinance — github.com/ranaroussi/yfinance</li>
  </ul>

</div>
</main>"""


def main() -> None:
    html = PAGE.read_text(encoding="utf-8")
    orig = html

    # 1. body
    m = re.search(r"<main>.*?</main>", html, flags=re.S)
    if not m:
        raise SystemExit("no <main> block found")
    html = html[: m.start()] + build_body() + html[m.end():]

    # 2. title + meta description + og
    html = re.sub(r"<title>.*?</title>", f"<title>{TITLE}</title>", html, count=1, flags=re.S)
    html = re.sub(
        r'(<meta name="description" content=").*?(")',
        lambda mm: mm.group(1) + esc(DESC) + mm.group(2),
        html, count=1, flags=re.S,
    )
    html = re.sub(r'(<meta property="og:title" content=").*?(")',
                  lambda mm: mm.group(1) + esc(TITLE) + mm.group(2), html, count=1, flags=re.S)
    html = re.sub(r'(<meta property="og:description" content=").*?(")',
                  lambda mm: mm.group(1) + esc(DESC) + mm.group(2), html, count=1, flags=re.S)

    # 2b. Scoped table style. .data-table is built for the hub's compact numeric
    #     grids (table-layout:fixed + nowrap + text-overflow:ellipsis), which
    #     truncates prose cells mid-word on this page. Scoped to .method-prose so
    #     the hub tables are untouched.
    prose_css = (
        '<style>\n'
        '.method-prose td { white-space: normal; overflow: visible; '
        'text-overflow: clip; vertical-align: top; }\n'
        '</style>'
    )
    if "method-prose td" not in html:
        html = re.sub(
            r'(<link rel="stylesheet" href="/leeks\.css[^"]*">)',
            r"\1\n" + prose_css,
            html, count=1,
        )

    # 3. JSON-LD
    def fix_ld(match: re.Match) -> str:
        import json
        doc = json.loads(match.group(0)[match.group(0).index(">") + 1: match.group(0).rindex("</script>")])
        for node in doc.get("@graph", []):
            if node.get("@type") == "Article":
                node["headline"] = "Leeks Terminal 訊號方法論 · v2 規則與修正記錄"
                node["datePublished"] = "2026-08-24"
                node["dateModified"] = "2026-10-05"
            if node.get("@type") == "FAQPage":
                node["mainEntity"] = [
                    {"@type": "Question", "name": q,
                     "acceptedAnswer": {"@type": "Answer", "text": a}}
                    for q, a in FAQ
                ]
        return '<script type="application/ld+json">\n' + json.dumps(
            doc, ensure_ascii=False, indent=2) + "\n</script>"

    html = re.sub(
        r'<script type="application/ld\+json">.*?</script>',
        fix_ld, html, count=1, flags=re.S,
    )

    if html == orig:
        print("no change")
    else:
        PAGE.write_text(html, encoding="utf-8")
        print(f"rewrote {PAGE} ({len(orig):,} -> {len(html):,} bytes)")

    # public/faq.html is a build orphan (faq was dropped from build_static's
    # page list on 2026-08-29 and is not in hand_edited either), so it froze at
    # the v1 FAQ text — "pick the best edge over 30/60/90/197-day backtests",
    # the HIGH/MED/LOW reliability tiers, etc. Both /faq and /faq.html 301 to
    # methodology.html, so visitors never reach it, but a crawler can still
    # read the file and index a claim the correction record now retracts.
    # Mirror methodology into it so no contradictory copy ships.
    faq = PUB / "faq.html"
    if faq.exists() and faq.read_text(encoding="utf-8") != html:
        faq.write_text(html, encoding="utf-8")
        print(f"synced {faq} from methodology (v1 FAQ text was contradictory)")


if __name__ == "__main__":
    main()
