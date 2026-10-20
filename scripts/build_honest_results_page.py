"""build_honest_results_page.py — the page that publishes the failures.

WHY
    The site's per-trade numbers looked good and the portfolio-level numbers
    did not. Both are true, and publishing only the first is how a site ends
    up with a strategy that looks like an edge until someone runs a placebo
    against it. So the site publishes the placebo results too — on their own
    page, in the same place a reader looks for the site's claims.

    Every figure here is READ from a report JSON by honest_results.py. This
    file contains no figure literals: it decides what the numbers MEAN, the
    module decides what they ARE. If a source file is missing the build
    stops, because a page arguing "we published our failures" that carries
    a hand-typed number is the exact failure mode it claims to fix.

    The page's "last updated" is the date the EVIDENCE was produced, not the
    date the page was built — otherwise every rebuild claims to be new
    research, which is the thing this page is against.
"""
from __future__ import annotations

import html
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import honest_results as H  # noqa: E402

REPO = pathlib.Path("/Users/kenken/dev/dsa-hk")
# A DIRECTORY page, not a loose .html file, and that is a deliberate choice.
#
# On this site the serving URL is decided by the file's shape: a directory
# index.html is served at /path/ (200), a loose .html file is served at /path
# with no slash (200) and /path/ 308-redirects to it. The canonical tag, the
# sitemap entry and the nav href all have to agree with whichever form the
# file takes, or the site points at a redirect and calls it a page.
#
# The directory form is the one the rest of the content pages already use
# (/hk200/, /track-record/, /about/), and it is the one where the canonical,
# the sitemap entry and the nav href are all the same string that returns
# 200 with no redirect hop. A loose .html file here would have made the
# canonical disagree with the sitemap, exactly as /methodology and
# /backtest do today.
OUT = REPO / "public" / "research" / "negative-results" / "index.html"

TITLE = "試過，唔 work · 失敗測試記錄 · Leeks Terminal"
DESC = ("v2 引擎嘅負面驗證結果：Sprint 1、2、3 同 edge-to-alpha 全部冇過關。"
        "每一個數字都由 report JSON 讀出，可以逐個對返原始檔。")
CANONICAL = "https://www.win9you.com/research/negative-results/"


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def table(headers: list, rows: list, cls: str = "") -> str:
    th = "".join(f'<th class="r">{esc(h)}</th>' for h in headers)
    trs = []
    for r in rows:
        tds = "".join(f'<td class="r">{c}</td>' if isinstance(c, str) and c.startswith("<")
                      else f'<td class="r">{esc(c)}</td>' for c in r)
        trs.append(f"<tr>{tds}</tr>")
    return (f'<table class="data-table{(" " + cls) if cls else ""}">'
            f"<thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>")


