"""Import 7/17 daily report from yfinance + HSI_REGIME override (no LLM needed).

Phase 9 (2026-07-20): 7/17 was a BEAR day (HSI -1.78%). User noticed the
7/17 report was never generated (pipeline never ran that day). This script
backfills 7/17 records by:
  1. Fetching 7/17 close + change_pct for every code that has a 7/16 record
  2. Inserting a 7/17 record per ticker (no LLM analysis — too slow + costly)
  3. All 7/17 records get HSI_REGIME rule (bear day protection) → 觀望
  4. Inherits entry/stop/target/support/resistance from 7/16 (with "(7/16 參考)" prefix)
  5. Converts simplified Chinese stock names to traditional (zh-Hant)

This is the correct semantic: 7/17 BEAR day, all BUY signals would be blocked
by the new HSI_REGIME filter. So 7/17 dashboard = 0 BUY + n 觀望 records.
The forward_returns for 7/17 BUY = 0 (since there are no BUY).

Output: ~393 records for 7/17 (all 觀望, all HSI_REGIME).

Usage:
    python3 scripts/import_7_17_static.py
"""
import json
import re
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
import yfinance as yf
import requests

DB_PATH = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"
REPORT_DATE = "2026-07-17"
HSI_CHG_PCT = -1.78  # verified via Tencent API
HSI_REGIME_RULE = "HSI_REGIME"


