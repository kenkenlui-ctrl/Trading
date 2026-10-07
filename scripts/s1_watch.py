#!/usr/bin/env python3
"""s1_watch.py — watch for S1 limit entries and tell Kenneth when price arrives.

WHY
    v2's entry is not a prediction, it is an order: "when price falls to the
    20-day low S1, buy — but only if the session closes back at or above S1".
    That rule is already executable in real time; what is missing is a trigger.
    This is that trigger, and nothing more.

THE ONE LIMITATION, STATED UP FRONT
    The close-hold confirmation is only knowable at the close. So the alert is
    REAL TIME (price is at S1, you may place your limit order now) and the
    CONFIRMATION IS END-OF-DAY (the fill counts only if the session closes at
    or above S1). That is a property of the rule, not a defect in this tool.
    A touch that fails to hold is a broken level, not a buy — and the ab_close_hold
    A/B measured what that rejection is worth: trade count roughly halves and
    per-trade return roughly doubles.

WHAT IT WILL NOT DO
    It never places an order. Futu is used read-only here. Execution stays in
    your broker app, where you can see the book and size it yourself.

IT IS NOT SCHEDULED
    Kenneth asked (2026-10-05) not to have work scheduled behind his back. This
    runs only when you start it. Start it before you want to watch, stop it when
    you are done; there is no launchd entry and no cron.

Usage
    python3 scripts/s1_watch.py --dry-run            # report once, send nothing
    python3 scripts/s1_watch.py --interval 60        # poll every 60s, notify
    python3 scripts/s1_watch.py --confirm            # EOD close-hold check
    python3 scripts/s1_watch.py --list               # show the watchlist
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
V2 = REPO / "public" / "v2-signals.json"
STATE = REPO / "data" / "s1_watch_state.json"
LOG = REPO / "data" / "s1_watch_log.jsonl"

# Futu market prefixes. Only the markets v2 actually serves — HK is excluded by
# build_v2_signals.INCLUDE_HK=False and has no v2 signal to watch.
PREFIX = {"us200": "US.", "jp200": "JP."}


def load_watchlist() -> dict[str, dict]:
    """{futu_code: {sym, market, s1, asof}} for every live v2 signal."""
    if not V2.exists():
        print(f"no v2 signals at {V2} — run scripts/build_v2_signals.py first")
        return {}
    d = json.load(open(V2))
    out: dict[str, dict] = {}
    for s in d.get("signals", []):
        mkt = s.get("market", "")
        sym = s.get("symbol", "")
        s1 = s.get("entry_s1")
        if not mkt or not sym or s1 is None:
            continue
        # JP symbols arrive as 3035_T; Futu wants 7203 style. Drop the _T suffix.
        bare = sym[:-2] if sym.endswith("_T") else sym
        pre = PREFIX.get(mkt)
        if not pre:
            continue
        out[pre + bare] = {"sym": sym, "market": mkt, "s1": float(s1),
                           "asof": s.get("asof", ""), "stop": s.get("stop")}
    return out


def futu_prices(codes: list[str]) -> dict[str, tuple[float, str]]:
    from futu import OpenQuoteContext
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        ret, data = ctx.get_market_snapshot(codes)
        if ret != 0:
            print(f"  futu error: {str(data)[:120]}")
            return {}
        out = {}
        for _, row in data.iterrows():
            try:
                out[row["code"]] = (float(row["last_price"]), str(row.get("update_time", "")))
            except (TypeError, ValueError):
                continue
        return out
    finally:
        ctx.close()


def notify(title: str, msg: str) -> None:
    """macOS notification. No server, no account, nothing to configure."""
    esc = lambda t: t.replace('\\', '\\\\').replace('"', '\\"')  # noqa: E731
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{esc(msg)}" with title "{esc(title)}"'],
                       check=False, capture_output=True, timeout=10)
    except Exception as e:
        print(f"  (notify failed: {e})")


def load_state() -> dict:
    return json.load(open(STATE)) if STATE.exists() else {}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def log_event(ev: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def check_once(watch: dict, dry: bool) -> list[dict]:
    """One pass. Returns the rows that are at or below their S1."""
    prices = futu_prices(sorted(watch))
    if not prices:
        return []
    hits = []
    for code, meta in watch.items():
        if code not in prices:
            continue
        px, upd = prices[code]
        gap = (px / meta["s1"] - 1) * 100
        if px <= meta["s1"]:
            hits.append({"code": code, **meta, "price": px, "update": upd,
                         "gap_pct": round(gap, 2)})
    return hits


def confirm_close(watch: dict) -> None:
    """End-of-day: did the session actually close at or above S1?

    Uses Futu's daily bar for the session just finished, so the answer does not
    depend on our own bar store being current — which, as of 2026-10-07, has
    been the subject of two separate staleness bugs.
    """
    from futu import OpenQuoteContext, KLType
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    touched = [c for c in watch if f"{c}|{watch[c]['s1']}" in state]
    if not touched:
        print("  （冇任何一隻今日觸及過 S1，冇嘢要確認）")
        ctx.close()
        return
    print(f"  只確認今日觸及過 S1 嘅 {len(touched)} 隻：\n")
    try:
        for code in sorted(touched):
            try:
                # autype MUST be the literal 'qfq' — SubType.NONE maps to "N/A",
                # which Futu rejects outright ("autype is N/A, which is not
                # valid"). start/end are mandatory for a bounded window.
                r = ctx.request_history_kline(
                    code, start="2026-09-01", end=today,
                    ktype=KLType.K_DAY, autype="qfq")
                ret, df, _ = r
                if ret != 0 or df is None or not len(df):
                    print(f"  {code:14} kline 失敗: {str(df)[:60]}")
                    continue
                close = float(df.iloc[-1]["close"])
                s1 = watch[code]["s1"]
                held = close >= s1
                st = state.setdefault(f"{code}|{s1}", {})
                st["close"], st["held"] = close, held
                log_event({"ts": datetime.now().isoformat(), "code": code,
                           "close": close, "s1": s1, "held": held,
                           "bar": str(df.iloc[-1]["time_key"]), "type": "eod-confirm"})
                print(f"  {code:14} close {close:>10.2f}  S1 {s1:>10.2f}"
                      f"  → {'CONFIRMED 算成交' if held else 'REJECTED 收市企唔穩 S1'}")
            except Exception as e:
                print(f"  {code}: {type(e).__name__} {e}")
        save_state(state)
    finally:
        ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60, help="seconds between polls")
    ap.add_argument("--dry-run", action="store_true", help="report once, no notifications")
    ap.add_argument("--notify", action="store_true", help="actually send macOS notifications")
    ap.add_argument("--confirm", action="store_true", help="end-of-day close-hold check")
    ap.add_argument("--list", action="store_true", help="show the watchlist and exit")
    a = ap.parse_args()

    watch = load_watchlist()
    if not watch:
        return 1

    if a.list:
        print(f"{len(watch)} symbols on watch (v2 entry levels, as of the last close)\n")
        print(f"{'Futu code':<14}{'sym':<10}{'market':<8}{'S1 entry':>10}{'stop':>10}")
        for c, m in sorted(watch.items(), key=lambda kv: kv[1]["s1"]):
            print(f"{c:<14}{m['sym']:<10}{m['market']:<8}{m['s1']:>10.2f}"
                  f"{(m['stop'] or 0):>10.2f}")
        return 0

    if a.confirm:
        print("EOD close-hold check — the session just finished:\n")
        confirm_close(watch)
        print("\n提醒：呢個只確認「有冇企穩 S1」，唔會落單。落單仍然經你嘅券商 app。")
        return 0

    state = load_state()
    if not a.notify:
        print(f"dry-run（唔會發通知）。加 --notify 先會彈 macOS 通知。\n")

    print(f"監察 {len(watch)} 隻 v2 入場位，每 {a.interval}s 查一次。Ctrl-C 停止。\n")
    try:
        while True:
            hits = check_once(watch, a.dry_run)
            for h in hits:
                key = f"{h['code']}|{h['s1']}"
                first = state.get(key) is None
                state[key] = {"last": h["price"], "ts": datetime.now().isoformat()}
                if first:
                    msg = (f"{h['sym']} 到 S1：現價 {h['price']:.2f} ≤ 入場 {h['s1']:.2f}"
                           f"（止損 {h['stop']}）")
                    print(f"  *** {msg}")
                    log_event({"ts": datetime.now().isoformat(), **h, "type": "s1-touch"})
                    if a.notify:
                        notify("Leeks Terminal · 到 S1", msg)
                else:
                    print(f"      {h['sym']} 仍然喺 S1 之下（{h['price']:.2f}）")
            save_state(state)
            if not hits:
                print(f"  {datetime.now():%H:%M:%S}  暫時未有任何一隻到 S1")
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print("\n停止監察。")
    print("\n注意：到 S1 只代表「可以考慮掛單」。收市企唔穩 S1 就唔算成交 —— "
          "ab_close_hold 量度過：拒絕呢啲單，交易數減半、每單回報翻倍。")
    return 0


if __name__ == "__main__":
    sys.exit(main())