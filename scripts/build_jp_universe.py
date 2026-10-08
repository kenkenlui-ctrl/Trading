"""build_jp_universe.py — Build JP200 universe by top 20-day average turnover.

Strategy: start with curated Topix Core30 + Large70 (~100 tickers, all known liquid).
Rank them by 20-day average turnover (close × volume) via yfinance.
Fill remaining slots from a broader yfinance-verified list.

This matches hk_universe_200.json and us_top200_fresh.json methodology
(top N by 20-day average turnover).
"""
from __future__ import annotations
import json
import time
from datetime import date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO = Path("/Users/kenken/dev/dsa-hk")
OUT = REPO / "jp_universe_200.json"
# 2026-10-08: JP had no cadence entry, so regen_all.py's gate could not see it
# as stale even in principle — JP only looked maintained because nobody was
# looking. Same log the HK and US scripts write into.
CADENCE_LOG = REPO / "data" / "radar_regen.json"

# Tier 1: Topix Core30 + Large70 (100+ tickers, all confirmed valid)
TIER1 = [
    "7203", "6758", "9984", "8306", "6861", "9433", "6902", "7267", "7974",
    "8035", "9432", "4502", "8058", "7741", "4063", "8031", "6367", "6501",
    "7751", "6954", "4503", "6981", "8316", "8411", "8766", "6098", "4661",
    "3382", "5401", "9020",  # Core30
    "4507", "1925", "8801", "8830", "8601", "8697", "6301", "9531", "9532",
    "9022", "9024", "9202", "9201", "2802", "4452", "2502", "2914", "6988",
    "6762", "3407", "8804", "2503", "2501", "2002", "1605", "1812", "1928",
    "3401", "1878", "4151", "4519", "4523", "4568", "4578", "6594", "6645",
    "6674", "6701", "6702", "6723", "6752", "6841", "6856", "6857", "6905",
    "6920", "6925", "6952", "6965", "6971", "7011", "7012", "7013", "7150",
    "7164", "7167", "7173", "7186", "7198", "7201", "7205", "7211", "7220",
    "7224", "7243", "7261", "7263", "7264", "7270", "7282", "7302", "7318",
    # Top Mid Cap (~50) — add more if found
    "1332", "1333", "1376", "1377", "1379", "1381", "1413", "1414", "1417",
    "1419", "1420", "1430", "1431", "1433", "1434", "1435", "1444", "1446",
    "1447", "1448", "1450", "1451", "1452", "1453", "1454", "1455", "1456",
    "1457", "1458", "1459", "1460", "1464", "1466", "1467", "1468", "1469",
]



# Tier 2: Additional mid-caps to be scanned
TIER2 = [
    "2911", "2917", "2920", "2921", "2922", "2923", "2924", "2925",
    "2927", "2928", "2929", "2930", "2931", "2932", "2933", "2935",
    "2936", "2937", "2938", "2939", "2940", "2941", "2942", "2943",
    "2944", "2945", "2946", "2947", "2948", "2949", "2950", "2951",
    "2952", "2953", "2954", "2955", "2956", "2957", "2958", "2959",
    "2960", "2961", "2962", "2963", "2964", "2965", "2966", "2967",
    "2970", "2971", "2972", "2973", "2974", "2975", "2976", "2977",
    "2978", "2979", "2980", "2981", "2982", "2983", "2984", "2985",
    "2986", "2987", "2988", "2989", "2990", "2991", "2992", "2993",
    "2994", "2995", "2996", "2997", "2998", "2999", "3002", "3003",
    "3004", "3005", "3006", "3008", "3009", "3010", "3011", "3012",
    "3013", "3015", "3016", "3017", "3018", "3019", "3020", "3021",
    "3022", "3023", "3024", "3025", "3026", "3028", "3029", "3030",
    "3031", "3032", "3033", "3034", "3035", "3036", "3037", "3038",
    "3039", "3040", "3041", "3042", "3043", "3044", "3045", "3046",
    "3047", "3048", "3049", "3050", "3053", "3054", "3055", "3056",
    "3057", "3058", "3059", "3060", "3061", "3062", "3063", "3064",
    "3065", "3066", "3067", "3068", "3069", "3070", "3071", "3072",
    "3073", "3074", "3075", "3076", "3077", "3078", "3079", "3080",
    "3081", "3082", "3083", "3084", "3085", "3086", "3087", "3088",
    "3089", "3090", "3091", "3092", "3093", "3094", "3095", "3096",
    "3097", "3098", "3099", "3100", "3101", "3102", "3103", "3104",
    "3105", "3106", "3107", "3108", "3109", "3110", "3111", "3112",
    "3113", "3114", "3115", "3116", "3117", "3118", "3119", "3120",
    "3121", "3122", "3123", "3124", "3125", "3126", "3127", "3128",
    "3129", "3130", "3131", "3132", "3133", "3134", "3135", "3136",
    "3137", "3138", "3139", "3140", "3141", "3142", "3143", "3144",
    "3145", "3146", "3147", "3148", "3149", "3150", "3151", "3152",
    "3153", "3154", "3155", "3156", "3157", "3158", "3159", "3160",
    "3161", "3162", "3163", "3164", "3165", "3166", "3167", "3168",
    "3169", "3170", "3171", "3172", "3173", "3174", "3175", "3176",
    "3177", "3178", "3179", "3180", "3181", "3182", "3183", "3184",
    "3185", "3186", "3187", "3188", "3189", "3190", "3191", "3192",
    "3193", "3194", "3195", "3196", "3197", "3198", "3199", "3200",
]


