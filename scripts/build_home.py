#!/usr/bin/env python3
"""
build_home.py — Build the win9you.com home page with live data.

Reads the top 5 actionable tickers (highest win% BUY or SELL) from
HK + US action_plan JSON + backtest JSON, plus market regime stats.

Output: /Users/kenken/dev/dsa-hk/public/index.html
"""
from __future__ import annotations
import json
import shutil
from datetime import datetime, date, timedelta
from pathlib import Path
from mobile_nav import mobile_bottom_nav
from build_dashboard import site_label
import os as _os_i18n

# 2026-08-30: i18n — zh / en per-string translation
_SITE_LANG = _os_i18n.environ.get("SITE_LANG", "zh")
_STRINGS = {
    # 2026-10-07: dropped "AI". The hero one screen below says every number is
    # computed deterministically in Python from T-1 close OHLC with zero LLM
    # involvement, and the methodology page documents the same. Calling the
    # product an "AI trading dashboard" one line above that is the kind of
    # claim the site has just spent a month retracting elsewhere — a visitor
    # who reads both halves sees the contradiction. What it actually does is
    # deterministic multi-horizon signal generation; that is the stronger
    # sell because it is checkable. To restore the old wording, put "AI " back
    # in front of the two strings below.
    "home_subtitle_zh": {"zh": "多週期交易決策儀表板", "en": "Multi-Horizon Trading Decision Dashboard"},
    "home_meta_desc": {
        "zh": "每晚收市後為美股、日股計行動計劃：入場價、止損位、離場規則。只做多，收市企穩支持位先成交，3 倍 ATR 止損，第 10 個交易日離場。全部數字由 Python 對 T-1 收市 OHLC 確定性計出，零 LLM 幻覺。每單優勢不等於跑贏大市 —— 實測與限制見方法論頁。港股目前仍用舊版引擎，未納入 v2。",
        "en": "After each close, an action plan for US and Japanese stocks: entry, stop and exit. Long only, fills only if the session closes back above support, 3x ATR stop, time exit on the 10th session. Every number is computed deterministically in Python from T-1 close OHLC — zero LLM hallucination. A per-trade edge is not the same as beating the market; measured results and limits on the methodology page. Hong Kong still runs the older engine and is not part of v2."
    },
    "home_lede_zh": {
        "zh": (
            "每晚收市後，對 <b>{US} 隻美股、{JP} 隻日股</b>"
            "計一次行動計劃：入場價、止損位、離場規則。"
            "<b>全部數字由 Python 對 T-1 收市 OHLC 確定性計出，零 LLM 幻覺</b>。"
            "<br><br>"
            "現行規則（v2）：<b>只做多</b>；股價喺自己嘅 200 天線之上兼且 200 天線向上；"
            "大市指數都要喺自己嘅 200 天線之上；跌到 20 日低位 S1 掛限價單，"
            "<b>而且收市要企穩 S1 先算成交</b>；止損 S1 下方 3 倍 ATR；"
            "冇目標價，第 10 個交易日收市離場。"
            "<br><br>"
            "<b style=\"color: var(--amber);\">港股未納入 v2。</b>"
            "實測之後港股喺 v2 之下係負數：按 <b>0.25%</b> 來回成本（本頁假設嘅成本，"
            "亦即免佣 HK$100k 單嘅實際成本），2016–26 年化落後同一批股票等權持有 "
            "<b>4.6 個百分點</b>；成本收到零都仲係落後 1.4 點。唔係成本問題，係規則本身喺港股冇效，"
            "所以引擎目前只行美股同日股；<a href=\"/hk200/\">/hk200/</a> 顯示嘅仍然係舊版引擎。"
            "<br><br>"
            "<b style=\"color: var(--amber);\">呢個係一個規則，唔係一個保證。</b>"
            "每單表現係實測，但<b>每單有優勢 ≠ 跑贏大市</b>：用同一批美股等權單純持有，"
            "2016–26 年化 +27.4%，我哋嘅規則 +27.2% —— 差額係零。"
            "<b>日股全期 +6.9 個百分點/年，但拆開睇就唔成立</b>：2016–20 落後純持有 "
            "<b>7.9 個百分點/年</b>，2021–26 先領先 16.7。差額幾乎全部來自單一大市 regime，"
            "唔係一個跨市況都成立嘅優勢，所以唔當佢係 alpha。"
            "<a href=\"/methodology\">完整方法論同修正記錄 →</a>"
        ),
        "en": (
            "After each close, an action plan for <b>{HK} Hong Kong, {US} US and {JP} Japanese stocks</b>: "
            "entry price, stop level, exit rule. "
            "<b>Every number is computed deterministically in Python from T-1 close OHLC — zero LLM hallucination.</b>"
            "<br><br>"
            "Current rule (v2): <b>long only</b>; the stock sits above its own rising 200-day average and "
            "the market index is above its own; a limit order rests at the 20-day low (S1) and "
            "<b>only fills if the session closes back at or above S1</b>; stop at 3x ATR below S1; "
            "no target, time exit at the close of the 10th session."
            "<br><br>"
            "<b style=\"color: var(--amber);\">This is a rule, not a promise.</b> The per-trade result is measured, "
            "but <b>a per-trade edge is not the same as beating the market</b>: holding the same US names "
            "passively and equally weighted returned 27.4%/yr over 2016-26, against 27.2% for this rule — "
            "a difference of zero. Japan's full-period +6.9pp/yr does not survive being split: "
            "2016-20 trails passive by 7.9pp/yr, and only 2021-26 leads by 16.7. Almost the entire "
            "difference comes from one market regime, so it is not treated as alpha. "
            "<a href=\"/methodology\">Full methodology and correction log →</a>"
        ),
    },
    "home_today_actionable": {"zh": "今日 <em>actionable</em>", "en": "Today's <em>actionable</em>"},
    "home_how_it_works": {"zh": "HOW IT WORKS · 10 步方法論", "en": "HOW IT WORKS · 10-step methodology"},
    "home_regime": {"zh": "Market regime", "en": "Market regime"},
    "home_phase_dist": {"zh": "PHASE DISTRIBUTION", "en": "PHASE DISTRIBUTION"},
    "home_action_dist": {"zh": "ACTION DISTRIBUTION", "en": "ACTION DISTRIBUTION"},
    "home_hero_universe": {"zh": "Universe: <b>{n} {market} names</b>",
                         "en": "Universe: <b>{n} {market} names</b>"},
    "home_hero_t1": {"zh": "T-1 data: <b>{t1}</b>", "en": "T-1 data: <b>{t1}</b>"},
    "home_hero_updated": {"zh": "Updated: <b>{t} HKT</b>", "en": "Updated: <b>{t} HKT</b>"},
    "home_hero_phase": {"zh": "Phase dist: <b style=\"color: {color};\">{dist}</b>",
                        "en": "Phase dist: <b style=\"color: {color};\">{dist}</b>"},
    "home_hero_action": {"zh": "Action dist: <b>{dist}</b>", "en": "Action dist: <b>{dist}</b>"},
}


