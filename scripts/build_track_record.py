#!/usr/bin/env python3
"""
build_track_record.py — /track-record/: the bar to beat, and your own log.

Why this page exists
--------------------
The site publishes a measured claim: on the same stock, the same day, the same
holding period, entering on a v2 signal beats entering at random by roughly
+1 pp per trade (data/control_test.csv, time-split train/test). That is a claim
about a population of signals, not about a person.

It can only be falsified by recorded trades. The honest state today is zero
recorded trades, and this page says so rather than seeding a flattering sample.

Two rules this builder enforces:

  1. The benchmark is READ from data/control_test.csv. It is never typed in
     here, so the page cannot drift from the measurement that produced it.
  2. The user's log is READ from data/track_record.json, which ships as an empty
     list. An empty log renders as an explicit empty state — the page never
     invents trades, averages, or a win rate.

Output: public/track-record/index.html

The directory name must match the URL people link to. build_sitemap.py derives
its URLs from the directory path, so an underscore here published
/track_record/ in the sitemap while every nav link said /track-record/ — a 404
in the one file search engines read to discover the site.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
CONTROL = REPO / "data" / "control_test.csv"
LOG = REPO / "data" / "track_record.json"
OUT = REPO / "public" / "track-record" / "index.html"

# Round-trip cost by market, same constants v2engine/core.py charges.
FEES = {"us200": 0.0005, "hk200": 0.0025, "jp200": 0.0010}


def load_bench() -> list[dict]:
    """Out-of-sample (test) arm comparison, straight from the CSV."""
    if not CONTROL.exists():
        return []
    out: list[dict] = []
    with CONTROL.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append({
                "market": r["market"],
                "arm": r["arm"],
                "train_n": int(r["train_n"]),
                "train_avg": float(r["train_avg%"]),
                "test_n": int(r["test_n"]),
                "test_avg": float(r["test_avg%"]),
                "test_t": float(r["test_t"]),
            })
    return out


def load_log() -> list[dict]:
    if not LOG.exists():
        return []
    try:
        d = json.load(open(LOG, encoding="utf-8"))
    except Exception:
        return []
    trades = d.get("trades") if isinstance(d, dict) else d
    return trades if isinstance(trades, list) else []


def summarise(trades: list[dict]) -> dict:
    """Net per-trade return after the market's round-trip cost."""
    rows = []
    for t in trades:
        mkt = t.get("market", "")
        try:
            entry, exit_ = float(t["entry"]), float(t["exit"])
        except (KeyError, TypeError, ValueError):
            continue
        if entry <= 0:
            continue
        fee = FEES.get(mkt, float(t.get("fees_pct", 0.0)))
        rows.append({**t, "net_pct": (exit_ / entry - 1) - fee, "fee": fee})
    n = len(rows)
    wins = [r for r in rows if r["net_pct"] > 0]
    return {
        "rows": rows, "n": n,
        "avg": sum(r["net_pct"] for r in rows) / n * 100 if n else 0.0,
        "win_rate": len(wins) / n * 100 if n else 0.0,
        "total": sum(r["net_pct"] for r in rows) * 100 if n else 0.0,
        "best": max((r["net_pct"] for r in rows), default=0.0) * 100,
        "worst": min((r["net_pct"] for r in rows), default=0.0) * 100,
        "by_market": Counter(r.get("market", "?") for r in rows),
    }