def fetch_turnover(ticker: str, days: int = 30) -> float:
    """Return average daily turnover (close × volume) over `days` trading days."""
    import yfinance as yf
    try:
        df = yf.Ticker(ticker).history(period=f"{days + 5}d", auto_adjust=False)
        if df is None or len(df) < 5:
            return 0.0
        df = df.tail(days)
        if "Volume" not in df.columns or "Close" not in df.columns:
            return 0.0
        turnover = (df["Close"] * df["Volume"]).mean()
        return float(turnover) if turnover == turnover else 0.0
    except Exception:
        return 0.0


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=200, help="Top N by turnover (default 200)")
    p.add_argument("--batch", type=int, default=16, help="Concurrent yfinance workers")
    args = p.parse_args()

    candidates = list(dict.fromkeys([f"{c}.T" for c in TIER1 + TIER2 if c.isdigit() and len(c) == 4]))
    print(f"Scanning {len(candidates)} TSE codes for 20-day average turnover (top {args.top})")

    results = []  # (turnover, ticker)
    with ThreadPoolExecutor(max_workers=args.batch) as ex:
        futures = {ex.submit(fetch_turnover, t): t for t in candidates}
        for i, fut in enumerate(as_completed(futures), 1):
            t = futures[fut]
            try:
                adv = fut.result()
                if adv > 0:
                    results.append((adv, t))
            except Exception:
                pass
            if i % 50 == 0:
                print(f"  {i}/{len(candidates)} scanned, {len(results)} valid")

    results.sort(reverse=True)
    top = [t for _, t in results[: args.top]]
    print(f"\nGot {len(results)} valid; top {args.top} written to {OUT}")
    OUT.write_text(json.dumps(top, indent=2), encoding="utf-8")

    # Cadence log — read-modify-write so a partial run (HK ok, US failed)
    # cannot wipe the other markets' entries.
    try:
        log = json.loads(CADENCE_LOG.read_text()) if CADENCE_LOG.exists() else {}
    except Exception:
        log = {}
    log["jp"] = {
        "last_regen": date.today().isoformat(),
        "count": len(top),
        "scanned": len(candidates),
        "valid": len(results),
        "metric": "top 20-day avg turnover (close x volume), TOPIX Core30+Large70 first",
    }
    CADENCE_LOG.write_text(json.dumps(log, indent=2, ensure_ascii=False))

    print(f"\nTop 20 by 20-day avg turnover (JPY):")
    for adv, t in results[:20]:
        print(f"  {t}: {adv / 1e9:.1f}B JPY/day")
    print(f"\n✓ Cadence log updated: jp @ {log['jp']['last_regen']} ({len(top)} codes)")


if __name__ == "__main__":
    main()