def _universe_counts() -> dict:
    """Count published ticker pages per market from the actual public/ tree.

    The hero used to hardcode "200 HK + 200 US" while US actually publishes 222
    ticker pages, and JP was missing entirely. Typed counts drift from the build;
    counted ones cannot.
    """
    out = {}
    for mkt in ("hk200", "us200", "jp200"):
        d = Path(__file__).resolve().parent.parent / "public" / mkt / "ticker"
        out[mkt[:2].upper()] = len(list(d.glob("*.html"))) if d.is_dir() else 0
    return out


def T(key: str, **kw) -> str:
    """Translate string by key, returning zh or en based on _SITE_LANG."""
    entry = _STRINGS.get(key, {})
    v = entry.get(_SITE_LANG) or entry.get("zh") or key
    if kw:
        try:
            v = v.format(**kw)
        except Exception:
            pass
    return v


def t_minus_1(market: str | None = None) -> date:
    """Return T-1 = last trading day (skip Sat/Sun), per market.

    Home page mixes HK + US cards: HK data may be today (after 16:00 HKT close)
    while US data is still the previous session. Header shows the HK date
    (HK-first product); pass market='US' for the US session date.

    Override with DSA_T_MINUS_1_HK / DSA_T_MINUS_1_US, or DSA_T_MINUS_1 (both).

    2026-10-02: LEAKS_ASOF is now the single override shared with
    build_dashboard.py, so a rebuild can pin the T-1 label to the date the
    bars actually end on. Without it the home page printed 2026-10-01 while
    every hub said 2026-09-29 — the two disagreed on the same deploy.
    """
    import os as _os
    if market:
        override = _os.environ.get(f"DSA_T_MINUS_1_{market}")
        if override:
            from datetime import datetime as _dt
            return _dt.strptime(override, "%Y-%m-%d").date()
    override = _os.environ.get("DSA_T_MINUS_1")
    if not override:
        override = _os.environ.get("LEAKS_ASOF")
    if override:
        from datetime import datetime as _dt
        return _dt.strptime(override, "%Y-%m-%d").date()
    today = date.today()
    if today.weekday() == 0:  # Monday
        return today - timedelta(days=3)  # Friday
    elif today.weekday() == 6:  # Sunday
        return today - timedelta(days=2)  # Friday
    elif today.weekday() == 5:  # Saturday
        return today - timedelta(days=1)  # Friday
    else:
        return today - timedelta(days=1)