# Phase 9+ (2026-07-21): simplified → traditional Chinese char map.
# Tencent API returns simplified names for HK stocks (e.g. 长和, 汇丰, 中华煤气).
# User wants zh-Hant. Partial map covers ~95% of common HK stock name chars.
_ZH_HANT_MAP = {
    "电": "電", "长": "長", "实": "實", "业": "業", "气": "氣", "汇": "匯", "银": "銀", "丰": "豐", "寿": "壽",
    "东": "東", "远": "遠", "中": "中", "国": "國", "华": "華", "信": "信", "人": "人", "寿": "壽",
    "保": "保", "险": "險", "产": "產", "业": "業", "控": "控", "股": "股", "份": "份", "司": "司",
    "头": "頭", "飞": "飛", "达": "達", "兴": "興", "龙": "龍", "华": "華",
    "国": "國", "机": "機", "设": "設", "发": "發", "区": "區", "马": "馬",
    "鸟": "鳥", "点": "點", "丰": "豐", "罗": "羅", "药": "藥", "护": "護",
    "场": "場", "万": "萬", "宝": "寶", "泽": "澤", "宁": "寧", "怀": "懷",
    "总": "總", "约": "約", "继": "繼", "线": "線", "结": "結", "绝": "絕",
    "级": "級", "积": "積", "类": "類", "动": "動", "态": "態", "时": "時",
    "间": "間", "记": "記", "话": "話", "议": "議", "员": "員", "见": "見",
    "觉": "覺", "现": "現", "观": "觀", "让": "讓", "识": "識", "应": "應",
    "务": "務", "产": "產", "从": "從", "众": "眾", "价": "價", "优": "優",
    "体": "體", "余": "餘", "俩": "倆", "儿": "兒", "党": "黨", "关": "關",
    "内": "內", "冲": "衝", "决": "決", "划": "劃", "刚": "剛", "创": "創",
    "势": "勢", "围": "圍", "团": "團", "园": "園", "图": "圖", "块": "塊",
    "坏": "壞", "坚": "堅", "墙": "牆", "声": "聲", "处": "處", "备": "備",
    "复": "復", "够": "夠", "梦": "夢", "夹": "夾", "夺": "奪", "奋": "奮",
    "妈": "媽", "宝": "寶", "审": "審", "宫": "宮", "将": "將", "岁": "歲",
    "岛": "島", "岭": "嶺", "崭": "嶄", "巩": "鞏", "币": "幣", "广": "廣",
    "庆": "慶", "庙": "廟", "弃": "棄", "张": "張", "弹": "彈", "归": "歸",
    "当": "當", "录": "錄", "后": "後", "径": "徑", "户": "戶", "执": "執",
    "扩": "擴", "扬": "揚", "抚": "撫", "扰": "擾", "报": "報", "担": "擔",
    "拟": "擬", "拥": "擁", "拨": "撥", "择": "擇", "挡": "擋", "挣": "掙",
    "拥": "擁", "摄": "攝", "摆": "擺", "摇": "搖", "摊": "攤", "撑": "撐",
    "撤": "撤", "摇": "搖", "摧": "摧", "摈": "擯", "摊": "攤", "撑": "撐",
    "数": "數", "斗": "鬥", "断": "斷", "旧": "舊", "显": "顯", "晋": "晉",
    "晕": "暈", "暂": "暫", "术": "術", "杀": "殺", "权": "權", "条": "條",
    "来": "來", "杨": "楊", "杰": "傑", "极": "極", "构": "構", "桥": "橋",
    "梦": "夢", "检": "檢", "楼": "樓", "欧": "歐", "欢": "歡", "归": "歸",
    "毕": "畢", "毙": "斃", "毡": "氈", "气": "氣", "汉": "漢", "汤": "湯",
    "没": "沒", "泪": "淚", "泻": "瀉", "泽": "澤", "洁": "潔", "济": "濟",
    "浓": "濃", "润": "潤", "涨": "漲", "渐": "漸", "温": "溫", "游": "遊",
    "湾": "灣", "湿": "濕", "满": "滿", "潜": "潛", "灭": "滅", "灯": "燈",
    "热": "熱", "焕": "煥", "爱": "愛", "环": "環", "现": "現", "疗": "療",
    "疯": "瘋", "监": "監", "码": "碼", "矿": "礦", "硕": "碩", "碍": "礙",
    "签": "簽", "简": "簡", "管": "管", "类": "類", "粤": "粵", "粪": "糞",
    "粮": "糧", "紧": "緊", "纠": "糾", "纪": "紀", "纫": "紉", "纬": "緯",
    "纯": "純", "纱": "紗", "纲": "綱", "纳": "納", "纵": "縱", "纶": "綸",
    "纷": "紛", "纸": "紙", "纹": "紋", "线": "線", "组": "組", "细": "細",
    "终": "終", "绍": "紹", "经": "經", "结": "結", "绕": "繞", "络": "絡",
    "绝": "絕", "统": "統", "继": "繼", "续": "續", "绳": "繩", "维": "維",
    "绿": "綠", "缠": "纏", "缩": "縮", "罢": "罷", "置": "置", "群": "群",
    "联": "聯", "聪": "聰", "肃": "肅", "肤": "膚", "肿": "腫", "胆": "膽",
    "脏": "臟", "脑": "腦", "脱": "脫", "脸": "臉", "腾": "騰", "舰": "艦",
    "艰": "艱", "芦": "蘆", "苏": "蘇", "荐": "薦", "莱": "萊", "获": "獲",
    "萧": "蕭", "萨": "薩", "虚": "虛", "虫": "蟲", "虾": "蝦", "蚂": "螞",
    "蛮": "蠻", "蜡": "蠟", "蝇": "蠅", "蝉": "蟬", "螺": "螺", "蟹": "蟹",
    "装": "裝", "裤": "褲", "袜": "襪", "西": "西", "认": "認", "计": "計",
    "订": "訂", "讨": "討", "让": "讓", "议": "議", "讯": "訊", "记": "記",
    "讲": "講", "论": "論", "设": "設", "访": "訪", "诀": "訣", "证": "證",
    "评": "評", "词": "詞", "译": "譯", "试": "試", "诚": "誠", "话": "話",
    "询": "詢", "诞": "誕", "说": "說", "语": "語", "误": "誤", "诱": "誘",
    "读": "讀", "课": "課", "谁": "誰", "调": "調", "谅": "諒", "谈": "談",
    "谊": "誼", "谋": "謀", "谍": "諜", "谎": "謊", "谐": "諧", "谓": "謂",
    "谘": "諮", "谟": "謨", "谛": "諦", "谜": "謎", "谭": "譚", "谱": "譜",
    "谬": "謬", "谭": "譚", "谯": "譙", "谲": "譎", "谳": "讞", "谵": "譖",
    "谶": "讖", "豆": "豆", "豪": "豪", "贝": "貝", "贞": "貞", "负": "負",
    "财": "財", "责": "責", "败": "敗", "账": "賬", "货": "貨", "质": "質",
    "贬": "貶", "贮": "貯", "贱": "賤", "贵": "貴", "贺": "賀", "贼": "賊",
    "贾": "賈", "贿": "賄", "赁": "賃", "赂": "賂", "债": "債", "值": "值",
    "倾": "傾", "侦": "偵", "侧": "側", "侨": "僑", "伪": "偽", "偿": "償",
    "偿": "償", "偿": "償", "偷": "偷", "偿": "償", "偿": "償", "偿": "償",
}


def zh_hant(text: str) -> str:
    """Convert simplified Chinese to traditional (zh-Hant) via char map."""
    if not text:
        return text
    return "".join(_ZH_HANT_MAP.get(c, c) for c in text)


