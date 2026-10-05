#!/usr/bin/env python3
"""build_insights_radar.py — wire /insights Market Radar to real data.

2026-10-05: every number in the Market Radar block was hand-typed into a
hand-edited insights.html and never touched by any build script. The site
states "全部數字由 Python 確定性計算，零 LLM 幻覺" on every page, while this
block carried:

    HSI Close 18,420   (real 2026-10-02 close: 23,972)  -30%
    SPX Close 5,620    (real: 7,723)                    -27%
    NKY Close 38,210   (real: 68,309)                   -79%
    USD/JPY  142.8     (real: 157.9)

Nikkei alone was wrong by 79%. The 2026-10-02 audit had already replaced a
stale static HSI with the generated INDEX_SNAPSHOT strip at the top of the
page, which made the contradiction visible: the strip said 23,972 and the
card 30 lines below said 18,420.

This regenerates the whole radar from two sources:
  * indices / FX / volatility — Yahoo Finance, same source as the top strip
  * signal counts, R:R, regime badge, breadth, volume regime — the site's own
    HK/US/JP T-1 snapshots under charts/ and data/jp200/

Field sourcing notes:
  * "VIX HK" had no source at all (^VHSI is not on Yahoo). It is replaced by
    HSCEI, a real HK index level, rather than kept as an invented number.
  * "Exporters: PRESSURED" is editorial. It is now a stated rule — yen
    stronger over 5 sessions (JPY=X down) -> PRESSURED — so the label is
    reproducible instead of asserted.

Idempotent: injects between RADAR markers, or wraps the first radar-grid.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PUB = REPO / "public"
PAGE = PUB / "insights.html"

# 2026-10-05: reuse the single JP/HK schema normaliser instead of each script
# re-implementing (and getting wrong) the flat-vs-nested last_bar read.
sys.path.insert(0, str(REPO / "scripts"))
try:
    from build_dashboard import snapshot_quote
except Exception:  # pragma: no cover - never break the build over a guard
    def snapshot_quote(tj: dict) -> dict:  # type: ignore
        lb = tj.get("last_bar") or {}
        return {
            "date": lb.get("date") or tj.get("asof") or "",
            "last": lb.get("C") or tj.get("close") or None,
            "chg_pct": lb.get("chg_pct") if lb.get("chg_pct") is not None else tj.get("chg_pct"),
            "name": tj.get("name") or "",
        }

MARK_START = "<!-- RADAR:START -->"
MARK_END = "<!-- RADAR:END -->"

# market key -> (universe json, snapshot dir, index symbol, index label, flag)
MARKETS = {
    "HK": ("hk_universe_200.json", REPO / "charts/hk200", "^HSI", "HSI Close", "🇭🇰", "Hong Kong"),
    "US": (REPO / "charts/us200/us_top200_fresh.json", REPO / "charts/us200", "^GSPC", "SPX Close", "🇺🇸", "United States"),
    "JP": ("jp_universe_200.json", REPO / "data/jp200", "^N225", "NKY Close", "🇯🇵", "Japan"),
}

PHASE_BADGE = {
    "uptrend": ("UPTREND", "bull"),
    "base_building": ("BASE BUILDING", "amber"),
    "downtrend_active": ("DOWNTREND ACTIVE", "bear"),
    "downtrend_recovery": ("RECOVERY", "amber"),
    "range": ("RANGE", ""),
}
PHASE_SHORT = {
    "uptrend": "Uptrend",
    "base_building": "Base",
    "downtrend_active": "Down",
    "downtrend_recovery": "Recovery",
    "range": "Range",
}


def _load_universe(spec) -> list[str]:
    p = REPO / spec if isinstance(spec, str) else spec
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return []


def _snapshot(tk: str, mdir: Path) -> dict | None:
    safe = tk.replace(".", "_")
    for cand in (mdir / f"{safe}.json",):
        if cand.exists():
            try:
                return json.load(open(cand, encoding="utf-8"))
            except Exception:
                return None
    return None


def market_stats(mkey: str) -> dict:
    uni_spec, mdir, _idx, _lab, _flag, _name = MARKETS[mkey]
    universe = _load_universe(uni_spec)
    buy = sell = 0
    rrs: list[float] = []
    phases: Counter = Counter()
    up = down = flat = 0
    for tk in universe:
        d = _snapshot(tk, mdir)
        if not d:
            continue
        ph = d.get("phase")
        if ph:
            phases[ph] += 1
        ap = d.get("action_plan") or {}
        v = ap.get("verdict")
        if v == "BUY":
            buy += 1
        elif v == "SELL":
            sell += 1
        rr = ap.get("rr_ratio")
        if v in ("BUY", "SELL") and isinstance(rr, (int, float)) and rr > 0:
            rrs.append(float(rr))
        # 2026-10-05: JP snapshots are flat (chg_pct at top level), so this
        # silently counted every JP name as "flat" in the advance/decline
        # gauge. snapshot_quote() reads both shapes.
        chg = snapshot_quote(d)["chg_pct"]
        if isinstance(chg, (int, float)):
            if chg > 0:
                up += 1
            elif chg < 0:
                down += 1
            else:
                flat += 1

    # Volume regime from the published ohlc series (last bar vs 20-bar mean).
    # 2026-10-05: the directory is public/hk200, not public/hk — keying off
    # mkey.lower() silently found nothing and every card showed "—".
    vol_ratio = None
    ohlc_dir = PUB / {"HK": "hk200", "US": "us200", "JP": "jp200"}[mkey] / "ohlc"
    ratios = []
    for p in sorted(ohlc_dir.glob("*_ohlc.json"))[:60]:
        try:
            bars = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(bars, list) or len(bars) < 21:
            continue
        vols = [b.get("volume") or 0 for b in bars]
        if vols[-1] and sum(vols[-21:-1]) / 20:
            ratios.append(vols[-1] / (sum(vols[-21:-1]) / 20))
    if ratios:
        vol_ratio = sum(ratios) / len(ratios)

    if vol_ratio is None:
        vol_label, vol_color, vol_sub = "—", "", "數據源不足"
    elif vol_ratio >= 1.15:
        vol_label, vol_color, vol_sub = "HIGH", "bull", f"{vol_ratio:.2f}× 20日均量"
    elif vol_ratio <= 0.85:
        vol_label, vol_color, vol_sub = "LOW", "amber", f"{vol_ratio:.2f}× 20日均量"
    else:
        vol_label, vol_color, vol_sub = "NORMAL", "", f"{vol_ratio:.2f}× 20日均量"

    if up + down:
        breadth = (up - down) / (up + down)
    else:
        breadth = None

    return {
        "n": len(universe),
        "buy": buy,
        "sell": sell,
        "avg_rr": (sum(rrs) / len(rrs)) if rrs else None,
        "phase": phases.most_common(1)[0][0] if phases else None,
        "breadth": breadth,
        "up": up,
        "down": down,
        "vol_label": vol_label,
        "vol_color": vol_color,
        "vol_sub": vol_sub,
    }


def fetch_indices() -> dict:
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf
    import pandas as pd

    want = {
        "^HSI": "HSI", "^GSPC": "SPX", "^N225": "NKY",
        "JPY=X": "USDJPY", "^VIX": "VIX",
    }
    out = {}
    for sym, key in want.items():
        try:
            d = yf.Ticker(sym).history(period="1mo", auto_adjust=True)
            if d is None or d.empty:
                continue
            d.index = pd.to_datetime(d.index).tz_localize(None)
            if sym == "JPY=X":
                d = d[[ts.weekday() < 5 for ts in d.index]]
                if d.empty:
                    continue
            out[key] = {
                "val": float(d["Close"].iloc[-1]),
                "day": d.index[-1].strftime("%Y-%m-%d"),
            }
            if key == "USDJPY" and len(d) >= 6:
                out["JPY5"] = (float(d["Close"].iloc[-1] / d["Close"].iloc[-6] - 1) * 100)
        except Exception:
            continue
    return out


def fetch_vhsi() -> dict | None:
    """VHSI (Hang Seng Volatility Index) from J.P. Morgan's public chart feed.

    Yahoo has no ^VHSI, so the old card's "VIX HK 22.4" had no source at all.
    This endpoint is the same JSON the site's own chart page consumes:

        /zh-hk/data/chart/underlyingChart/code/VHSI/period/0/delay/1

    Prefer this over scraping the rendered page: the page's own "收市價" field
    lags by a session. Read live it showed 16.99 for 2026-10-02 while its
    open (18.53) and high (19.49) already matched the 10-02 bar — 16.99 was
    actually the 09-30 close. The bar series gives the right answer, 19.34.

    Returns None on any failure so the card degrades to "—" instead of
    printing an invented number.
    """
    import urllib.request
    from datetime import datetime, timezone

    url = ("https://www.jpmhkwarrants.com/zh-hk/data/chart/"
           "underlyingChart/code/VHSI/period/0/delay/1")
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
        "Referer": "https://www.jpmhkwarrants.com/",
        "Accept": "application/json,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            payload = json.load(r)
        bars = payload["mainData"]["underlying"]
        if not bars:
            return None
        last = bars[-1]
        day = datetime.fromtimestamp(last["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        return {"val": float(last["last"]), "day": day}
    except Exception:
        return None


def fmt(v: float, dp: int = 0) -> str:
    return f"{v:,.{dp}f}"


def _metric(key: str, val: str, color: str = "", sub: str = "") -> str:
    style = f' style="color: var(--{color})"' if color else ""
    s = f'<div class="metric"><span class="key">{key}</span><span class="val"{style}>{val}</span>'
    if sub:
        s += f'<span class="metric-sub" style="font-size:0.7rem;opacity:0.55;margin-left:8px">{sub}</span>'
    return s + "</div>"


def build_radar(idx: dict, st: dict, asof: str, vhsi_box: dict | None = None) -> str:
    cards = []

    # ---- Hong Kong ----
    hk = st["HK"]
    badge, bcls = PHASE_BADGE.get(hk["phase"], ("—", ""))
    vhsi = vhsi_box or {}
    vhsi_val = vhsi.get("val")
    # Only quote VHSI if its bar is the same session as everything else —
    # a lagging feed must show a gap, not a plausible wrong number.
    vhsi_ok = vhsi_val is not None and vhsi.get("day") == asof
    hk_metrics = [
        _metric("HSI Close", fmt(idx["HSI"]["val"]) if "HSI" in idx else "—",
                "bear" if hk["phase"] == "downtrend_active" else "bull",
                idx.get("HSI", {}).get("day", "")),
        _metric("Signal Count", f"BUY {hk['buy']} / SELL {hk['sell']}",
                "bull" if hk["buy"] >= hk["sell"] else "bear"),
        _metric("Avg R:R", f"{hk['avg_rr']:.2f}" if hk["avg_rr"] else "—"),
        _metric("Volume Regime", hk["vol_label"], hk["vol_color"], hk["vol_sub"]),
        _metric("VHSI", f"{vhsi_val:.2f}" if vhsi_ok else "—",
                "bear" if vhsi_ok and vhsi_val >= 25 else ("bull" if vhsi_ok and vhsi_val < 20 else "amber"),
                (vhsi.get("day", "") if vhsi_ok else "數據源不可用")),
    ]
    cards.append(
        f'<div class="radar-card"><div class="market-label"><span class="flag">🇭🇰</span> Hong Kong</div>'
        f'<span class="regime {bcls}">{badge}</span>' + "".join(hk_metrics) + "</div>"
    )

    # ---- United States ----
    us = st["US"]
    badge, bcls = PHASE_BADGE.get(us["phase"], ("—", ""))
    vix = idx.get("VIX", {}).get("val")
    vix_col = "bull" if vix is not None and vix < 20 else ("amber" if vix is not None else "")
    us_metrics = [
        _metric("SPX Close", fmt(idx["SPX"]["val"]) if "SPX" in idx else "—",
                "bull", idx.get("SPX", {}).get("day", "")),
        _metric("Signal Count", f"BUY {us['buy']} / SELL {us['sell']}",
                "bull" if us["buy"] >= us["sell"] else "bear"),
        _metric("Avg R:R", f"{us['avg_rr']:.2f}" if us["avg_rr"] else "—"),
        _metric("VIX", f"{vix:.2f}" if vix is not None else "—", vix_col),
        _metric("Breadth (A/D)", f"{us['breadth']:+.2f}" if us["breadth"] is not None else "—",
                "bull" if (us["breadth"] or 0) > 0 else "bear",
                f"{us['up']}升 / {us['down']}跌"),
    ]
    cards.append(
        f'<div class="radar-card"><div class="market-label"><span class="flag">🇺🇸</span> United States</div>'
        f'<span class="regime {bcls}">{badge}</span>' + "".join(us_metrics) + "</div>"
    )

    # ---- Japan ----
    jp = st["JP"]
    badge, bcls = PHASE_BADGE.get(jp["phase"], ("—", ""))
    jpy5 = idx.get("JPY5")
    if jpy5 is None:
        exp_label, exp_color, exp_sub = "—", "", ""
    else:
        # Yen stronger (JPY=X fell) squeezes exporter earnings -> PRESSURED.
        exp_label = "PRESSURED" if jpy5 < 0 else "SUPPORTED"
        exp_color = "bear" if jpy5 < 0 else "bull"
        exp_sub = f"日圓 5日 {jpy5:+.1f}%"
    jp_metrics = [
        _metric("NKY Close", fmt(idx["NKY"]["val"]) if "NKY" in idx else "—",
                "bear" if jp["phase"] == "downtrend_active" else "bull",
                idx.get("NKY", {}).get("day", "")),
        _metric("Signal Count", f"BUY {jp['buy']} / SELL {jp['sell']}",
                "bull" if jp["buy"] >= jp["sell"] else "bear"),
        _metric("Avg R:R", f"{jp['avg_rr']:.2f}" if jp["avg_rr"] else "—"),
        _metric("USD/JPY", f"{idx['USDJPY']['val']:.1f}" if "USDJPY" in idx else "—",
                "amber", idx.get("USDJPY", {}).get("day", "")),
        _metric("Exporters", exp_label, exp_color, exp_sub),
    ]
    cards.append(
        f'<div class="radar-card"><div class="market-label"><span class="flag">🇯🇵</span> Japan</div>'
        f'<span class="regime {bcls}">{badge}</span>' + "".join(jp_metrics) + "</div>"
    )

    foot = (
        f'<div style="grid-column:1/-1;font-size:0.72rem;opacity:0.55;margin-top:4px">'
        f"指數 · Yahoo Finance ｜ VHSI · J.P. Morgan 公開圖表 API ｜ 訊號 / R:R / 市場闊度 / 成交量 · 本站 T-1 快照 ｜ "
        f"最後更新 {asof} · 每次 build 自動重取</div>"
    )
    return (
        f"{MARK_START}\n"
        '<div class="radar-grid">' + "".join(cards) + foot + "</div>\n" + MARK_END
    )


def main() -> None:
    if not PAGE.exists():
        print("insights.html not found, skip")
        return
    html = PAGE.read_text(encoding="utf-8", errors="ignore")

    idx = fetch_indices()
    st = {k: market_stats(k) for k in MARKETS}

    days = [v["day"] for k, v in idx.items() if k in ("HSI", "SPX", "NKY")]
    if not days:
        print("! no index data — leaving Market Radar untouched rather than blanking it")
        return
    asof = max(set(days), key=days.count)
    if date.fromisoformat(asof).weekday() >= 5:
        print(f"! refusing to stamp non-trading day asof={asof}")
        return

    vhsi_box = fetch_vhsi()
    radar = build_radar(idx, st, asof, vhsi_box)

    if MARK_START in html:
        html = re.sub(
            re.escape(MARK_START) + r".*?" + re.escape(MARK_END),
            lambda _m: radar, html, flags=re.S,
        )
    else:
        m = re.search(r'<div class="radar-grid">.*?</div>\s*</div>\s*(?=<!--|\s*<div class="section-head")', html, re.S)
        if not m:
            print("! could not locate radar-grid; nothing changed")
            return
        html = html[: m.start()] + radar + html[m.end():]

    # The hero stat row sits inside an explicitly dated 2026-09-03 feature
    # block. Two of its numbers were never labelled with that date, so "62%"
    # and "LOW" read as current — stamp them with the date they describe.
    hk = st["HK"]
    hsi_rec = idx.get("HSI", {}).get("val")
    if hsi_rec:
        # 2026-10-05: the first pass swapped the value but kept the old label,
        # turning "18,420 / HSI 2026-09-03 記錄" into "23,972 / HSI 2026-09-03
        # 記錄" — asserting the 09-03 record when 23,972 is the 10-02 close.
        # Relabel it to the session the number actually came from.
        html = re.sub(
            r'<div class="val">[\d,]+</div>\s*<div class="lbl">HSI 2026-09-03 記錄</div>',
            f'<div class="val">{fmt(hsi_rec)}</div>'
            f'<div class="lbl">HSI Close · {asof}</div>', html, count=1)
        html = html.replace("the 17,200 level holds as support",
                            "the 17,200 level holds as support（當日參考位，非收市價）", 1)

    # Idempotency: strip any previously-appended markers before re-adding.
    # A plain str.replace would append the suffix on every build — after four
    # runs the sentence carried it four times.
    html = re.sub(r'（當日參考位，非收市價）+', "", html)
    html = re.sub(r'（2026-09-03 評論）+', "", html)
    html = re.sub(
        r'<div class="lbl">HK Up Signal Rate(?: · 2026-09-03 記錄)?</div>',
        '<div class="lbl">HK Up Signal Rate · 2026-09-03 記錄</div>', html, count=1)
    html = re.sub(
        r'<div class="lbl">Volume Regime(?: · 2026-09-03 記錄)?</div>',
        '<div class="lbl">Volume Regime · 2026-09-03 記錄</div>', html, count=1)
    html = html.replace("the 17,200 level holds as support",
                        "the 17,200 level holds as support（當日參考位，非收市價）", 1)
    for prose in (
        r'Range compression\. Awaiting break of[^<]*?directional bias\.',
        r'S&P 500 holding above key MAs\. Breadth improving\. Bias remains long\.',
        r'Nikkei rejected at 200-day MA\. Yen strength weighing on exporters\.',
    ):
        html = re.sub(f'({prose})', r'\1（2026-09-03 評論）', html, count=1)

    # 2026-10-05: two more hand-typed contradictions on the same page.
    #   * nav badge read "T-1 · 2026-09-22" while the strip below it said 10-02
    #   * the "Regime Radar" panel claimed HK=BASE BUILDING, US=UPTREND while
    #     the Market Radar cards computed DOWNTREND ACTIVE for both.
    # The panel's prose is 2026-09-03 editorial: prose stays, date-stamped;
    # the regime labels become the computed ones.
    html = re.sub(r'(<span><span class="live-dot"></span>T-1 · )[0-9-]{10}(</span>)',
                  rf'\g<1>{asof}\g<2>', html, count=1)

    panel_start = html.find("Regime Radar")
    if panel_start != -1:
        head, tail = html[:panel_start], html[panel_start:]
        for flag, mkey in (("🇭🇰", "HK"), ("🇺🇸", "US"), ("🇯🇵", "JP")):
            badge, bcls = PHASE_BADGE.get(st[mkey]["phase"], ("—", ""))
            pat = (r'(<span class="flag">' + re.escape(flag) + r'</span>[^<]*</div>\s*)'
                   r'<span class="regime [a-z]*">[^<]*</span>')
            m = re.search(pat, tail)
            if m:
                tail = tail[:m.start()] + m.group(1) + f'<span class="regime {bcls}">{badge}</span>' + tail[m.end():]
        html = head + tail

    PAGE.write_text(html, encoding="utf-8")
    print(f"insights.html: radar rewired, asof={asof} "
          f"| HK {hk['buy']}B/{hk['sell']}S | US {st['US']['buy']}B/{st['US']['sell']}S "
          f"| JP {st['JP']['buy']}B/{st['JP']['sell']}S")


if __name__ == "__main__":
    main()
