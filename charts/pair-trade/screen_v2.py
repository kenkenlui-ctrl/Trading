#!/usr/bin/env python3
"""
pair_screen_v2.py — Enhanced pair screen with:
  1. Real 5d correlation (Pearson)
  2. Cointegration test (ADF on spread)
  3. Better sector classifier (expanded rules)
  4. Long/Short ratio (volatility-based dollar-neutral)

Pipeline:
  1. Load HK200 + US200 (with sectors from expanded classifier)
  2. For each pair candidate, fetch 30d prices via yfinance
  3. Compute correlation, cointegration, L/S ratio
  4. Score: high corr + low p-value + phase mismatch = top pair
  5. Output enhanced HTML
"""
from __future__ import annotations
import csv
import json
import logging
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

OUT_DIR = Path("/Users/kenken/dev/dsa-hk/charts/pair-trade")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUT_DIR / "progress.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, mode="a"), logging.StreamHandler()],
)
log = logging.getLogger("pair_v2")

HK_CSV = Path("/Users/kenken/dev/dsa-hk/charts/hk200/top200_summary.csv")
US_CSV = Path("/Users/kenken/dev/dsa-hk/charts/us200/top200_summary.csv")

# ── Expanded sector classifier ──
SECTOR_RULES = [
    ("AI Infrastructure", [
        r"Cloudflare", r"CrowdStrike", r"Zscaler", r"Palo Alto", r"Datadog",
        r"Snowflake", r"MongoDB", r"Confluent", r"HashiCorp", r"Splunk",
        r"SentinelOne", r"Tenable", r"Rapid7", r"Sumo Logic", r"Dynatrace",
        r"AppLovin", r"Applovin", r"Unity", r"Palantir", r"C3\.ai",
        r"ServiceNow", r"NOW ", r"Workday", r"WDAY", r"Atlassian", r"TEAM",
        r"Twilio", r"Twlo", r"Zoom", r"ZM ", r"RingCentral", r"RNG",
        r"DocuSign", r"DOCU", r"Asana", r"ASAN", r"Smartsheet", r"SMAR",
    ]),
    ("Semiconductor", [
        r"NVIDIA", r"NVDA", r"AMD", r"Advanced Micro", r"Intel", r"INTC",
        r"Broadcom", r"AVGO", r"Qualcomm", r"QCOM", r"Texas Instruments", r"TXN",
        r"Applied Materials", r"AMAT", r"ASML", r"Lam Research", r"LRCX",
        r"KLA", r"Marvell", r"MRVL", r"Microchip", r"MCHP", r"Analog Devices", r"ADI",
        r"ON Semi", r"Monolithic Power", r"MPWR", r"NXP", r"NXPI", r"Skyworks", r"SWKS",
        r"Qorvo", r"QRVO", r"Synaptics", r"SYNA", r"Silicon Labs", r"SLAB",
        r"Allegro Micro", r"ALGM", r"Navitas Semiconductor", r"NVTS",
        r"SMIC", r"中芯国际", r"Hua Hong", r"华虹", r"GigaDevice", r"兆易",
        r"Montage", r"澜起", r"长电", r"长川", r"JCET", r"华润微",
        r"CAMTEK", r"ASMPT", r"力积电", r"旺矽", r"欣兴", r"南亚",
        r"Micron", r"MU\b", r"Western Digital", r"WDC", r"STX\b",
        r"SanDisk", r"SNDK", r"SK Hynix", r"海力士", r"Renesas", r"瑞萨",
        r"ARM Holdings", r"ARM ", r"Cadence", r"CDNS", r"Silicon Labs",
    ]),
    ("Bank", [
        r"HSBC", r"Hang Seng Bank", r"Bank of China", r"BOC", r"中国银行",
        r"ICBC", r"中国工商", r"CCB", r"中国建设", r"ABC", r"农业银行",
        r"JPMorgan", r"JPM", r"Bank of America", r"BAC", r"Wells Fargo", r"WFC",
        r"Goldman", r"GS ", r"Morgan Stanley", r"MS ", r"America Express", r"AXP",
        r"BlackRock", r"BLK", r"Citigroup", r"C ", r"Charles Schwab", r"SCHW",
        r"U\.S\. Bancorp", r"USB", r"PNC", r"Truist", r"TFC", r"Capital One", r"COF",
        r"American Express", r"AXP", r"State Street", r"STT", r"Northern Trust", r"NTRS",
        r"Discover", r"DFS", r"Synchrony", r"SYF", r"Ally Financial", r"ALLY",
        r"Regions", r"RF ", r"KeyCorp", r"KEY", r"M&T Bank", r"MTB",
        r"StanChart", r"Standard Chartered", r"渣打", r"BOCOM", r"交通银行",
        r"中国信达", r"中国华融", r"BOC HK", r"BOCOM", r"建设银行", r"中信",
        r"招商", r"China Merchants", r"CMB", r"招行", r"平安银行", r"Ping An Bank",
        r"民生", r"Minsheng", r"浦发", r"SPDB", r"兴业", r"Industrial Bank",
        r"光大", r"EVERBRIGHT", r"华夏", r"Hua Xia",
    ]),
    ("Insurance", [
        r"AIA", r"中国平安", r"Ping An", r"PING AN", r"中国人寿", r"China Life",
        r"CHINA LIFE", r"新华保险", r"新华", r"PICC", r"中国太保", r"CPIC",
        r"中国太平", r"China Taiping", r"Allstate", r"Progressive", r"PGR",
        r"GEICO", r"Travelers", r"TRV", r"MetLife", r"MET", r"Prudential", r"PRU",
        r"Aflac", r"AFL", r"Lincoln National", r"LNC", r"Globe Life", r"GL ",
        r"Equitable", r"EQH", r"Unum", r"UNM", r"Voya Financial", r"VOYA",
        r"Humana", r"HUM", r"UnitedHealth", r"UNH", r"Centene", r"CNC",
        r"Elevance", r"ELV", r"CVS Health", r"CVS", r"Cigna", r"CI",
        r"Molina", r"MOH",
    ]),
    ("Real Estate", [
        r"REIT", r"Link REIT", r"领展", r"Sun Hung Kai", r"新鸿基",
        r"Henderson Land", r"恒基", r"China Vanke", r"万科", r"Country Garden", r"碧桂园",
        r"Longfor", r"龙湖", r"CK Asset", r"置富", r"Fortune REIT",
        r"American Tower", r"AMT", r"Prologis", r"PLD", r"Crown Castle", r"CCI",
        r"Equinix", r"EQIX", r"Public Storage", r"PSA", r"Realty Income", r"O ",
        r"Welltower", r"WELL", r"Simon Property", r"SPG", r"Extra Space", r"EXR",
        r"AvalonBay", r"AVB", r"Equity Residential", r"EQR", r"Essex Property", r"ESS",
        r"Mid-America", r"MAA", r"UDR", r"Camber Property", r"CPT",
        r"Invitation Homes", r"INVH", r"Sun Communities", r"SUI",
    ]),
    ("EV / Auto", [
        r"Tesla", r"TSLA", r"蔚来", r"NIO", r"小鹏", r"XPEV", r"理想", r"LI ",
        r"BYD", r"比亚迪", r"Ford", r"F ", r"General Motors", r"GM ",
        r"Rivian", r"RIVN", r"Lucid", r"LCID", r"Stellantis", r"STLA",
        r"Geely", r"吉利", r"长城", r"Great Wall", r"奇瑞", r"Chery",
        r"零跑", r"LEAPMOTOR", r"XPeng", r"Polestar", r"PSNY", r"VinFast", r"VFS",
        r"Li Auto", r"Nikola", r"NKLA", r"Faraday", r"FSR", r"Canoo", r"GOEV",
        r"Toyota", r"TM ", r"Honda", r"HMC", r"BMW", r"Mercedes", r"VLKAY",
        r"Volkswagen", r"VWAGY", r"Volvo", r"VOLAF", r"Subaru", r"FUJHY",
        r"Mazda", r"MZDAY", r"Nissan", r"NSANY",
    ]),
    ("Pharma / Biotech", [
        r"Pfizer", r"PFE", r"Merck", r"MRK", r"AbbVie", r"ABBV", r"Lilly", r"LLY",
        r"Bristol-Myers", r"BMY", r"Gilead", r"GILD", r"Amgen", r"AMGN",
        r"Vertex", r"VRTX", r"Biogen", r"BIIB", r"Moderna", r"MRNA",
        r"Regeneron", r"REGN", r"Innovent", r"信达", r"Junshi", r"君实",
        r"BeiGene", r"百济", r"BEONE", r"Hengrui", r"恒瑞", r"三生", r"3SBIO",
        r"翰森", r"Hansoh", r"Alnylam", r"ALNY", r"Ionis", r"IONS",
        r"Biomarin", r"BMRN", r"Sarepta", r"SRPT", r"Genscript", r"金斯瑞",
        r"SKB Bio", r"药明", r"药明生物", r"WuXi Bio", r"WuXi AppTec",
        r"Tonghua Dongbao", r"通化东宝", r"君实", r"百奥泰", r"Bio-Thera",
        r"BeiGene", r"Hutchmed", r"和黄医药", r"HUTCHMED",
        r"INNOVENT", r"LianBio", r"Genor Biopharma",
    ]),
    ("Healthcare / Medtech", [
        r"UnitedHealth", r"Humana", r"HUM", r"CVS Health", r"CVS",
        r"Thermo Fisher", r"TMO ", r"Danaher", r"DHR", r"Intuitive", r"ISRG",
        r"Boston Scientific", r"BSX", r"Medtronic", r"MDT", r"Stryker", r"SYK",
        r"Baxter", r"BAX", r"Zimmer", r"ZBH", r"Edwards", r"EW ",
        r"Intuitive Surgical", r"Veeva", r"VEEV", r"IQVIA", r"IQV",
        r"Idexx", r"IDXX", r"ResMed", r"RMD", r"Align Technology", r"ALGN",
        r"Dexcom", r"DXCM", r"Insulet", r"PODD", r"Penumbra", r"PEN",
    ]),
    ("Energy / Oil", [
        r"Exxon", r"XOM", r"Chevron", r"CVX", r"Conoco", r"COP",
        r"Schlumberger", r"SLB", r"Occidental", r"OXY", r"EOG Resources", r"EOG",
        r"Marathon", r"MPC", r"Phillips 66", r"PSX", r"Valero", r"VLO",
        r"Devon", r"DVN", r"Diamondback", r"FANG", r"Pioneer Natural", r"PXD",
        r"APA ", r"Devon Energy", r"CNPC", r"PetroChina", r"中石油",
        r"中石化", r"Sinopec", r"CNOOC", r"中海油", r"中海油服", r"China Oilfield",
        r"哈里伯顿", r"Haliburton", r"HAL", r"Baker Hughes", r"BKR",
        r"ONEOK", r"OKE", r"Williams", r"WMB", r"Kinder Morgan", r"KMI",
        r"Cheniere", r"LNG", r"Energy Transfer", r"ET ",
        r"Halliburton", r"HAL", r"Range Resources", r"RRC", r"EQT Corp",
        r"EOG", r"Permian Resources", r"PR",
    ]),
    ("Consumer / Retail", [
        r"Walmart", r"WMT", r"Costco", r"COST", r"Target", r"TGT",
        r"Home Depot", r"HD", r"Lowe", r"LOW", r"Starbucks", r"SBUX",
        r"McDonald", r"MCD", r"Nike", r"NKE", r"Chipotle", r"CMG",
        r"Domino", r"DPZ", r"Lululemon", r"LULU", r"Alibaba", r"BABA",
        r"京东", r"JD ", r"拼多多", r"PDD", r"美团", r"Meituan",
        r"Trip\.com", r"携程", r"POP MART", r"泡泡玛特", r"百胜", r"Yum",
        r"百胜中国", r"Yum China", r"Anta", r"安踏", r"Li Ning", r"李宁",
        r"申洲", r"美的", r"Midea", r"海尔", r"Haier", r"格力", r"Gree",
        r"Ross", r"ROST", r"Burlington", r"BURL", r"TJX", r"Dollar Tree", r"DLTR",
        r"Five Below", r"FIVE", r"Ulta", r"ULTA", r"Williams-Sonoma", r"WSM",
        r"Dollar General", r"DG", r"Kroger", r"DR", r"Walgreens", r"WBA",
        r"CVS", r"Tractor Supply", r"TSCO", r"AutoZone", r"AZO",
        r"O'Reilly", r"ORLY", r"Advance Auto", r"AAP", r"Best Buy", r"BBY",
        r"Gap", r"GPS", r"Levi Strauss", r"LEVI", r"Under Armour", r"UAA",
        r"V\.F. Corp", r"VFC", r"Hanesbrands", r"HBI", r"Tapestry", r"TPR",
        r"Capri Holdings", r"CPRI", r"Bath & Body", r"BBWI", r"Kroger", r"KR",
    ]),
    ("Industrial / Defense", [
        r"Boeing", r"BA", r"Caterpillar", r"CAT", r"Deere", r"DE ",
        r"General Electric", r"GE", r"Honeywell", r"HON", r"RTX",
        r"Lockheed", r"LMT", r"Northrop", r"NOC", r"General Dynamics", r"GD",
        r"TransDigm", r"TDG", r"Eaton", r"ETN", r"Parker", r"PH", r"Emerson", r"EMR",
        r"ITW ", r"Trinity", r"Cummins", r"CMI", r"PACCAR", r"PCAR",
        r"United Rentals", r"URI", r"Deere", r"三一", r"Sany", r"中联", r"Zoomlion",
        r"XCMG", r"徐工", r"Huntington Ingalls", r"HII",
        r"L3Harris", r"LHX", r"Textron", r"TXT", r"AeroVironment", r"AVAV",
        r"Kratos", r"KTOS", r"中航沈飞", r"AVIC", r"中国航发", r"洪都",
    ]),
    ("Utility", [
        r"NextEra", r"NEE", r"Duke", r"DUK", r"Southern Co", r"SO ",
        r"Constellation", r"CEG", r"Vistra", r"VST", r"American Electric", r"AEP",
        r"Exelon", r"EXC", r"Xcel", r"XEL", r"Sempra", r"SRE",
        r"China Yangtze", r"长江电力", r"华能", r"Huaneng", r"国电", r"Datang",
        r"华润电力", r"中广核", r"CGN", r"Public Service", r"PEG",
        r"Consolidated Edison", r"ED", r"WEC Energy", r"WEC",
        r"American Water Works", r"AWK", r"Essential Utilities", r"WTRG",
    ]),
    ("Telecom", [
        r"AT&T", r"Verizon", r"VZ", r"China Mobile", r"中国移动",
        r"T-Mobile", r"TMUS", r"Comcast", r"CMCSA", r"HKT", r"电讯盈科",
        r"PCCW", r"电讯", r"香港电讯", r"China Unicom", r"中国联通",
        r"China Telecom", r"中国电信", r"中華電信", r"Chunghwa", r"CHT",
        r"Liberty Global", r"LBTYA", r"Frontier", r"FYBR", r"Lumen", r"LUMN",
        r"Iridium", r"IRDM", r"Viasat", r"VSAT",
    ]),
    ("Solar / Clean Energy", [
        r"Enphase", r"ENPH", r"First Solar", r"FSLR", r"SolarEdge", r"SEDG",
        r"Sunrun", r"RUN", r"Array Technologies", r"ARRY",
        r"Plug Power", r"PLUG", r"FuelCell", r"FCEL", r"Ballard", r"BLDP",
        r"QuantumScape", r"QS", r"信义光能", r"Xinyi Solar", r"龙源", r"Longyuan",
        r"金风", r"Goldwind", r"天合", r"Trina", r"JinkoSolar", r"JKS",
        r"Canadian Solar", r"CSIQ", r"Maxeon", r"MAXN",
    ]),
    ("Media / Entertainment", [
        r"Disney", r"DIS", r"Netflix", r"NFLX", r"Warner", r"WBD",
        r"Paramount", r"PARA", r"Spotify", r"SPOT", r"Roku", r"ROKU",
        r"Tencent Music", r"TME ", r"网易", r"NetEase", r"NTES",
        r"Bilibili", r"BILI", r"虎牙", r"HUYA", r"爱奇艺", r"IQIYI",
        r"阿里影业", r"阿里鱼", r"IMAX", r"IMAX", r"Live Nation", r"LYV",
    ]),
    ("Gold / Precious Metals", [
        r"Barrick", r"GOLD", r"Newmont", r"NEM", r"Franco-Nevada", r"FNV",
        r"Wheaton Precious", r"WPM", r"AngloGold", r"AU ",
        r"Gold Fields", r"GFI", r"紫金黄金", r"紫金", r"Zijin", r"山东黄金", r"Shandong Gold",
        r"招金", r"Zhaojin", r"中金黄金", r"China Gold", r"银泰", r"Silver",
        r"Royal Gold", r"RGLD", r"Coeur Mining", r"CDE", r"First Majestic", r"AG",
        r"Hecla Mining", r"HL",
    ]),
    ("Internet / Software", [
        r"Microsoft", r"MSFT", r"Oracle", r"ORCL", r"Salesforce", r"CRM",
        r"Adobe", r"ADBE", r"ServiceNow", r"NOW", r"Workday", r"WDAY",
        r"HubSpot", r"HUBS", r"Atlassian", r"TEAM", r"Intuit", r"INTU",
        r"Autodesk", r"ADSK", r"Workiva", r"WK", r"Twilio", r"TWLO",
        r"Zoom", r"ZM", r"DocuSign", r"DOCU", r"Slack", r"金蝶", r"Kingdee",
        r"用友", r"Yonyou", r"明源云", r"Ming Yuan", r"Wix\.com", r"WIX",
        r"GoDaddy", r"GDDY", r"Veeva", r"VEEV", r"Procore", r"PCOR",
    ]),
    ("Crypto / Fintech", [
        r"Coinbase", r"COIN", r"MicroStrategy", r"MSTR", r"Strategy Inc",
        r"Robinhood", r"HOOD", r"PayPal", r"PYPL", r"Block", r"SQ",
        r"SoFi", r"SOFI", r"Upstart", r"UPST", r"Riot Platforms", r"RIOT",
        r"Marathon Digital", r"MARA", r"CleanSpark", r"CLSK", r"Hut 8", r"HUT",
        r"Hive Digital", r"HIVE", r"Cipher Mining", r"CIFR",
        r"Core Scientific", r"CORZ", r"Bitfarms", r"BITF",
        r"Applied Digital", r"APLD", r"Iren", r"IREN", r"Bitdeer", r"BTDR",
        r"Hut 8 Mining", r"Bitfarms",
    ]),
    ("Logistics / Shipping", [
        r"UPS", r"FedEx", r"FDX", r"中远海控", r"COSCO", r"OOCL",
        r"东方海外", r"CIMC", r"中集", r"嘉里物流", r"Kerry Logistics",
        r"嘉里", r"嘉里建设", r"阳明", r"Yang Ming", r"万海", r"Wan Hai",
        r"长荣", r"Evergreen", r"ZIM", r"Matson", r"MATX", r"Kuehne",
        r"DSV", r"Expeditors", r"EXPD", r"CH Robinson", r"CHRW",
    ]),
    ("Internet China ADR", [
        r"BABA", r"Alibaba", r"JD ", r"京东", r"PDD", r"拼多多",
        r"BIDU", r"百度", r"NIO", r"蔚来", r"XPEV", r"小鹏",
        r"LI ", r"理想", r"TME ", r"网易", r"BILI", r"TAL",
        r"YUMC", r"百胜中国", r"NTES", r"NetEase",
    ]),
    ("Holding / Conglomerate", [
        r"CK Hutchison", r"CKH", r"长江实业", r"长江和记", r"太古", r"Swire",
        r"国泰", r"Cathay", r"信和", r"Sino", r"恒隆", r"Hang Lung",
        r"九龙仓", r"Wharf", r"会德丰", r"Wheelock", r"Berkshire", r"BRK",
        r"嘉道理", r"Richard Li", r"李嘉诚",
    ]),
    ("Luxury / Brand", [
        r"LVMH", r"LVMUY", r"Hermes", r"HESAY", r"Kering", r"PPRUY",
        r"Richemont", r"CFRUY", r"Prada", r"PRDSY", r"Burberry", r"BURBY",
        r"Tapestry", r"TPR", r"Capri", r"CPRI", r"Ralph Lauren", r"RL",
        r"Estee Lauder", r"EL ", r"L'Oreal", r"OR ", r"P&G", r"PG",
        r"Unilever", r"UL ", r"Reckitt", r"RBGLY", r"Colgate", r"CL",
    ]),
]