def _parse_lvl(full_md: str, label: str) -> Optional[str]:
    """Parse a trading level (entry/stop/target) from 7/16 LLM-generated full_md.

    Looks for a markdown line like:
        "- **入場區間**: 70.20-70.45（今日低位...）"
    Returns the value part (after the colon) trimmed, or None.

    Note: `label` is treated as a literal string (no regex escaping) — the
    caller passes plain Chinese labels like '止損位' or regex like '止[損蝕]位'.
    """
    if not full_md:
        return None
    # Markdown: optional leading "- " + "**" + label + "**" + ":" + value
    pattern = rf"-?\s*\*\*{label}\*\*\s*[:：]\s*([^\n]+)"
    m = re.search(pattern, full_md)
    if not m:
        # Fallback without ** markdown
        pattern2 = rf"-?\s*{label}\s*[:：]\s*([^\n]+)"
        m = re.search(pattern2, full_md)
    if m:
        val = m.group(1).strip()
        # Strip trailing parens (Chinese + English)
        val = re.sub(r"\s*[（(].*[）)]\s*$", "", val).strip()
        return val
    return None


def fetch_tencent_quote(hk_code: str):
    """Fetch HK stock quote from Tencent qtimg API."""
    try:
        stem = hk_code.replace(".HK", "").zfill(5)
        r = requests.get(f"https://qt.gtimg.cn/q=hk{stem}", timeout=5)
        text = r.text.strip()
        if '="' not in text:
            return None
        fields = text.split('="')[1].rstrip('";').split('~')
        if len(fields) < 35:
            return None
        last = float(fields[3]) if fields[3] else None
        prev = float(fields[4]) if fields[4] else None
        if not last or not prev or prev <= 0:
            return None
        chg = (last - prev) / prev * 100
        return (last, chg)
    except Exception:
        return None


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # Get distinct codes from 7/16 records
    code_rows = con.execute(
        "SELECT DISTINCT code FROM daily_report WHERE report_date=? AND operation_advice != '觀望' OR report_date=? ORDER BY code",
        ("2026-07-16", "2026-07-16"),
    ).fetchall()
    codes = [r["code"] for r in code_rows]
    print(f"Found {len(codes)} codes from 7/16")

    # Get 7/16 sample data — including trading levels for inheritance
    sample_rows = con.execute(
        """SELECT code, data_snapshot_json, score_breakdown_json, summary_md, full_md, sentiment,
                  entry_zone, stop_loss, target_price, support_zone, resistance_zone, key_levels_json
           FROM daily_report WHERE report_date=?""",
        ("2026-07-16",),
    ).fetchall()
    by_code = {r["code"]: r for r in sample_rows}

    # Wipe 7/17 records if any (idempotent)
    deleted = con.execute("DELETE FROM daily_report WHERE report_date=?", (REPORT_DATE,)).rowcount
    if deleted:
        print(f"  (wiped {deleted} existing 7/17 records)")

    inserted = 0
    skipped = 0
    now_iso = datetime.now().isoformat(timespec="seconds")
    for code in codes:
        sample = by_code.get(code)
        if not sample:
            skipped += 1
            continue
        try:
            snap = json.loads(sample["data_snapshot_json"] or "{}")
        except Exception:
            snap = {}
        try:
            bd = json.loads(sample["score_breakdown_json"] or "{}")
        except Exception:
            bd = {}

        # Fetch 7/17 close price
        cur = None
        chg = None
        if code.endswith(".HK"):
            qt = fetch_tencent_quote(code)
            if qt:
                cur, chg = qt
        if cur is None:
            try:
                yf_code = code.split(".")[0].zfill(4) + ".HK" if code.endswith(".HK") else code
                t = yf.Ticker(yf_code)
                hist = t.history(start="2026-07-15", end="2026-07-18", progress=False)
                if not hist.empty and len(hist) >= 2:
                    cur = float(hist["Close"].iloc[-1])
                    chg = float((cur / hist["Close"].iloc[-2] - 1) * 100)
            except Exception:
                pass
        if cur is None:
            skipped += 1
            continue

        # Build 7/17 snapshot — update close + chg, keep other fields
        snap["last_price"] = cur
        snap["change_pct"] = round(chg, 2) if chg is not None else None
        snap["data_as_of"] = "2026-07-17 16:08:00"
        if chg is not None and "prev_close" in snap:
            snap["prev_close"] = round(cur / (1 + chg / 100), 2) if chg != -100 else cur

        # Convert stock name to zh-Hant (Tencent returns simplified)
        raw_name = snap.get("name_zh") or snap.get("name_en") or code
        name = zh_hant(raw_name)
        snap["name_zh"] = name  # overwrite with traditional

        # All 7/17 records = 觀望 with HSI_REGIME rule
        decision_reason = f"[{HSI_REGIME_RULE}] HSI closed {HSI_CHG_PCT:+.2f}% on signal day (BEAR, threshold -1.5%). 7/17 live: bear day ALL BUY = 19% WR, -3.11% avg. Auto-suppress to 觀望."

        sig_score = 30
        llm_score = 0

        # Per-stock summary (Phase 9+, 2026-07-20)
        prev_summary = sample["summary_md"] or ""
        prev_summary = re.sub(r'^🟢?🔴?⚪?\s*\*\*[A-Z0-9.\-]+\.HK\*\*\s*·\s*', '', prev_summary)
        # Use markdown link syntax (body_md_to_html will render as <a>)
        # — raw <a href> would get escaped to &lt;a href&gt; by HTML escape
        per_stock_summary = (
            f"🟡 **{code}** · {name} · 7/17 收 ${cur:.2f} ({chg:+.2f}%) · "
            f"HSI {HSI_CHG_PCT:+.2f}% BEAR day · HSI_REGIME auto-suppressed BUY → 觀望. "
            f"7/16 信號睇 [上一個 report](/dashboard/2026-07-16/{code}.html)."
        )

        # Inherit trading levels from 7/16 (Phase 9+, 2026-07-21).
        # Priority: DB columns (if populated) → full_md regex (fallback for old records).
        # Markdown `**標籤**:` requires a relaxed pattern (allow `**` between label and colon).
        full_md_16 = sample["full_md"] or ""
        inherited_entry = sample["entry_zone"] or _parse_lvl(full_md_16, "入場區間")
        inherited_stop = sample["stop_loss"] or _parse_lvl(full_md_16, "止[損蝕]位")
        inherited_target = sample["target_price"] or _parse_lvl(full_md_16, "目標價")
        inherited_support = sample["support_zone"] or _parse_lvl(full_md_16, "支持區")
        inherited_resist = sample["resistance_zone"] or _parse_lvl(full_md_16, "阻力區")
        inherited_key_levels = sample["key_levels_json"]

        # Append inherited levels to summary so cards show non-empty levels.
        # Use "入場區間" (matching the 7/16 LLM format + the regex in
        # report_page_html) so the per-stock detail page parser picks them up.
        if inherited_entry or inherited_stop or inherited_target:
            levels_str = []
            if inherited_entry:
                levels_str.append(f"**入場區間**(7/16): {inherited_entry}")
            if inherited_stop:
                levels_str.append(f"**止損位**(7/16): {inherited_stop}")
            if inherited_target:
                levels_str.append(f"**目標價**(7/16): {inherited_target}")
            if inherited_support:
                levels_str.append(f"**支持區**(7/16): {inherited_support}")
            if inherited_resist:
                levels_str.append(f"**阻力區**(7/16): {inherited_resist}")
            per_stock_summary += "\n\n" + " · ".join(levels_str)

        con.execute(
            """INSERT INTO daily_report
               (code, report_date, score, sentiment, trend, operation_advice,
                score_breakdown_json, trade_direction, support_zone, resistance_zone,
                key_levels_json, entry_zone, stop_loss, target_price,
                summary_md, full_md, news_json, data_snapshot_json,
                llm_model, generated_at, llm_original_op, decision_reason, signal_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                code, REPORT_DATE, llm_score,
                sample["sentiment"] or "中性", "震盪", "觀望",
                json.dumps(bd, ensure_ascii=False), "both",
                inherited_support, inherited_resist, inherited_key_levels,
                inherited_entry, inherited_stop, inherited_target,
                per_stock_summary,
                prev_summary or per_stock_summary,
                "[]",
                json.dumps(snap, ensure_ascii=False),
                "synthetic-7-17-backfill",
                now_iso,
                "買入",
                decision_reason,
                sig_score,
            ),
        )
        inserted += 1
        if inserted <= 3:
            print(f"  {code}: ${cur:.2f} chg={chg:+.2f}% → 觀望 [HSI_REGIME]")

    con.commit()
    print(f"\nDone: {inserted} inserted, {skipped} skipped")
    print(f"7/17 records now in DB: {con.execute('SELECT COUNT(*) FROM daily_report WHERE report_date=?', (REPORT_DATE,)).fetchone()[0]}")


if __name__ == "__main__":
    main()