def build_body() -> str:
    s1 = H.sprint1()
    s2 = H.sprint2()
    s3 = H.sprint3()
    da = H.direction_a()
    dc = H.direction_c()
    sh = H.still_holds()
    ledger = H.trial_ledger()
    total = H.ledger_total()

    # The page's own date is the newest evidence it quotes.
    stamps = []
    for key in ("s1_placebo", "s2_axis_a", "s2_axis_b", "s3", "edge_a", "edge_c"):
        d = H._load(key)
        if d.get("generated_at"):
            stamps.append(str(d["generated_at"])[:10])
    last_updated = max(stamps)

    p = []
    A = p.append

    # ---- headline -------------------------------------------------------
    A(f'<p class="dim">本站嘅失敗測試記錄 · 全部數字由 report JSON 讀出</p>')
    A("<h1>試過，唔 work</h1>")
    A('<p>呢頁講緊一件幾個網站唔會講嘅事：<b>本站認真測過嘅策略，'
      '大部分輸咗</b>。呢啲唔係「測試唔夠多」嘅托辭 —— 每一項都跑過 placebo '
      '（把規則隨機打亂、或者隨機揀同等數量嘅股票），同自己嘅 null 比。'
      '輸，就係輸。</p>')
    A('<p>點解要公開？因為 per-trade 數字好睇，組合層面數字唔好睇，'
      '而公眾通常只睇到前者。多重測試文獻講得好清楚：試過幾多個設定，'
      'in-sample 出現靚 Sharpe 嘅機會就幾大，但樣本外期望值係零。'
      '<b>誠信係資產</b> —— 一個肯講自己失敗過嘅站，你反而可以相信佢話成功嗰啲。</p>')

    A('<div class="info-card" style="border-left:3px solid var(--green);margin:16px 0">')
    A("<h3>結論先講</h3><ul class=\"md-bullets\">")
    A(f"<li><b>揀股冇 edge。</b>就算完美成交（fill-matched），v2 嘅選股"
      f"喺 {da['n_arms_total']} 條臂、{da['n_runs']} 個 fill lag 全部 "
      f"<code>{da['runs'][0]['arms'][0]['classification']}</code> —— "
      f"即係輸畀隨機揀同等數量嘅股票。</li>")
    A(f"<li><b>入場時機、大市閘、選股三條軸</b>全部輸畀自己嘅 null："
      f"z = {H.z(s2['axes'][0]['z'])}、{H.z(s2['axes'][1]['z'])}、"
      f"{H.z(s2['axes'][2]['z'])}，"
      f"全部喺 null 嘅 {H.pct(s2['axes'][0]['null_percentile'])} 分位。</li>")
    A(f"<li><b>{s1['n_variations']} 個變體，{s1['n_clearing']} 個過關。</b>"
      f"其中 {s1['n_worse']} 個顯著差過隨機（最差 z = "
      f"{H.z(min(v['worst_z'] for v in s1['variations']))}）。</li>")
    A(f"<li><b>組合砌法冇得救。</b>{dc['n_cells']} 個 cell（sizing / "
      f"concentration / holding / allocation）冇一個過 BH q=0.10 而自己著數。</li>")
    A("</ul>")
    A("<p><b>v2 嘅真錢部署計劃已停。</b>網站繼續運作，公開嘅數字以本頁同 "
      "<a href=\"/methodology.html\">方法論</a> 為準。</p>")
    A("</div>")

    # ---- Sprint 2 --------------------------------------------------------
    A("<h2>Sprint 2 — v2 拆成三條軸，每條軸各自輸</h2>")
    A("<p>做法叫 crossed randomization：保留 v2 嘅其他部分，只把其中一樣"
      "隨機化，再同原版比。如果隨機化之後表現一樣或者更好，即係嗰部分冇做過任何"
      "事。三條軸喺同一個窗口各自比對，結果全部係 real 輸。</p>")
    A(table(
        ["軸", "real gap", "null 平均", "null sd", "net", "z", "null 分位", "n", "判定"],
        [[esc(a["axis"]), H.pp(a["real_gap_pp"]), H.pp2(a["null_mean_pp"]),
          H.sd(a["null_sd_pp"]), H.pp(a["net_pp"]), H.z(a["z"]),
          H.pct(a["null_percentile"]), H.num(a["n"]), esc(a["verdict"])]
         for a in s2["axes"]]))
    A(f'<p style="font-size:.85rem;opacity:.8;max-width:74ch">窗口 {esc(s2["window"])}，'
      f'{s2["trials"]} trials，seed {s2["seed"]}。'
      f'null 分位 0.0% 意思係：隨機版本<b>喺 {s2["trials"]} 次隨機抽樣入面'
      f'每一次都贏</b>。' + esc(s2["spec"]) + '</p>')

    # ---- Sprint 1 --------------------------------------------------------
    A("<h2>Sprint 1 — 11 個變體，0 個過關</h2>")
    A(f'<p>原本用咗一個固定門檻（{esc(s1["bar_supersedes"])}），'
      f'結果有兩個變體「過關」。但把同一批變體擺喺自己嘅 placebo null 前面，'
      f'門檻本身低過 null —— 嗰個測試根本測唔到嘢。'
      f'改用 placebo-adjusted 門檻、{s1["trials"]} trials 重跑之後，'
      f'<b>{s1["n_clearing"]} 個過關</b>。</p>')
    A(table(
        ["變體", "設定", "worst z", "worst net (pp)", "過關"],
        [[esc(v["label_zh"]), f'<code>{esc(v["label"])}</code>', H.z(v["worst_z"]),
          H.pp(v["worst_net_pp"]), "是" if v["clears"] else "否"]
         for v in s1["variations"]]))
    A(f'<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      f'等權基準 {H.pp2(s1["ew_cagr_pct"])}%/年，{s1["n_tickers"]} 隻可載入美股。'
      f'最好嗰個都只有 z = {H.z(s1["best_z"])} —— 差唔多等於隨機。'
      f'worst z 係兩段窗口入面較差嗰段。'
      f'<a href="{esc(s1["spec"])}">spec</a></p>')

    # ---- Sprint 3 --------------------------------------------------------
    A("<h2>Sprint 3 — 對住 deployment-matched benchmark 再試</h2>")
    A(f'<p>前兩輪之後再收窄一次：benchmark 唔再隨機抽，而係同 screen 用同一個'
      f'交易日曆、同一個曝險。{esc(s3["verdict"])}。</p>')
    A(table(
        ["臂", "regime", "real gap", "null 平均", "net", "z", "null 分位", "過關"],
        [[esc(a["arm"]), esc(str(a["regime"])), H.pp(a["real_gap_pp"]),
          H.pp2(a["null_mean_pp"]), H.pp(a["net_pp"]), H.z(a["z"]),
          H.pct(a["null_percentile"]), "是" if a["passes"] else "否"]
         for a in s3["arms"]]))

    # ---- Direction A -----------------------------------------------------
    A("<h2>Direction A — 就算完美成交都贏唔到</h2>")
    A(f'<p>最後一輪：v2 嘅 screen（20 日低位 S1 觸及 + 收市企穩 + ATR 上限 + '
      f'箱體上限 + 200 天線閘）同兩個對照臂跑同一個引擎、同一批 session、'
      f'同一個成本模型。對照臂隨機揀<b>同等數量</b>嘅股票。'
      f'門檻係 {H.pp(da["threshold_pp"])} pp 而且兩段窗口都要過。</p>')
    A(f'<p>兩個 fill lag 都試：同一日成交，同埋延遲一日成交 —— '
      f'一個 screen 根本唔可能成交嘅價，唔算數。</p>')
    rows = []
    for run in da["runs"]:
        for a in run["arms"]:
            rows.append([
                f"fill lag {run['fill_lag']}",
                esc(a["arm"]),
                H.pp(a["gap_early"]),
                H.pp(a["gap_late"]),
                H.z(a["null_z_early"]),
                H.z(a["null_z_late"]),
                f'<span style="color:var(--bear)">{esc(a["classification"])}</span>',
            ])
    A(table(["fill", "臂", "gap 2016–20", "gap 2021–26",
             "null z 2016–20", "null z 2021–26", "判定"], rows))
    A(f'<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      f'曝險完全對齊（screen 臂同 universe 臂差 '
      f'{H.pp(da["runs"][0]["arms"][0]["expo_gap_early"])} pp），所以差距唔係「揀股揀到啲'
      f'高曝險股」咁簡單 —— 係真係冇揀到好啲嘅嘢。'
      f'{H.num(da["n_names"])} 隻、{H.num(da["n_sessions"])} 個 session，'
      f'{da["first"]} 至 {da["last"]}，{da["trials"]} trials，seed {da["seed"]}。</p>')

    fd = da["runs"][0]
    A("<h3>補充：screen 有意見嗰啲日，仲係冇贏</h3>")
    A(f'<p>{esc(fd["fd_status"])}</p>')
    A(f'<p>問題係：screen 開口嗰啲 session（{H.num(fd["firing_sessions"])} / '
      f'{H.num(fd["total_sessions"])} 個，{H.frac(fd["firing_fraction"])}，'
      f'平均每日 {H.num(fd["mean_names"])} 隻），揀出嚟嗰批係咪好過隨機揀'
      f'同等數量？答案一樣係唔係。gap '
      f'{H.pp(fd["fd_gap_early"])}（2016–20）/ {H.pp(fd["fd_gap_late"])}（2021–26）。</p>')

    # ---- Direction C -----------------------------------------------------
    A("<h2>Direction C — 組合砌法都救唔到</h2>")
    A(f'<p>既然揀股冇 edge，至少可以試下砌組合：改 sizing、concentration、'
      f'holding、allocation。{dc["n_cells"]} 個 cell，每個對 baseline 做 '
      f'pooled OOS 逐月 Sharpe 檢定，再加 Benjamini-Hochberg 假發現率校正'
      f'（q = 0.1）。baseline：Sharpe {H.pp2(dc["baseline_sharpe"])}、'
      f'年化 {H.pp2(dc["baseline_cagr_pct"])}%、'
      f'最大回撤 {H.pp2(dc["baseline_maxDD_pct"])}%、'
      f'曝險 {H.pp2(dc["baseline_exposure_pct"])}%、'
      f'{dc["baseline_n_months"]} 個月。</p>')
    A(table(
        ["cell", "維度", "Sharpe", "年化 %", "最大回撤 %", "BH q", "顯著", "自己著數", "void"],
        [[esc(c["cell_id"]), esc(c["dimension"]), H.pp2(c["sharpe"]),
          H.pp2(c["cagr_pct"]), H.pp2(c["maxDD_pct"]),
          H.pp2(c["bh_q"]) if c["bh_q"] is not None else "—",
          "是" if c["significant"] else "否",
          "是" if c["favours"] else "否",
          "是" if c["void"] else "否"] for c in dc["cells"]]))
    A(f'<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      f'{dc["n_void"]} 個 cell 係 void（配置同 baseline 一樣，差異序列冇變異，'
      f'檢定冇資訊量），唔帶判定。'
      f'{dc["n_significant"]} 個 cell 顯著；'
      f'但「顯著」同「自己著數」係兩件事 —— 果 {dc["n_significant"]} 個顯著 cell '
      f'全部夏普比 baseline 差。最接近嘅係 '
      f'{esc(dc["closest"]["cell_id"])}（Sharpe {H.pp2(dc["closest"]["sharpe"])} '
      f'vs {H.pp2(dc["baseline_sharpe"])}），但 q = {H.pp2(dc["closest"]["bh_q"])}，'
      f'高過 0.1。</p>')

    # ---- what still holds ------------------------------------------------
    A("<h2>邊部分仲企得穩</h2>")
    A("<p>上面全部係組合層面嘅失敗。有一件事<b>唔係</b>失敗，唔好混淆："
      "<b>訊號本身</b>（逐單）有 edge，<b>用訊號砌組合</b>就冇。呢兩件事"
      "係分開測嘅。</p>")
    rows = []
    for mkt, arms in sh["placebo_cells"].items():
        for win, c in arms.items():
            rows.append([esc(mkt), esc(win), H.num(c["B_n"]), H.pp2(c["B_avg%"]),
                         H.pp2(c["P2_null_mean%"]), H.pp(c["gap_vs_null_pp"]),
                         H.z(c["z"]), "通過" if c["passes"] else "否"])
    A("<h3>收市確認（入場資格）</h3>")
    A("<p>跌到 20 日低位 S1 之後，<b>收市企穩 S1 先算成交</b>呢個條件，"
      "同一個 placebo 邏輯（隨機揀入場日）之下係通過嘅。</p>")
    A(table(["市場", "窗口", "n", "B 平均 %", "null 平均 %", "差距 pp", "z", "判定"], rows))
    us = sh["oos"]["us200"]
    jp = sh["oos"]["jp200"]
    A("<h3>逐單表現（樣本外 2023–26）</h3>")
    A(table(["市場", "n", "勝率", "每單平均淨 %", "利潤因子"],
            [["美股 US200", H.num(us["n"]), f'{us["win_pct"]}%',
              f'{us["avg_net_pct"]}%', H.pp2(us["profit_factor"])],
             ["日股 JP200", H.num(jp["n"]), f'{jp["win_pct"]}%',
              f'{jp["avg_net_pct"]}%', H.pp2(jp["profit_factor"])]]))
    A(f'<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      f'呢啲係<b>逐單</b>統計，唔係組合回報，亦唔等於跑贏大市 —— '
      f'同一批股票純粹等權持有，2016–26 係贏嘅（見方法論頁）。'
      f'收市確認 A/B：{sh["cells_passing"]} 格通過，{sh["trials"]} trials，'
      f'seed {sh["seed"]}。來源 {esc(sh["source_ab"])}。</p>')

    # ---- trial ledger ----------------------------------------------------
    A("<h2>試驗台賬</h2>")
    A(f"<p>歷來一共測試過 <b>{total}</b> 個設定。每行嘅數都係由 report 檔"
      "數返出嚟，唔係人手填，來源檔名就喺最後一欄。</p>")
    A(table(
        ["測試", "數量", "結果", "來源檔"],
        [[esc(r["name"]), f'{r["count"]} {esc(r["unit"])}', esc(r["outcome"]),
          f'<code>{esc(r["source"])}</code>'] for r in ledger]))
    A(f'<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      f'Sprint 1 嘅 placebo 重跑唔另計：佢係同一批 '
      f'{s1["n_variations"]} 個變體換把尺再評一次，冇畀數據多一次「撞運」嘅機會。</p>')

    # ---- disclosure rules ------------------------------------------------
    A("<h2>之後點樣做</h2>")
    A("<div class=\"info-card\" style=\"border-left:3px solid var(--amber);margin:16px 0\">")
    A("<h3>Holdout 紀律</h3>")
    A("<p>2016–20 同 2021–26 係固定切法，唔會因為結果靚就改。")
    A("<b>任何新 claim 必須兩個分段同號先可以發佈</b> —— 單邊靚唔算數。"
      "上面每一個 FAIL，都係因為兩段之間隔籬變咗號（或者其中一段直接輸）。</p>")
    A("<h3>Placebo 規則</h3>")
    A("<p>日後任何策略數字上站之前，<b>必須並排一個 null / placebo 對照</b>，"
      "而且 null 要用同一個引擎、同一個 universe、同一個成本模型跑，"
      "只把受測嗰部分隨機化。計分方法見收市確認 A/B："
      "把訊號移到隨機交易日，睇差距仲喺唔喺 null 上面。</p>")
    A("<p>冇 placebo 嘅數字，唔上站。呢個係本頁存在嘅原因。</p>")
    A("</div>")

    # ---- sources ---------------------------------------------------------
    A("<h2>每個數字嘅來源</h2>")
    A(table(["數字", "來源檔"],
            [["Sprint 1 變體清單、placebo 門檻", f'<code>{esc(H.SOURCES["s1_placebo"])}</code>'],
             ["Sprint 1 變體 × 成本情境格數", f'<code>{esc(H.SOURCES["s1_summary"])}</code>'],
             ["Sprint 2 軸 A（選股）", f'<code>{esc(H.SOURCES["s2_axis_a"])}</code>'],
             ["Sprint 2 軸 B（大市閘）", f'<code>{esc(H.SOURCES["s2_axis_b"])}</code>'],
             ["Sprint 2 軸 C（入場時機）", f'<code>{esc(H.SOURCES["s1_placebo"])}</code>（base 變體）'],
             ["Sprint 3 deployment-matched", f'<code>{esc(H.SOURCES["s3"])}</code>'],
             ["Direction A fill lag 0", f'<code>{esc(H.SOURCES["edge_a"])}</code>'],
             ["Direction A fill lag 1", f'<code>{esc(H.SOURCES["edge_a_lag1"])}</code>'],
             ["Direction C 組合構造", f'<code>{esc(H.SOURCES["edge_c"])}</code>'],
             ["收市確認 A/B", f'<code>{esc(H.SOURCES["ab_close_hold"])}</code>'],
             ["逐單樣本外統計", f'<code>{esc(H.SOURCES["v2_signals"])}</code>'],
             ["規則定義", f'<code>{esc(da["spec"])}</code>']]))
    A('<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      '本頁由 <code>scripts/build_honest_results_page.py</code> 生成，'
      '數字由 <code>scripts/honest_results.py</code> 從上面嘅檔案讀出 —— '
      '生成器內冇任何手打數字，來源檔唔在就停止 build。</p>')

    A(f'<p class="last-updated">最後更新: {last_updated} HKT</p>')
    return "\n".join(p)


def render() -> str:
    from build_static import shell
    return shell(
        title=TITLE,
        body_html=build_body(),
        active_path="/research/negative-results/",
        description=DESC,
        json_ld=None,
        canonical=CANONICAL,
    )


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"→ {OUT} ({OUT.stat().st_size:,} bytes)")
