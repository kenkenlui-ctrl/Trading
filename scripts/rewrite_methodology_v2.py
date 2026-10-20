"""rewrite_methodology_v2.py — Rewrite public/methodology.html to describe v2.

WHY
    2026-10-05: the v2 signal engine (scripts/build_v2_signals.py, which cites
    the 2026-10-01 third-party 10-year signal audit) shipped on 2026-10-02, but
    methodology.html is a hand-edited page and was never updated. The site was
    therefore publishing v2 signals while its own methodology page still
    documented the four v1 strategies the audit had just retired.

    Worse, the same audit (Finding 3) had already flagged that the T+10 headline
    appeared as +3.02% on methodology, +1.63% on /backtest and a third figure on
    the T+10 equity curve. All three were live simultaneously.

    User decision (2026-10-05): v2 becomes the primary description, a public
    correction record is added, third-party figures carry explicit provenance,
    and T+10 stops being a headline.

2026-10-18: the T+10 figure this page promoted as "the honest fixed-capital
    number" is itself withdrawn. No artifact anywhere produced it: the run that
    could have, scripts/monthly_swing_backtest.py, reads data/dsa_hk.db, whose
    daily_report table has 0 rows, so it exits "No BUY signals in window" and
    cannot reproduce anything. A performance number with no run behind it is
    not evidence, so it is removed rather than replaced. Note the 10-year v2
    walk on /equity_curve_t* is a DIFFERENT experiment (US, 2017-26) and is not
    a substitute here — this page must not borrow it.

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

2026-10-15: the JP portfolio figures were still TYPED LITERALS here and had
    drifted. The 2026-10-12 retest (data/hk_retest_summary.json) withdrew the
    old +25.4%/yr vs +18.5%/yr = +6.9pp/yr headline; the home page, llms.txt
    and build_seo.py all moved to +18.03% vs +18.00% = +0.04pp/yr, and this
    page — which every "how were these numbers produced?" link points at —
    did not. A figure typed in two places cannot stay in sync with itself, so
    the JP cells are now READ from the retest summary. The old v2 figure was
    also internally inconsistent: its stated +702.0% cumulative does not
    follow from its own +25.4% CAGR over the 10.08-year bar window, while the
    equal-weight row's +453.5% does. Cumulative returns here are therefore
    computed from the verified CAGR over the window measured off the bar store
    rather than pasted from a run that no longer exists.
"""
from __future__ import annotations

import glob
import json
import re
from datetime import date
from pathlib import Path

# The home page's single source of truth for "v2 vs equal weight"
# (scripts/perf_figures.py -> data/hk_retest_summary.json). 2026-10-18: this
# page was missed in that migration, so it kept its own literals and started
# contradicting the home page. Imported so the two pages cannot be pointed at
# different cost legs again — see the JP_COST check below.
import perf_figures

REPO = Path("/Users/kenken/dev/dsa-hk")
PUB = REPO / "public"
PAGE = PUB / "methodology.html"

# Per-trade out-of-sample statistics, read from the run that produced them
# (scripts/ab_close_hold.py -> data/ab_close_hold.json). Raised, not degraded.
_AB_PATH = REPO / "data" / "ab_close_hold.json"
try:
    AB = json.loads(_AB_PATH.read_text())
    AB_RULE = AB["rule_set"]
except Exception as _e:  # noqa: BLE001
    raise SystemExit(f"FATAL: cannot read {_AB_PATH}: {_e} — run "
                     f"scripts/ab_close_hold.py first")

RETEST = REPO / "data" / "hk_retest_summary.json"
BARS = REPO / "data" / "bt10y2"
# The round-trip cost this page's US and JP numbers are quoted at. It must match
# the cost build_home.py / build_seo.py quote, or the two pages disagree again.
#
# 2026-10-18: this was "cost_0.10%". That is the cheaper-cost leg and it is the
# one that flatters v2, while the home page publishes every market at
# perf_figures.COST (0.25%) — the harshest leg that exists for all three
# markets. Quoting the cheap leg here and the harsh leg on the home page is the
# same cherry-pick this project already withdrew once, so both markets now
# quote the home page's leg and the check below fails the build if they ever
# drift apart again. A 0.10% figure may still appear, but only as an explicitly
# labelled cheaper-cost scenario sitting next to the 0.25% one.
JP_COST = "cost_0.25%"
if JP_COST != perf_figures.COST:
    raise SystemExit(
        f"FATAL: this page quotes {JP_COST} but the home page publishes "
        f"{perf_figures.COST}; two cost legs across two pages is exactly the "
        f"cherry-pick being removed."
    )


def _bar_window_years() -> float:
    """Length of the bar store the retest ran on, in years.

    The retest summary stores CAGR and maxDD but not the cumulative return, and
    a cumulative figure cannot be recovered from a CAGR without knowing the
    window. Deriving the window from the bars themselves keeps the arithmetic
    checkable instead of asserting a period someone once typed.
    """
    firsts, lasts = [], []
    for p in glob.glob(str(BARS / "*.json")):
        try:
            rows = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(rows, list) and rows:
            firsts.append(rows[0]["date"])
            lasts.append(rows[-1]["date"])
    if not lasts:
        raise SystemExit("cannot read the bar store; refusing to invent a window")
    a, b = date.fromisoformat(min(firsts)), date.fromisoformat(max(lasts))
    return (b - a).days / 365.25


def _num(x: float, dp: int = 2) -> str:
    """Signed number using the typographic minus the rest of the page uses.

    An ASCII "-" sitting in the same column as a hand-written U+2212 "−" renders
    as a visibly different dash, and on a page whose entire claim is numerical
    precision that is the wrong kind of detail to get wrong.
    """
    return f"{x:+.{dp}f}".replace("-", "\u2212")


def _pct(x: float, dp: int = 2) -> str:
    return f"{_num(x, dp)}%"


def _mkt_figs(mkt: str) -> dict:
    """Portfolio figures for ONE market, DERIVED from data/hk_retest_summary.json.

    2026-10-18: US was still a wall of literals here while JP had already been
    converted, so the two markets were being measured from the same file by two
    different standards — and the US literals had drifted far enough to say
    "the gap is -0.2pp, i.e. a draw" when the regenerated measurement puts it
    at -8.9pp/yr. One loader, one cost leg, every market.

    Raises on a missing key rather than falling back to a literal. A silent
    fallback would reproduce exactly the drift this replaces: the page would
    keep printing a withdrawn number whenever the source file moved or was
    renamed, and the only symptom would be a stale public claim.
    """
    d = json.loads(RETEST.read_text(encoding="utf-8"))
    p = d[mkt]["portfolio"]
    ew, v2 = p["ew_bh"], p[f"v2_{JP_COST}"]
    gap = p["v2_vs_ew_pp"][JP_COST]
    # 2026-10-18: the cheaper-cost leg, carried ONLY because the page publishes
    # it as an explicitly labelled scenario sitting next to the headline leg.
    # A cheaper number on its own is the cherry-pick this page is being fixed
    # for, so it is never allowed to appear without JP_COST's figures beside it.
    mid, gap_mid = p[f"v2_{perf_figures.MID_COST}"], p["v2_vs_ew_pp"][perf_figures.MID_COST]
    yrs = _bar_window_years()

    # Cumulative ("total") is not a column in the summary, so it is raised from
    # the verified CAGR over the same measured window the CAGR came from —
    # never pasted from a run that no longer exists.
    def cum(x: float) -> float:
        return ((1 + x / 100) ** yrs - 1) * 100

    return {
        "mkt": mkt,
        "cost_label": JP_COST.replace("cost_", ""),
        "mid_cost_label": perf_figures.MID_COST.replace("cost_", ""),
        "full": "2016-2026",
        # full-period cells
        "v2_cagr": v2["2016-2026"]["cagr%"],
        "ew_cagr": ew["2016-2026"]["cagr%"],
        "gap_pp": gap["2016-2026"]["v2_minus_ew_pp"],
        "v2_mid_cagr": mid["2016-2026"]["cagr%"],
        "gap_mid_pp": gap_mid["2016-2026"]["v2_minus_ew_pp"],
        "v2_dd": v2["2016-2026"]["maxDD%"],
        "ew_dd": ew["2016-2026"]["maxDD%"],
        "trades": v2["2016-2026"]["trades"],
        "v2_cum": cum(v2["2016-2026"]["cagr%"]),
        "ew_cum": cum(ew["2016-2026"]["cagr%"]),
        "window_years": yrs,
        # split-regime cells (§4.8)
        "early_v2": v2["2016-2020"]["cagr%"],
        "early_ew": ew["2016-2020"]["cagr%"],
        "early_gap": gap["2016-2020"]["v2_minus_ew_pp"],
        "late_v2": v2["2021-2026"]["cagr%"],
        "late_ew": ew["2021-2026"]["cagr%"],
        "late_gap": gap["2021-2026"]["v2_minus_ew_pp"],
    }


