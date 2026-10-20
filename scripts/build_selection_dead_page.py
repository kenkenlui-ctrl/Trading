"""build_selection_dead_page.py — the page that publishes the Direction A test.

WHY
    The failure-log page (/research/negative-results/) tells the reader that
    v2's stock selection died. This page is the TEST itself: the question that
    was asked, the design that was fixed before the answer was known, all
    eight cells, the post-hoc diagnostic, and the decision that followed. A
    negative result published without its method is a claim, not evidence.

    Everything numeric here is READ from reports/edge_alpha_a_us.json and
    reports/edge_alpha_a_us_lag1.json by honest_results.direction_a_detail().
    This file contains no figure literals: it decides what the numbers MEAN,
    the loader decides what they ARE. If a source file is missing, or a key
    moves, the load raises and the page cannot be built with a plausible
    placeholder.

    The page's "last updated" is the date the EVIDENCE was produced, not the
    date the page was built — otherwise every rebuild claims to be new
    research, which is the thing this page is against.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import honest_results as H  # noqa: E402
from build_honest_results_page import esc, table  # noqa: E402

REPO = pathlib.Path("/Users/kenken/dev/dsa-hk")

# A DIRECTORY page, like /research/negative-results/ and the other content
# pages: on this site the serving URL follows the file's shape, and a
# directory index.html is served at /path/ with no redirect hop, so the
# canonical, the sitemap entry and the nav href can all be the same string.
OUT = REPO / "public" / "research" / "selection-dead" / "index.html"

TITLE = "Direction A · 揀股冇 edge · 測試記錄 · Leeks Terminal"
DESC = ("Direction A 測試記錄：v2 嘅選股就算完美成交都輸畀等權基準，"
        "4 條臂全部 SELECTION_DEAD。每一個數字都由 report JSON 讀出。")
CANONICAL = "https://www.win9you.com/research/selection-dead/"


def build_body() -> str:
    d = H.direction_a_detail()
    run0, run1 = d["runs"]

    # The page's own date is the newest evidence it quotes.
    last_updated = max(str(r["generated_at"])[:10] for r in d["runs"])

    p = []
    A = p.append

    # ---- headline -------------------------------------------------------
    A('<p class="dim">研究記錄 · Direction A · 全部數字由 report JSON 讀出</p>')
    A("<h1>揀股冇 edge —— 就算完美成交都贏唔到</h1>")
    A("<p>呢頁公開嘅係一個<b>完整失敗嘅測試</b>，唔係一個摘要。問題、"
      "設計、門檻、全部 cell、事後診斷、最後嘅決定，全部喺下面，"
      "而且每個數字都係由跑出結果嗰個 report 檔讀出嚟 —— "
      "原始檔名就列喺頁尾，你可以逐個對返。</p>")

    A('<div class="info-card" style="border-left:3px solid var(--green);margin:16px 0">')
    A("<h3>結論先講</h3><ul class=\"md-bullets\">")
    A(f"<li><b>判定：<code>{esc(d['verdict'])}</code>。</b>"
      f"v2 嘅選股喺 {d['n_arms_total']} 條臂、{d['n_runs']} 個 fill lag "
      f"全部一樣，冇一條贏過等權基準。</li>")
    A(f"<li><b>預先登記嘅門檻冇過。</b>要求兩個窗口都要贏 "
      f"{H.pp(d['threshold_pp'])} pp 以上；"
      f"{d['early_label']} 段 {d['n_early_clearing']} / {d['n_cells']} 個 cell 過，"
      f"{d['late_label']} 段 {d['n_late_clearing']} / {d['n_cells']} 個 cell 過。"
      f"一邊過一邊唔過，就算唔過。</li>")
    A(f"<li><b>最好嗰個 cell 都只有 z = {H.z(d['best_z'])}</b>"
      f"（{d['best_cell']['arm']}，fill lag {d['best_cell']['fill_lag']}，"
      f"{d['best_cell']['window_label']}）—— 呢個係<b>天花板</b>，唔係領先。"
      f"null 分位 {H.pct(d['best_cell']['null_percentile'])}，"
      f"遠未企到會被拒絕嗰一邊。</li>")
    A(f"<li><b>{d['late_label']} 係「死咗」，唔係「反住有效」。</b>"
      f"z 全部喺 {H.z(d['late_z_min'])} 至 {H.z(d['late_z_max'])} 之間，"
      f"由來未去到 ±{H.z(d['z_crit'])} —— 方向係差，但差得唔夠顯著到可以講"
      f"「反效果」。</li>")
    A("</ul>")
    A("<p><b>呢個 screen 嘅真錢部署計劃停。</b>港股維持排除（v2 喺港股嘅"
      "回測落後等權持有，見 <a href=\"/methodology.html\">方法論頁</a>）。"
      "win9you.com 繼續做研究發佈，包括呢個唔靚嘅結果。</p>")
    A("</div>")

    # ---- 1. the question ------------------------------------------------
    A("<h2>一、問乜嘢</h2>")
    A("<p>之前所有測試都卡喺同一個問題：v2 組合跑輸，係因為<b>揀錯股</b>，"
      "定係因為<b>揀得啱但砌得差</b>？如果係前者，只要令成交完美、"
      "再公平比較，應該見到選股本身嘅 edge。呢頁就係嗰個測試。</p>")
    A(f"<p>問法：<b>v2 嘅選股規則，本身贏唔贏得過等權持有？</b>"
      f"用最公平嘅設計 —— 兩條臂都接近 100% 投入，空選股嘅日子"
      f"照計（fallback 落全 universe），所以冇「佢冇揀股所以冇蝕」"
      f"呢類藉口。</p>")
    A(f"<p><b>預先登記門檻（跑之前定死）：</b>選股臂要喺 "
      f"<b>{d['early_label']}</b> 同 <b>{d['late_label']}</b> "
      f"<b>兩個窗口都</b>贏等權臂至少 {H.pp(d['threshold_pp'])} pp。"
      f"美股 universe，{d['first']} 至 {d['last']}。"
      f"單邊靚唔算數 —— 呢個係喺 2016–26 出現過嘅教訓。</p>")

    # ---- 2. the design --------------------------------------------------
    A("<h2>二、點試</h2>")
    A(table(
        ["項目", "設定"],
        [["市場 / universe", f"美股，{H.num(d['n_names'])} 隻"],
         ["session", f"{H.num(d['n_sessions'])} 個（{d['first']} 至 {d['last']}）"],
         ["臂 1（受測）", esc(d["selection"])],
         ["臂 2（基準）", "等權持有同一個 universe，收市成交"],
         ["投入", "兩臂都接近 100%；空選股日 fallback 落全 universe"],
         ["成交假設", "fill lag 0（同一日收市成交）同 fill lag 1（延遲一日）"],
         ["持有", f"{d['hold']} 個交易日"],
         ["成本", f"{H.pp2(d['cost_rt'] * 100)}% 來回"],
         ["null", f"隨機揀同等數量嘅股票，{d['trials']} trials，seed {d['seed']}"]]))
    A(f'<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      f'兩個 fill lag 都要試，因為一個 screen 根本唔可能成交嘅價唔算數 —— '
      f'延遲一日成交先係誠實嗰個。判讀門檻 z = {H.z(d["z_crit"])}。'
      f'規則定義見 <code>{esc(d["spec"])}</code>。</p>')

    # ---- 3. the results --------------------------------------------------
    A("<h2>三、結果：八個 cell，全部冇過門檻</h2>")
    rows = []
    for run in d["runs"]:
        for a in run["arms"]:
            for win in d["windows"]:
                w = a["wins"][win]
                mark = "過" if w["clears"] else "唔過"
                rows.append([
                    f"lag {run['fill_lag']}",
                    esc(a["arm"]),
                    esc(H._win_label(win)),
                    H.pp(w["gap_pp"]),
                    H.pp(w["net_pp"]),
                    H.z(w["z"]),
                    H.pct(w["null_percentile"]),
                    mark,
                ])
    A(table(["fill lag", "臂", "窗口", "gap (pp)", "net (pp)", "z",
             "null 分位", f"≥{H.pp(d['threshold_pp'])} pp"], rows))
    A(f'<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      f'gap 係選股臂年化減等權臂年化（單位百分點）；net 係扣咗 null 平均之後嘅值；'
      f'null 分位係呢個 gap 喺 {d["trials"]} 次隨機抽樣入面排第幾。'
      f'{d["n_early_clearing"]} 個 cell 喺 {d["early_label"]} 段過門檻，'
      f'{d["n_late_clearing"]} 個喺 {d["late_label"]} 段過 —— '
      f'所以兩邊都要過嘅條件，冇達到。</p>')

    A("<h3>全樣本（2016–26 一次過計）</h3>")
    fs0 = run0["full_sample"]
    fs1 = run1["full_sample"]
    A(table(
        ["fill lag", "選股臂年化 %", "等權臂年化 %", "差距 (pp)",
         "選股臂 Sharpe", "等權臂 Sharpe"],
        [[f"lag {run0['fill_lag']}", H.pp2(fs0["arm1_cagr_pct"]),
          H.pp2(fs0["arm2_cagr_pct"]), H.pp(fs0["arm1_gap_vs_arm2_pp"]),
          H.pp2(fs0["arm1_sharpe"]), H.pp2(fs0["arm2_sharpe"])],
         [f"lag {run1['fill_lag']}", H.pp2(fs1["arm1_cagr_pct"]),
          H.pp2(fs1["arm2_cagr_pct"]), H.pp(fs1["arm1_gap_vs_arm2_pp"]),
          H.pp2(fs1["arm1_sharpe"]), H.pp2(fs1["arm2_sharpe"])]]))
    A(f'<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      f'全樣本唔係判讀規則嘅一部分（規則只睇兩個窗口），'
      f'只係擺出嚟方便同本站其他公開數字對照。'
      f'去咗大市閘嗰條臂一樣：lag {run0["fill_lag"]} '
      f'{H.pp(fs0["arm1_nogate_gap_vs_arm2_pp"])} pp、lag {run1["fill_lag"]} '
      f'{H.pp(fs1["arm1_nogate_gap_vs_arm2_pp"])} pp，'
      f'兩樣都係 <code>{esc(d["verdict_secondary"])}</code>。</p>')

    # ---- 4. dead, not anti-dead -----------------------------------------
    A("<h2>四、點解話「死咗」而唔係「反住有效」</h2>")
    A(f"<p>{d['late_label']} 段四個 cell 全部係負數，"
      f"z 由 {H.z(d['late_z_min'])} 到 {H.z(d['late_z_max'])}。"
      f"如果係真正嘅反效果（揀股令組合更差），"
      f"我哋期望見到 z 低過 −{H.z(d['z_crit'])}，"
      f"即係「差到可以否認個 null」。</p>")
    A(f"<p>實際上{'有' if d['late_reaches_z_crit'] else '冇'}"
      f"任何一個 cell 去到 ±{H.z(d['z_crit'])}。"
      f"正確嘅講法係：<b>呢段時間揀股冇帶嚟任何可辨認嘅好處</b>，"
      f"方向偏負但差得唔夠遠，唔足以講「揀股有害」。"
      f"兩個 fill lag 都一樣，所以呢個判定唔係成交假設做出嚟。</p>")
    A(f"<p>睇返最好嗰個 cell（{esc(d['best_cell']['arm'])}，lag "
      f"{d['best_cell']['fill_lag']}，{d['best_cell']['window_label']}，"
      f"gap {H.pp(d['best_cell']['gap_pp'])} pp，z {H.z(d['best_z'])}，"
      f"null 分位 {H.pct(d['best_cell']['null_percentile'])}）—— "
      f"連最好嗰格都企唔穩喺 null 上面。所以呢個唔係「差少少」嘅結論，"
      f"係「由頭到尾冇 edge」嘅結論。</p>")

    # ---- 5. the post-hoc diagnostic -------------------------------------
    A("<h2>五、事後診斷：screen 開口嗰啲日都冇贏</h2>")
    A("<p>呢個係<b>事後診斷</b> —— 唔係預先登記判斷規則嘅一部分，"
      "佢冇自己嘅門檻，亦都唔會改變上面嘅判定。"
      "目的只係答一個問題：screen 有開口嘅日子，佢揀出嚟嗰批"
      "係咪好過隨機揀同等數量嘅股票？</p>")
    A(f'<p style="font-size:.8rem;opacity:.7;max-width:74ch">'
      f'report 原文標示：<code>{esc(run0["fd"]["status"])}</code></p>')
    A("<p>做法：同一個引擎、同一批 session 行三條臂 —— "
      "screen 揀嘅股票、同一批日子嘅全 universe、"
      "同隨機揀同等數量嘅股票；screen 冇開口嘅日子三條都持有現金。"
      "如果 screen 有真係嘅辨識力，佢喺自己有觀點嘅日子"
      "應該企喺隨機嗰條之上。</p>")
    rows = []
    for run in d["runs"]:
        fd = run["fd"]
        for win in d["windows"]:
            n = fd["null"][win]
            rows.append([
                f"lag {run['fill_lag']}",
                esc(H._win_label(win)),
                H.pp(fd["gap_pp"][win]),
                H.pp(n["net_pp"]),
                H.z(n["z"]),
                H.pct(n["null_percentile"]),
            ])
    A(table(["fill lag", "窗口", "gap (pp)", "net (pp)", "z", "null 分位"], rows))
    A(f'<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      f'screen 開口嘅 session：{H.num(run0["fd"]["firing_sessions"])} / '
      f'{H.num(run0["fd"]["total_sessions"])} 個（'
      f'{H.frac(run0["fd"]["firing_fraction"])}），'
      f'平均每日 {H.num1(run0["fd"]["mean_names"])} 隻。'
      f'讀法：screen 揀出嚟嗰批，喺佢自己有觀點嘅日子，'
      f'<b>冇</b>贏過隨機揀同等數量嘅股票。'
      f'呢個診斷唔會改變判定 —— 佢只係解釋點解會死。</p>')

    # ---- 6. what still holds --------------------------------------------
    A("<h2>六、仲企得穩嘅嘢</h2>")
    A("<p>呢個測試<b>只</b>否定咗一件事：<b>揀股</b>。"
      "佢冇否定逐單訊號本身。收市確認（跌到 S1 觸及之後，"
      "收市企穩 S1 先算成交）呢個條件，逐單層面係有 edge 嘅 —— "
      "嗰部分有獨立測試、獨立 placebo、獨立數字，見 "
      "<a href=\"/research/negative-results/\">失敗測試記錄</a> 同 "
      "<a href=\"/methodology.html\">方法論頁</a>。</p>")
    A("<p>兩句說話都係真嘅，唔好混淆：<b>訊號本身有 edge</b>，"
      "<b>用訊號揀股砌組合冇 edge</b>。"
      "本站之前就因為淨係公佈第一句而招受過批評，所以兩句都寫喺度。</p>")

    # ---- 7. the decision -------------------------------------------------
    A("<h2>七、決定</h2>")
    A("<ul class=\"md-bullets\">")
    A(f"<li><b>呢個 screen 嘅真錢部署計劃停止。</b>"
      f"判定係 <code>{esc(d['verdict'])}</code>，冇任何窗口同時過門檻。</li>")
    A("<li><b>港股維持排除。</b>v2 喺港股嘅結果喺方法論頁有完整交代，"
      "同今次測試冇矛盾：兩個市場都係揀股砌組合冇跑贏等權。</li>")
    A("<li><b>win9you.com 繼續做研究發佈。</b>"
      "網站唔會因為呢個結果收埋，反而因為公開咗佢而更可信 —— "
      "一頁只講成功嘅站，你冇辦法判斷佢試過幾多樣嘢先揀中嗰個。</li>")
    A("</ul>")

    # ---- 8. method and discipline ---------------------------------------
    A("<h2>八、方法論同紀律</h2>")
    A("<p><b>點樣計：</b>選股臂同等權臂行同一個引擎、同一批 session、"
      "同一個成本模型；null 係用同一個引擎隨機揀同等數量嘅股票，"
      f"跑 {d['trials']} 次，seed {d['seed']}。"
      "每個數字都有 null 對照，冇單獨一個數字攞出嚟講。</p>")
    A("<p><b>紀律：</b>規則同門檻喺跑之前寫死（預先登記），"
      "唔會因為結果靚就改；所有特徵都係 T-1 收市之後先計得出，"
      "<b>冇前視</b>（no look-ahead）；兩個子窗口係固定切法，唔會因為"
      "其中一段好睇就重切。呢啲紀律嘅作用，就係令一個"
      "「輸咗」的結論同「贏咗」嘅結論一樣值得信。</p>")
    A(f'<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      f'規則同門檻嘅原文：<code>{esc(d["spec"])}</code>'
      f'（修訂 {esc(d["amendments"])}）。'
      f'本站嘅公開數字同披露規則見 '
      f'<a href="/methodology.html">方法論頁</a>，'
      f'其他失敗測試見 <a href="/research/negative-results/">失敗測試記錄</a>。</p>')

    # ---- sources ---------------------------------------------------------
    A("<h2>每個數字嘅來源</h2>")
    A(table(["數字", "來源檔"],
            [["Direction A 全部數字（fill lag 0）",
              f'<code>{esc(H.SOURCES["edge_a"])}</code>'],
             ["Direction A 全部數字（fill lag 1）",
              f'<code>{esc(H.SOURCES["edge_a_lag1"])}</code>'],
             ["規則、門檻、修訂", f'<code>{esc(d["spec"])}</code>']]))
    A('<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      '本頁由 <code>scripts/build_selection_dead_page.py</code> 生成，'
      '數字由 <code>scripts/honest_results.py</code> 從上面嘅檔案讀出 —— '
      '生成器內冇任何手打數字，來源檔唔在就停止 build。'
      '頁面入面用文字講嘅幾個數（最好嗰個 cell、'
      '後期窗口嘅 z 範圍）都係由同一批數值算出嚟，唔係另外填。'
      '</p>')
    A('<p style="font-size:.85rem;opacity:.8;max-width:74ch">'
      '<b>一啲刻意冇印喺呢頁嘅數字：</b>逐單勝率同每單平均回報唔喺上面'
      '兩個 report 入面 —— 嗰啲數有自己嘅來源檔同自己嘅測試，'
      '印喺 <a href="/research/negative-results/">失敗測試記錄</a>，'
      '唔喺呢頁重複一次。'
      '</p>')

    A(f'<p class="last-updated">最後更新: {last_updated} HKT</p>')
    return "\n".join(p)


def render() -> str:
    from build_static import shell
    return shell(
        title=TITLE,
        body_html=build_body(),
        active_path="/research/selection-dead/",
        description=DESC,
        json_ld=None,
        canonical=CANONICAL,
    )


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"→ {OUT} ({OUT.stat().st_size:,} bytes)")
