#!/usr/bin/env python3
"""
build_home.py — Build the win9you.com home page with live data.

Reads the top 5 actionable tickers (highest win% BUY or SELL) from
HK + US action_plan JSON + backtest JSON, plus market regime stats.

Output: /Users/kenken/Documents/dsa-hk/public/index.html
"""
from __future__ import annotations
import json
import shutil
from datetime import datetime, date, timedelta
from pathlib import Path


def t_minus_1() -> date:
    """Return T-1 = last trading day (skip Sat/Sun)."""
    today = date.today()
    if today.weekday() == 0:  # Monday
        return today - timedelta(days=3)  # Friday
    elif today.weekday() == 6:  # Sunday
        return today - timedelta(days=2)  # Friday
    elif today.weekday() == 5:  # Saturday
        return today - timedelta(days=1)  # Friday
    else:
        return today - timedelta(days=1)

REPO = Path("/Users/kenken/Documents/dsa-hk")
PUBLIC = REPO / "public"
HK_OUT = Path("/Users/kenken/Documents/Minimax/charts/hk200")
US_OUT = Path("/Users/kenken/Documents/Minimax/charts/us200")
BACKTEST_OUT = Path("/tmp/backtest_out")


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
            if n >= 3 and tot > 0:
                return {
                    "strategy": strat,
                    "window": w,
                    "win_pct": win,
                    "n": n,
                    "total_pnl": tot,
                }
    return None


def collect_actionables(market: str, top_n: int = 5):
    """Pick top N actionable tickers (BUY or SELL with high win%)."""
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
        edge = pick_recent_edge(bt)
        if not edge or edge["win_pct"] < 50:
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
            "edge": edge,
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
    action_cls = "bull" if verdict == "BUY" else "bear"
    safe = row["ticker"].replace(".", "_")
    market = row["market"]
    detail_url = f"/{market.lower()}200/ticker/{safe}/"
    edge = row["edge"]
    return f'''
      <a href="{detail_url}" class="signal-card {cls}">
        <div>
          <div class="signal-ticker">{row["ticker"]}</div>
          <div class="signal-name">{row["name"]}</div>
        </div>
        <div>
          <div class="signal-name">{edge["strategy"]} · {edge["win_pct"]:.0f}% win ({edge["window"]}d, n={edge["n"]})</div>
        </div>
        <div class="signal-action {action_cls}">{verdict}</div>
        <div class="signal-detail">{fmt(row["trigger"])} → <b>{fmt(row["target"])}</b> · stop {fmt(row["stop"])}</div>
      </a>'''


def build_home_page():
    today = t_minus_1()
    hk_top = collect_actionables("HK", top_n=3)
    us_top = collect_actionables("US", top_n=2)
    actionable = hk_top + us_top
    stats = collect_phase_stats()

    phase_dist = stats.get("phase", {})
    hk_dt = phase_dist.get("downtrend_active", 0)
    hk_total = sum(phase_dist.values()) or 1

    cards = "".join(render_signal_card(r) for r in actionable)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Leeks Terminal · 港美股即日鮮交易信號</title>
<meta name="description" content="AI 港美股即日鮮交易信號儀表板 · 200+200 隻主流股票 · T-1 數據 · 10 步價格行為框架 · 開市前 5 分鐘決策。">
<meta name="theme-color" content="#0a0e1a">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght,SOFT@0,9..144,300..600,0..100;1,9..144,300..600,0..100&family=JetBrains+Mono:wght@400;500;600;700&family=Manrope:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="/leeks.css?v=2026-08-25">
<script>
(function(){{
  const t = localStorage.getItem('leeks-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', t);
}})();
</script>
</head>
<body>

<header class="site-header">
  <nav class="nav">
    <a href="/" class="nav-brand"><span class="leek">L</span>eeks <em class="italic">Terminal</em></a>
    <div class="nav-links">
      <a href="/" class="active">Home</a>
      <a href="/hk200/">HK Signals</a>
      <a href="/us200/">US Signals</a>
      <a href="/pair-trades/">Pair Trades</a>
      <a href="/methodology">Methodology</a>
    </div>
    <div class="nav-meta">
      <span class="live-dot"></span>
      T-1 · {today.isoformat()}
      <button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle theme">
        <span class="icon" id="themeIcon">●</span>
        <span id="themeLabel">DARK</span>
      </button>
    </div>
  </nav>
</header>
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