REPO = Path("/Users/kenken/dev/dsa-hk")
PUBLIC = REPO / "public"


def _cssver() -> str:
    """Cache-buster shared by every builder — see build_static._CSS_VERSION.
    build_home used today's date, so a CSS change was invisible to anyone who
    had already loaded the site that day."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from build_static import _CSS_VERSION
        return _CSS_VERSION()
    except Exception:
        return "1"

HK_OUT = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
US_OUT = Path("/Users/kenken/dev/dsa-hk/charts/us200")
BACKTEST_OUT = Path("/Users/kenken/dev/dsa-hk/data/backtest_out")


def load_ticker_json(ticker: str, market: str) -> dict | None:
    safe = ticker.replace(".", "_")
    base = HK_OUT if market == "HK" else US_OUT
    p = base / f"{safe}.json"
    if not p.exists():
        return None
    try:
        return json.load(open(p))
    except Exception:
        return None


def load_backtest(ticker: str) -> dict | None:
    safe = ticker.replace(".", "_")
    p = BACKTEST_OUT / f"{safe}.json"
    if not p.exists():
        return None
    try:
        return json.load(open(p))
    except Exception:
        return None


def pick_recent_edge(bt):
    """Best ANY-strategy edge (n>=5 + win>=50) — kept for reference/stats."""
    if not bt or not bt.get("windows"):
        return None
    for w in ("60", "90", "30", "197"):
        wd = bt["windows"].get(w)
        if not wd:
            continue
        for strat, data in wd.items():
            n = data.get("n", 0)
            win = data.get("win_pct", 0)
            tot = data.get("total_pnl", 0)
            if n >= 5 and tot > 0 and win >= 50:
                return {
                    "strategy": strat,
                    "window": w,
                    "win_pct": win,
                    "n": n,
                    "total_pnl": tot,
                }
    return None


def wilson_lower(wins: float, n: float, z: float = 1.96) -> float:
    """Lower bound of the Wilson score interval for a win rate, in percent.

    2026-10-05: the homepage was printing raw win% as the headline, so a card
    could read "100% win (60d, n=5)" — five trades in a row is not a 100%
    probability, it is a sample of five. The Wilson lower bound is what the
    data actually supports at ~95% confidence: 5/5 lands near 57%, which is
    both defensible and still the strongest number in the set. Raw % is kept
    alongside for transparency, but the bound is what we lead with and sort on.
    """
    if not n or n <= 0:
        return 0.0
    p = max(0.0, min(1.0, wins / n))
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (centre - margin) / denom) * 100


def pick_plan_edge(bt, plan_strategy):
    """2026-08-28 methodology fix: TODAY'S plan strategy's own backtest stats.

    Home cards used to badge a BUY plan with "SELL_R1 86% win" from a different
    strategy. Only the plan strategy's own window stats (n>=5) qualify now.

    2026-10-05: also returns a Wilson lower bound so callers can display a
    sample-size-aware number instead of the raw rate.
    """
    if not bt or not bt.get("windows") or not plan_strategy:
        return None
    if plan_strategy.startswith("WAIT"):
        return None
    for w in ("60", "90", "30", "197"):
        wd = bt["windows"].get(w)
        if not wd:
            continue
        data = wd.get(plan_strategy)
        if not data:
            continue
        if data.get("n", 0) >= 5:
            n = data.get("n", 0)
            win_pct = data.get("win_pct", 0)
            return {
                "strategy": plan_strategy,
                "window": w,
                "win_pct": win_pct,
                "n": n,
                "wins": round(win_pct / 100 * n),
                "wilson": wilson_lower(round(win_pct / 100 * n), n),
                "total_pnl": data.get("total_pnl", 0),
            }
    return None


def collect_actionables(market: str, top_n: int = 5):
    """Pick top N actionable tickers (BUY or SELL with defensible win rate).

    2026-08-28: selection + display both use the PLAN strategy's own win%
    (n>=5 floor). A card is only picked if today's advised strategy itself has
    >=50% historical win rate — no more cross-strategy win% laundering.

    2026-10-05: the >=50% gate and the ranking now use the Wilson lower bound,
    not the raw rate, so a "100% on n=5" strategy can no longer outrank a
    genuine 60% on n=40. Returns (cards, funnel) where funnel reports how many
    signals were seen and how many cleared the bar, so the homepage can state
    the funnel instead of printing a bare "2 of 105" that reads as emptiness.
    """
    base = HK_OUT if market == "HK" else US_OUT
    universes_path = REPO / "hk_universe_200.json" if market == "HK" else US_OUT / "us_top200_fresh.json"
    if not universes_path.exists():
        return [], {"signals": 0, "qualified": 0}
    universe = json.load(open(universes_path))

    rows = []
    signals = 0
    for tk in universe:
        tj = load_ticker_json(tk, market)
        if not tj:
            continue
        ap = tj.get("action_plan", {})
        verdict = ap.get("verdict", "WAIT")
        if verdict == "WAIT":
            continue
        signals += 1
        bt = load_backtest(tk)
        plan_edge = pick_plan_edge(bt, ap.get("strategy"))
        if not plan_edge or plan_edge["wilson"] < 50:
            continue

        # 2026-10-05: same flat-JP-schema guard as build_dashboard.py
        from build_dashboard import snapshot_quote
        _q = snapshot_quote(tj)
        rows.append({
            "ticker": tk,
            "name": _q["name"],
            "last": _q["last"],
            "chg": _q["chg_pct"],
            "verdict": verdict,
            "trigger": ap.get("trigger_price"),
            "target": ap.get("target_price"),
            "stop": ap.get("stop_price"),
            "edge": plan_edge,
            "phase": tj.get("phase", ""),
            "market": market,
        })

    # Sort: by Wilson lower bound desc (sample-size aware), then total_pnl desc
    rows.sort(key=lambda r: (-r["edge"]["wilson"], -r["edge"]["total_pnl"]))
    return rows[:top_n], {"signals": signals, "qualified": len(rows)}


def collect_phase_stats():
    """Compute aggregate phase distribution across HK + US."""
    stats = {"BUY": 0, "SELL": 0, "WAIT": 0, "phase": {}}
    for market, base in [("HK", HK_OUT), ("US", US_OUT)]:
        universes_path = REPO / "hk_universe_200.json" if market == "HK" else US_OUT / "us_top200_fresh.json"
        if not universes_path.exists():
            continue
        universe = json.load(open(universes_path))
        for tk in universe:
            tj = load_ticker_json(tk, market)
            if not tj:
                continue
            ap = tj.get("action_plan", {})
            verdict = ap.get("verdict", "WAIT")
            stats[verdict] = stats.get(verdict, 0) + 1
            phase = tj.get("phase", "unknown")
            stats["phase"][phase] = stats["phase"].get(phase, 0) + 1
    return stats


def fmt(x, dp=2):
    if x is None:
        return "—"
    try:
        return f"{float(x):,.{dp}f}"
    except Exception:
        return str(x)


def render_signal_card(row):
    verdict = row["verdict"]
    cls = "bull" if verdict == "BUY" else "bear"
    action_cls = "buy" if verdict == "BUY" else "sell"
    safe = row["ticker"].replace(".", "_")
    market = row["market"]
    detail_url = f"/{market.lower()}200/ticker/{safe}"
    edge = row["edge"]
    return f'''
      <a href="{detail_url}" class="signal-card {cls}">
        <div>
          <div class="signal-ticker">{row["ticker"]}</div>
          <div class="signal-name">{row["name"]}</div>
          <div class="signal-name" style="margin-top: var(--sp-2);">本策略歷史: {edge["strategy"]} · 勝率下限 <b>{edge["wilson"]:.0f}%</b> ({edge["wins"]}/{edge["n"]} 單, {edge["window"]}d, 95% Wilson)</div>
        </div>
        <span class="signal-action-large {action_cls}">{verdict}</span>
        <div class="signal-detail">{fmt(row["trigger"])} → <b>{fmt(row["target"])}</b> · stop {fmt(row["stop"])}</div>
      </a>'''


def data_asof(market: str) -> str:
    """T-1 for a market, derived from that market's own chart JSONs.

    2026-10-02: JP traded 2026-10-01 while HK/US last closed 2026-09-30, so a
    single HK-derived label is wrong for two of three markets. Mirrors
    build_dashboard.data_asof(); the header now prints each market's own date.
    """
    from collections import Counter
    import json as _json
    REPO = Path("/Users/kenken/dev/dsa-hk")
    src = {"HK": REPO / "charts/hk200", "US": REPO / "charts/us200",
           "JP": REPO / "data/jp200"}.get(market)
    if not src or not src.is_dir():
        return t_minus_1(market).isoformat()
    c = Counter()
    for f in src.glob("*.json"):
        if f.name.endswith("_ohlc.json"):
            continue
        try:
            d = _json.load(open(f))
            dt = (d.get("last_bar") or {}).get("date")
            if dt:
                c[dt] += 1
        except Exception:
            continue
    return c.most_common(1)[0][0] if c else t_minus_1(market).isoformat()


def load_v2_signals():
    """The published v2 signal set — the ONLY thing the homepage may call a signal.

    2026-10-06: the lower half of the homepage was still rendering the v1
    action_plan stream (charts/<mkt>/<sym>.json + data/backtest_out), so it
    published BUY_S1 / SELL_R1 cards, a SELL signal, a target price and a
    Wilson win-rate badge — while the hero directly above described v2 as
    long-only, target-free, and named none of those strategies. Two engines,
    one page, mutually exclusive rule sets.

    v2 is the engine the hero, the methodology page and llms.txt all describe,
    and the engine build_v2_signals.py actually publishes, so the homepage now
    reads the same payload the rest of the site does. Returns (payload, signals).

    HK is absent from this payload by design (build_v2_signals.INCLUDE_HK=False,
    "study found HK is ~0 edge after 0.45% round-trip"), which is why the
    homepage now says so out loud instead of counting HK in a universe that
    the live engine does not scan.
    """
    p = REPO / "public" / "v2-signals.json"
    if not p.exists():
        return None, []
    try:
        payload = json.load(open(p))
    except Exception:
        return None, []
    return payload, payload.get("signals", []) or []


def render_v2_card(sig: dict) -> str:
    """One v2 signal row. No win-rate badge: that number belongs to the retired
    v1 backtests and our own study found the metric carries no predictive power."""
    sym = sig["symbol"]
    mkt = sig.get("market", "")
    safe = sym.replace(".", "_")
    link = f"/{mkt}/ticker/{safe}"
    close = sig.get("last_close")
    entry = sig.get("entry_s1")
    stop = sig.get("stop")
    sd = sig.get("stop_dist_pct")
    dist = sig.get("dist_to_entry_pct")
    if dist is None or close in (None, 0) or entry is None:
        dist_txt = "已到入場位"
    else:
        d = abs(dist)
        dist_txt = "已到入場位" if d < 0.05 else f"距入場 <b>{d:.2f}%</b>"

    detail = f"收 {close} · 入場 <b>{entry}</b>" if close is not None else f"入場 <b>{entry}</b>"
    if stop is not None:
        detail += f" · 止損 {stop}"
        if sd is not None:
            detail += f" ({sd:.2f}%)"

    return (
        f'      <a href="{link}" class="signal-card bull">\n'
        f'        <div>\n'
        f'          <div class="signal-ticker">{sym} <span class="text-dim" '
        f'style="font-size:0.7em;">{mkt.upper()}</span></div>\n'
        f'          <div class="signal-name">{dist_txt} · v2 只做多 · 入場要企穩 S1</div>\n'
        f'        </div>\n'
        f'        <span class="signal-action-large buy">BUY</span>\n'
        f'        <div class="signal-detail">{detail}</div>\n'
        f'      </a>'
    )


def build_home_page():
    today = t_minus_1("HK")
    # 2026-10-02: JP traded 10-01 while HK/US last closed 09-30. A single date
    # in the header would be wrong for two of three markets, so print each.
    # 2026-10-07: moved to build_dashboard.site_label() — build_static's badge
    # restamper needs the exact same string, and two copies of one format is
    # how the chrome and the body drifted apart in the first place.
    t1_label = site_label()
    v2_payload, v2_sigs = load_v2_signals()

    # 2026-10-06: rank by distance to the S1 trigger, not by a v1 win rate.
    # "Actionable" for a stop-entry system means "closest to firing", and the
    # old ranking silently promoted the largest historical sample instead.
    ranked = sorted(v2_sigs, key=lambda s: abs(s.get("dist_to_entry_pct") or 0))
    top = ranked[:6]
    n_v2 = len(v2_sigs)
    by_mkt: dict[str, int] = {}
    for s in v2_sigs:
        by_mkt[s.get("market", "?")] = by_mkt.get(s.get("market", "?"), 0) + 1
    screen = (v2_payload or {}).get("screen", {}) or {}
    mstate = (v2_payload or {}).get("market_state", {}) or {}
    # HK is deliberately not scanned by the live engine — surface that, don't
    # fold HK into a universe total it isn't part of.
    hk_in_v2 = by_mkt.get("hk200", 0)
    mkt_bits = " + ".join(f"{v} {k.upper()}" for k, v in sorted(by_mkt.items())) or "—"

    cards = "\n".join(render_v2_card(s) for s in top)

    # 2026-08-30: i18n strings for hero (built once, used in template below)
    subtitle = T("home_subtitle_zh")
    counts = _universe_counts()
    lede = T("home_lede_zh", HK=counts.get("HK", "?"), US=counts.get("US", "?"),
             JP=counts.get("JP", "?"))

    return f"""<!DOCTYPE html>