def jp_figs() -> dict:
    """JP portfolio figures, DERIVED. See _mkt_figs()."""
    return _mkt_figs("jp")


def us_figs() -> dict:
    """US portfolio figures, DERIVED. Same shape as jp_figs(), same source file."""
    return _mkt_figs("us")


def _pp(x: float, dp: int = 2) -> str:
    """Format a gap, which is measured in percentage POINTS per year."""
    return f"{_num(x, dp)} pp/年"


def _vs_hold(x: float, dp: int = 1) -> str:
    """A gap against equal-weight holding, with the direction computed from it.

    2026-10-18: the copy used to assert the direction in words ("只領先",
    "落後 0.2 個百分點", "基本上打和") while the number underneath came from a
    measurement. When the measurement moved and the words did not, the page
    said v2 "leads" while printing a negative gap. The verdict now follows the
    sign, so a reversal cannot survive in the prose.
    """
    return f"{'領先' if x > 0 else '落後'} {_num(abs(x), dp)} 點/年"


def _gap_color(x: float) -> str:
    """Row colour follows the sign of the gap, for the same reason as _vs_hold.

    2026-10-18: the JP 2021-26 row was painted green unconditionally. When the
    regenerated measurement made that gap negative, the page would have printed
    a loss in the colour of a gain. Colour is not decoration on a table whose
    entire claim is "did this beat holding still".
    """
    return "var(--red)" if x < 0 else "var(--green)"


# --- sign-robust verdict copy ------------------------------------------------
# 2026-10-18. data/hk_retest_summary.json was regenerated *while this edit was
# in progress* (generated_at 16:07 -> 16:12), and every hard-coded direction
# word on the page went stale in the space of five minutes: US flipped from
# -8.90pp to +0.96pp, JP from -8.40pp to -0.34pp. That is the whole argument for
# deriving the verdicts instead of writing them: a page that is rebuilt from a
# file which can change under it must compute what it says. Nothing below
# asserts a magnitude threshold (there is no stated one in the source data);
# every word is a function of the sign that was actually measured.


def _sub_line(f: dict) -> str:
    """The two sub-window gaps, direction computed."""
    return (f"2016–20 {_vs_hold(f['early_gap'])}、2021–26 {_vs_hold(f['late_gap'])}")


def _regime(f: dict) -> str:
    """One clause on whether the gap holds its sign across both halves."""
    if (f["early_gap"] > 0) != (f["late_gap"] > 0):
        return ("但要留意：差額嘅正負號喺 2016–20 同 2021–26 之間<b>會變</b>，"
                "即係全期嗰個數由單一 regime 決定，唔應該當成持久 alpha 去引用")
    return "而且兩個時段方向一致"


def _verdict(f: dict) -> str:
    """The closing conclusion for one market — always true of the measurement.

    Written so that no branch can claim an edge the split-regime data does not
    support, and no branch can claim a persistent loss that is actually a flip.
    """
    e, l, full = f["early_gap"], f["late_gap"], f["gap_pp"]
    if (e > 0) != (l > 0):
        return ("兩個時段嘅正負號相反，所以全期差距唔係一個跨市況都成立嘅優勢 —— "
                "我哋唔當佢係 alpha")
    if e > 0 and full > 0:
        return "兩個時段都係正數；但差額細，唔足以當成持久 alpha"
    return "兩個時段都係負數，換 regime 救唔到 —— 我哋唔當佢係 alpha"


def _table_note(f: dict) -> str:
    """Short annotation for the gap row of the §4.7 table."""
    if (f["early_gap"] > 0) != (f["late_gap"] > 0):
        return "全期差距細，拆分市況後正負號會變（§4.8）"
    return "拆分市況後方向唔變（§4.8）"


def _ab_n_range() -> str:
    """Trade-count range across the four A/B cells, read not typed.

    The page used to hardcode "2,193–5,812", which were the counts from the
    superseded data/bt10y run. The current data/bt10y2 run measures a different
    range entirely, so the sentence was describing a measurement that no longer
    exists."""
    ns = [AB["windows"][m][AB_RULE][w_]["n"]
          for m in ("us200", "jp200") for w_ in ("train", "test")]
    return f"{min(ns):,}–{max(ns):,}"


def _close_hold_control_note() -> str:
    """The placebo verdict, or an explicit statement that it was not run.

    "B beats A" on its own cannot separate two readings: the close carries
    information, or the rule simply trades less. B keeps roughly half of A's
    trades, so a reader who is not told about a control has no way to tell
    which one this evidence supports. The thresholds were fixed in advance in
    docs/close-confirmation-prereg-2026-10-18.md."""
    pl = AB.get("placebo")
    if not pl:
        return ('<p style="font-size: var(--text-sm); color: var(--amber);">'
                '⚠ <b>呢個 A/B 暫時冇對照組。</b>來源檔 <code>data/ab_close_hold.json</code> '
                '嘅 <code>placebo</code> 區係空嘅（嗰次重跑冇加 <code>--placebo</code>），'
                '所以上面只證明「B 嘅平均高過 A」，<b>未能排除「只係交易少咗所以平均高」</b>。'
                '重跑 <code>scripts/ab_close_hold.py --placebo 300</code> 就會出現。</p>')
    cells = pl.get("cells", {})
    zs = [c["z"] for m in cells.values() for c in m.values() if c.get("z") is not None]
    gaps = [c["gap_vs_null_pp"] for m in cells.values() for c in m.values()
            if c.get("gap_vs_null_pp") is not None]
    powered = all(c.get("has_power") for m in cells.values() for c in m.values())
    verdict = pl.get("verdict", "?")
    ok = verdict == "PASS" and powered
    return (
        f'<p style="font-size: var(--text-sm);"><b>但呢個比較有一個必要嘅對照組，'
        f'因為 B 臂只保留 A 臂一半交易</b> —— 「交易少咗所以平均高」同「揀得準」'
        f'睇落一模一樣。為此喺跑之前已經把門檻寫死落 '
        f'<code>docs/close-confirmation-prereg-2026-10-18.md</code>（z ≥ {pl["z_crit"]}，'
        f'四格全要，並且規定如果 B 落喺對照 null 之內就要公開為 negative result），'
        f'然後用一個<b>同 B 一樣大細、但係隨機抽嘅 A 臂子集</b>做安慰劑對照 '
        f'（{pl["trials"]} trials/cell）。</p>'
        f'<p style="font-size: var(--text-sm);">'
        f'{"四格全部企喺 null 之上" if ok else "預先登記結果：" + verdict}：'
        f'差距 {min(gaps):+.2f} 至 {max(gaps):+.2f} 個百分點，z 由 {min(zs):.2f} 到 '
        f'{max(zs):.2f}，門檻 {pl["z_crit"]}。'
        f'{"四個對照組嘅標準差都唔係 0，即係個對照組真係有檢定力（sd = 0 嘅安慰劑會喺冇測過任何嘢嘅情況下通過）。" if powered else "⚠ 有對照組 sd = 0，個測試無效。"}'
        f' 結論係<b>收市企穩呢個條件帶資訊</b>，唔係單純減少交易量。</p>'
        f'<p style="font-size: var(--text-sm); color: var(--amber);">'
        f'<b>兩點要講清楚：</b>（一）呢個測試<b>只有一個對照組</b>，唔係兩個 —— '
        f'主要對照組 P2（同 B 一樣大細、隨機抽嘅 A 臂子集）已跑並通過；次要對照組 P1'
        f'（隨機換交易日）<b>已經決定唔跑</b>（2026-10-19）。P2 已經直接測緊假設本身'
        f'嗰條軸（揀邊啲交易，唔係揀邊一日），再跑一個成本高好多、改變另一條軸嘅對照'
        f'唔會令 H1/H2 條問題更利落。（二）呢個測試<b>唔係</b>10 日持有嘅結論 —— '
        f'兩臂都固定 HOLD = 10，量度嘅係<b>入場資格</b>；10 日、5 日、15 日、20 日邊個好'
        f'仍然未答。</p>')


