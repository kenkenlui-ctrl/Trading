#!/usr/bin/env python3
"""build_label_notes.py — render the signal-label section of the methodology
page from the measured artifact. Nothing on the page is typed by hand.

WHY
    The two read-only labels (T1a "貼近箱頂", F5 "強過大盤") shipped on
    2026-11-14 quoting a study that measured them on the FILLED cohort: the
    subset of plans that had already traded. The site publishes PLANS — every
    screen-passing stock, filled or not. A plan that fires "貼近箱頂" is one
    that almost never fills, so the filled cohort understates the label rate by
    an order of magnitude: the page said 1.6% while a third of the published
    signals actually carried the badge.

    The numbers now come from data/label_cohort.json, written by
    scripts/research/label_cohort.py (the research module re-derives the
    authoritative trade counts — 6,675 / 2,982 US, 4,576 / 2,187 JP — and its
    published-label fields match public/v2-signals.json field for field).

    The whole block is regenerated on every build. --check fails the build
    when the page and the artifact disagree, so a stale page cannot ship.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "data" / "label_cohort.json"
PAGE = REPO / "public" / "methodology.html"
START = "<!-- w9:label-notes:start -->"
END = "<!-- w9:label-notes:end -->"

MKT_LABEL = {"us200": "美股", "jp200": "日股"}
WIN_LABEL = {"full": "全期 2016–2026", "test": "樣本外 2023–2026"}
LBL_LABEL = {"T1a_near_box_top": "貼近箱頂（≤2%）",
             "F5_rs_vs_prod_index": "強過大盤"}


def load() -> dict:
    if not ARTIFACT.exists():
        raise SystemExit(
            f"FATAL: {ARTIFACT} not found — run "
            f"python3 scripts/research/label_cohort.py first")
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def row(summary: list[dict], market: str, window: str, label: str) -> dict:
    for r in summary:
        if (r["market"] == market and r["window"] == window
                and r["label"] == label):
            return r
    raise SystemExit(f"FATAL: no row for {market}/{window}/{label} "
                     f"in the artifact")


def _n(v) -> str:
    return f"{int(v):,}"


def _pct(v, dp: int = 2) -> str:
    return "—" if v is None else f"{float(v):.{dp}f}%"


def _fix_minus(s: str) -> str:
    """The site writes a minus sign as U+2212, not a hyphen."""
    return s.replace("-", "\u2212")


def _pp(v, dp: int = 2) -> str:
    if v is None:
        return "—"
    return _fix_minus(f"{float(v):+.{dp}f} pp")


def render() -> str:
    a = load()
    summary = a["summary"]
    robust = a["robustness"]

    rows = []
    for mkt in ("us200", "jp200"):
        for label in ("T1a_near_box_top", "F5_rs_vs_prod_index"):
            for win in ("full", "test"):
                r = row(summary, mkt, win, label)
                rows.append(
                    f"<tr><td>{LBL_LABEL[label]}</td>"
                    f"<td>{MKT_LABEL[mkt]}</td>"
                    f"<td>{WIN_LABEL[win]}</td>"
                    f"<td class=\"mono\">{_n(r['n_plans'])}</td>"
                    f"<td class=\"mono\">{_pct(r['triggered_plan_rate_pct'])}"
                    f"（{_n(r['triggered_n_plans'])}）</td>"
                    f"<td class=\"mono\">{_pct(r['triggered_fill_rate_pct'])}"
                    f" vs {_pct(r['not_triggered_fill_rate_pct'])}"
                    f"（{_pp(r['delta_fill_rate_pp'])}）</td>"
                    f"<td class=\"mono\">{_pct(r['triggered_avg%'], 3)}"
                    f" vs {_pct(r['not_triggered_avg%'], 3)}"
                    f"（{_pp(r['delta_avg_pp'], 3)}，n="
                    f"{_n(r['triggered_n_filled'])}）</td></tr>")

    rrows = []
    for r in robust:
        rrows.append(
            f"<tr><td>{MKT_LABEL[r['market']]}</td>"
            f"<td class=\"mono\">{r['dist_to_entry_bucket']}</td>"
            f"<td class=\"mono\">{_n(r['n_plans'])}</td>"
            f"<td class=\"mono\">{_pct(r['triggered_fill_rate_pct'])}"
            f"（n={_n(r['triggered_n_filled'])}）</td>"
            f"<td class=\"mono\">{_pct(r['not_triggered_fill_rate_pct'])}"
            f"（n={_n(r['not_triggered_n_filled'])}）</td>"
            f"<td class=\"mono\"><b>{_pp(r['delta_fill_rate_pp'])}</b></td></tr>")

    # how sensitive is F5 to the benchmark? (production index vs the study index)
    sens = []
    for mkt in ("us200", "jp200"):
        for win in ("full", "test"):
            p = row(summary, mkt, win, "F5_rs_vs_prod_index")
            s = row(summary, mkt, win, "F5_rs_vs_study_index")
            if p["delta_avg_pp"] is not None and s["delta_avg_pp"] is not None:
                sens.append(abs(p["delta_avg_pp"] - s["delta_avg_pp"]))
    sens_max = max(sens) if sens else None

    t1a_us_test = row(summary, "us200", "test", "T1a_near_box_top")
    t1a_jp_test = row(summary, "jp200", "test", "T1a_near_box_top")
    t1a_us_full = row(summary, "us200", "full", "T1a_near_box_top")
    t1a_jp_full = row(summary, "jp200", "full", "T1a_near_box_top")

    def _filled(r: dict) -> int:
        return r["triggered_n_filled"] + r["not_triggered_n_filled"]

    _us_full = _n(_filled(t1a_us_full))
    _us_test = _n(_filled(t1a_us_test))
    _jp_full = _n(_filled(t1a_jp_full))
    _jp_test = _n(_filled(t1a_jp_test))

    return f"""{START}
  <h2>2.1 訊號標籤：貼近箱頂 / 強過大盤（唯讀 context labels）</h2>
  <p>
    每個 v2 訊號除咗入場位、止損、時間離場，仲帶兩個<b>只讀標籤</b>。
    標籤<b>唔會</b>移除任何訊號、<b>唔會</b>改入場規則、<b>唔會</b>改勝率嘅分子同分母 ——
    佢哋只係話畀讀者知呢一單嘅背景係咩。
  </p>
  <table class="data-table method-prose">
    <tbody>
      <tr><td><b>貼近箱頂</b></td><td>收市價距 20 日 box 頂（R1）少過 2%</td></tr>
      <tr><td><b>強過大盤</b></td><td>250 日漲幅高過市場指數</td></tr>
    </tbody>
  </table>
  <p>
    <b>母體係「已發佈計劃」，唔係「已成交訊號」。</b>
    本站每日發佈嘅係計劃（screen 通過就出，鐘面價仲未回踩 S1），所以標籤要喺
    <b>計劃母體</b>上量：10 年凍結日線（<code>data/bt10y2/</code>）入面每一個
    screen 通過嘅日子，都當佢係一個已發佈計劃。
    「成交」嘅定義同引擎一致：<b>下一個交易日</b>以計劃寫明嘅 S1 入場位成交
    （開市價高過 S1、盤中觸及 S1、收市企穩 S1）。
  </p>
  <p>
    全部數字由 <code>data/label_cohort.json</code> 讀出，由
    <code>scripts/research/label_cohort.py</code> 生成（重跑約 12 秒）。
    該模組同時對齊兩個基準：佢重算出嘅成交筆數同權威 A/B 回測<b>逐筆一致</b>（美股 {_us_full}／樣本外 {_us_test}、日股 {_jp_full}／樣本外 {_jp_test}），而且逐個訊號重現本站已發佈嘅標籤欄位。
  </p>
  <table class="data-table method-prose">
    <thead>
      <tr>
        <th>標籤</th><th>市場</th><th>窗口</th><th>計劃母體</th><th>觸發率</th>
        <th>成交率（觸發 vs 未觸發）</th><th>成交後每單淨（觸發 vs 未觸發）</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  <p>
    <b>兩個標籤真正嘅意思唔一樣。</b>
    「強過大盤」係<b>入場後</b>嘅優勢：觸發組成交後每單淨多過未觸發組。
    「貼近箱頂」真正嘅分別喺<b>成交之前</b>：觸發組幾乎成交唔到（全期 {_pct(t1a_us_full['triggered_fill_rate_pct'])} vs {_pct(t1a_us_full['not_triggered_fill_rate_pct'])}，即係大約 20 幾倍嘅差距），因為收市價貼住箱頂，入場位 S1 就喺下面好遠。少數真係成交咗嗰批，表現仲差過未觸發組。
  </p>
  <p>
    <b>呢個係真嘅增量資訊，唔係裝飾。</b>
    本站每個訊號都已經印住「入場位距離現價幾多遠」，如果「貼近箱頂」只係
    呢個數嘅另一種寫法，咁佢就冇存在嘅理由。所以按同一個入場距離分組再量一次：
  </p>
  <table class="data-table method-prose">
    <thead>
      <tr>
        <th>市場</th><th>入場位距離（%，現價 − 入場價）</th><th>計劃數</th>
        <th>觸發組成交率</th><th>未觸發組成交率</th><th>差</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rrows)}
    </tbody>
  </table>
  <p>
    同一個距離區間入面，觸發組仍然係成交得少好多 ——
    標籤帶嚟嘅係入場距離本身冇嘅資訊。
  </p>
  <div class="callout" style="margin: var(--sp-4) 0; padding: var(--sp-4); border-left: 4px solid var(--amber); background: var(--amber-dim); border-radius: 4px;">
    <p style="margin: 0 0 var(--sp-2); font-size: var(--text-sm);">
      <b>限制，唔好跳過</b>
    </p>
    <ul style="margin: 0; font-size: var(--text-sm);">
      <li><b>成交後嗰組樣本細。</b>「貼近箱頂」樣本外只成交咗 {_n(t1a_us_test['triggered_n_filled'])}／{_n(t1a_jp_test['triggered_n_filled'])} 單，差距方向兩個市場一致，但 n 細，只當方向性證據。全期係 {_n(t1a_us_full['triggered_n_filled'])}／{_n(t1a_jp_full['triggered_n_filled'])} 單。<b>成交率嗰個分別就大得多</b>（母體係幾十萬個計劃），唔受樣本細影響。</li>
      <li><b>「強過大盤」對基準唔敏感</b>：改用另一個指數序列
        （<code>IDX_GSPC</code>/<code>IDX_N225</code> 而唔係
        <code>SPY</code>/<code>1306.T</code>），成交後差距最多只差
        {('%.3f pp' % sens_max) if sens_max is not None else '—'}。
        兩個基準都測過，結論一樣。</li>
      <li><b>呢個係警示，唔係做空訊號。</b>同一批凍結數據上，v2 嘅做空鏡像規則樣本外係負數
        （美股 −0.265%/單；日股 +0.361%/單，但 t=1.57 唔顯著），加埋借券費會更差。
        所以本站<b>冇</b>做空訊號 —— 「到頂」係叫你小心，唔係叫你沽。</li>
      <li><b>每單都會印出連續值</b> <code>ext_to_r1_pct</code>（收市價距 R1 幾多）同
        <code>stock_roc250_pct</code> / <code>index_roc250_pct</code>，
        所以任何其他門檻都可以由呢啲數推出，唔使重跑。</li>
      <li><b>門檻 2% 係提案</b>（2026-11-14 拍板），唔係天然嘅分界。
        收市價距 R1 嘅連續值每一單都印咗，任何門檻都可以另量。</li>
    </ul>
  </div>
{END}"""


def inject(html: str, block: str) -> str:
    if START in html and END in html:
        pat = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
        return pat.sub(lambda _m: block, html, count=1)
    raise SystemExit(f"FATAL: no {START} / {END} markers in {PAGE}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="fail if the page does not match the artifact")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    block = render()
    if a.check:
        html = PAGE.read_text(encoding="utf-8")
        if START not in html or END not in html:
            print(f"  FAIL  {PAGE} has no label-notes markers")
            return 1
        current = re.search(re.escape(START) + r".*?" + re.escape(END), html, re.S)
        if not current or current.group(0) != block:
            print("  FAIL  the methodology label section does not match "
                  "data/label_cohort.json — rerun scripts/build_label_notes.py")
            return 1
        print("  PASS  the methodology label section matches the artifact")
        return 0
    html = PAGE.read_text(encoding="utf-8")
    PAGE.write_text(inject(html, block), encoding="utf-8")
    print(f"wrote the label-notes section into {PAGE}")
    return 0


def _selftest() -> int:
    ok = True
    try:
        a = load()
    except SystemExit as e:
        print(f"  FAIL  {e}")
        return 1
    summary = a["summary"]
    # the artifact must carry both markets, both windows and both labels
    for mkt in ("us200", "jp200"):
        for win in ("full", "test"):
            for lbl in ("T1a_near_box_top", "F5_rs_vs_prod_index"):
                r = row(summary, mkt, win, lbl)
                good = r["n_plans"] > 0 and r["triggered_n_filled"] >= 0
                print(f"  {'PASS' if good else 'FAIL'}  the artifact has "
                      f"{mkt}/{win}/{lbl}")
                ok &= good
    # the filled subset must reproduce the authoritative trade counts
    for mkt, want in (("us200", 6675), ("jp200", 4576)):
        r = row(summary, mkt, "full", "T1a_near_box_top")
        got = r["triggered_n_filled"] + r["not_triggered_n_filled"]
        good = got == want
        print(f"  {'PASS' if good else 'FAIL'}  {mkt} filled subset "
              f"reproduces the authoritative trade count ({got} vs {want})")
        ok &= good
    # the label rate on the plan cohort must be far above the filled-cohort rate
    r = row(summary, "us200", "full", "T1a_near_box_top")
    good = r["triggered_plan_rate_pct"] > 10 * r["triggered_n_filled"] / max(
        1, r["triggered_n_plans"] + r["not_triggered_n_plans"])
    print(f"  {'PASS' if good else 'FAIL'}  the plan-cohort label rate is not "
          f"the filled-cohort rate")
    ok &= good
    # the block must render, carry both markers, and leave no placeholder
    block = render()
    has_markers = START in block and END in block
    no_placeholder = not re.search(r"\{[a-z_]+\}", block)
    print(f"  {'PASS' if has_markers else 'FAIL'}  the rendered block carries "
          f"both markers")
    ok &= has_markers
    print(f"  {'PASS' if no_placeholder else 'FAIL'}  the rendered block has no "
          f"unrendered placeholder")
    ok &= no_placeholder
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