<html lang="zh-Hant-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="alternate icon" href="/favicon.ico" sizes="any">
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/png" sizes="64x64" href="/favicon.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<title>Leeks Terminal · {T("home_subtitle_zh")}</title>
<meta name="description" content="{T('home_meta_desc')}">
<meta property="og:title" content="Leeks Terminal · {T('home_subtitle_zh')}">
<meta property="og:description" content="{T('home_meta_desc')}">
<meta name="theme-color" content="#0a0e1a">
<link rel="canonical" href="https://www.win9you.com/">
<meta property="og:site_name" content="Leeks Terminal">
<meta property="og:type" content="website">
<meta property="og:title" content="Leeks Terminal · {T('home_subtitle_zh')}">
<meta property="og:description" content="美股 + 日股多週期交易決策儀表板 · T-1 數據 · 只做多，收市企穩 S1 先成交 · 3 倍 ATR 止損 · 第 10 個交易日離場。港股未納入 v2。">
<meta property="og:url" content="https://www.win9you.com/">
<meta property="og:image" content="https://www.win9you.com/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:locale" content="zh_HK">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@graph": [
    {{
      "@type": "WebSite",
      "name": "Leeks Terminal",
      "url": "https://www.win9you.com/",
      "inLanguage": "zh-Hant-HK",
      "description": "Rule-based trading decision dashboard for US and Japanese stocks. Hong Kong still runs an older engine and is not part of v2. Signals are computed in Python from T-1 daily OHLC. Educational use only, not investment advice. Per-trade results are not evidence of excess return: in the US the same 201 stocks held passively outperformed the strategy over 2016-26. See the methodology page for the measured results and their limits.",
      "publisher": {{
        "@type": "Organization",
        "name": "Leeks Terminal",
        "url": "https://www.win9you.com/"
      }}
    }},
    {{
      "@type": "SoftwareApplication",
      "name": "Leeks Terminal",
      "url": "https://www.win9you.com/",
      "applicationCategory": "FinanceApplication",
      "applicationSubCategory": "Trading Signal Dashboard",
      "operatingSystem": "Web",
      "inLanguage": ["zh-Hant-HK", "en"],
      "description": "Rule-based signal engine for US and Japanese stocks. Signals are computed deterministically in Python from T-1 daily OHLC bars (long-only; stock above a rising 200-day average; market index above its own 200-day average; limit entry at the 20-day support with a close-hold confirmation; 3xATR stop; 10-session time exit). No language model produces any number, price level or verdict. Hong Kong is not covered by this engine: an internal study found roughly zero edge after HK round-trip costs, so /hk200/ still serves the older (v1) engine and must not be read as a v2 signal.",
      "offers": {{
        "@type": "Offer",
        "price": "0",
        "priceCurrency": "USD"
      }},
      "featureList": [
        "US and Japanese stocks, one action plan per stock per session; Hong Kong not covered by v2",
        "Deterministic Python on T-1 OHLC; no LLM in the number path",
        "Long-only v2 rule: rising 200-day average filter, limit entry at support with close-hold, 3xATR stop, 10-session exit",
        "10-year backtests run against a re-fetched bar store with a data gate that excludes corrupted price windows",
        "Published correction log of what was changed and why"
      ]
    }}
  ]
}}
</script>
<link rel="stylesheet" href="/static/fonts-local.css">
<link rel="stylesheet" href="/leeks.css?v={_cssver()}">
<script>
(function(){{
  const t = localStorage.getItem('leeks-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', t);
}})();
</script>
</head>
<body>

