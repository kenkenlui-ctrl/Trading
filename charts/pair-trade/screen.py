#!/usr/bin/env python3
"""
pair_screen.py — Find pair trade opportunities across HK200 + US200.

Strategy:
  1. Load all 400 stocks with their phase, last, chg_pct
  2. Categorize into rough sectors (via name/ticker pattern)
  3. For each sector with 2+ stocks, compute:
     - 5d return divergence (long candidate = best 5d, short candidate = worst 5d)
     - Phase mismatch (one base/uptrend vs other downtrend = strongest)
  4. Output top pair ideas with:
     - Long ticker, short ticker
     - Sector, current prices, phase, recent returns
     - Divergence score

Output:
  pair_trades.csv          all candidates
  pair_trades.html         sortable view
  print summary
"""
from __future__ import annotations
import csv
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import pandas as pd
import yfinance as yf
import numpy as np

OUT_DIR = Path("/Users/kenken/dev/dsa-hk/charts/pair-trade")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUT_DIR / "progress.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, mode="w"), logging.StreamHandler()],
)
log = logging.getLogger("pair")

HK_CSV = Path("/Users/kenken/dev/dsa-hk/charts/hk200/top200_summary.csv")
US_CSV = Path("/Users/kenken/dev/dsa-hk/charts/us200/top200_summary.csv")
JSON_DIR_HK = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
JSON_DIR_US = Path("/Users/kenken/dev/dsa-hk/charts/us200")