def render(bench: list[dict], trades: list[dict]) -> str:
    sys.path.insert(0, str(REPO / "scripts"))
    from build_static import shell, _CSS_VERSION

    # ---- Section 1: the bar, measured ------------------------------------
    by_mkt: dict[str, dict[str, dict]] = {}
    for b in bench:
        by_mkt.setdefault(b["market"], {})[b["arm"]] = b
    bench_rows, bench_notes = [], []
    for mkt, arms in sorted(by_mkt.items()):
        v2, ctl = arms.get("v2"), arms.get("control")
        if not (v2 and ctl):
            continue
        gap = v2["test_avg"] - ctl["test_avg"]
        col = "var(--bull)" if gap > 0 else "var(--bear)"
        bench_rows.append(
            f'<tr><td class="mono">{mkt}</td>'
            f'<td class="mono">{v2["test_n"]:,}</td>'
            f'<td class="mono">{v2["test_avg"]:+.3f}%</td>'
            f'<td class="mono">{ctl["test_n"]:,}</td>'
            f'<td class="mono">{ctl["test_avg"]:+.3f}%</td>'
            f'<td class="mono" style="color:{col};"><b>{gap:+.3f} pp</b></td></tr>'
        )
        bench_notes.append(
            f"<li><b>{mkt}</b>：樣本外 {v2['test_n']:,} 個 v2 訊號平均 "
            f"<b>{v2['test_avg']:+.3f}%</b>，同日同股隨便入場嘅 "
            f"{ctl['test_n']:,} 個對照平均 <b>{ctl['test_avg']:+.3f}%</b> → "
            f"差距 <b style='color:{col};'>{gap:+.3f} pp/單</b>"
            f"（train→test 時間切分，test t={v2['test_t']:.2f} / {ctl['test_t']:.2f}）。</li>"
        )

    s = summarise(trades)

    # ---- Section 2: your log ---------------------------------------------
    if s["n"]:
        trs = "".join(
            f'<tr><td class="mono">{r.get("date","")}</td>'
            f'<td class="mono">{r.get("market","")}</td>'
            f'<td class="mono">{r.get("ticker","")}</td>'
            f'<td class="mono cell-right">{r["entry"]:.4g}</td>'
            f'<td class="mono cell-right">{r["exit"]:.4g}</td>'
            f'<td class="mono cell-right">{r["fee"]*100:.2f}%</td>'
            f'<td class="mono cell-right" style="color:'
            f'{"var(--bull)" if r["net_pct"]>0 else "var(--bear)"};">'
            f'{r["net_pct"]*100:+.2f}%</td></tr>'
            for r in s["rows"])
        log_block = f"""
<table class="tr-table">
<thead><tr><th>日期</th><th>市場</th><th>代號</th><th>入場</th><th>離場</th>
<th>成本</th><th>淨值</th></tr></thead>
<tbody>{trs}</tbody>
</table>
<div class="tr-stats">
  <div class="tr-stat"><span>單數</span><b>{s['n']}</b></div>
  <div class="tr-stat"><span>勝率</span><b>{s['win_rate']:.1f}%</b></div>
  <div class="tr-stat"><span>每單平均</span><b>{s['avg']:+.3f}%</b></div>
  <div class="tr-stat"><span>最好</span><b>{s['best']:+.2f}%</b></div>
  <div class="tr-stat"><span>最差</span><b>{s['worst']:+.2f}%</b></div>
</div>
<p style="font-size:var(--text-sm);color:var(--dim);margin-top:var(--sp-3);">
  {s['n']} 單嘅樣本遠細過上面基準嗰 {sum(a['v2']['test_n'] for a in by_mkt.values()):,} 個訊號 ——
  <b>唔好由呢個數字下結論</b>。要推翻或者確認上面嘅 +1 pp，最少要幾百單、而且要包括
  冇成交嗰啲（限價單唔中就係冇賺，亦都冇蝕）。</p>
"""
    else:
        log_block = """
<div class="tr-empty">
<b>暫時 0 單記錄。</b>
<p style="margin:8px 0 0">呢個係刻意留白，唔係 bug。呢一頁唯一嘅目的係記錄<b>真實</b>成交 ——
而家未有一單。補資料方法：打開 <code>data/track_record.json</code>，喺 <code>trades</code>
陣列每次成交加一行：</p>
<pre>{"trades": [
  {"date": "2026-10-08", "market": "us200", "ticker": "AAPL",
   "entry": 332.89, "exit": 341.20, "note": "S1 限價單成交，第 10 個交易日收市離場"}
]}</pre>
<p style="margin:8px 0 0"><code>market</code> 用 <code>us200</code> / <code>jp200</code> /
<code>hk200</code>；成本會自動按各市場實際費率扣除，唔使自己填
（美股 0.05%、日股 0.10%、港股 0.25% round-trip）。
下次跑 <code>python3 scripts/build_track_record.py</code> 就會出現喺上面。</p>
</div>
"""

    body = f"""
<h1>📓 Track record</h1>
<p class="lede" style="color:var(--fg-2);max-width:820px;">
  呢頁分兩半：上面係<b>實測出嚟嘅基準</b>（由 <code>data/control_test.csv</code> 直接讀出嚟，
  唔係打字入去）；下面係<b>你自己嘅實盤記錄</b>（由 <code>data/track_record.json</code> 讀）。
  兩者唔會混為一談 —— 基準係統計結論，記錄係真金白銀。
</p>

<div class="cav" style="border-left:4px solid var(--red);background:var(--red-dim);">
<strong>先講清楚呢個 +1 pp 係乜。</strong><br>
基準答嘅係「<b>揀股</b>有冇價值」：同一日、同一隻股、同一持倉期、同一成本，
分別用 v2 規則入場 vs 隨便入場。<b>佢唔係</b>話「你照抄可以賺 1%」。
實盤有三重折讓：入場係 S1 限價單（唔係每單都成交）、只有 6/176 個訊號離觸發位
喺 0.5% 之內、2–3 倉上限之下揀邊幾個本身未驗證。<b>唯一能證偽呢個結論嘅方法
就係下面嗰個記錄</b>，唔係再跑多個回測。
</div>

<h2 style="margin-top:var(--sp-5);font-size:var(--text-md);">基準（實測 · 樣本外）</h2>
<table class="tr-table">
<thead><tr><th>市場</th><th>v2 訊號數</th><th>v2 平均/單</th>
<th>對照數</th><th>對照平均/單</th><th>差距</th></tr></thead>
<tbody>{''.join(bench_rows)}</tbody>
</table>
<ul style="color:var(--fg-2);font-size:var(--text-sm);line-height:1.7;margin-top:var(--sp-3);">
{''.join(bench_notes)}
</ul>

<div class="cav">
<strong>同時要記住相反嗰個結論。</strong>
同一個引擎對「同一批 200 隻股票等權持有」嘅基準，組合層面 <b>冇 alpha</b>
（美股 v2 27.18% vs 等權 27.4%，差 −0.2 pp/年）。所以正確用法係：
<b>呢個係一個揀股工具，唔係一個要跟嘅組合</b>。兩個基準唔同，答案就唔同。
</div>

<h2 style="margin-top:var(--sp-5);font-size:var(--text-md);">你嘅實盤記錄</h2>
{log_block}

<div class="cav">
<strong>呢頁唔會由網站落單。</strong>
訊號只係通知，落手永遠喺你嘅券商 app。記錄呢一頁亦都係人手填 ——
冇任何自動對接會幫你「記低」一單你冇做過嘅交易。
</div>
"""
    return shell(
        title="Track record · 實盤記錄 vs 實測基準 · Leeks Terminal",
        body_html=body,
        active_path="/track-record/",
        description="The measured bar the v2 entry rule has to clear (same stock, same day, same holding period vs a random-entry control), plus your own logged trades. Read from data/control_test.csv and data/track_record.json.",
        canonical="https://www.win9you.com/track-record/",
    ).replace(
        "</main>",
        """<style>
.tr-table { width:100%; border-collapse:collapse; font-size:var(--text-sm); font-family:var(--font-mono); margin-top:var(--sp-3); overflow-x:auto; display:block; }
.tr-table th, .tr-table td { padding:7px 10px; border-bottom:1px solid var(--border); text-align:left; white-space:nowrap; }
.tr-table th { color:var(--dim); font-weight:600; }
.cell-right { text-align:right; }
.tr-stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:10px; margin-top:var(--sp-4); }
.tr-stat { background:var(--panel); border:1px solid var(--border); border-radius:6px; padding:10px; text-align:center; }
.tr-stat span { display:block; font-size:var(--text-xs); color:var(--dim); text-transform:uppercase; letter-spacing:.5px; }
.tr-stat b { font-family:var(--font-mono); font-size:var(--text-lg); }
.tr-empty { padding:var(--sp-4); background:var(--panel); border:1px solid var(--border); border-radius:6px; font-size:var(--text-sm); }
.tr-empty pre { background:var(--bg-2); padding:var(--sp-3); border-radius:4px; overflow-x:auto; font-size:var(--text-xs); }
.cav { padding:var(--sp-3) var(--sp-4); background:var(--amber-dim); border-left:3px solid var(--amber); border-radius:4px; margin:var(--sp-3) 0; font-size:var(--text-sm); }
</style>
</main>""",
    )


def main() -> None:
    bench, trades = load_bench(), load_log()
    if not bench:
        print(f"⚠  {CONTROL} missing or empty — refusing to publish a hand-typed "
              f"benchmark. Run the control test first.")
        raise SystemExit(1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(bench, trades), encoding="utf-8")
    arms = {b["arm"] for b in bench}
    print(f"→ {OUT} ({OUT.stat().st_size:,} bytes)")
    print(f"  benchmark from {CONTROL.name}: arms={sorted(arms)}, "
          f"{len(bench)} rows")
    print(f"  user log from {LOG.name}: {len(trades)} trade(s)")


if __name__ == "__main__":
    main()