<section class="hero">
  <div class="container">
    <div class="hero-grid">
      <div class="fade-in">
        <div class="kicker">Day-Trade · 即日鮮 · T-1 Data</div>
        <h1 class="hero-title">
          開市前 5 分鐘<br>
          知邊隻 <em class="italic">做</em>，<br>
          邊隻 <em class="italic">唔做</em>。
        </h1>
        <p class="lede">
          AI 港美股 <b>200+200</b> 隻主流股票，10 步價格行為框架 + backtested signal
          — 每一隻都答到你：<b>買 / 沽 / 觀望</b>，trigger 喺邊，target 喺邊，止蝕喺邊。
        </p>
        <div class="hero-meta">
          <span>Source: <b>Futu OpenD</b></span>
          <span>Update: <b>On-demand</b></span>
          <span>Method: <b>OHLC only · No LLM hallucination</b></span>
        </div>
        <div class="btn-row mt-4">
          <a href="/hk200/" class="btn btn-primary">View HK Signals →</a>
          <a href="/us200/" class="btn">View US Signals →</a>
          <a href="/methodology" class="btn btn-ghost">How it works</a>
        </div>
      </div>

      <div class="fade-in fade-in-2">
        <div class="card mb-3">
          <h4>Today · {today.isoformat()} (T-1 close)</h4>
          <div class="hero-stat mt-3">
            <div class="label">Actionable signals</div>
            <div class="value text-bull">{stats.get("BUY", 0) + stats.get("SELL", 0)}</div>
            <div class="delta text-dim">of {sum(stats.get(k, 0) for k in ("BUY", "SELL", "WAIT"))} total</div>
          </div>
          <div class="hero-stat mt-3">
            <div class="label">High confidence (≥70%)</div>
            <div class="value text-accent">{sum(1 for r in actionable if r["edge"]["win_pct"] >= 70)}</div>
          </div>
          <div class="hero-stat mt-3">
            <div class="label">Regime</div>
            <div class="value text-bear" style="font-size: var(--text-lg);">🔴 {hk_dt / hk_total * 100:.0f}% Downtrend</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<section style="padding-top: 0;">
  <div class="container">
    <div class="section-head">
      <h2>今日 <em class="italic">actionable</em></h2>
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
      <span class="section-meta">200 HK + 200 US · {today.isoformat()}</span>
    </div>
    <div class="card-grid card-grid-3">
      <div class="card fade-in fade-in-1">
        <h4>Phase distribution</h4>
        <div style="margin-top: var(--sp-4); display: grid; gap: var(--sp-3);">
          <div class="flex justify-between"><span class="text-dim">Downtrend active</span><span class="mono text-bear">{phase_dist.get("downtrend_active", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">Base building</span><span class="mono text-amber">{phase_dist.get("base_building", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">Uptrend</span><span class="mono text-bull">{phase_dist.get("uptrend", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">Recovery</span><span class="mono text-amber">{phase_dist.get("downtrend_recovery", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">Range</span><span class="mono text-dim">{phase_dist.get("range", 0)}</span></div>
        </div>
      </div>

      <div class="card fade-in fade-in-2">
        <h4>Action distribution</h4>
        <div style="margin-top: var(--sp-4); display: grid; gap: var(--sp-3);">
          <div class="flex justify-between"><span class="text-dim">BUY</span><span class="mono text-bull">{stats.get("BUY", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">SELL</span><span class="mono text-bear">{stats.get("SELL", 0)}</span></div>
          <div class="flex justify-between"><span class="text-dim">WAIT</span><span class="mono text-dim">{stats.get("WAIT", 0)}</span></div>
        </div>
      </div>

      <div class="card fade-in fade-in-3">
        <h4>How it works</h4>
        <div style="margin-top: var(--sp-4); display: grid; gap: var(--sp-3); font-size: var(--text-sm); color: var(--fg-2);">
          <div>① Phase 分類 (5 種) — 20d rolling</div>
          <div>② S/R ladder (4-tier)</div>
          <div>③ Action plan (4 strategies)</div>
          <div>④ Backtest 4 windows × 4 strategies</div>
          <div>⑤ Pick recent edge (30-90d)</div>
        </div>
      </div>
    </div>
  </div>
</section>

<div class="container">
  <div class="disclaimer">
    <b>⚠ Disclaimer</b> · All content is informational and educational only. Not investment advice.
    Trading involves substantial risk. Past backtest performance does not guarantee future results.
  </div>
</div>

<footer class="site-footer">
  <div class="container">
    <div class="row">
      <div>© 2026 Leeks Terminal · win9you.com · <a href="/methodology">Methodology</a> · <a href="/disclaimer">Disclaimer</a></div>
      <div class="mono text-dim">T-1 · {today.isoformat()} · Futu OpenD</div>
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