# ── Sector classification (rough, by name patterns) ──
SECTOR_RULES = [
    ("AI/Tech", [r"AI", r"artificial", r"Tencent", r"百度", r"阿里", r"BABA", r"PDD", r"JD", r"BIDU",
                 r"NET", r"Cloudflare", r"AI Infra", r"Palantir", r"PLTR", r"AppLovin", r"ARM",
                 r"TSM", r"AVGO", r"MU\b", r"NVDA", r"AMD", r"INTC", r"SNDK", r"SMSC", r"NVDA",
                 r"SMCI", r"AppLovin", r"Z\.AI", r"智谱", r"Zhipu", r"TENCENT", r"Alibaba",
                 r"Snowflake", r"SNOW", r"DDOG", r"Datadog", r"NET", r"Cloudflare",
                 r"CrowdStrike", r"CRWD", r"Datadog", r"DDOG", r"Zscaler", r"ZS ",
                 r"Palo Alto", r"PANW", r"Arista", r"ANET", r"MongoDB", r"MDB",
                 r"SKB Bio", r"金 斯 瑞", r"金 斯 瑞", r"02513"]),
    ("Semiconductor", [r"SMIC", r"中芯", r"Hua Hong", r"华虹", r"Gigadevice", r"兆易",
                        r"Montage", r"澜起", r"长电", r"长川", r"CAMTEK", r"ASMPT", r"ASML",
                        r"AMAT", r"LRCX", r"MRVL", r"Marvell", r"KLAC", r"ON Sem",
                        r"Micron", r"MU\b", r"Western Digital", r"WDC", r"STX\b",
                        r"SK Hynix", r"TSM", r"Renesas", r"ON ", r"MPWR", r"Monolithic",
                        r"ARM Holdings", r"ARM ", r"台积电"]),
    ("AI/HBM/Memory", [r"Micron", r"SK Hynix", r"Western Digital", r"SanDisk", r"SNDK",
                       r"HBM", r"memory", r"DRAM", r"NAND", r"SK Hynix",
                       r"GigaDevice", r"兆易创新", r"Kingston"]),
    ("Bank", [r"HSBC", r"Bank of China", r"BOC", r"ICBC", r"CCB", r"ABC",
              r"China Construction", r"中国建设", r"中国工商", r"中国银行",
              r"JPM", r"BAC", r"WFC", r"GS ", r"MS ", r"AXP", r"BLK", r"C ", r"SCHW",
              r"USB", r"PNC", r"TFC", r"COF", r"StanChart", r"渣打",
              r"BOC HK", r"BOCOM"]),
    ("Insurance", [r"AIA", r"中国平安", r"Ping An", r"中国人寿", r"China Life",
                   r"新华保险", r"PICC", r"中国太保", r"中国太平", r"Allstate",
                   r"Progressive", r"PGR", r"GEICO", r"Travelers", r"TRV",
                   r"MetLife", r"Prudential", r"PRU", r"Aflac", r"AFL",
                   r"Lincoln National", r"LNC", r"Globe Life", r"GL"]),
    ("Real Estate", [r"REIT", r"REIT ", r"Link REIT", r"领展", r"置富", r"Sun Hung Kai",
                     r"新鸿基", r"Henderson", r"恒基", r"China Vanke", r"万科",
                     r"Country Garden", r"碧桂园", r"Longfor", r"龙湖",
                     r"AMT", r"PLD", r"CCI", r"EQIX", r"PSA", r"O ", r"WELL", r"SPG",
                     r"EXR", r"AVB", r"Realty Income", r"O ", r"EQR", r"ESS",
                     r"MAA", r"UDR", r"CPT", r"Camden", r"CXP"]),
    ("EV/Auto", [r"Tesla", r"TSLA", r"蔚来", r"NIO", r"小鹏", r"XPEV", r"理想", r"LI ",
                 r"BYD", r"比亚迪", r"Ford", r"F ", r"GM ", r"General Motors",
                 r"RIVN", r"Lucid", r"LCID", r"Stellantis", r"STLA",
                 r"Geely", r"吉利", r"长城", r"Great Wall", r"奇瑞", r"Chery",
                 r"蔚来", r"理想", r"零跑", r"LEAPMOTOR", r"XPeng",
                 r"Polestar", r"PSNY", r"VinFast", r"VFS", r"Li Auto"]),
    ("Pharma/Biotech", [r"Pfizer", r"PFE", r"Merck", r"MRK", r"AbbVie", r"ABBV",
                        r"Lilly", r"LLY", r"BMS", r"BMY", r"Gilead", r"GILD",
                        r"Amgen", r"AMGN", r"Vertex", r"VRTX", r"Biogen", r"BIIB",
                        r"Moderna", r"MRNA", r"Regeneron", r"REGN",
                        r"Innovent", r"信达", r"Junshi", r"君实", r"BeiGene", r"百济",
                        r"Hengrui", r"恒瑞", r"三生", r"3SBIO", r"翰森",
                        r"Alnylam", r"ALNY", r"Ionis", r"IONS", r"Biomarin", r"BMRN",
                        r"Sarepta", r"SRPT", r"Vertex", r"VRTX",
                        r"Genscript", r"金斯瑞"]),
    ("Healthcare/Medtech", [r"UnitedHealth", r"UNH", r"Humana", r"HUM",
                            r"CVS", r"Eli Lilly", r"LLY", r"TMO ", r"Thermo",
                            r"Danaher", r"DHR", r"Intuitive", r"ISRG",
                            r"Boston Scientific", r"BSX", r"Medtronic", r"MDT",
                            r"Stryker", r"SYK", r"Baxter", r"BAX",
                            r"Zimmer", r"ZBH", r"Edwards", r"EW ",
                            r"Intuitive Surgical", r"Veeva", r"VEEV"]),
    ("Energy/Oil", [r"Exxon", r"XOM", r"Chevron", r"CVX", r"Conoco", r"COP",
                    r"Schlumberger", r"SLB", r"OXY", r"EOG", r"Marathon", r"MPC",
                    r"Phillips 66", r"PSX", r"Valero", r"VLO", r"Devon", r"DVN",
                    r"CNPC", r"PetroChina", r"中石油", r"中石化", r"Sinopec",
                    r"CNOOC", r"中海油", r"中海油服", r"China Oilfield",
                    r"Schlumberger", r"SLB", r"哈里伯顿", r"Haliburton", r"HAL",
                    r"Baker Hughes", r"BKR", r"ONEOK", r"OKE", r"Williams", r"WMB",
                    r"Kinder Morgan", r"KMI", r"Cheniere", r"LNG", r"Energy Transfer", r"ET"]),
    ("Consumer/Retail", [r"Walmart", r"WMT", r"Costco", r"COST", r"Target", r"TGT",
                         r"Home Depot", r"HD", r"Lowe", r"LOW", r"Starbucks", r"SBUX",
                         r"McDonald", r"MCD", r"Nike", r"NKE", r"Chipotle", r"CMG",
                         r"Domino", r"DPZ", r"Lululemon", r"LULU",
                         r"Alibaba", r"BABA", r"京东", r"JD ", r"拼多多", r"PDD",
                         r"美团", r"Meituan", r"Pinduoduo", r"Trip.com", r"携程",
                         r"POP MART", r"泡泡玛特", r"百胜", r"Yum",
                         r"Anta", r"安踏", r"Li Ning", r"李宁", r"申洲", r"申洲国际",
                         r"美的", r"Midea", r"海尔", r"Haier", r"格力", r"Gree",
                         r"Ross", r"ROST", r"Burlington", r"BURL", r"TJX", r"Dollar Tree", r"DLTR",
                         r"Five Below", r"FIVE", r"Ulta", r"ULTA", r"Williams-Sonoma", r"WSM"]),
    ("Industrial", [r"Boeing", r"BA", r"Caterpillar", r"CAT", r"Deere", r"DE ",
                    r"General Electric", r"GE", r"Honeywell", r"HON",
                    r"RTX", r"Lockheed", r"LMT", r"Northrop", r"NOC",
                    r"General Dynamics", r"GD", r"TransDigm", r"TDG",
                    r"Eaton", r"ETN", r"Parker", r"PH", r"Emerson", r"EMR",
                    r"ITW ", r"Trinity", r"Cummins", r"CMI", r"PACCAR", r"PCAR",
                    r"United Rentals", r"URI", r"Deere", r"三一", r"Sany",
                    r"中联", r"Zoomlion", r"XCMG", r"徐工"]),
    ("Utility", [r"NextEra", r"NEE", r"Duke", r"DUK", r"Southern Co", r"SO ",
                 r"Constellation", r"CEG", r"Vistra", r"VST",
                 r"American Electric", r"AEP", r"Exelon", r"EXC",
                 r"Xcel", r"XEL", r"Sempra", r"SRE",
                 r"China Yangtze", r"长江电力", r"华能", r"Huaneng",
                 r"国电", r"Datang", r"华润电力", r"中广核", r"CGN"]),
    ("Telecom", [r"AT&T", r"Verizon", r"China Mobile", r"中国移动",
                 r"T-Mobile", r"TMUS", r"Comcast", r"CMCSA",
                 r"HKT", r"电讯盈科", r"PCCW", r"电讯", r"香港电讯",
                 r"China Unicom", r"中国联通", r"China Telecom", r"中国电信",
                 r"中華電信", r"Chunghwa"]),
    ("Solar/Clean", [r"Enphase", r"ENPH", r"First Solar", r"FSLR",
                     r"SolarEdge", r"SEDG", r"Sunrun", r"RUN",
                     r"Array Technologies", r"ARRY", r"Maxeon", r"MAXN",
                     r"SunPower", r"SPWR", r"Brookfield Renewable", r"BEPC",
                     r"Plug Power", r"PLUG", r"FuelCell", r"FCEL",
                     r"Ballard", r"BLDP", r"QuantumScape", r"QS",
                     r"Array", r"信义光能", r"Xinyi Solar", r"龙源", r"Longyuan",
                     r"金风", r"Goldwind", r"天合", r"Trina"]),
    ("Defense", [r"Lockheed", r"LMT", r"Raytheon", r"RTX", r"Northrop", r"NOC",
                 r"General Dynamics", r"GD", r"Boeing Defense", r"L3Harris",
                 r"Textron", r"TXT", r"Huntington Ingalls", r"HII",
                 r"AeroVironment", r"AVAV", r"Kratos", r"KTOS",
                 r"中航沈飞", r"AVIC", r"中国航发", r"洪都", r"航空工业"]),
    ("Media/Entertainment", [r"Disney", r"DIS", r"Netflix", r"NFLX",
                              r"Warner", r"WBD", r"Paramount", r"PARA",
                              r"Spotify", r"SPOT", r"Roku", r"ROKU",
                              r"Tencent Music", r"TME ", r"网易", r"NetEase", r"NTES",
                              r"Bilibili", r"BILI", r"虎牙", r"HUYA",
                              r"爱奇艺", r"IQIYI", r"阿里影业", r"阿里鱼"]),
    ("Gold/Precious Metals", [r"Barrick", r"GOLD", r"Newmont", r"NEM",
                              r"Franco-Nevada", r"FNV", r"Wheaton", r"WPM",
                              r"AngloGold", r"AU ", r"Gold Fields", r"GFI",
                              r"Newcrest", r"紫金黄金", r"山东黄金", r"Shandong Gold",
                              r"招金", r"Zhaojin", r"中金黄金", r"China Gold"]),
    ("Internet/Software", [r"Microsoft", r"MSFT", r"Oracle", r"ORCL",
                            r"Salesforce", r"CRM", r"Adobe", r"ADBE",
                            r"ServiceNow", r"NOW", r"Workday", r"WDAY",
                            r"HubSpot", r"HUBS", r"Atlassian", r"TEAM",
                            r"Intuit", r"INTU", r"Autodesk", r"ADSK",
                            r"Workiva", r"WK", r"Twilio", r"TWLO",
                            r"Zoom", r"ZM", r"DocuSign", r"DOCU",
                            r"Slack", r"金蝶", r"Kingdee", r"用友", r"Yonyou",
                            r"明源云", r"Ming Yuan"]),
    ("Crypto/Fintech", [r"Coinbase", r"COIN", r"MicroStrategy", r"MSTR",
                         f"Strategy", r"Robinhood", r"HOOD",
                         r"PayPal", r"PYPL", r"Block", r"SQ",
                         r"SoFi", r"SOFI", r"Upstart", r"UPST",
                         r"Coin", r"Bitfarms", r"BITF", r"Riot", r"RIOT",
                         r"Marathon Digital", r"MARA", r"CleanSpark", r"CLSK",
                         r"Hut 8", r"HUT", r"Hive", r"HIVE",
                         r"Cipher Mining", r"CIFR", r"Core Scientific", r"CORZ"]),
    ("Logistics/Shipping", [r"UPS", r"FedEx", r"FDX", r"USPS",
                             r"Maersk", r"中远海控", r"COSCO",
                             r"COSCO Shipping", r"东方海外", r"OOCL",
                             r"中集", r"CIMC", r"嘉里物流", r"Kerry Logistics",
                             r"嘉里", r"嘉里建设", r"阳明", r"Yang Ming",
                             r"万海", r"Wan Hai", r"长荣", r"Evergreen"]),
    ("Insurance/Brokerage", [r"Charles Schwab", r"SCHW", r"Interactive Brokers", r"IBKR",
                              f"Robinhood", r"HOOD", r"eToro", r"ETOR",
                              r"TradeStation", r"Tradier", r"Tastyworks",
                              r"Webull", r"BULL"]),
    ("ETF", [r"SPDR", r"iShares", r"Vanguard", r"Invesco",
             r"ProShares", r"Direxion", r"VanEck",
             r"盈富", r"恒生", r"沪深", r"南方", r"华夏", r"易方达",
             r"嘉实", r"博时", r"汇添富", r"广发"]),
    ("Holding/Trust", [r"CK Hutchison", r"CKH", r"长江实业", r"长江和记",
                        r"太古", r"Swire", r"国泰", r"Cathay",
                        r"信和", r"Sino", r"恒隆", r"Hang Lung",
                        r"九龙仓", r"Wharf", r"会德丰", r"Wheelock",
                        r"Berkshire", r"BRK"]),
    ("Luxury/Consumer Brand", [r"LVMH", r"LVMUY", r"Hermes", r"HESAY",
                                r"Kering", r"PPRUY", r"Richemont", r"CFRUY",
                                r"Prada", r"PRDSY", r"Burberry", r"BURBY",
                                r"Tapestry", r"TPR", r"Capri", r"CPRI",
                                r"Ralph Lauren", r"RL", r"Tommy Bahama",
                                r"Tapestry", r"Estee Lauder", r"EL ",
                                r"L'Oreal", r"OR ", r"P&G", r"PG",
                                r"Unilever", r"UL ", r"Reckitt", r"RBGLY",
                                r"Colgate", r"CL"]),
]