def classify(name: str, ticker: str) -> str:
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


# ── 1) Real 5d return + 2) Cointegration test ──
def fetch_30d(ticker: str, market: str) -> Optional[pd.Series]:
    """Fetch 30d daily close prices."""
    try:
        if market == "US":
            yf_t = ticker
        else:
            digits = ticker.replace(".HK", "")
            yf_t = f"{int(digits)}.HK"
        hist = yf.Ticker(yf_t).history(period="40d", auto_adjust=False)
        if hist is None or hist.empty or len(hist) < 15:
            return None
        # Strip timezone to allow cross-market alignment
        hist.index = hist.index.tz_localize(None) if hist.index.tz else hist.index
        closes = hist["Close"].dropna()
        return closes
    except Exception:
        return None


def compute_correlation(s1: pd.Series, s2: pd.Series, window: int = 20) -> float:
    """Pearson correlation over last `window` days."""
    aligned = pd.concat([s1, s2], axis=1).dropna()
    if len(aligned) < window:
        return 0.0
    recent = aligned.tail(window)
    s_a = recent.iloc[:, 0]
    s_b = recent.iloc[:, 1]
    if s_a.std() == 0 or s_b.std() == 0:
        return 0.0
    return float(s_a.corr(s_b))


def compute_coint_pvalue(s1: pd.Series, s2: pd.Series) -> float:
    """Cointegration test (Engle-Granger). Returns p-value (lower = more coint)."""
    try:
        from statsmodels.tsa.stattools import coint
        aligned = pd.concat([s1, s2], axis=1).dropna()
        if len(aligned) < 20:
            return 1.0
        # p-value < 0.05 typically means cointegrated
        _, pvalue, _ = coint(aligned.iloc[:, 0], aligned.iloc[:, 1])
        return float(pvalue)
    except Exception:
        return 1.0