def _oos_win_sentence() -> str:
    """Real per-trade out-of-sample win rates, read from data/ab_close_hold.json.

    2026-10-18: replaced "v2 嘅回測樣本外勝率約 50–54%". That range contradicted
    this page's own table a screen above it (US 60.3%, JP 65.5% out-of-sample).
    Understating a measured win rate in a disclaimer is its own kind of
    dishonesty — it reads as comfort rather than risk. Replaced with the real
    figures, and the honest caveat is kept: a per-trade win rate is not
    portfolio excess return, and §4.7 measures the portfolio layer as negative
    for both markets.
    """
    us = AB["windows"]["us200"][AB_RULE]["test"]
    jp = AB["windows"]["jp200"][AB_RULE]["test"]
    return (f"美股 {us['win%']}%／{us['n']:,} 單、日股 {jp['win%']}%／{jp['n']:,} 單"
            f"（樣本外 2023–26，<code>data/ab_close_hold.json</code>）")


_JP: dict | None = None
_US: dict | None = None


def JP() -> dict:
    """Cached jp_figs(), so build_body's f-strings stay readable."""
    global _JP
    if _JP is None:
        _JP = jp_figs()
    return _JP


def US() -> dict:
    """Cached us_figs()."""
    global _US
    if _US is None:
        _US = us_figs()
    return _US


TITLE = "Methodology · v2 訊號規則 · Leeks Terminal"
DESC = (
    "現行 v2 訊號規則：只做多 · S1 限價入場 · 3×ATR 止損 · 10 個交易日時間離場 · "
    "200 天線 + 大市過濾。附 2026-10 第三方 10 年審計之修正記錄。OHLC-only，零 LLM 幻覺。"
)