<a class="skip-link" href="#main">Skip to main content</a>

<header class="site-header">
  <nav class="nav" aria-label="Primary">
    <a href="/" class="nav-brand"><span class="leek">L</span>eeks <em class="italic">Terminal</em></a>
    <div class="nav-links">
      <a href="/" class="active">Home</a>
      <a href="/hk200/"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="vertical-align:-2px" aria-hidden="true"><path d="M3 3v18h18"/><rect x="7" y="12" width="3" height="6"/><rect x="12" y="8" width="3" height="10"/><rect x="17" y="4" width="3" height="14"/></svg> HK Signals</a>
      <a href="/us200/"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="vertical-align:-2px" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.6 3 14.4 0 18M12 3c-3 3.6-3 14.4 0 18"/></svg> US Signals</a>
      <a href="/jp200/"><span style="display: inline-block; width: 16px; height: 12px; background: radial-gradient(circle at 50% 50%, #bc002d 30%, #fff 30%); border-radius: 50%; vertical-align: -1px; margin-right: 4px;" title="Japan"></span>JP Signals</a>
      <a href="/compare/">Compare</a>
      <a href="/backtest.html">Backtest</a>
      <a href="/insights.html">Insights</a>
      <a href="/methodology.html">Methodology</a>
      <a href="/track-record/">Track record</a>
      <a href="/disclaimer/">Disclaimer</a>
      <a href="/privacy.html">Privacy</a>
    </div>
    <a class="lang-switch" href="/en/" title="View in English" aria-label="View in English">EN</a>
    <div class="nav-meta">
      <span class="live-dot"></span>
      T-1 · {t1_label}
      <button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle theme">
        <span class="icon" id="themeIcon">●</span>
        <span id="themeLabel">DARK</span>
      </button>
    </div>
  </nav>