def compute_hedge_ratio(s1: pd.Series, s2: pd.Series) -> tuple[float, float]:
    """OLS hedge ratio (beta) and intercept. Used for L/S ratio."""
    try:
        aligned = pd.concat([s1, s2], axis=1).dropna()
        if len(aligned) < 20:
            return 1.0, 0.0
        y = aligned.iloc[:, 0]  # long leg
        x = aligned.iloc[:, 1]  # short leg
        beta = float(np.cov(y, x, ddof=0)[0, 1] / np.var(x, ddof=0)) if np.var(x, ddof=0) > 0 else 1.0
        alpha = float(np.mean(y) - beta * np.mean(x))
        return beta, alpha
    except Exception:
        return 1.0, 0.0


def compute_ls_ratio(prices_long: pd.Series, prices_short: pd.Series, hedge_beta: float) -> float:
    """Compute long/short ratio for dollar-neutral: short_dollars / long_dollars = (vol_long) / (vol_short * beta)."""
    try:
        rets_long = prices_long.pct_change().dropna().tail(20)
        rets_short = prices_short.pct_change().dropna().tail(20)
        if len(rets_long) < 5 or len(rets_short) < 5:
            return 1.0
        vol_long = float(rets_long.std())
        vol_short = float(rets_short.std())
        if vol_short == 0:
            return 1.0
        # For dollar-neutral: short_notional = long_notional * (vol_long / (vol_short * beta))
        ratio = (vol_long / (vol_short * abs(hedge_beta)))
        return float(ratio)
    except Exception:
        return 1.0


