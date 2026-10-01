"""fetch_us_names.py — Build us_names.json from Tencent qt for US tickers missing name.

For each US ticker in src/us200/{safe}.json where `name` is empty/missing,
hit qt.gtimg.cn/q=us{code} and extract name from parts[1].

Output: /dsa-hk/data/us_names.json (overwrite) — {ticker: "English Name"} or
{code: "English Name"} for lookup.

Note: Tencent qt returns Chinese names for popular US stocks (苹果 Apple),
and English names for less-known ones. We take whatever Tencent gives us.
This is the best real-time source — yfinance .info takes 1-2s/ticker.
"""
import json
import re
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

US_OUT = Path("/Users/kenken/dev/dsa-hk/charts/us200")
NAMES_PATH = Path("/Users/kenken/dev/dsa-hk/data/us_names.json")


def fetch_qt(code: str) -> str | None:
    url = f"http://qt.gtimg.cn/q=us{code}"
    try:
        r = urllib.request.urlopen(url, timeout=5)
        raw = r.read()
    except Exception:
        return None
    try:
        d = raw.decode("gbk", errors="replace")
    except Exception:
        d = raw.decode("utf-8", errors="replace")
    m = re.search(r'="([^"]+)"', d)
    if not m:
        return None
    parts = m.group(1).split("~")
    if len(parts) > 1 and parts[1].strip():
        return parts[1].strip()
    return None


def main():
    names = {}
    if NAMES_PATH.exists():
        names = json.load(open(NAMES_PATH))
    print(f"Existing entries: {len(names)}", flush=True)

    # Find missing
    missing = []
    for fp in sorted(US_OUT.glob("*.json")):
        if "_ohlc" in fp.name or fp.stem.startswith("US."):
            continue
        try:
            d = json.load(open(fp))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        ticker = d.get("ticker", "")
        code = ticker.replace("US.", "")
        existing_name = d.get("name", "") or ""
        # Lookup key: use code without .T/.HK
        if not existing_name and code not in names:
            missing.append((code, fp))
    print(f"Missing names: {len(missing)}", flush=True)

    if not missing:
        print("Nothing to do")
        return

    def fetch_one(args):
        code, _ = args
        return code, fetch_qt(code)

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch_one, m): m[0] for m in missing}
        for fut in as_completed(futures):
            code, name = fut.result()
            if name:
                names[code] = name
                ok += 1
            else:
                fail += 1

    print(f"Fetched ok={ok}, fail={fail}", flush=True)
    NAMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    NAMES_PATH.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(names)} entries to {NAMES_PATH}", flush=True)


if __name__ == "__main__":
    main()