def classify(name: str, ticker: str) -> str:
    """Rough sector classification by name/ticker pattern."""
    for sector, patterns in SECTOR_RULES:
        for p in patterns:
            if re.search(p, name, re.IGNORECASE):
                return sector
    return "Other"


def load_universe(csv_path: Path, market: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["market"] = market
    df["sector"] = df["name"].apply(lambda n: classify(str(n), ""))
    return df


def get_recent_returns(tickers: list[str], market: str) -> dict[str, float]:
    """Get TODAY's chg_pct for each ticker (best proxy from cached data)."""
    log.info(f"  fetching recent returns for {len(tickers)} tickers")
    out = {}
    for t in tickers:
        safe = t.replace(".", "_").replace("-", "_")
        json_path = JSON_DIR_HK / f"{safe}.json" if market == "HK" else JSON_DIR_US / f"{safe}.json"
        if not json_path.exists():
            out[t] = 0
            continue
        try:
            with open(json_path) as f:
                data = json.load(f)
            out[t] = float(data.get("chg_pct", 0))
        except Exception:
            out[t] = 0
    return out


def enrich_with_5d_yahoo(tickers: list[str], market: str) -> dict[str, float]:
    """Compute actual 5d return for each ticker via yfinance (parallel)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def fetch(t):
        try:
            yf_t = t if market == "US" else f"{int(t):04d}.HK"
            hist = yf.Ticker(yf_t).history(period="10d", auto_adjust=False)
            if hist is None or hist.empty or len(hist) < 6:
                return (t, 0.0)
            hist = hist.dropna(subset=["Close"])
            if len(hist) < 6:
                return (t, 0.0)
            last = float(hist["Close"].iloc[-1])
            five_d_ago = float(hist["Close"].iloc[-6])
            ret = (last - five_d_ago) / five_d_ago * 100
            return (t, ret)
        except Exception:
            return (t, 0.0)

    out = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, t): t for t in tickers}
        done = 0
        for fut in as_completed(futures):
            t, v = fut.result()
            out[t] = v
            done += 1
            if done % 50 == 0:
                log.info(f"    yahoo progress: {done}/{len(tickers)}")
    return out


def find_pairs(hk_df: pd.DataFrame, us_df: pd.DataFrame, hk_5d: dict, us_5d: dict) -> list[dict]:
    """Find pair trade ideas by sector + divergence.

    Uses today's chg_pct as proxy for recent relative performance.
    Long = best in sector, short = worst in sector.
    """
    all_df = pd.concat([hk_df, us_df], ignore_index=True)
    all_df["ret_proxy"] = all_df["ticker"].map(
        lambda t: hk_5d.get(t, 0) if t in hk_5d else us_5d.get(t, 0)
    )

    pairs = []
    for sector in all_df["sector"].unique():
        sector_df = all_df[all_df["sector"] == sector]
        if len(sector_df) < 2:
            continue
        # Sort by recent performance
        sorted_df = sector_df.sort_values("ret_proxy", ascending=False)
        # Try top 3 best × bottom 3 worst
        for i in range(min(3, len(sorted_df) - 1)):
            for j in range(len(sorted_df) - 1, max(len(sorted_df) - 4, i), -1):
                long_pick = sorted_df.iloc[i]
                short_pick = sorted_df.iloc[j]
                if long_pick["ticker"] == short_pick["ticker"]:
                    continue
                div_score = long_pick["ret_proxy"] - short_pick["ret_proxy"]
                # Bonus for phase mismatch
                phase_bonus = 0
                if long_pick["phase"] in ("uptrend", "base_building") and short_pick["phase"] in ("downtrend_active", "downtrend_recovery"):
                    phase_bonus = 5
                if long_pick["phase"] in ("downtrend_active", "downtrend_recovery") and short_pick["phase"] in ("uptrend", "base_building"):
                    phase_bonus = -5
                score = div_score + phase_bonus
                if abs(div_score) < 1.5:  # minimum divergence (today chg)
                    continue
                pairs.append({
                    "sector": sector,
                    "long_ticker": long_pick["ticker"],
                    "long_name": long_pick["name"],
                    "long_market": long_pick["market"],
                    "long_price": long_pick["last"],
                    "long_phase": long_pick["phase"],
                    "long_chg_pct": long_pick["chg_pct"],
                    "long_ret_proxy": long_pick["ret_proxy"],
                    "short_ticker": short_pick["ticker"],
                    "short_name": short_pick["name"],
                    "short_market": short_pick["market"],
                    "short_price": short_pick["last"],
                    "short_phase": short_pick["phase"],
                    "short_chg_pct": short_pick["chg_pct"],
                    "short_ret_proxy": short_pick["ret_proxy"],
                    "div_score": div_score,
                    "phase_bonus": phase_bonus,
                    "total_score": score,
                })
    pairs.sort(key=lambda p: -abs(p["total_score"]))
    return pairs


def main():
    log.info("=" * 60)
    log.info(f"Pair trade screen start @ {datetime.now().isoformat()}")
    log.info("=" * 60)

    log.info("Loading universes...")
    hk = load_universe(HK_CSV, "HK")
    us = load_universe(US_CSV, "US")
    log.info(f"  HK: {len(hk)} | US: {len(us)}")
    log.info(f"  HK sectors: {hk['sector'].value_counts().head(10).to_dict()}")
    log.info(f"  US sectors: {us['sector'].value_counts().head(10).to_dict()}")

    log.info("Computing real 5d returns via yfinance (parallel, all 400 stocks)...")
    hk_5d = enrich_with_5d_yahoo(hk["ticker"].tolist(), "HK")
    us_5d = enrich_with_5d_yahoo(us["ticker"].tolist(), "US")
    log.info(f"  HK: {len(hk_5d)} computed, US: {len(us_5d)} computed")
    nonzero = sum(1 for v in {**hk_5d, **us_5d}.values() if v != 0)
    log.info(f"  non-zero: {nonzero}")

    log.info("Finding pairs...")
    pairs = find_pairs(hk, us, hk_5d, us_5d)
    log.info(f"  found {len(pairs)} pair candidates (real 5d-based)")

    # Add real_5d_div
    for p in pairs:
        p["long_5d_real"] = p["long_ret_proxy"]
        p["short_5d_real"] = p["short_ret_proxy"]
        p["real_5d_div"] = p["div_score"]

    # Save CSV
    df = pd.DataFrame(pairs)
    df.to_csv(OUT_DIR / "pair_trades.csv", index=False)
    log.info(f"  saved → {OUT_DIR / 'pair_trades.csv'}")

    log.info("\n" + "=" * 60)
    log.info("TOP 20 PAIR TRADE CANDIDATES (real 5d return)")
    log.info("=" * 60)
    for p in pairs[:20]:
        direction = "↑" if p["div_score"] > 0 else "↓"
        log.info(
            f"  {p['sector']:18s} | {direction} "
            f"LONG {p['long_ticker']:10s} ({p['long_market']}) chg:{p['long_chg_pct']:+.1f}% 5d:{p['long_5d_real']:+.1f}% {p['long_phase'][:8]:8s} | "
            f"SHORT {p['short_ticker']:10s} ({p['short_market']}) chg:{p['short_chg_pct']:+.1f}% 5d:{p['short_5d_real']:+.1f}% {p['short_phase'][:8]:8s} | "
            f"5d div: {p['real_5d_div']:+.1f}%"
        )

    log.info("=" * 60)
    log.info(f"Done. {len(pairs)} pairs in {OUT_DIR / 'pair_trades.csv'}")


if __name__ == "__main__":
    main()
