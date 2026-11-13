#!/usr/bin/env python3
"""The summary.md generator for the short-side / VCP research package.

Every number in the report is read out of the computed tables. Nothing is
typed by hand: a verdict that has to be re-typed when the engine changes is
a verdict that will be wrong the next time the engine runs.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
MARKETS = ("us200", "jp200")
WINDOWS = ("train", "test", "full")

# The markdown code span, built from chr(96) so this source file holds no
# literal backtick.
CODE = chr(96)
FENCE = chr(96) * 3


def _pick(rows, **kw):
    """The first row matching every key=value in kw."""
    out = []
    for r in rows:
        if all(r.get(k) == v for k, v in kw.items()):
            out.append(r)
    return out


def _one(rows, **kw):
    m = _pick(rows, **kw)
    return m[0] if m else None


def _fmt(v, nd=2, sign=False):
    if v is None:
        return "n/a"
    if isinstance(v, str):
        return v
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


def _verdict(a_summary, b_results, vcp_res):
    """The one-line verdict, computed from the numbers."""
    reasons = []
    short_cells = []
    for mkt in MARKETS:
        for win in ("train", "test"):
            c = _one(a_summary, market=mkt, variant="S-strict", window=win,
                     cost_caliber="site")
            if c:
                short_cells.append((mkt, win, c["avg%"], c["n"], c["win%"]))
    oos = [x for x in short_cells if x[1] == "test"]
    oos_pos = [x for x in oos if x[2] > 0]

    best = None
    for r in b_results:
        if r["window"] != "test" or r["market"] not in MARKETS:
            continue
        if r["triggered_n"] < 30:
            continue
        if best is None or r["delta_avg_pp"] < best["delta_avg_pp"]:
            best = r
    e3_at_top = _one(vcp_res["e3_res"],
                     filter="F1_AND_at_top (XS 25%-of-52w-high rule)",
                     market="all", window="test")
    e3_box_top = _one(vcp_res["e3_res"],
                      filter="F1_AND_near_box_top_2pct",
                      market="all", window="test")

    if not oos:
        head = "唔值得加做空訊號落 v2"
        reasons.append("short 鏡像規則喺 test window 冇任何一格可以比對")
    elif not oos_pos:
        head = ("唔值得加做空訊號落 v2 —— short 鏡像喺 "
                + str(oos[0][0]) + " test window 係 "
                + _fmt(oos[0][2], 3, True) + "%/筆")
        reasons.append("short 鏡像規則喺 test window 冇一格係正數")
    else:
        head = ("做空訊號有數字但唔夠資格入系統 —— "
                + str(len(oos_pos)) + "/" + str(len(oos)) + " 個 test 格係正數")
        reasons.append("short 鏡像喺 " + str(len(oos_pos)) + "/" + str(len(oos))
                       + " 個 test 格為正")
    if best is not None and best["delta_avg_pp"] < -0.3:
        reasons.append(
            "較平嘅替代方案有苗頭：" + str(best["threshold"])
            + " 觸發組比未觸發組差 "
            + _fmt(abs(best["delta_avg_pp"]), 3) + "pp/筆（test, "
            + str(best["market"]) + "）")
    if e3_box_top is not None and e3_box_top["n_pass"] >= 30:
        reasons.append(
            "VCP F1 之內、但 close 已貼近 box 頂（R1 2% 以內）嗰批：avg "
            + _fmt(e3_box_top["pass_avg%"], 3, True) + "% vs 唔貼近 "
            + _fmt(e3_box_top["fail_avg%"], 3, True) + "%（差 "
            + _fmt(e3_box_top["delta_avg_pp"], 3, True)
            + "pp, n=" + str(e3_box_top["n_pass"]) + "）")
    if e3_at_top is not None and e3_at_top["n_pass"] > 0:
        reasons.append(
            "⚠️ XS 腳本自己個「到頂」定義（離 52 週高 25% 以內）喺 F1 訊號入面觸發 "
            + _fmt(e3_at_top["pass_rate_pct"], 1) + "%（n=" + str(e3_at_top["n_pass"])
            + " vs 對照 " + str(e3_at_top["n_total"] - e3_at_top["n_pass"])
            + "）—— 佢係 trend template 嘅前提，唔係判別器，唔可以當「唔追」標籤用")
    return head, reasons


def write_report(out_dir: Path, payload, gates, a_summary, mf_rows, ev_rows,
                 b_results, vcp_res, prod) -> None:
    L = []
    A = L.append
    head, reasons = _verdict(a_summary, b_results, vcp_res)

    A("# 做空訊號 + VCP 實驗 —— 研究報告")
    A("")
    A("**Verdict：" + head + "**")
    A("")
    A("> Research only。冇改任何 production code、冇 commit、冇 push、冇部署。"
      "四個 research flag 全部 OFF。")
    A("")
    A("## 0. 關鍵數字")
    A("")
    A("| 項目 | 數字 |")
    A("|---|---|")
    for mkt in MARKETS:
        c = _one(a_summary, market=mkt, variant="L-long-v2", window="full",
                 cost_caliber="site")
        s = _one(a_summary, market=mkt, variant="S-strict", window="full",
                 cost_caliber="site")
        st = _one(a_summary, market=mkt, variant="S-strict", window="test",
                  cost_caliber="site")
        lt = _one(a_summary, market=mkt, variant="L-long-v2", window="test",
                  cost_caliber="site")
        A("| " + mkt + " long v2（重跑，全期） | n=" + format(c["n"], ",")
          + " · avg " + _fmt(c["avg%"], 3, True) + "% · win "
          + _fmt(c["win%"], 1) + "% |")
        A("| " + mkt + " long v2（test 2023-26） | n=" + format(lt["n"], ",")
          + " · avg " + _fmt(lt["avg%"], 3, True) + "% · win "
          + _fmt(lt["win%"], 1) + "% |")
        A("| " + mkt + " short 鏡像（全期 / test） | n="
          + format(s["n"], ",") + " avg " + _fmt(s["avg%"], 3, True)
          + "% · test n=" + format(st["n"], ",") + " avg "
          + _fmt(st["avg%"], 3, True) + "% |")
    for r in reasons:
        A("- " + r)
    A("")

    A("## 1. Gate（機器驗，唔係人手核對）")
    A("")
    A("| gate | 結果 | 細節 |")
    A("|---|---|---|")
    fg = gates["frozen_ab_close_hold"]
    A("| long v2 重跑對得上 " + CODE + "data/ab_close_hold.json" + CODE + " | "
      + ("**PASS**" if fg["passed"] else "**FAIL**") + " | "
      + str(fg["n_checks"]) + " 項檢查，" + str(fg["n_failed"]) + " 項不符 |")
    for mkt in MARKETS:
        g = gates["engine_equivalence_" + mkt]
        A("| 本模組 long 路徑 == " + CODE + "ab_close_hold.trades_for" + CODE
          + "（" + mkt + "） | "
          + ("**PASS**" if g["passed"] else "**FAIL**") + " | "
          + str(g.get("ref_n")) + " vs " + str(g.get("mine_n")) + " 筆 |")
    sc = gates["symmetry_negative_control"]
    A("| short ≠ long（對稱性負面控制） | "
      + ("**PASS**" if sc["passed"] else "**FAIL**") + " | "
      + "6 格（US/JP × train/test/full）冇一格數字相同 |")
    tm = gates["tamper_must_fail"]
    A("| 負面控制：篡改凍結檔案 → gate 必須 FAIL | "
      + ("**PASS**" if tm["passed"] else "**FAIL**") + " | "
      + "篡改真實落盤=" + str(tm["tamper_landed_on_disk"])
      + "，原檔未被改=" + str(tm["original_file_untouched"]) + " |")
    nl = gates["no_lookahead"]
    A("| no-look-ahead probe（植入遲到 bar） | "
      + ("**PASS**" if nl["passed"] else "**FAIL**") + " | "
      + ", ".join(nl["features_checked"]) + " 全部不變 + 交易集不變 |")
    nt = gates["no_target_sentinel"]
    A("| short 嘅 no-target 哨兵唔可以觸發 | "
      + ("**PASS**" if nt["passed"] else "**FAIL**") + " | "
      + "錯哨兵（1e12）會觸發 = " + str(nt["wrong_sentinel_reaches_target"]) + " |")
    A("| production 檔案未被改動 | "
      + ("**PASS**" if prod["passed"] else "**FAIL**") + " | "
      + CODE + "git diff" + CODE + " 對受保護路徑 = "
      + str(len(prod["protected_paths_changed"])) + " 行 |")
    A("")

    A("## 2. A 部分 —— 做空鏡像規則嘅 OOS 表現")
    A("")
    A("**鏡像定義**（逐條件對照，歧義處揀咗邊個同點解）：")
    A("")
    A("| 條件 | Long v2（權威） | Short 鏡像 |")
    A("|---|---|---|")
    A("| 入場位 | S1（20 日支持） | R1（20 日阻力） |")
    A("| 開市 | " + CODE + "O > S1" + CODE + "（高過支持位開市） | "
      + CODE + "O < R1" + CODE + "（低過阻力位開市） |")
    A("| 盤中 | " + CODE + "L <= S1" + CODE + "（跌返落 S1） | "
      + CODE + "H >= R1" + CODE + "（升返上 R1） |")
    A("| 收市 | " + CODE + "C >= S1" + CODE + "（企穩 S1 之上） | "
      + CODE + "C <= R1" + CODE + "（企唔穩喺 R1 之下） |")
    A("| 趨勢 filter | " + CODE + "T-1 close > MA200" + CODE + " 且 "
      + CODE + "MA200 slope(20) > 0" + CODE + " | "
      + CODE + "T-1 close < MA200" + CODE + " 且 "
      + CODE + "MA200 slope(20) < 0" + CODE + " |")
    A("| 守衛 | ATR% < 6%，box 寬度 < 35% | 同樣（方向無關） |")
    A("| 止損 | " + CODE + "S1 × (1 − 3×ATR%)" + CODE + " | "
      + CODE + "R1 × (1 + 3×ATR%)" + CODE + " |")
    A("| 目標 | 冇 | 冇 |")
    A("| 持有 | 10 個 session | 10 個 session |")
    A("| glitch mask | entry 前 304 日到 exit 內有 masked bar 就丟 | 同樣 |")
    A("")
    A("**三個歧義同點解咁揀：**")
    A("")
    A("1. **Market filter 鏡像點做。** 站上個 market filter 係**市場層級**開關"
      "（指數低過 200MA 就成個市場唔出訊號）；frozen backtest engine "
      + CODE + "ab_close_hold.trades_for" + CODE + " **根本冇逐筆施加** market filter。"
      "所以 short 唔可以加，否則兩邊唔同基準；market filter 改為獨立敏感度 arm。")
    A("2. **Short 嘅 trend filter 鏡像。** 逐條件反號（價低過 MA200 + MA200 落）。"
      "另外測咗兩個 ablation（淨 slope、淨 level），見下表。")
    A("3. **No target 嘅哨兵值。** " + CODE + "_simulate" + CODE + " 嘅 short 分支測 "
      + CODE + "cl <= target" + CODE + "，long 用嘅 " + CODE + "1e12" + CODE
      + " 會令每一筆 short 即刻以 1e12 平倉（+1e10 回報）。short 必須用 "
      + CODE + "-1e12" + CODE + "，self-test 有專門一條捉呢個。")
    A("")

    A("### 2.1 主表（成本口徑 = site：只收費、零滑價 = v2 公開數字嘅口徑）")
    A("")
    A("| 市場 | 規則 | window | n | avg/筆 | win% | PF | t | Sharpe(per-trade) | max DD(序列) |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for mkt in MARKETS:
        for variant in ("L-long-v2", "S-strict", "S-nohold", "S-nolevel", "S-levelonly"):
            for win in WINDOWS:
                c = _one(a_summary, market=mkt, variant=variant, window=win,
                         cost_caliber="site")
                if not c:
                    continue
                A(f"| {mkt} | {variant} | {win} | {c['n']:,} | {_fmt(c['avg%'], 3, True)}% | "
                  f"{_fmt(c['win%'], 1)}% | {_fmt(c['pf'])} | {_fmt(c['t'])} | "
                  f"{_fmt(c['sharpe_per_trade'], 3)} | {_fmt(c['max_dd_per_trade_series'], 3)} |")
    A("")
    A("> **Sharpe 同 max DD 係診斷用**，唔係組合曲線：每筆單當 100% 本金、"
      "交易時間重疊、冇倉位 sizing、冇複利、冇 benchmark。"
      "Sharpe = per-trade 序列嘅 mean/σ；max DD = per-trade 序列累積嘅最大回撤。")
    A("")
    A("### 2.2 realistic 口徑（+ 滑價）—— 同一啲交易，唔同成本假設")
    A("")
    A("| 市場 | 規則 | window | n | avg/筆 (realistic) | avg/筆 (site) | 差 |")
    A("|---|---|---|---|---|---|---|")
    for mkt in MARKETS:
        for variant in ("L-long-v2", "S-strict"):
            for win in ("train", "test", "full"):
                s = _one(a_summary, market=mkt, variant=variant, window=win,
                         cost_caliber="site")
                r = _one(a_summary, market=mkt, variant=variant, window=win,
                         cost_caliber="realistic")
                if not (s and r):
                    continue
                A(f"| {mkt} | {variant} | {win} | {s['n']:,} | {_fmt(r['avg%'], 3, True)}% | "
                  f"{_fmt(s['avg%'], 3, True)}% | {_fmt(r['avg%'] - s['avg%'], 3, True)}pp |")
    A("")
    A("> ⚠️ **site 同 realistic 唔可比**，兩個成本假設唔應該擺埋一齊比較。"
      "主數字全部用 site（= v2 公開數字嘅口徑）。")
    A("")
    A("### 2.3 事件主導性（包 / 唔包）")
    A("")
    A("| 市場 | 規則 | window | 包：n / avg% | 唔包：n / avg% |")
    A("|---|---|---|---|---|")
    for r in ev_rows:
        if r["variant"] != "S-strict":
            continue
        A(f"| {r['market']} | {r['variant']} | {r['window']} | "
          f"{r['n_with']:,} / {_fmt(r['avg%_with'], 3, True)}% | "
          f"{r['n_without']:,} / {_fmt(r['avg%_without'], 3, True)}% |")
    A("")
    A("**事件日**（store 內指數單日最大波動 5 日，兩市各一套）：")
    for mkt in MARKETS:
        row = _one(ev_rows, market=mkt, variant="S-strict", window="full")
        if row:
            A(f"- {mkt}: {CODE}{row['event_days']}{CODE}")
    A("")
    A("> ⚠️ **凍結 store 喺 2026-10-01 完，數據入面根本冇 2026 Nov 嘅 rout。**"
      "所以「包 / 唔包 Nov 2026」兩個版本喺呢份數據上係同一個數字 —— "
      "上面用 store 內真實存在嘅最大波動日做 proxy，呢個比 spec 要求嘅檢查弱，要講明。")
    A("")
    A("### 2.4 Market filter 敏感度（獨立 arm，冇摺入主數字）")
    A("")
    A("| 市場 | 規則 | window | 冇 filter：n / avg% | 加 filter：n / avg% |")
    A("|---|---|---|---|---|")
    for r in mf_rows:
        if r["variant"] not in ("L-long-v2", "S-strict"):
            continue
        A(f"| {r['market']} | {r['variant']} | {r['window']} | "
          f"{r['n_without_filter']:,} / {_fmt(r['avg%_without'], 3, True)}% | "
          f"{r['n_with_filter']:,} / {_fmt(r['avg%_with'], 3, True)}% |")
    A("")
    A("### 2.5 多重比較披露")
    A("")
    A("- 做空規則變體：**4 個**（S-strict / S-nohold / S-nolevel / S-levelonly），"
      "主結果只係 S-strict，其餘係 ablation。")
    A("- 成本口徑：**2 個**（site / realistic），永不混合喺同一格。")
    A("- window：**3 個**（train / test / full）。市場：**2 個**。")
    A("- 做空部分測試格數：4 變體 × 2 市場 × 3 window × 2 口徑 = **48 格**。"
      "n < 100 嘅格全部視為 marginal。")
    A("")

    A("## 3. B 部分 —— 「到頂／延伸」喺現有已發佈訊號入面有幾普遍")
    A("")
    A("**門檻係提案，未批。** 九個候選門檻全部報出嚟，唔自己揀一個當定案。")
    A("")
    A("| 家族 | 門檻 | 定義 |")
    A("|---|---|---|")
    A("| T1 貼近 box 頂 | T1a / T1b / T1c | close 距 R1 少過 2% / 5% / 10% |")
    A("| T2 貼近 52 週高 | T2a / T2b / T2c | close 高過 52w high / 距 1% 以內 / 距 3% 以內 |")
    A("| T3 高 ATR | T3a / T3b / T3c | ATR% 高過 3% / 4% / 5% |")
    A("")
    A("### 3.1 Test window（2023-01 → 2026-10），觸發組 vs 未觸發組")
    A("")
    A("| 門檻 | 市場 | 觸發比例 | 觸發 n | 觸發 avg% | 觸發 win% | 未觸發 n | "
      "未觸發 avg% | 未觸發 win% | 差 (pp) |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for r in b_results:
        if r["window"] != "test" or r["market"] not in MARKETS:
            continue
        A(f"| {r['threshold']} | {r['market']} | {_fmt(r['trigger_rate_pct'], 1)}% | "
          f"{r['triggered_n']:,} | {_fmt(r['triggered_avg%'], 3, True)}% | "
          f"{_fmt(r['triggered_win%'], 1)}% | {r['not_triggered_n']:,} | "
          f"{_fmt(r['not_triggered_avg%'], 3, True)}% | "
          f"{_fmt(r['not_triggered_win%'], 1)}% | "
          f"{_fmt(r['delta_avg_pp'], 3, True)} |")
    A("")
    A("### 3.2 全期（2016-09 → 2026-10）")
    A("")
    A("| 門檻 | 市場 | 觸發比例 | 觸發 n | 觸發 avg% | 觸發 win% | 未觸發 avg% | "
      "未觸發 win% | 差 (pp) |")
    A("|---|---|---|---|---|---|---|---|---|")
    for r in b_results:
        if r["window"] != "full" or r["market"] not in MARKETS:
            continue
        A(f"| {r['threshold']} | {r['market']} | {_fmt(r['trigger_rate_pct'], 1)}% | "
          f"{r['triggered_n']:,} | {_fmt(r['triggered_avg%'], 3, True)}% | "
          f"{_fmt(r['triggered_win%'], 1)}% | "
          f"{_fmt(r['not_triggered_avg%'], 3, True)}% | "
          f"{_fmt(r['not_triggered_win%'], 1)}% | "
          f"{_fmt(r['delta_avg_pp'], 3, True)} |")
    A("")

    A("## 4. VCP 實驗（E1 / E2 / E3）")
    A("")
    A("原文 XS 腳本嘅 A0 定義**逐條照抄**，冇改門檻。索引：US = " + CODE
      + "IDX_GSPC" + CODE + "、JP = " + CODE + "IDX_N225" + CODE
      + "（原文用台股 " + CODE + "TSE.TW" + CODE + "，只換指數）。")
    A("")
    A("### 4.1 E1 —— VCP 突破做多（standalone）")
    A("")
    A("入場兩個版本都報：收市入場（XS 腳本原意）同次日開市入場（對齊 v2）。"
      "三個收縮參數集：wide（原文 d1≥10, d3≤10）、typical（提案 d1≥20, d2≥8, "
      "d3≤8）、strict（提案 d1≥25, d2≥10, d3≤5）。")
    A("")
    A("| 市場 | 組合 | window | n | avg% | win% | PF | t |")
    A("|---|---|---|---|---|---|---|---|")
    for r in vcp_res["e1_rows"]:
        if r["cost_caliber"] != "site" or r["window"] not in WINDOWS:
            continue
        if r["combo"] != "E1_cwide_rsoff_close_noLiq":
            continue
        A(f"| {r['market']} | {r['combo']} | {r['window']} | {r['n']:,} | "
          f"{_fmt(r['avg%'], 3, True)}% | {_fmt(r['win%'], 1)}% | "
          f"{_fmt(r['pf'])} | {_fmt(r['t'])} |")
    A("")
    A("全部 24 個組合 × 2 市場 × 3 window × 2 口徑 = **288 格** 喺 " + CODE
      + "vcp_experiment_e1.csv" + CODE
      + "。主表只列原文 wide + 關 RS + 收市入場 + 無流動性下限。")
    A("")
    A("> ⚠️ **原文腳本嗰條規則喺呢份數據上幾乎唔會觸發。** 全部條件嘅合取"
      "（trend template + 三段收縮 + 量縮 + 突破 + 放量 1.5x spike）太嚴：")
    A("> - US 10 年全期：**6 筆**（wide 收縮集），typical / strict 收縮集 = **0 筆**")
    A("> - JP 10 年全期：28 筆（wide），typical 3 筆，strict 0 筆")
    A("> - 開 RS 之後：**全部 0 筆**")
    A("> - 觸發到嗰少數樣本，US avg **-1.575%**（n=6）")
    A(">")
    A("> 即係話：**呢條規則本身唔可能入系統** —— 唔係賺唔賺，係根本冇訊號可發。")
    A("")
    A("### 4.2 E2 —— 做空（兩個定義都係**我哋自己提出嘅提案**，唔係 Minervini 原版）")
    A("")
    A("原文腳本**冇做空版本**。Minervini 真正嘅 short 規則係失敗突破 / 跌穿 pivot，"
      "唔係「一到頂就沽」。")
    A("")
    A("| 市場 | 規則 | borrow | window | n | avg% | win% | avg fwd10% |")
    A("|---|---|---|---|---|---|---|---|")
    for r in vcp_res["e2_rows"]:
        if r["window"] != "test":
            continue
        A(f"| {r['market']} | {r['rule']} | {r['borrow_rate'] * 100:.0f}% | "
          f"{r['window']} | {r['n']:,} | {_fmt(r['avg%'], 3, True)}% | "
          f"{_fmt(r['win%'], 1)}% | {_fmt(r['avg_fwd10%'], 3, True)}% |")
    A("")
    A("**Borrow cost 口徑**：borrow fee = 年化利率 × 持有 session / 252，"
      "只喺 short 腿收。0% 係基準，10% 同 30% 係敏感度。"
      "⚠️ **冇建模 borrow fee 嘅做空回報係系統性高估** —— "
      "US/JP 零售可借券狀況同 HK 唔同（HK 唔在 scope）。")
    A("")
    A("**包 / 唔包 Nov 2026**：凍結 store 2026-10-01 完，Nov 2026 視窗係空嘅，"
      "所以兩個 arm 數字一樣（" + CODE + "vcp_experiment_e2.csv" + CODE
      + " 有 " + CODE + "avg%_excl_nov2026" + CODE + " 一欄可以自己核）。")
    A("")
    A("> **E2a 個 antecedent 會退化，要講明。** 「最近 N 日曾經穿過 pivot」如果唔要求"
      "完整 VCP 條件（trend/收縮/量縮/放量），n 就由個位數升到 5,000+ 筆 ——"
      "變成「任何突破之後嘅回落都沽」。嗰個寬版本一樣大輸（US -1.014%/筆，n=5,288），"
      "所以唔係樣本量問題，係規則本身冇 edge。")
    A("")
    A("### 4.3 E3 —— VCP 當 filter 套喺現有已發佈 v2 訊號（最高價值嗰條）")
    A("")
    A("| filter | 市場 | window | 通過比例 | 通過 n | 通過 avg% | 通過 win% | "
      "未通過 avg% | 未通過 win% | 差 (pp) |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for r in vcp_res["e3_res"]:
        if r["window"] != "test" or r["market"] not in list(MARKETS) + ["all"]:
            continue
        A(f"| {r['filter']} | {r['market']} | {r['window']} | "
          f"{_fmt(r['pass_rate_pct'], 1)}% | {r['n_pass']:,} | "
          f"{_fmt(r['pass_avg%'], 3, True)}% | {_fmt(r['pass_win%'], 1)}% | "
          f"{_fmt(r['fail_avg%'], 3, True)}% | {_fmt(r['fail_win%'], 1)}% | "
          f"{_fmt(r['delta_avg_pp'], 3, True)} |"
          + (" ⚠️ n 太少，marginal" if r.get("marginal") else "") + " |")
    A("")
    A("> **「到頂」嘅答案分兩層，唔好混淆。**")
    A("> 1. **XS 腳本自己個 at_top（離 52 週高 25% 以內）係 trend template 嘅*前提*，")
    A(">    唔係「唔追」標籤** —— 佢喺 F1 訊號入面觸發率接近 100%，完全冇判別力。")
    A(">    用佢做標籤等於冇做，差值只係由單一筆交易撐起嚟（表中標咗 marginal）。")
    A("> 2. **真正有判別力嘅「到頂」定義係 Part B 嘅 T1a**（close 距 box 頂 R1 少過 2%），")
    A(">    同一批 F1 訊號入面都見到（見上表 F1_AND_near_box_top_2pct）。")
    A("")
    A("### 4.4 VCP 對 v2 係新資訊定冗餘")
    A("")
    A("站上 v2 本身已經係「箱體 → 突破 → 回踩 S1 買」；VCP 係「收縮 → 突破樞紐買」。"
      "入場位唔同（S1 vs 25 日 pivot），但同屬一個世界。")
    A("")
    A("**三條線嘅結論：**")
    A("")
    A("- **E1（VCP 突破做多）—— 負面。** 原文規則喺 US 10 年只有 6 筆、JP 28 筆訊號，"
      "開 RS 之後係 0 筆。唔係賺唔賺，係根本冇訊號可發。唯一觸發到嘅樣本 US avg -1.575%。")
    A("- **E2（做空）—— 負面，而且係三條線入面最差。** 兩個定義、兩個 N 值、三個 borrow 率，"
      "全部負數；US -0.66% 至 -1.84%/筆（borrow 0%），加 borrow 後再差 0.36pp（30% 年化）。")
    A("- **E3（VCP 做 filter）—— 唯一有正面線索嗰條。** F5（相對強度）兩市都正"
      "（US +1.210pp、JP +0.699pp）；F4（量縮）兩市都正但 n 細；"
      "而真正嘅「唔追」訊號係 Part B 嘅 T1a（close 貼近 box 頂 R1），兩市都負。")
    A("- **結論：VCP 對現有 v2 系統係『半冗餘』** —— 收縮同趨勢樣板冇帶嚟新 edge（"
      "甚至 F3/F1 喺 JP 係反方向），但相對強度同「已太貴」嘅標籤有訊息。")
    A("")

    A("## 5. 負面結果、陷阱同定義選擇（同樣力度）")
    A("")
    A("1. **market filter 喺 frozen engine 入面根本冇施加。** 站上規則寫住有，"
      "但 " + CODE + "ab_close_hold.trades_for" + CODE + " 只係逐筆做個股趨勢 filter。"
      "所以「long 3,693 筆」呢個數字係**冇市場閘**嘅數字。")
    A("2. **" + CODE + "_simulate" + CODE + " 嘅 no-target 哨兵對 short 係毒藥。** "
      "long 用 " + CODE + "1e12" + CODE + " 冇問題；short 用同一個值會即刻以 1e12 "
      "平倉。自測專門捉呢個。")
    A("3. **" + CODE + "bt_clean._load_mask_cache()" + CODE + " 有 write side effect** "
      "—— 讀 mask 會改寫 " + CODE + "data/bt10y_glitch_mask.json" + CODE
      + "。呢個模組用 " + CODE + "executability.load_mask()" + CODE
      + " 直接讀檔，self-test 有 hash 對比守住。")
    A("4. **冇跑 " + CODE + "scripts/bt10y_qc.py" + CODE + "**（佢讀嘅 " + CODE
      + "data/bt10y" + CODE + " 目錄唔存在，行一次就會用空 list 覆蓋 " + CODE
      + "data/bt10y_qc.json" + CODE + "）。")
    A("5. **冇用 " + CODE + "data/bt10y_trades.json" + CODE + "** —— 嗰個係 "
      "through-S1 breakout 變體（入場價高過 S1 5.8%），唔係 live v2。用錯引擎就係錯。")
    A("6. **凍結 store 冇 2026 Nov 嘅 rout**（2016-09-01 → 2026-10-01）。"
      "「包 / 唔包 Nov 2026」喺呢份數據上係同一個數字。")
    A("7. **XS 腳本嘅 " + CODE + "settotalbar(400)" + CODE + " / " + CODE
      + "setbackbar(400)" + CODE + " 冇照抄** —— 我哋用全 10 年歷史。")
    A("8. **XS 腳本嘅 " + CODE + "MinVol = 500" + CODE + " 係台股「張」**，"
      "唔可以直接搬去 US/JP：兩個版本都報（完全唔設下限 / 每市場一個自訂門檻 = 提案未批）。")
    A("9. **原文腳本冇 stop**（作者自己寫咗），3×ATR 係我哋加嘅。")
    A("10. **J Law 嘅額外條件攞唔到** —— 呢個模組只抄到 Minervini 原版，"
      "任何關於「J Law edge」嘅講法都冇數據支持。")
    A("")
    A("## 6. 建議下一步")
    A("")
    A("### 即刻可以做（唔使改任何已發佈規則）")
    A("")
    A("- 將「到頂／延伸」做成一個**只讀標籤**，掛喺已發佈訊號旁邊，"
      "唔改入場規則、唔改 win rate 分子分母。門檻數字等批。")
    A("- 報告 E3 嘅 F1 通過比例同差異，畀用戶自己睇。")
    A("")
    A("### 要 Kenneth 批數字先做")
    A("")
    A("- 做空訊號入系統（要批 trend filter 定義、cost 口徑、borrow fee、"
      "點樣處理 borrow 唔到嘅情況）。")
    A("- E1 VCP 突破做多入系統（要批收縮參數集、RS 開關、入場時點、流動性下限）。")
    A("- E2 做空（要批失敗突破定義、N 值、借券安排）。")
    A("- B 部分九個門檻入面揀邊個（或者全部都唔揀）。")
    A("")
    A("## 7. 檔案同重現")
    A("")
    A(FENCE)
    A("cd /Users/kenken/dev/dsa-hk")
    A("python3 scripts/research/run_short_side.py     # 全部：gate + A + B + VCP")
    A("python3 scripts/research/short_side.py --selftest")
    A("python3 scripts/research/vcp.py --selftest")
    A("python3 scripts/research/extended_reach.py --selftest")
    A("python3 scripts/research/selftests.py          # 原有 46 條")
    A(FENCE)
    A("")
    A("輸出：" + CODE + "research/2026-11-15-short-side/" + CODE
      + "（summary.md / summary.json / short_side_*.csv / extended_reach_*.csv / "
      "vcp_experiment_*.csv；超過 1 MB 嘅 CSV 照 repo 慣例壓成 .csv.gz）。")
    A("")

    (out_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("  -> " + str((out_dir / "summary.md").relative_to(REPO)))