def find_pairs_v2(hk_df: pd.DataFrame, us_df: pd.DataFrame,
                  hk_prices: dict, us_prices: dict) -> list[dict]:
    """Find pair candidates with all 4 enhancements."""
    all_df = pd.concat([hk_df, us_df], ignore_index=True)
    all_df["sector"] = all_df["name"].apply(lambda n: classify(str(n), ""))

    pairs = []
    for sector in all_df["sector"].unique():
        sector_df = all_df[all_df["sector"] == sector]
        if len(sector_df) < 2:
            continue
        sorted_df = sector_df.sort_values("chg_pct", ascending=False)
        for i in range(min(3, len(sorted_df) - 1)):
            for j in range(len(sorted_df) - 1, max(len(sorted_df) - 4, i), -1):
                long_pick = sorted_df.iloc[i]
                short_pick = sorted_df.iloc[j]
                if long_pick["ticker"] == short_pick["ticker"]:
                    continue

                long_p = hk_prices.get(long_pick["ticker"]) if long_pick["market"] == "HK" else us_prices.get(long_pick["ticker"])
                short_p = hk_prices.get(short_pick["ticker"]) if short_pick["market"] == "HK" else us_prices.get(short_pick["ticker"])
                if long_p is None or short_p is None:
                    continue

                # Compute metrics
                corr = compute_correlation(long_p, short_p, window=20)
                coint_p = compute_coint_pvalue(long_p, short_p)
                beta, alpha = compute_hedge_ratio(long_p, short_p)
                ls_ratio = compute_ls_ratio(long_p, short_p, beta)

                # Phase mismatch bonus
                phase_bonus = 0
                if long_pick["phase"] in ("uptrend", "base_building") and short_pick["phase"] in ("downtrend_active", "downtrend_recovery"):
                    phase_bonus = 5
                if long_pick["phase"] in ("downtrend_active", "downtrend_recovery") and short_pick["phase"] in ("uptrend", "base_building"):
                    phase_bonus = -5

                div_chg = float(long_pick["chg_pct"] or 0) - float(short_pick["chg_pct"] or 0)
                # Composite score: high corr + low p-value + phase bonus
                score = (abs(corr) * 10) + ((1 - coint_p) * 10) + phase_bonus + (abs(div_chg) * 0.5)

                pairs.append({
                    "sector": sector,
                    "long_ticker": long_pick["ticker"],
                    "long_name": long_pick["name"],
                    "long_market": long_pick["market"],
                    "long_price": float(long_pick["last"]),
                    "long_phase": long_pick["phase"],
                    "long_chg_pct": float(long_pick["chg_pct"] or 0),
                    "short_ticker": short_pick["ticker"],
                    "short_name": short_pick["name"],
                    "short_market": short_pick["market"],
                    "short_price": float(short_pick["last"]),
                    "short_phase": short_pick["phase"],
                    "short_chg_pct": float(short_pick["chg_pct"] or 0),
                    "correlation_20d": round(corr, 3),
                    "coint_pvalue": round(coint_p, 3),
                    "hedge_beta": round(beta, 3),
                    "ls_ratio": round(ls_ratio, 2),
                    "chg_div": round(div_chg, 2),
                    "phase_bonus": phase_bonus,
                    "score": round(score, 2),
                })

    pairs.sort(key=lambda p: -p["score"])
    return pairs