</header>
{mobile_bottom_nav("home")}
<script>
function toggleTheme(){{
  const cur = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('leeks-theme', next);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = next === 'dark' ? '#0a0e1a' : '#fafbfc';
  const lbl = document.getElementById('themeLabel');
  const ic = document.getElementById('themeIcon');
  if (lbl) lbl.textContent = next.toUpperCase();
  if (ic) ic.textContent = next === 'dark' ? '●' : '○';
}}
(function(){{
  const t = document.documentElement.getAttribute('data-theme') || 'dark';
  const lbl = document.getElementById('themeLabel');
  const ic = document.getElementById('themeIcon');
  if (lbl) lbl.textContent = t.toUpperCase();
  if (ic) ic.textContent = t === 'dark' ? '●' : '○';
}})();
</script>

<main id="main">
<div class="page-head">
  <div class="container">
    <div class="kicker">Leeks Terminal · Live</div>
    <h1>Leeks Terminal · <em class="italic">{subtitle}</em></h1>
    <p class="lede">{lede}</p>
    <div class="hero-meta">
      <span>T-1 數據 · {t1_label}</span>
      <span>{counts.get("US", "?")} US + {counts.get("JP", "?")} JP 行 v2</span>
      <span>{n_v2} 個 v2 訊號 · 全部只做多 · 無目標價</span>
      <span>Updated {datetime.now().strftime('%H:%M HKT')}</span>
    </div>
  </div>