# FAQ answers are serialised straight into the page's
# <script type="application/ld+json"> FAQPage block by build_faq_json()/main().
# Nothing inside an FAQ entry may be a Python comment that reaches the JSON —
# a "#" line inside that f-string shipped 674 unparseable ticker pages on
# 2026-10-18 (commit aea38b0c). Explanations live HERE, above the literal.
#
# 2026-10-18, two answers in this list were quoting withdrawn numbers:
#   - the timing question carried US literals (equal weight +27.4%, v2 +27.2%,
#     "差額 −0.2 pp/年，等於零") from a run that has since been regenerated. The
#     live figures come from US() now, and the verdict wording follows the sign
#     of the measured gap instead of asserting a direction in the copy.
#   - the disclaimer question claimed "樣本外勝率約 50–54%", which contradicts
#     this page's own table a screen above it (US 60.3%, JP 65.5%). Fixed to the
#     measured per-trade rates via _oos_win_sentence(), keeping the caveat that
#     per-trade win rate is not portfolio excess return.
#   - the T+10 answer promoted a fixed-capital T+10 total return as "the honest
#     number"; that figure is withdrawn too (no run produced it).
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
        # 2026-10-18: the second half of this answer promoted a fixed-capital
        # T+10 total return as "the honest number" in place of +3.02%. That
        # number has been withdrawn too — nothing anywhere produced it, and the
        # run that could have (scripts/monthly_swing_backtest.py) reads
        # data/dsa_hk.db, whose daily_report table has 0 rows, so it exits
        # "No BUY signals in window" and cannot reproduce it. The part of the
        # answer that is still true is kept (the +3.02% sum-of-per-trade-
        # percentages claim is retracted); the unsupported replacement is
        # dropped rather than swapped for another number. The 10-year v2 walk
        # on /equity_curve_t* is a different experiment and is NOT borrowed here.
        "以前網站多處引用 T+10 swing +3.02%，嗰個係逐筆百分比直接相加，"
        "等於假設每一單都用盡 100% 資金同時持有，唔係一個投資組合回報 —— "
        "<b>呢個數我哋已經撤回</b>。"
        "<br><br>2026-10-18：我哋曾經用一個「誠實定資模擬」嘅 T+10 總回報去取代佢，"
        "而家<b>連嗰個數都一齊撤回</b>，因為佢冇任何 run 產生過："
        "唯一可能產生佢嘅 <code>scripts/monthly_swing_backtest.py</code> 讀嘅 "
        "<code>data/dsa_hk.db</code> 入面，<code>daily_report</code> 表係 <b>0 行</b>，"
        "個 script 會直接以「No BUY signals in window」結束，根本跑唔到。"
        "一個背後冇 run 嘅回測數字唔係證據，所以我哋唔會用另一個數字填返個位。"
        "（<code>/equity_curve_t*</code> 嗰條係十年美股 v2 曲線，係另一個實驗，"
        "唔係同一個六星期港股 swing 窗口，唔可以攞嚟頂。）"
        "而家網站唔再將 T+10 當成 edge 宣傳。",
    ),
    (
        "頁面啲 performance 數字係你哋自己驗證㗎？",
        "係，2026-10-05 起用嘅係本站自測數字：Yahoo 10 年日線、同一個引擎、"
        "按市場計成本、日期叢集 t。重跑方法同輸入資料都公開：scripts/ab_close_hold.py（每單 A/B）、"
        "scripts/control_test.py（對照組）、scripts/portfolio_test.py（組合層面），"
        "輸入資料 data/bt10y2/。"
        "⚠ 舊嘅 data/bt10y/ 快取經逐隻重抓比對後確認有 8 個檔案損壞，唔應該再用。"
        "要留意一點：我哋引擎重現唔到 2026-10-01 第三方審計報告嘅基線，"
        "同一規則高約 1.7 倍，差異原因未查清 —— 所以兩邊數字唔可以直接比較，"
        "有效嘅係同一引擎內嘅 A/B 相對差。",
    ),
    (
        "v2 係咪真係有選時能力，定只係買上升趨勢股？",
        "要分兩層答。單筆層面：有優勢 —— 同一批股票、同一個篩選、同一個大市過濾，"
        "唯一分別係等 S1 定喺開市價直接買，v2 每單收益喺四個窗口全部高過對照組 2.6 至 6.6 倍。"
        f"但組合層面：美股冇穩定超額 —— 將同一批股票等權每月再平衡，2016–26 年化 "
        f"{_pct(US()['ew_cagr'])}，v2 {_pct(US()['v2_cagr'])}，"
        f"即係 v2 {_vs_hold(US()['gap_pp'])}；{_regime(US())}。"
        "即係話每單揀得准，但呢個優勢喺美股冇轉化成一個穩定、"
        "跑贏「唔做嘢」嘅回報。"
        f"日股喺同一個 {JP()['cost_label']} 成本口徑下，v2 {_vs_hold(JP()['gap_pp'])}"
        f"（v2 {_pct(JP()['v2_cagr'])} 對等權 {_pct(JP()['ew_cagr'])}）；"
        f"按市況拆開：{_sub_line(JP())} —— "
        f"{_verdict(JP())}。"
        "詳見方法論頁 §4.6 同 §4.7。",
    ),
    (
        "數據幾時更新？用即市價定收市價？",
        "永遠使用 T-1 收市價（hard rule）。各市場喺自己收市後重建全站，"
        "開市前查看訊號即可，不會使用 live intraday 數據計算。",
    ),
    (
        "呢個站係投資建議嗎？",
        "不是。全部內容屬教育用途並附免責聲明。短線交易風險極高，過往表現不代表將來回報；"
        f"v2 每單樣本外勝率係{_oos_win_sentence()}。"
        f"但要睇清楚：每單勝率唔等於組合超額回報 —— 組合層面 v2 喺美股"
        f"{_vs_hold(US()['gap_pp'])}、日股{_vs_hold(JP()['gap_pp'])}（對「乜都唔做、"
        f"淨係等權持有同一批股票」，{JP()['cost_label']} 成本口徑）。"
        "唔構成任何回報承諾。",
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


def _honest_results_disclosure() -> str:
    """Section 4.9 — the trial ledger and the two disclosure rules.

    2026-10-20: this page used to state what the site FOUND without saying
    how many things it had tried first. A reader has no way to weigh a claim
    that "v2 beats equal weight" without knowing that 58 settings were
    tested to get there, or that every one of them was scored against a
    placebo. The ledger and the two rules below are the disclosure that
    makes the other numbers on this page readable.

    Every count comes from honest_results.py, which reads the report files —
    the same module the /research/negative-results/ page reads, so the two
    pages cannot disagree about how much was tried.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import honest_results as H

    ledger = H.trial_ledger()
    total = H.ledger_total()
    s1 = H.sprint1()
    s2 = H.sprint2()

    rows = "".join(
        f'<tr><td>{esc(r["name"])}</td>'
        f'<td class="r">{r["count"]} {esc(r["unit"])}</td>'
        f'<td>{esc(r["outcome"])}</td>'
        f'<td><code>{esc(r["source"])}</code></td></tr>'
        for r in ledger
    )
    return f"""
  <h2>4.9 試驗台賬同披露規則</h2>
  <p>上面每一個數字，都要配上「試過幾多個先」同「點解唔係隨機」先讀得明。
  本站至今一共測過 <b>{total}</b> 個設定，每一項都跑過 placebo（將受測嗰部分
  隨機化，或者隨機揀同等數量嘅股票），同自己嘅 null 比。完整結果、逐個變體同
  原始檔案喺 <a href="/research/negative-results/"><b>失敗測試記錄</b></a>。</p>

  <table class="data-table">
    <thead><tr><th>測試</th><th class="r">數量</th><th>結果</th><th>來源檔</th></tr></thead>
    <tbody>
      {rows}
    </tbody>
  </table>
  <p class="text-dim" style="font-size: var(--text-sm)">
    Sprint 1 嘅 placebo 重跑唔另計：佢係同一批 {s1['n_variations']} 個變體換把尺
    再評一次（{s1['trials']} trials），冇畀數據多一次「撞運」嘅機會。
  </p>

  <div class="info-card" style="border-left:3px solid var(--amber);margin:16px 0">
    <h3>Holdout 紀律</h3>
    <p>2016–20 同 2021–26 係固定切法，唔會因為結果靚就改。
    <b>任何新 claim 必須兩個分段同號先可以發佈</b> —— 單邊靚唔算數。
    Sprint 2 三條軸（{s2['n_axes']} 條，全部 FAIL）就係因為兩段之間隔籬變咗號，
    或者其中一段直接輸。逐個數字同逐個檔案都喺
    <a href="/research/negative-results/">失敗測試記錄</a>。</p>

    <h3>Placebo 規則</h3>
    <p>日後任何策略數字上站之前，<b>必須並排一個 null / placebo 對照</b>，
    而且 null 要用同一個引擎、同一個 universe、同一個成本模型跑，只把受測嗰部分
    隨機化。做法見 4.6 嘅對照組測試：把訊號移到隨機交易日，睇差距仲喺唔喺 null
    上面。<b>冇 placebo 嘅數字，唔上站</b> —— 本頁 4.7 嘅等權對照、4.5 嘅收市確認
    A/B，全部都係咁做嘅。</p>
  </div>
"""


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
         # 2026-10-18: this cell used to carry a fixed-capital T+10 total return
         # as the "honest" replacement for +3.02%. Withdrawn as well — no run
         # produced it, and scripts/monthly_swing_backtest.py reads a
         # daily_report table with 0 rows, so it cannot reproduce it. Not
         # replaced by another number.
         "嗰個係逐筆百分比相加，假設每單都用盡 100% 資金。曾經用一個「誠實定資模擬」"
         "嘅 T+10 總回報頂上，<b>該數字已於 2026-10-18 一併撤回</b>："
         "冇任何 run 產生過佢，唯一可能產生佢嘅 "
         "<code>scripts/monthly_swing_backtest.py</code> 讀嘅 "
         "<code>data/dsa_hk.db</code> 入面 <code>daily_report</code> 表係 0 行，"
         "個 script 直接以「No BUY signals in window」結束",
         "兩個數都已撤回，唔再當 headline，亦唔會用另一個數字填返"),
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
        ("10 年日線庫（407 個代號）",
         "逐隻重新抓取後發現 <b>8 個檔案損壞</b>：7 隻日股加 1 隻美股，"
         "最嚴重嗰隻價格偏離原始數據 43,781%（日線被印成另一個價位級別）。"
         "源頭 Yahoo 本身亦有一兩支壞 bar（例如 1306.T 有兩日被印成 1/10 價）",
         "已重新逐隻抓取成獨立資料庫，"
         "並加咗 fail-closed 過濾：任何交易窗（連同前 210 日特徵暖機）碰到壞 bar 就唔准入統計。"
         "舊嘅 +1811.8%／Sharpe 2.97 組合數字已撤回，原因見 §4.7"),
        # 2026-10-18: this cell was typed in from a superseded run and asserted a
        # direction in words ("等於零"). The summary was then regenerated twice
        # in one afternoon and the sign moved twice. Derived from US() now, and
        # the verdict sentence is computed from the measured sign via _verdict()
        # so the cell cannot contradict the number in the same row.
        ("「v2 有選時優勢」呢句話",
         f"每單層面成立，但組合層面：v2 美股年化 {_pct(US()['v2_cagr'])}，"
         f"同一批股票等權每月再平衡就有 {_pct(US()['ew_cagr'])} —— "
         f"差額 {_pp(US()['gap_pp'])}（{_vs_hold(US()['gap_pp'])}）；{_regime(US())}",
         "已改寫：公開講明每單優勢 ≠ 超額回報，美股層面冇可引用嘅持久 alpha。見 §4.7"),
    ]
    fix_rows = "\n".join(
        f"""      <tr>
        <td>{a}</td>
        <td>{b}</td>
        <td><b>{c}</b></td>
      </tr>"""
        for a, b, c in rows_fix
    )

    # 2026-10-18: these three tables were literals, and two of them disagreed
    # with the run that produced them — n=2,992/60.2%/1.78% where the engine
    # measures 2,982/60.3%/1.772%, and a JP row wrong in every column
    # including profit factor and t. Nothing re-derived them, so a typo lived
    # on the published page indefinitely. They are rendered from
    # data/ab_close_hold.json now; a changed measurement changes the page.
    def _win(mkt, arm, window):
        return AB["windows"][mkt][arm][window]

    def _numrow(mkt, arm, window):
        w = _win(mkt, arm, window)
        return (mkt.upper(), w["n"], f'{w["win%"]}%', f'{w["avg%"]:+.3f}%',
                f'{w["pf"]:.2f}', f'{w["t"]:+.2f}')

    def _abrow(mkt, window, label):
        a, b = _win(mkt, "A_open-hold", window), _win(mkt, "B_close-hold", window)
        kept = b["n"] / a["n"] * 100
        return (mkt.upper(), label, f"{kept:.1f}%",
                f'{a["avg%"]:+.3f}% → {b["avg%"]:+.3f}%',
                f'{a["t"]:+.2f} → {b["t"]:+.2f}')

    train_rows = "\n".join(
        f"""      <tr>
        <td>{m}</td>
        <td class="mono">{n:,}</td>
        <td class="mono">{w}</td>
        <td class="mono">{avg}</td>
        <td class="mono">{pf}</td>
        <td class="mono">{t}</td>
      </tr>"""
        for m, n, w, avg, pf, t in (
            _numrow("us200", AB_RULE, "train"), _numrow("jp200", AB_RULE, "train"))
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
        for m, n, w, avg, pf, t in (
            _numrow("us200", AB_RULE, "test"), _numrow("jp200", AB_RULE, "test"))
    )

    ab_rows = "\n".join(
        f"""      <tr>
        <td>{m}</td>
        <td>{w}</td>
        <td class="mono">{kept}</td>
        <td class="mono">{avg}</td>
        <td class="mono">{t}</td>
      </tr>"""
        # 2026-10-18: this generator unpacked 7 names from _abrow()'s 5-tuple
        # ("a1 → a2" and "t1 → t2" were being re-joined in the template even
        # though _abrow already formats the arrow inside each string), so the
        # build raised ValueError and this page had not rendered at all. The
        # five columns match the §4.5 header (市場 / 窗口 / 保留交易 / 每單平均 /
        # 日期叢集 t); the derived content is unchanged, only the unpacking was.
        for m, w, kept, avg, t in (
            _abrow("us200", "train", "訓練 2016–22"), _abrow("us200", "test", "樣本外 2023–26"),
            _abrow("jp200", "train", "訓練 2016–22"), _abrow("jp200", "test", "樣本外 2023–26"))
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
            ("US", "訓練 2016–22", 3693, "+1.922%", "+0.535%", "+1.387pp"),
            ("US", "樣本外 2023–26", 2982, "+1.772%", "+0.696%", "+1.076pp"),
            ("JP", "訓練 2016–22", 2389, "+1.058%", "+0.112%", "+0.946pp"),
            ("JP", "樣本外 2023–26", 2187, "+2.354%", "+0.693%", "+1.661pp"),
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
            # 2026-10-18: both US rows were literals (+814.1% / +27.2% / −20.6%
            # / 2.19 / 6,675 and +1048.8% / +27.4% / −21.4%) from a superseded
            # run; the regenerated summary now reads both markets off the same
            # loader as JP. Cumulative is derived from the verified CAGR over the
            # measured bar window, because the summary carries no cumulative
            # column — the same treatment the JP rows got on 2026-10-15.
            # Sharpe is "—" for the same reason JP's is: the summary does not
            # report it, and a figure carried over from a superseded run is worse
            # than an admitted gap.
            (f"v2 · 美股 · 2016–26（{US()['cost_label']} 成本）",
             f"{_pct(US()['v2_cum'], 1)}", _pct(US()["v2_cagr"]),
             _pct(US()["v2_dd"]), "—", f"{US()['trades']:,}"),
            # 2026-10-15: the two JP rows are read from the 2026-10-12 retest.
            # Cumulative is derived from the verified CAGR over the measured bar
            # window, because the retest summary carries no cumulative column
            # and the pasted one (+702.0%) did not follow from its own CAGR.
            # Sharpe is "—" rather than the old 1.93: the summary does not
            # report it, and a figure carried over from a superseded run is
            # worse than an admitted gap.
            (f"v2 · 日股 · 2016–26（{JP()['cost_label']} 成本）",
             f"{_pct(JP()['v2_cum'], 1)}", _pct(JP()["v2_cagr"]),
             _pct(JP()["v2_dd"]), "—", f"{JP()['trades']:,}"),
            (f"同一批股票等權每月再平衡 · 美股",
             f"{_pct(US()['ew_cum'], 1)}", _pct(US()["ew_cagr"]),
             _pct(US()["ew_dd"]), "—", "—"),
            (f"同一批股票等權每月再平衡 · 日股",
             f"{_pct(JP()['ew_cum'], 1)}", _pct(JP()["ew_cagr"]),
             _pct(JP()["ew_dd"]), "—", "—"),
            ("大市指數 buy &amp; hold · 美股（^GSPC）", "+253.2%", "+13.3%", "−33.9%", "—", "—"),
            ("大市指數 buy &amp; hold · 日股（^N225）", "+307.4%", "+15.0%", "−31.8%", "—", "—"),
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
      <span>最後更新: <b><span id="meth-updated">2026-10-16</span></b></span>
    </div>
  </div>

  <div class="callout" style="margin: var(--sp-5) 0; padding: var(--sp-4); border-left: 4px solid var(--red); background: var(--red-dim); border-radius: 4px;">
    <h3 style="margin-top: 0; font-size: var(--text-md);">⚠ 閱讀本頁前請先知：每單有優勢，但美股層面冇超額回報</h3>
    <p style="margin: var(--sp-2) 0; font-size: var(--text-sm);">
      我哋將 v2 放喺同一個 10 倉組合（每單風險 1%、單一名字上限 10% 名義、總曝險 ≤100%）
      同一批日線跑足 2016–26，再加一個真正嘅對照：<b>乜都唔做，淨係將同一批股票等權每月再平衡</b>。
    </p>
    <table class="data-table method-prose" style="margin: var(--sp-3) 0;">
      <thead><tr><th>2016–26 年化</th><th>美股</th><th>日股</th></tr></thead>
      <tbody>
        <tr><td>v2 組合</td><td>{_pct(US()['v2_cagr'])}</td><td>{_pct(JP()['v2_cagr'])}</td></tr>
        <tr><td>同一批股票等權持有</td><td><b>{_pct(US()['ew_cagr'])}</b></td><td>{_pct(JP()['ew_cagr'])}</td></tr>
        <tr><td>大市指數 buy &amp; hold</td><td>+13.3%</td><td>+15.0%</td></tr>
      </tbody>
    </table>
    <!-- 2026-10-18: the US cells of this table and the paragraph below were
         typed in by hand from a superseded run; derived from US() now. The
         "beats the index by N points" claim is deleted outright — it is
         v2-minus-^GSPC and ^GSPC is not in the retest summary, so it cannot be
         derived. The JP verdict now follows the sign of the measured gap. -->
    <p style="margin: var(--sp-2) 0; font-size: var(--text-sm);">
      <b>美股：v2 {_vs_hold(US()['gap_pp'])}</b>（2016–20 {_vs_hold(US()['early_gap'])}、
      2021–26 {_vs_hold(US()['late_gap'])}）。呢批股票本身就係一個極強嘅對照 ——
      「乜都唔做、淨係等權持有同一批今日嘅大型股」已經跑贏大市十幾個百分點，
      而嗰十幾個百分點全部來自「呢批係今日嘅大型股」呢個選擇，唔係來自揀時段。
      <b>日股：全期 v2 {_vs_hold(JP()['gap_pp'])}</b>（{JP()['cost_label']} 成本口徑；
      2016–20 {_vs_hold(JP()['early_gap'])}、2021–26 {_vs_hold(JP()['late_gap'])}）——
      兩個市場嘅正負號都喺兩段之間變換（見 §4.8），所以全期嗰個差距唔應該當成
      持久 alpha 去引用。
      最大回撤反而淺過純持有（{_pct(JP()['v2_dd'])} 對 {_pct(JP()['ew_dd'])}）——
      減少咗回撤，但冇製造穩定嘅超額回報。
    </p>
    <p style="margin: 0; font-size: var(--text-sm);">
      即係話：<b>每單優勢係真嘅（見 §4.6），但喺美股，佢冇轉化成一個穩定、跑贏
      「攞住同一批股票」嘅超額回報。</b> 詳情同限制見 §4.7。
    </p>
  </div>

  <h2>1. 修正記錄：我哋改咗乜、點解</h2>
  <p>
    2026-10-01 收到一份第三方 10 年訊號審計（2016–2026）。佢用本站公開嘅規則重跑，
    結論係舊版四個策略喺整個週期都冇正期望值。以下係逐項處理結果。
    呢一節我哋刻意保留，唔打算改。
  </p>
  <div class="callout" style="margin: var(--sp-4) 0; padding: var(--sp-4); border-left: 4px solid var(--amber); background: var(--amber-dim); border-radius: 4px;">
    <!-- 2026-10-18: the US sub-period gaps quoted in the paragraph below were
         hard-coded from the old measurement; derived from US() now. The JP
         figures in these two callouts are the historical measurement being
         withdrawn here — what the 2026-10-12 retest replaced. They are not in
         the regenerated summary, so they stay as the dated record of the
         retracted claim. -->
    <p style="margin:0 0 var(--sp-2); font-size: var(--text-sm)">
      <b>2026-10-06 — 撤回一句聲稱。</b>
      我哋之前喺首頁、方法論同 llms.txt 都寫過「日股 +6.9 個百分點/年」。
      <b>呢個數唔企穩，我哋撤回佢。</b> 拆開市況之後：日股 2016–20 落後純持有
      <b>7.9 點/年</b>，2021–26 先領先 16.7 點/年；美股兩個時段分別
      <b>{_vs_hold(US()['early_gap'])}</b>同<b>{_vs_hold(US()['late_gap'])}</b>
      （2026-10-18 按統一成本口徑重測後嘅數）。
      當時四格冇一格係穩定正數，所以 +6.9 只係「輸嘅五年」同「贏嘅五年」嘅平均，
      唔係一個跨市況都成立嘅優勢 —— 2026-10-18 重測之後，兩個市場一樣冇一個
      regime 企得穩（見 §4.8）。同時我哋用 Nikkei 官方成分股變更紀錄做咗
      point-in-time 名單（<code>scripts/portfolio_pit.py</code>）：剔除 17 隻遲入選股票後
      差額只由 +6.9 變 +6.0，證明遲入選唔係主因；但真正嘅偏差（已經唔存在嘅股票）
      用公開名單重建唔到。詳見 §4.7、§4.8 同第三項未解決限制。
    </p>
  </div>
  <div class="callout" style="margin: var(--sp-4) 0; padding: var(--sp-4); border-left: 4px solid var(--amber); background: var(--amber-dim); border-radius: 4px;">
    <p style="margin:0 0 var(--sp-2); font-size: var(--text-sm)">
      <b>2026-10-15 — 撤回嗰句之後，連「差幾多」都要再量一次。</b>
      上面 2026-10-06 撤回咗 +6.9，但當時只係話個數唔企穩，<b>冇重新報一個數</b>，
      所以頁面一直留低 +25.4% 對 +18.5%、分段 7.9／16.7。2026-10-12 我哋重跑咗完整
      10 年組合測試（<code>scripts/hk_retest.py</code> → <code>data/hk_retest_summary.json</code>）。
      <br><br>
      <b>2026-10-18 更新：</b>呢一節原本報嘅係 {JP()['mid_cost_label']} 成本口徑
      （v2 <b>{_pct(JP()['v2_mid_cagr'])}</b>、等權 <b>{_pct(JP()['ew_cagr'])}</b>，
      差 {_pp(JP()['gap_mid_pp'])}），而首頁三個市場一律用 {JP()['cost_label']}。
      同一頁寫兩個成本口徑 = 自行揀一個好睇嘅講，所以我哋
      改用同首頁一致嘅 {JP()['cost_label']}：v2 <b>{_pct(JP()['v2_cagr'])}</b>、
      同一批股票等權持有 <b>{_pct(JP()['ew_cagr'])}</b>，差 <b>{_pp(JP()['gap_pp'])}</b>
      —— 兩個成本口徑嘅正負號根本唔同（{JP()['mid_cost_label']} 成本下係
      <b>{_vs_hold(JP()['gap_mid_pp'])}</b>），所以淨係報好睇嗰個就係自欺。
      分段睇：{_sub_line(JP())}。
      <br><br>
      本頁所有美股同日股數字而家係<b>由 <code>data/hk_retest_summary.json</code> 讀出嚟</b>，
      唔再係手打 —— 同一個數字寫兩遍，就一定會有一遍過時。
      同一個道理，呢頁<b>連方向嘅措辭都係由符號計出嚟</b>：呢個檔案喺 2026-10-18
      當日下午重跑過兩次，差距嘅正負號轉咗兩次，所以頁面唔會再自己斷言
      「領先」定「落後」。
    </p>
  </div>
  <div class="callout" style="margin: var(--sp-4) 0; padding: var(--sp-4); border-left: 4px solid var(--red); background: var(--red-dim); border-radius: 4px;">
    <h3 style="margin-top: 0; font-size: var(--text-md);">⚠ 2026-10-16 — 我哋自己嘅入場時段規則，量出嚟係<b>差過隨機揀時段</b></h3>
    <p style="margin: var(--sp-2) 0; font-size: var(--text-sm)">
      Signal Search Sprint 1（<code>scripts/signal_search_s1.py</code>）將本頁描述嘅 v2 規則
      每個維度逐個改（入場位、收市確認、止損、持有期、市況過濾），美股 11 個變體 × 3 個成本情境，
      每一個都對同一批 179 隻股票、同一本 10 倉組合、同一組成本去跑。
      為咗分清楚「規則有效」同「呢批股票本身就升」，每個變體都做咗 <b>placebo</b>：
      規則、universe、倉位、成本全部照舊，只係<b>把入場日隨機換到另一個交易日</b>，跑 30 次。
      <br><br>
      現行入場規則（20 日低 S1 限價買入 + 收市確認）嘅結果：
    </p>
    <table class="data-table method-prose" style="margin: var(--sp-3) 0;">
      <thead><tr><th>期間</th><th>真實差距</th><th>隨機入場 null</th><th>淨差距</th><th>z</th><th>喺 null 嘅百分位</th></tr></thead>
      <tbody>
        <tr><td>2016–2020</td><td>+0.41pp</td><td>+5.67 ± 3.98pp</td><td>−5.26pp</td><td>−1.32</td><td>6.7%</td></tr>
        <tr><td>2021–2026</td><td>−6.48pp</td><td>+9.42 ± 3.38pp</td><td>−15.90pp</td><td><b>−4.71</b></td><td><b>0.0%</b></td></tr>
      </tbody>
    </table>
    <p style="margin: var(--sp-2) 0; font-size: var(--text-sm)">
      即係話：<b>喺 2021–26 嗰半，現行入場時段比隨機揀時段仲差，而 30 次隨機trial
      冇一次好過佢（0.0 百分位）。</b> 11 個變體入面 <b>10 個</b> 喺兩個時段都係負 z。
      最好嗰個（取消市況過濾）worst z 只有 +0.14，等於同自己嘅 null 分唔開。
      <br><br>
      <b>呢個結論唔係話策略蝕錢</b>，亦唔係話隨機入場係好策略 —— null 本身係正數，
      嗰個正值來自「持有呢批 2016 年後被選中嘅大型股」同「組合平均只用咗 76.9% 資金」
      呢兩件事，同入場時段無關。單筆層面嘅優勢亦冇變（見 §4.6）。
      真正被否定嘅只有一樣：<b>用 S1 限價 + 收市確認去揀時段，喺組合層面冇帶來可量度嘅選股價值</b>。
      <br><br>
      <b>我們冇改 production</b>，現行規則照跑。原因係：呢個係一個 falsification 結果，
      唔係一個新規則；Sprint 1 冇任何變體通過修正後 bar（要贏 null、且 z ≥ 1.96、且兩段都要），
      所以唯一可以負責任嘅做法係公開咗佢，而唔係換一條未經證明嘅規則上去。
      全部 11 個變體 × 3 個成本情境嘅 grid（每個變體嘅曝險、null 均值／標準差／z／百分位）
      可以用 <code>scripts/signal_search_s1.py --placebo 30</code> 一次過重跑；
      規則定義寫喺 <code>docs/signal-search-sprint-1-spec.md</code>。
    </p>
  </div>
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
      輸入資料在 <code>data/bt10y2/</code>，每個結論都可以覆核；含壞 bar 嘅交易窗會被自動剔除（<code>scripts/bt_clean.py</code>）。
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
    <b>呢個表係組合層面，唔係每單。</b> 重點唔係 v2 跑贏指數，而係佢<b>跑唔贏
    「乜都唔做、淨係等權持有同一批股票」</b> —— 詳情見 §4.7。
  </p>
  <p>
    分年睇（美股 v2 組合）：2017 +15%、2018 +18%、2019 +48%、2020 +12%、2021 +53%、
    2022 +3%、2023 +28%、2024 +44%、2025 +16%、2026 +20%。十個曆年全部為正，
    但 2022 嗰年得 +3% —— 大市過濾減少咗曝險，冇完全避開。
    同一個對照組（開市價直接買上升趨勢股）喺 2018 −14%、2022 −13%、2026 −1%。
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
    每一格交易筆數 {_ab_n_range()}，遠高於本項目「1,000 單先可以出貨」嘅門檻。
    代價係交易量減半 —— 呢個規則賺錢嘅方式係<em>唔做錯單</em>，唔係做多啲單。
  </p>
  {_close_hold_control_note()}
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
      每單贏多啲，唔等於同等資金下賺多啲。呢一項我哋已經喺 <b>§4.7</b> 補做咗組合層面測試，
      答案係：美股零超額。
    </p>
    <p style="margin: var(--sp-2) 0; font-size: var(--text-sm);">
      <b>二、呢個結果同 2026-10-01 第三方審計報告相反。</b>
      嗰份報告嘅對照組數字（美股樣本外 +0.58%）同我哋（+0.694%）好接近，
      但佢哋報 v2 只有 +0.45%，我哋量到 +1.77%。
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

  <h3>4.7 組合層面測試：同「攞住同一批股票」比，仲有冇超額？</h3>
  <p>
    §4.6 嘅對照組答緊「等 S1 係咪好過追上升趨勢」。但真正嘅問題係另一條：
    <b>如果我淨係將呢 201 隻股票等權持有，我仲使唔使搞 v2？</b>
    答呢條問題先至重要，因為「攞住一批股票」本身已經好叻 ——
    呢 201 隻係<b>今日</b>嘅大型股，唔係當年嗰 200 隻。
  </p>
  <p>
    測試設計：v2 同對照組行<b>完全同一套組合機器</b> —— 每單風險 1%、最多 10 倉、
    單一名字名義上限 10%、總曝險 ≤100%、倉位滿時以最超賣者優先。
    <!-- 2026-10-18: was "成本照市場所屬扣除（美股 0.05% 費用 + 雙邊滑價，日股另
         計）", i.e. a per-market cost. Both markets are now quoted at one
         uniform leg — the same one the home page publishes — so a page cannot
         flatter v2 by quietly picking the cheaper market. -->
    兩個市場一律用同一個 {JP()['cost_label']} 來回成本口徑（<code>{JP_COST}</code>，
    同首頁一致）。兩邊唯一分別仍然係入場方式。
  </p>
  <table class="data-table method-prose">
    <thead>
      <tr><th>2016–26</th><th>美股年化</th><th>美股最大回撤</th><th>日股年化</th><th>日股最大回撤</th></tr>
    </thead>
    <tbody>
      <!-- 2026-10-18: the US column of this table was typed in by hand from a
         superseded run and is now derived from US(). The "beats the index by
         N points" claim is deleted outright — it is v2-minus-^GSPC and ^GSPC
         is not in the retest summary, so it cannot be derived. The JP verdict
         now follows the sign of the measured gap instead of asserting a draw. -->
      <tr><td>v2 組合</td><td>{_pct(US()['v2_cagr'])}</td><td>{_pct(US()['v2_dd'])}</td><td>{_pct(JP()['v2_cagr'])}</td><td>{_pct(JP()['v2_dd'])}</td></tr>
      <tr><td>同一批股票等權每月再平衡</td><td><b>{_pct(US()['ew_cagr'])}</b></td><td>{_pct(US()['ew_dd'])}</td><td>{_pct(JP()['ew_cagr'])}</td><td>{_pct(JP()['ew_dd'])}</td></tr>
      <tr><td>大市指數 buy &amp; hold</td><td>+13.3%</td><td>−33.9%</td><td>+15.0%</td><td>−31.8%</td></tr>
      <tr><td><b>v2 減「等權持有」</b></td><td><b>{_pp(US()['gap_pp'])}</b></td><td>—</td><td><b>{_pp(JP()['gap_pp'])}</b><br><span style="font-size:0.82em;opacity:0.75">{_table_note(JP())}</span></td><td>—</td></tr>
    </tbody>
  </table>
  <!-- 2026-10-18: the "beats the index by N points" pair deleted — it is
       v2-minus-^GSPC and ^GSPC is not in the retest summary, so it cannot be
       derived. The "difference is zero" verdict is now derived and no longer
       zero. -->
  <p>
    <b>美股答案係「冇一個穩定嘅超額」。</b> 同一批股票乜都唔做，年化就有
    <b>{_pct(US()['ew_cagr'])}</b>，v2 <b>{_pct(US()['v2_cagr'])}</b> ——
    <b>{_pp(US()['gap_pp'])}</b>，即係 v2 {_vs_hold(US()['gap_pp'])}。
    {_verdict(US())}。
    呢個對照本身就已經跑贏大市十幾個百分點，而嗰十幾個百分點全部來自
    「呢批係今日嘅大型股」呢個選擇，唔係來自揀時段。
  </p>
  <p>
    <!-- 2026-10-18: this paragraph asserted a fixed direction and a fixed
         "the gap breaks apart when you split by regime" story. Both were true
         of one particular regeneration and false of the next; the summary was
         rebuilt twice in one afternoon. Every clause below is now computed
         from the measured signs via _sub_line() / _verdict(). -->
    <b>日股答案係「冇一個穩定嘅超額」。</b> 2026-10-12 重測、2026-10-18 改用
    {JP()['cost_label']} 口徑之後，v2 全期 <b>{_vs_hold(JP()['gap_pp'])}</b>
    —— 同一批股票乜都唔做，差距只有咁多。拆開市況睇：
    <b>{_sub_line(JP())}</b>。
    {_verdict(JP())}。
  </p>

  <h3>4.8 全期差額拆開睇：會唔會淨係係單一個市況造成？</h3>
  <p style="margin: 0 0 var(--sp-3); font-size: var(--text-sm)">
    <!-- 2026-10-18: the heading asserted a conclusion ("佢來自單一個市況")
         and the opening asserted "一正一負". Both described one particular
         regeneration of the summary and were wrong by the next one, so the
         heading is now a question and the assertion is gone. The split is still
         run: it is the test that decides the question. -->
    全期數字最容易被誤讀成「十年都有優勢」。但 2016–26 其實橫跨兩個完全相反嘅 regime，
    所以最緊要係睇清楚兩段各自點。下表用同一套機械重跑（<code>scripts/portfolio_pit.py</code>），
    只係將日曆切成兩段，四格全部由 <code>data/hk_retest_summary.json</code> 讀出：
  </p>
  <table style="width:100%;border-collapse:collapse;margin:0 0 var(--sp-3);font-size: var(--text-sm)">
    <thead>
      <tr>
        <th style="text-align:left;padding:var(--sp-2);border-bottom:1px solid var(--border)">期間</th>
        <th style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border)">v2 年化</th>
        <th style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border)">等權持有</th>
        <th style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border)">v2 減持有</th>
      </tr>
    </thead>
    <tbody>
      <!-- 2026-10-18: both US rows were typed in from the superseded run;
           derived from US() now. Cell colour also follows the sign of the
           measured gap — the JP late-period row used to be painted green
           unconditionally. -->
      <tr><td style="padding:var(--sp-2);border-bottom:1px solid var(--border)">日股 2016–2020</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border)">{_pct(JP()['early_v2'])}</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border)">{_pct(JP()['early_ew'])}</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border);color:{_gap_color(JP()['early_gap'])}"><b>{_pp(JP()['early_gap'])}</b></td></tr>
      <tr><td style="padding:var(--sp-2);border-bottom:1px solid var(--border)">日股 2021–2026</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border)">{_pct(JP()['late_v2'])}</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border)">{_pct(JP()['late_ew'])}</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border);color:{_gap_color(JP()['late_gap'])}"><b>{_pp(JP()['late_gap'])}</b></td></tr>
      <tr><td style="padding:var(--sp-2);border-bottom:1px solid var(--border)">美股 2016–2020</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border)">{_pct(US()['early_v2'])}</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border)">{_pct(US()['early_ew'])}</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border);color:{_gap_color(US()['early_gap'])}"><b>{_pp(US()['early_gap'])}</b></td></tr>
      <tr><td style="padding:var(--sp-2);border-bottom:1px solid var(--border)">美股 2021–2026</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border)">{_pct(US()['late_v2'])}</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border)">{_pct(US()['late_ew'])}</td>
          <td style="text-align:right;padding:var(--sp-2);border-bottom:1px solid var(--border);color:{_gap_color(US()['late_gap'])}"><b>{_pp(US()['late_gap'])}</b></td></tr>
    </tbody>
  </table>
  <p style="margin: 0 0 var(--sp-3); font-size: var(--text-sm)">
    <!-- 2026-10-18: this paragraph hard-coded a reading ("four cells, none
         positive", "both periods in the same direction"). Those described one
         regeneration of the summary and were wrong by the next one — the file
         was rebuilt twice during this edit. Every clause is now computed from
         the measured signs so it cannot contradict the table above it. -->
    日股全期 {_pp(JP()['gap_pp'])}，{_sub_line(JP())} —— {_verdict(JP())}。
    美股：兩個時段分別 {_vs_hold(US()['early_gap'])} 同
    {_vs_hold(US()['late_gap'])} —— {_verdict(US())}。
  </p>
  <p style="margin: 0 0 var(--sp-3); font-size: var(--text-sm)">
    呢個唔代表規則完全冇用 ——
    <a href="/equity_curve_t_1.html">佢喺單一交易層面仍然有可重現嘅優勢</a>，
    而 v2 嘅市況過濾本身亦確實減少咗熊市持倉（美股最大回撤 {_pct(US()['v2_dd'])} vs 純持有 {_pct(US()['ew_dd'])}）。
    但「減少虧損」同「產生超額回報」係兩件事，我哋只證明咗前一半。
    <b>實際意義：唔好預期呢條規則喺大市唔升嘅年份跑贏單純持有。</b>
  </p>
  <div class="callout" style="margin: var(--sp-4) 0; padding: var(--sp-4); border-left: 4px solid var(--red); background: var(--red-dim); border-radius: 4px;">
    <h3 style="margin-top: 0; font-size: var(--text-md);">三個未解決嘅限制</h3>
    <p style="margin: var(--sp-2) 0; font-size: var(--text-sm)">
      <b>一、股票名單係靜態嘅，結構上 100% 帶倖存者偏差。</b>
      呢 201 隻係用今日市值／排名抽出嚟，之後直接套落 2016–26 全部歷史。
      名單冇 as-of 日期，所以任何 10 年絕對水平都唔可信 ——
      <!-- 2026-10-18: the equal-weight figure quoted in this limitation used to
           be a literal; it now points at the derived row so the limitation and
           the number it qualifies cannot drift apart. -->
      包括上面「等權持有 {_pct(US()['ew_cagr'])}」嗰個數。唯一可信嘅係 v2 同等權持有之間嘅<b>相對差</b>，
      因為兩邊行同一批股票。
    </p>
    <p style="margin: var(--sp-2) 0; font-size: var(--text-sm)">
      <b>二、我哋自己喺呢個測試上面出過三個錯，全部令結果嚴重偏離。</b>
      2026-10-05 首次跑，印出美股 +1811.8%／Sharpe 2.97。三個成因：
      (a) 程式計咗成本但<em>從來冇用過</em>，盈虧係毛數；
      (b) 10% 名義上限只限制<em>每筆單</em>，唔係<em>每隻股票</em> ——
      實測同時可以持有多過 4 筆同一隻股；
      (c) Sharpe 喺「有成交嘅日子」之間做差分，再當每個間隔都係一日去年化。
      另外，10 年日線庫本身有 8 個檔案損壞（最嚴重嗰隻偏離原始數據 43,781%），
      已重新逐隻抓取並剔除含壞 bar 嘅交易窗。修正後就係上面嘅數字。
      <b>呢個過程記錄喺修正記錄一節，因為佢正正係點解絕對水平唔可以當真。</b>
    </p>
    <p style="margin: 0; font-size: var(--text-sm)">
      <b>三、日股本來嗰 +6.9 個百分點已經拆開就冇咗，重測之後連全期都只剩 {_pp(JP()['gap_pp'])}，而且 point-in-time 偏差仍然未能量化。</b>
      我哋用 Nikkei 官方 1970 年至今嘅成分股變更紀錄做咗 point-in-time 名單
      （<code>scripts/portfolio_pit.py</code>）：剔除 17 隻 2016-09-01 之後先入選嘅股票，
      當年嗰個差額只由 +6.9 變 +6.0 —— 即遲入選唔係主因。
      但真正嘅偏差係<b>已經唔存在嘅股票</b>（2016 年係大盤、後來縮細／被剔除／除牌），
      佢哋完全唔喺今日名單，所以兩邊都冇計到。而 Nikkei log 只覆蓋 Nikkei 225 成員，
      同我哋嘅 TOPIX Core30 + Large70 只有 41/201 重疊，<b>重建唔到</b>。
      再加上上面嘅市況拆分，我哋唔會聲稱日股有 alpha。
    </p>
  </div>

{_honest_results_disclosure()}

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
    <!-- 2026-10-18: the old out-of-sample win-rate range contradicted this
           page's own derived table above it and is replaced by the measured
           per-trade rates. The honest caveat is kept, in the
           direction the measurement supports: a per-trade win rate is not
           portfolio excess return, and §4.7 measures the portfolio layer as
           negative for both markets. -->
    <p style="margin: 0;">
      <b>免責聲明：</b>教育用途，非投資建議。Backtest 唔等於 live 表現。
      v2 每單樣本外勝率係{_oos_win_sentence()}；但每單勝率唔等於組合超額回報 ——
      組合層面 v2 喺美股{_vs_hold(US()['gap_pp'])}、日股{_vs_hold(JP()['gap_pp'])}
      對「乜都唔做、淨係等權持有同一批股票」。短線及 swing 交易都可能損失全部本金。
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

    # 2c. Nav refresh. build_static leaves this page alone (it is in the
    #     hand-edited list), so its nav was last rendered before
    #     /track-record/ existed: it linked /methodology.html twice and had
    #     no way to reach the site's other honesty page, let alone the new
    #     one. The nav block is replaced wholesale with the current one,
    #     which also picks up the newer theme script.
    from build_static import nav_html
    fresh_nav = nav_html("/methodology.html")
    m_nav = re.search(r'<header class="site-header">.*?</script>\s*', html, flags=re.S)
    if not m_nav:
        raise SystemExit("no nav block found")
    html = html[: m_nav.start()] + fresh_nav + "\n\n" + html[m_nav.end():]

    # 3. JSON-LD
    def fix_ld(match: re.Match) -> str:
        import json
        doc = json.loads(match.group(0)[match.group(0).index(">") + 1: match.group(0).rindex("</script>")])
        for node in doc.get("@graph", []):
            if node.get("@type") == "Article":
                node["headline"] = "Leeks Terminal 訊號方法論 · v2 規則與修正記錄"
                node["datePublished"] = "2026-08-24"
                node["dateModified"] = "2026-10-16"
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

    # 2026-10-06: delete public/faq.html rather than mirroring methodology into
    # it. The file is a build orphan — build_static dropped it from its page
    # list on 2026-08-29, so it is in neither the page list nor hand_edited, and
    # /faq + /faq.html both 301 to methodology.html, so no visitor ever reaches
    # it. It only existed so a crawler could read a second copy of the claims,
    # which is the one artifact the correction log has just retracted.
    #
    # Mirroring was the previous answer and it was wrong: a synced duplicate is
    # a second source of truth for the same claims, so every future retraction
    # has to remember to update it. One page, no copy, no drift. The 301 in
    # _redirects stays, so external links keep working.
    faq = PUB / "faq.html"
    if faq.exists():
        faq.unlink()
        print(f"removed orphan {faq} (never served; /faq -> /methodology.html 301)")


if __name__ == "__main__":
    main()