def main():
    log.info("=" * 60)
    log.info(f"Enhanced pair screen v2 @ {datetime.now().isoformat()}")
    log.info("=" * 60)

    hk = load_universe(HK_CSV, "HK")
    us = load_universe(US_CSV, "US")
    log.info(f"  HK: {len(hk)} | US: {len(us)}")
    # Sector distribution with new classifier
    all_df = pd.concat([hk, us])
    log.info(f"  Sector distribution: {all_df['sector'].value_counts().to_dict()}")

    # ── Fetch 30d prices for all tickers ──
    log.info("Fetching 30d price data via yfinance (parallel)...")
    all_tickers = [(t, m) for t, m in zip(hk["ticker"], ["HK"] * len(hk))] + \
                   [(t, m) for t, m in zip(us["ticker"], ["US"] * len(us))]

    def fetch_one(t, m):
        return (t, m, fetch_30d(t, m))

    hk_prices = {}
    us_prices = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(fetch_one, t, m) for t, m in all_tickers]
        done = 0
        for fut in as_completed(futures):
            t, m, prices = fut.result()
            if prices is not None and not prices.empty:
                if m == "HK":
                    hk_prices[t] = prices
                else:
                    us_prices[t] = prices
            done += 1
            if done % 50 == 0:
                log.info(f"  price fetch: {done}/{len(all_tickers)}")
    log.info(f"  prices: HK {len(hk_prices)} | US {len(us_prices)}")

    # ── Find pairs with all 4 metrics ──
    log.info("Finding pairs with full analysis...")
    pairs = find_pairs_v2(hk, us, hk_prices, us_prices)
    log.info(f"  found {len(pairs)} pair candidates")

    # Save
    df = pd.DataFrame(pairs)
    df.to_csv(OUT_DIR / "pair_trades_v2.csv", index=False)
    log.info(f"  saved → {OUT_DIR / 'pair_trades_v2.csv'}")

    # Show top 20
    log.info("\n" + "=" * 60)
    log.info("TOP 20 ENHANCED PAIRS (corr + coint + L/S ratio)")
    log.info("=" * 60)
    for p in pairs[:20]:
        arrow = "↑" if p["chg_div"] > 0 else "↓"
        coint_emoji = "🟢" if p["coint_pvalue"] < 0.05 else ("🟡" if p["coint_pvalue"] < 0.20 else "⚪")
        log.info(
            f"  {p['sector']:22s} {arrow} "
            f"LONG {p['long_ticker']:10s} ({p['long_market']}) {p['long_phase'][:5]:5s} | "
            f"SHORT {p['short_ticker']:10s} ({p['short_market']}) {p['short_phase'][:5]:5s} | "
            f"corr:{p['correlation_20d']:+.2f} coint:{coint_emoji}{p['coint_pvalue']:.2f} β:{p['hedge_beta']:.2f} L/S:{p['ls_ratio']:.1f} | "
            f"score:{p['score']:.1f}"
        )

    log.info("=" * 60)
    log.info(f"Done. {len(pairs)} pairs in {OUT_DIR / 'pair_trades_v2.csv'}")


if __name__ == "__main__":
    main()