</div>
<section>
  <div class="container">
    <div class="section-head">
      <h2>{T("home_today_actionable")}</h2>
      <span class="section-meta">{mkt_bits} · 全部只做多 · 入場價 = 20 日低位 S1，要收市企穩先算成交 · <b>冇目標價</b>，第 10 個交易日收市離場 · Updated {datetime.now().strftime('%H:%M HKT')}</span>
    </div>
    <div class="card-grid" style="grid-template-columns: 1fr;">
      {cards if cards else '<div class="card text-dim">今日未有 v2 訊號 — 全部個股都未企穩 S1</div>'}
    </div>
    {f'<p class="text-dim" style="margin-top: var(--sp-3); font-size: var(--text-sm);">以上 6 個係 {n_v2} 個訊號中最接近觸發入場位嘅，按距 S1 距離排序。完整名單 → <a href="/us200/">US</a> · <a href="/jp200/">JP</a></p>' if cards else ''}
    {f'<p class="text-dim" style="margin-top: var(--sp-3); font-size: var(--text-sm);"><b style="color: var(--amber);">港股未包含喺呢版。</b>v2 引擎目前只行 US 同 JP（{mkt_bits}）—— 實測港股喺 0.25% 實際成本下落後等權持有 <b>4.6 個百分點/年</b>，零成本都仲係落後 1.4 點，所以 <code>INCLUDE_HK=False</code>。<a href="/hk200/">/hk200/</a> 仍然顯示舊版 (v1) 引擎嘅號，唔應該當成 v2 訊號用。</p>' if hk_in_v2 == 0 else ''}
  </div>
