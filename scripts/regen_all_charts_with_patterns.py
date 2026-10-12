#!/usr/bin/env python3
"""重出全部 ticker 圖（含蠟燭形態標記），本地 build，唔部署。

點解唔直接跑 scripts/refresh.sh
-------------------------------
1. refresh.sh 有 skip guard：JSON 嘅 last_bar.date == DATA_DATE 就跳過，
   而啱啱已經有部分圖更新到目標日期 -> 唔刪就永遠唔會重出，形態標記唔會出現。
2. refresh.sh 最後會 `git push` + `wrangler pages deploy` = 直接上生產。
   呢個要人批准，唔應該由呢個 script 靜靜做。

做法：強制重出（忽略 skip guard），三個市場各自用返原本嘅資料源
（HK/US = futu，JP = yfinance），輸出寫返去原本嘅目錄。
之後 build_dashboard.py / build_static.py 負責拷去 public/。

用法：
  python3 scripts/regen_all_charts_with_patterns.py            # 三個市場
  python3 scripts/regen_all_charts_with_patterns.py --markets HK
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
SKILL = Path("/Users/kenken/.minimax/skills/daily-sr-chart/scripts/daily_sr.py")

MARKETS = {
    "HK": {"universe": REPO / "hk_universe_200.json",
           "out": REPO / "charts/hk200",
           "asof": ["--asof", "2026-10-09"]},
    "US": {"universe": REPO / "us_universe_200.json",
           "out": REPO / "charts/us200",
           "asof": ["--asof", "2026-10-09"]},
    "JP": {"universe": REPO / "jp_universe_200.json",
           "out": REPO / "charts/jp200",
           "asof": []},          # JP 走 yfinance，唔支援 --asof
}
# futu 歷史 K 線每 30 秒 60 次；兩個 worker 會打爆閘，所以用 2 + 節流。
WORKERS = 2
SLEEP_BETWEEN = 0.62


def one(ticker: str, out_dir: Path, asof_args: list[str],
        mirror: Path | None = None) -> tuple[str, bool, str]:
    safe = ticker.replace(".", "_")
    png, js = out_dir / f"{safe}.png", out_dir / f"{safe}.json"
    cmd = [sys.executable, str(SKILL), "-t", ticker, "-o", str(png),
           "--source", "auto", "--json", str(js), *asof_args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and png.exists() and js.exists():
            # JP's builder reads data/jp200/, not charts/jp200/ — writing only to
            # charts/jp200 left public/jp200/charts/ a week stale and shipped it.
            if mirror is not None:
                import shutil
                for _f in (png, js):
                    try:
                        shutil.copy2(_f, mirror / _f.name)
                    except Exception:
                        pass
            return ticker, True, "ok"
        return ticker, False, (r.stderr or r.stdout)[-90:].replace("\n", " ")
    except Exception as e:
        return ticker, False, f"{type(e).__name__}"
    finally:
        time.sleep(SLEEP_BETWEEN)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="HK,US,JP")
    ap.add_argument("--asof-hk", default="2026-10-12")
    ap.add_argument("--asof-us", default="2026-10-09")
    ap.add_argument("--asof-jp", default="2026-10-12")
    a = ap.parse_args()
    # Markets do not share a trading calendar: on 2026-10-12 (Mon) HK and JP have
    # closed, the US has not opened yet (21:30 HKT). Passing one date to all three
    # would ask the US vendor for a session that has not happened.
    overrides = {"HK": a.asof_hk, "US": a.asof_us, "JP": a.asof_jp}

    t0 = time.time()
    for m in a.markets.split(","):
        cfg = MARKETS[m]
        if overrides.get(m): cfg["asof"] = ["--asof", overrides[m]]
        tickers = json.loads(cfg["universe"].read_text())
        cfg["out"].mkdir(parents=True, exist_ok=True)
        print(f"\n=== {m}: {len(tickers)} 隻 -> {cfg['out']}")
        ok = fail = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            mirror = (REPO / "data/jp200") if m == "JP" else None
            if mirror:
                mirror.mkdir(parents=True, exist_ok=True)
                print(f"   （JP 同時寫入 {mirror}，build_dashboard 從呢度讀）")
            futs = [ex.submit(one, t, cfg["out"], cfg["asof"], mirror) for t in tickers]
            for i, f in enumerate(futs, 1):
                _, good, msg = f.result()
                ok += good
                if not good:
                    fail += 1
                    if fail <= 3:
                        print(f"   x {msg}")
                if i % 50 == 0:
                    print(f"   {i}/{len(tickers)}  成功 {ok} 失敗 {fail}")
        print(f"   {m} 完成：成功 {ok} / 失敗 {fail}")

    print(f"\n總計 {time.time()-t0:.0f} 秒")
    print("下一步：python3 scripts/build_dashboard.py  （會把 charts/ 拷去 public/）")
    print("部署請另外跑 scripts/refresh.sh，或手動 wrangler deploy。")
    return 0


if __name__ == "__main__":
    sys.exit(main())