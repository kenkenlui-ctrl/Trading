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
import os as _os_i18n

# 2026-08-30: i18n — zh / en per-string translation
_SITE_LANG = _os_i18n.environ.get("SITE_LANG", "zh")
_STRINGS = {
    "home_subtitle_zh": {"zh": "多週期 AI 交易決策儀表板", "en": "Multi-Horizon AI Trading Decision Dashboard"},
    "home_meta_desc": {
        "zh": "Leeks Terminal 每日對 200 隻港股 + 200 隻美股跑 10 步價格行為框架，輸出 BUY/SELL/WAIT 行動計劃，附入場 trigger、目標價、止損位同策略自身歷史勝率。T+1 / T+3 / T+5 / T+10 多週期 backtest（已扣 Futu 免佣真實成本 HK 0.25% / US 0.05%）。全部數字 Python 確定性計算，T-1 收市數據，零 LLM 幻覺。",
        "en": "Leeks Terminal runs a 10-step price-action framework daily on 200 HK + 200 US stocks, producing BUY/SELL/WAIT action plans with entry trigger, target, stop, R:R ratio and strategy own 60-day win rate. T+1/T+3/T+5/T+10 multi-horizon backtest net of Futu commission-free real costs (HK 0.25%/US 0.05% per round-trip). Python deterministic, T-1 close, zero LLM hallucination."
    },
    "home_lede_zh": {
        "zh": (
            "每日對 <b>200 隻港股 + 200 隻美股</b> 跑 10 步價格行為框架"
            "（phase 分類 → 20/60/120/200 日 S/R ladder → "
            "30/60/90/197 日四窗口回測），輸出 BUY / SELL / WAIT 行動計劃，"
            "附入場 trigger、目標價、止損位同 R:R ratio。"
            "<b>全部數字由 Python 對 T-1 收市 OHLC 確定性計出，零 LLM 幻覺</b>；"
            "每隻股票嘅訊號策略仲有自身 60 日歷史勝率（n≥5 先顯示）。"
            "<b style=\"color: var(--bull);\">T+1 / T+3 / T+5 三個 short-cycle horizon（已扣 Futu 免佣真實成本 HK 0.25% / US 0.05% round-trip）</b>："
            "T+1 +0.35% / T+3 +0.80% / T+5 +1.08%（對 US$20k 注碼）。"
            "<b style=\"color: var(--amber);\">T+10 swing 結果（+3.02% net）— 統計上係最強 window（4.39 t-statistic 過 deflated Sharpe Ratio 修正），"
            "但屬 swing horizon，請按個人風格同 lifestyle 自決。</b>"
        ),
        "en": (
            "Daily runs a 10-step price-action framework on <b>200 HK + 200 US stocks</b> "
            "(phase → 20/60/120/200-day S/R ladder → 30/60/90/197-day backtest windows), "
            "producing BUY / SELL / WAIT action plans with entry trigger, target, stop, and R:R ratio. "
            "<b>All numbers are computed deterministically by Python on T-1 close OHLC — zero LLM hallucination</b>; "
            "each signal strategy also carries its own 60-day historical win rate (n≥5 only). "
            "<b style=\"color: var(--bull);\">T+1 / T+3 / T+5 short-cycle horizons (net of Futu commission-free real costs: HK 0.25% / US 0.05% per round-trip)</b>: "
            "T+1 +0.35% / T+3 +0.80% / T+5 +1.08% (on US$20k size). "
            "<b style=\"color: var(--amber);\">T+10 swing result (+3.02% net) — statistically the strongest window "
            "(4.39 t-statistic passes deflated Sharpe Ratio correction), but it is a swing horizon — your call based on style and lifestyle.</b>"
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


def pick_plan_edge(bt, plan_strategy):
    """2026-08-28 methodology fix: TODAY'S plan strategy's own backtest stats.

    Home cards used to badge a BUY plan with "SELL_R1 86% win" from a different
    strategy. Only the plan strategy's own window stats (n>=5) qualify now.
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
            return {
                "strategy": plan_strategy,
                "window": w,
                "win_pct": data.get("win_pct", 0),
                "n": data.get("n", 0),
                "total_pnl": data.get("total_pnl", 0),
            }
    return None


def collect_actionables(market: str, top_n: int = 5):
    """Pick top N actionable tickers (BUY or SELL with high win%).

    2026-08-28: selection + display both use the PLAN strategy's own win%
    (n>=5 floor). A card is only picked if today's advised strategy itself has
    >=50% historical win rate — no more cross-strategy win% laundering.
    """
    base = HK_OUT if market == "HK" else US_OUT
    universes_path = REPO / "hk_universe_200.json" if market == "HK" else US_OUT / "us_top200_fresh.json"
    if not universes_path.exists():
        return []
    universe = json.load(open(universes_path))

    rows = []
    for tk in universe:
        tj = load_ticker_json(tk, market)
        if not tj:
            continue
        ap = tj.get("action_plan", {})
        verdict = ap.get("verdict", "WAIT")
        if verdict == "WAIT":
            continue
        bt = load_backtest(tk)
        plan_edge = pick_plan_edge(bt, ap.get("strategy"))
        if not plan_edge or plan_edge["win_pct"] < 50:
            continue

        last_bar = tj.get("last_bar", {})
        rows.append({
            "ticker": tk,
            "name": tj.get("name", ""),
            "last": last_bar.get("C"),
            "chg": last_bar.get("chg_pct"),
            "verdict": verdict,
            "trigger": ap.get("trigger_price"),
            "target": ap.get("target_price"),
            "stop": ap.get("stop_price"),
            "edge": plan_edge,
            "phase": tj.get("phase", ""),
            "market": market,
        })

    # Sort: by win% desc, then total_pnl desc
    rows.sort(key=lambda r: (-r["edge"]["win_pct"], -r["edge"]["total_pnl"]))
    return rows[:top_n]


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
          <div class="signal-name" style="margin-top: var(--sp-2);">本策略歷史: {edge["strategy"]} · {edge["win_pct"]:.0f}% win ({edge["window"]}d, n={edge["n"]})</div>
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


def build_home_page():
    today = t_minus_1("HK")
    # 2026-10-02: JP traded 10-01 while HK/US last closed 09-30. A single date
    # in the header would be wrong for two of three markets, so print each.
    _hk, _us, _jp = data_asof("HK"), data_asof("US"), data_asof("JP")
    t1_label = f"{_hk} · US {_us}" + (f" · JP {_jp}" if _jp != _hk else "")
    hk_top = collect_actionables("HK", top_n=3)
    us_top = collect_actionables("US", top_n=2)
    actionable = hk_top + us_top
    stats = collect_phase_stats()

    phase_dist = stats.get("phase", {})

    # 2026-08-30: i18n strings for hero (built once, used in template below)
    subtitle = T("home_subtitle_zh")
    lede = T("home_lede_zh")

    cards = "".join(render_signal_card(r) for r in actionable)

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
<meta property="og:description" content="AI 港美股多週期 AI 交易決策儀表板 · 200+200 隻主流股票 · T-1 數據 · 10 步價格行為框架 · 4 個持倉期 backtest（T+1 / T+3 / T+5 / T+10）。">
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
      "description": "HK + US multi-horizon AI trading decision dashboard. 200 HK + 200 US stocks, daily 10-step price-action signals from T-1 OHLC data. T+1 / T+3 / T+5 / T+10 horizons backtested net of Futu commission-free real costs (HK 0.25% / US 0.05% per round-trip). Educational use only.",
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
      "description": "Daily AI-driven HK and US stock signal engine using a 10-step price-action framework (5-phase market regime + 4-tier support/resistance ladder + 4 action strategies with T+1/T+3/T+5/T+10 backtest horizons).",
      "offers": {{
        "@type": "Offer",
        "price": "0",
        "priceCurrency": "USD"
      }},
      "featureList": [
        "200 HK stocks + 200 US stocks daily signals",
        "10-step price-action framework (phase / S/R / strategy / backtest)",
        "T+1 / T+3 / T+5 / T+10 multi-horizon backtest (Futu HK 0.25% / US 0.05% cost model)",
        "Anomaly filter (Futu capital-flow signals)",
        "Plan-strategy 60-day win rate gate (MED ≥50%, HIGH ≥60%)"
      ]
    }}
  ]
}}
</script>
<link rel="stylesheet" href="/static/fonts-local.css">
<link rel="stylesheet" href="/leeks.css?v={__import__('datetime').date.today().isoformat()}">
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
      <a href="/faq.html">FAQ</a>
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
      <span>200 HK + 200 US</span>
      <span>4 種策略 × 4 窗口回測</span>
      <span>Updated {datetime.now().strftime('%H:%M HKT')}</span>
    </div>
  </div>
</div>
<section>
  <div class="container">
    <div class="section-head">
      <h2>{T("home_today_actionable")}</h2>
      <span class="section-meta">{len(actionable)} of {stats.get("BUY", 0) + stats.get("SELL", 0)} · Updated {datetime.now().strftime('%H:%M HKT')}</span>
    </div>
    <div class="card-grid" style="grid-template-columns: 1fr;">
      {cards if cards else '<div class="card text-dim">No actionable signals yet — run refresh pipeline</div>'}
    </div>
  </div>
</section>

<section>
  <div class="container">
    <div class="section-head">
      <h2>Market <em class="italic">regime</em></h2>
      <span class="section-meta">200 HK + 200 US · {t1_label}</span>
    </div>
    <div class="card-grid card-grid-2">
      <div class="card fade-in fade-in-1">
        <h3 class="card-label">Phase distribution</h3>
        <div style="margin-top: var(--sp-4); display: grid; gap: var(--sp-3);">
          <div class="flex justify-between"><span class="text-dim">Downtrend active</span><span class="mono text-bear">{phase_dist.get("downtrend_active", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">Base building</span><span class="mono text-amber">{phase_dist.get("base_building", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">Uptrend</span><span class="mono text-bull">{phase_dist.get("uptrend", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">Recovery</span><span class="mono text-amber">{phase_dist.get("downtrend_recovery", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">Range</span><span class="mono text-dim">{phase_dist.get("range", 0)}</span></div>
        </div>
      </div>

      <div class="card fade-in fade-in-2">
        <h3 class="card-label">Action distribution</h3>
        <div style="margin-top: var(--sp-4); display: grid; gap: var(--sp-3);">
          <div class="flex justify-between"><span class="text-dim">BUY</span><span class="mono text-bull">{stats.get("BUY", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">SELL</span><span class="mono text-bear">{stats.get("SELL", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">WAIT</span><span class="mono text-dim">{stats.get("WAIT", 0)}</span></div>
        </div>
      </div>
    </div>
  </div>
</section>

<section style="padding-top: 0;">
  <div class="container">
    <div class="card fade-in fade-in-3">
      <h3 class="card-label">How it works</h3>
      <div style="margin-top: var(--sp-3); display: grid; gap: var(--sp-3); font-size: var(--text-sm); color: var(--fg-2);">
        <div>① Phase 分類 (5 種) — 20d rolling</div>
        <div>② S/R ladder (4-tier)</div>
        <div>③ Action plan (4 strategies)</div>
        <div>④ Backtest 4 windows × 4 strategies</div>
        <div>⑤ Pick recent edge (30-90d)</div>
      </div>
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