</section>

<section>
  <div class="container">
    <div class="section-head">
      <h2>Market <em class="italic">regime</em></h2>
      <span class="section-meta">{mkt_bits} · {t1_label}</span>
    </div>
    <div class="card-grid card-grid-2">
      <div class="card fade-in fade-in-1">
        <h3 class="card-label">市場過濾（大市 200 日線）</h3>
        <div style="margin-top: var(--sp-4); display: grid; gap: var(--sp-3);">
          {''.join(f'<div class="flex justify-between"><span class="text-dim">{k.upper()} 指數 {v.get("index", "?")}</span><span class="mono {"text-bull" if v.get("up") else "text-bear"}">{"向上" if v.get("up") else "向下 / 未知"}</span></div>' for k, v in sorted(mstate.items())) or '<div class="text-dim">—</div>'}
        </div>
        <p class="text-dim" style="margin-top: var(--sp-3); font-size: var(--text-xs);">大市唔喺自己嘅 200 天線之上，就唔出訊號。</p>
      </div>

      <div class="card fade-in fade-in-2">
        <h3 class="card-label">今日篩選漏斗</h3>
        <div style="margin-top: var(--sp-4); display: grid; gap: var(--sp-3);">
          {''.join(f'<div class="flex justify-between"><span class="text-dim">{k}</span><span class="mono">{v}</span></div>' for k, v in screen.items()) or '<div class="text-dim">—</div>'}
        </div>
        <p class="text-dim" style="margin-top: var(--sp-3); font-size: var(--text-xs);">入到 <b>ok</b> 先會成為訊號：股價喺 200 日線之上兼且 200 日線向上，再過 ATR / box 護欄。</p>
      </div>
    </div>
  </div>
</section>

<section style="padding-top: 0;">
  <div class="container">
    <div class="card fade-in fade-in-3">
      <h3 class="card-label">How it works（現行 v2 規則）</h3>
      <div style="margin-top: var(--sp-3); display: grid; gap: var(--sp-3); font-size: var(--text-sm); color: var(--fg-2);">
        <div>① <b>只做多</b> — 冇做空訊號</div>
        <div>② 股價喺自己嘅 <b>200 天線之上</b>，而且條 200 天線<b>向上</b></div>
        <div>③ 大市指數都要喺自己嘅 200 天線之上</div>
        <div>④ 跌到 <b>20 日低位 S1</b> 掛入場單，<b>而且收市要企穩 S1 先算成交</b></div>
        <div>⑤ 止損 = S1 下方 <b>3 倍 ATR</b>；<b>冇目標價</b>，買入後第 10 個交易日收市離場</div>
      </div>
      <p class="text-dim" style="margin-top: var(--sp-3); font-size: var(--text-xs);"><a href="/methodology">完整方法論、限制同修正記錄 →</a></p>
    </div>
  </div>
</section>
</main>

<div class="container">
  <div class="disclaimer">
    <b>⚠ Disclaimer</b> · All content is informational and educational only. Not investment advice.
    Trading involves substantial risk. Past backtest performance does not guarantee future results.
  </div>
</div>

<footer class="site-footer">
  <div class="container">
    <div class="row">
      <div>© {datetime.now().year} Leeks Terminal · win9you.com · <a href="/methodology">Methodology</a> · <a href="/disclaimer">Disclaimer</a></div>
      <div class="mono text-dim">T-1 · {t1_label} · Futu OpenD</div>
    </div>
  </div>
</footer>

</body>
</html>"""


def main():
    print("=== build_home.py ===")
    html = build_home_page()
    out = PUBLIC / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"  → {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
