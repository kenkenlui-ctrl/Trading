"""Import 7/15 daily_report records from static HTML reports into DB.

Phase 7 (2026-07-17): Owner wants 7/15 to be a permanent part of the DB
so build_static.py auto-generates the dashboard hub card going forward.
Source: public/dashboard/2026-07-15/reports/*.html (600 files from
another machine, 2026-07-16 13:25 HKT).

Parser extracts from each per-ticker HTML:
  - code (filename)
  - operation_advice (買入/觀望/賣出 from .badge.op)
  - signal_score / score (下日勝率 % from .badge.win-score)
  - sentiment, trend, confidence (from 評分 line)
  - 4 sub-scores (value/quality/momentum/order_flow) from progress bars
  - levels (entry_zone, stop_loss, target_price, support/resistance)
  - data_snapshot (price, change_pct, day_high/low, pe_ttm, etc. from markdown)
  - summary_md, full_md (markdown body)

Output: INSERT into daily_report table for report_date='2026-07-15'.
"""
import json
import re
import sqlite3
import sys
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path("/Users/kenken/Documents/dsa-hk/data/dsa_hk.db")
REPORTS_DIR = Path("/Users/kenken/Documents/dsa-hk/public/dashboard/2026-07-15/reports")
REPORT_DATE = "2026-07-15"


def extract_first_int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text)
    if m:
        try:
            return int(m.group(1))
        except (ValueError, TypeError):
            return None
    return None


def extract_first_float(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except (ValueError, TypeError):
            return None
    return None


def parse_report(html_path: Path) -> dict | None:
    """Parse one per-ticker HTML report and return a dict of fields for daily_report."""
    try:
        h = html_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  read err {html_path.name}: {e}")
        return None

    code = html_path.stem

    # Op + 下日勝率 from header badges
    op_m = re.search(r'<span class="badge op">([^<]+)</span>', h)
    if not op_m:
        return None
    op = op_m.group(1).strip()

    wr_m = re.search(r'<span class="badge win-score"[^>]*>下日勝率\s*(\d+)%</span>', h)
    score = int(wr_m.group(1)) if wr_m else 50  # default neutral

    # 評分 / sentiment / trend / op (core line)
    core_m = re.search(
        r'<b>➡️ 評分\s*(\d+)/100</b>\s*·\s*([^·]+)·\s*([^·]+)·\s*<b>([^<]+)</b>\s*·\s*信心\s*(\S+)',
        h,
    )
    narrative_score = int(core_m.group(1)) if core_m else score
    sentiment = core_m.group(2).strip() if core_m else "中性"
    trend = core_m.group(3).strip() if core_m else "震盪"
    confidence = core_m.group(5).strip() if core_m else "中"

    # 4 sub-scores from score-breakdown divs
    # Each: <div class="dim"><span>估值 5%</span><div class="bar"><div class="fill" style="width:70%;..."></div></div><b>70</b></div>
    # The whole breakdown block has 4 such divs nested in one parent.
    # We extract each individual <div class="dim"> block.
    value_score = quality_score = momentum_score = order_flow_score = news_score = None
    for m in re.finditer(
        r'<div class="dim"><span>([^<]+)</span><div class="bar"><div class="fill"[^>]*width:(\d+)%[^"]*"[^>]*></div></div><b>(\d+)</b></div>',
        h,
        re.DOTALL,
    ):
        label, width, val = m.group(1), int(m.group(2)), int(m.group(3))
        if '估值' in label: value_score = val
        elif '質素' in label: quality_score = val
        elif '動能' in label: momentum_score = val
        elif '資金流' in label: order_flow_score = val
    # news_score: not in breakdown, default 50 (neutral)
    news_score = 50

    # Levels
    lv_block = re.search(r'<div class="levels">(.*?)</div></div>', h, re.DOTALL)
    entry_zone = stop_loss = target_price = support_zone = resistance_zone = None
    if lv_block:
        for m in re.finditer(r'<div><span>([^<]+)</span><b[^>]*>([^<]+)</b></div>', lv_block.group(1)):
            label, val = m.group(1).strip(), m.group(2).strip()
            if '入場' in label: entry_zone = val
            elif '止損' in label: stop_loss = val
            elif '目標' in label: target_price = val
            elif '支持' in label: support_zone = val
            elif '阻力' in label: resistance_zone = val

    # 現價
    price_m = re.search(r'<b>現價</b>:\s*([\d.]+)\s*(\S+)\s*\(([+-]?\d+\.?\d*)%\)', h)
    last_price = float(price_m.group(1)) if price_m else None
    currency = price_m.group(2) if price_m else None
    change_pct = float(price_m.group(3)) if price_m else None

    # Data table — in <p>|...</p> format
    # Extract key-value pairs
    data_table = {}
    for m in re.finditer(r'<p>\| ([^|]+) \| ([^|]+) \|</p>', h):
        k = m.group(1).strip()
        v = m.group(2).strip()
        if v not in ("---", "0", ""):
            data_table[k] = v

    # Day high / low / open
    def safe_float(v):
        if v in (None, "", "0", "None", "N/A", "—", "-"): return None
        try: return float(v.replace(",", ""))
        except (ValueError, TypeError): return None
    day_high = safe_float(data_table.get("day_high_value"))
    day_low = safe_float(data_table.get("day_low_value"))
    ma20 = safe_float(data_table.get("ma20_value"))
    ma50 = safe_float(data_table.get("ma50_value"))
    ma100 = safe_float(data_table.get("ma100_value"))
    ma200 = safe_float(data_table.get("ma200_value"))
    support_floor = safe_float(data_table.get("support_floor"))
    support_ceiling = safe_float(data_table.get("support_ceiling"))
    resistance_target = safe_float(data_table.get("resistance_target"))

    # Extract from prose (PE TTM, 股息率, PB, 52w hi/lo, 成交額, 市值)
    pe_ttm = None
    pe_m = re.search(r'PE\s*TTM\s*(?:僅|約|為|為約)?\s*([\d.]+)', h)
    if pe_m: pe_ttm = float(pe_m.group(1))

    dividend_yield = None
    dy_m = re.search(r'股息率\s*([\d.]+)\s*%', h)
    if dy_m: dividend_yield = float(dy_m.group(1))

    pb = None
    pb_m = re.search(r'(?:^|\s)PB\s*(?:僅|約|為|為約)?\s*([\d.]+)', h)
    if pb_m: pb = float(pb_m.group(1))

    high_52w = None
    h_m = re.search(r'52\s*週\s*高位\s*([\d.]+)', h)
    if h_m: high_52w = float(h_m.group(1))
    low_52w = None
    l_m = re.search(r'52\s*週\s*低位\s*([\d.]+)', h)
    if l_m: low_52w = float(l_m.group(1))

    turnover_m_hkd = None
    if currency == "HKD" or ".HK" in code:
        to_m = re.search(r'成交額\s*(?:僅|約|為)?\s*([\d,.]+)\s*萬\s*HKD', h)
        if to_m:
            turnover_m_hkd = float(to_m.group(1).replace(",", "")) / 100  # 萬 → 百萬
        else:
            to_m2 = re.search(r'成交額\s*(?:僅|約|為)?\s*([\d,.]+)\s*億\s*HKD', h)
            if to_m2:
                turnover_m_hkd = float(to_m2.group(1).replace(",", "")) * 100  # 億 → 百萬
    else:
        to_m = re.search(r"(?:turnover|交易額|成交額)\s*\$?\s*([\d.]+)\s*([BMK]?)", h)
        if to_m:
            num = float(to_m.group(1))
            unit = to_m.group(2)
            if unit == "B": turnover_m_hkd = num * 1000
            elif unit == "M" or unit == "": turnover_m_hkd = num
            elif unit == "K": turnover_m_hkd = num / 1000

    market_cap = None
    mc_m = re.search(r'市值\s*([\d,.]+)\s*億', h)
    if mc_m:
        market_cap = float(mc_m.group(1).replace(",", "")) * 1e8  # in HKD

    rsi14 = None
    rsi_m = re.search(r'RSI14\s*(?:=|為)?\s*([\d.]+)', h)
    if rsi_m: rsi14 = float(rsi_m.group(1))

    dist_to_52w_high = None
    if high_52w and last_price:
        dist_to_52w_high = (last_price - high_52w) / high_52w * 100
    dist_to_52w_low = None
    if low_52w and last_price:
        dist_to_52w_low = (last_price - low_52w) / low_52w * 100

    # 核心結論 = summary
    summary_m = re.search(r'<h2>📋 核心結論</h2>\s*<p>(.*?)</p>', h, re.DOTALL)
    summary_md = unescape(summary_m.group(1)) if summary_m else None

    # Full body (everything from 核心結論 onward, before </main>)
    main_m = re.search(r'<main>(.*?)</main>', h, re.DOTALL)
    full_md_html = main_m.group(1) if main_m else h
    # Strip HTML tags for plain markdown (best effort)
    full_md = re.sub(r'<[^>]+>', ' ', full_md_html)
    full_md = unescape(full_md)
    full_md = re.sub(r'\s+', ' ', full_md).strip()

    # Build data_snapshot_json
    data_snapshot = {
        "code": code,
        "last_price": last_price,
        "prev_close": (last_price / (1 + change_pct / 100)) if (last_price and change_pct is not None) else None,
        "change_pct": change_pct,
        "day_high": day_high,
        "day_low": day_low,
        "open": None,  # not in static
        "volume": None,  # not in static
        "turnover_m_hkd": turnover_m_hkd,
        "currency": currency,
        "ma20": ma20,
        "ma50": ma50,
        "ma100": ma100,
        "ma200": ma200,
        "rsi14": rsi14,
        "pe_ttm": pe_ttm,
        "pb": pb,
        "dividend_yield": dividend_yield,
        "dist_to_52w_high": dist_to_52w_high,
        "dist_to_52w_low": dist_to_52w_low,
        "support_zone": support_zone,
        "resistance_zone": resistance_zone,
        "entry_zone": entry_zone,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "market_cap_hkd": market_cap,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "source": "static_html_import_2026-07-17",
    }

    score_breakdown = {
        "value_score": value_score,
        "quality_score": quality_score,
        "momentum_score": momentum_score,
        "order_flow_score": order_flow_score,
        "news_score": news_score,
    }

    return {
        "code": code,
        "report_date": REPORT_DATE,
        "score": score,  # = 下日勝率
        "narrative_score": narrative_score,  # = 評分
        "sentiment": sentiment,
        "trend": trend,
        "confidence": confidence,
        "operation_advice": op,
        "summary_md": summary_md or "",
        "full_md": full_md,
        "data_snapshot_json": json.dumps(data_snapshot, ensure_ascii=False),
        "score_breakdown_json": json.dumps(score_breakdown, ensure_ascii=False),
        "decision_reason": f"[IMPORTED] Static HTML import from {html_path.name}",
        "signal_score": score,  # 下日勝率 IS the signal score for this dataset
    }


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # Check existing
    n_existing = con.execute(
        "SELECT COUNT(*) FROM daily_report WHERE report_date=?", (REPORT_DATE,)
    ).fetchone()[0]
    print(f"Existing 7/15 records: {n_existing}")
    if n_existing > 0:
        ans = input("  Already have 7/15 records. Overwrite? (yes/no): ").strip().lower()
        if ans != "yes":
            print("Aborted.")
            return
        con.execute("DELETE FROM daily_report WHERE report_date=?", (REPORT_DATE,))
        con.commit()
        print(f"  Cleared {n_existing} existing records")

    files = sorted(REPORTS_DIR.glob("*.html"))
    print(f"Found {len(files)} per-ticker report files")
    print()

    inserted = 0
    failed = 0
    buy_count = hold_count = sell_count = 0

    for f in files:
        rec = parse_report(f)
        if not rec:
            failed += 1
            continue

        try:
            con.execute(
                """
                INSERT INTO daily_report
                (code, report_date, score, sentiment, trend, operation_advice,
                 summary_md, full_md, data_snapshot_json, score_breakdown_json,
                 decision_reason, signal_score, llm_original_op, llm_model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec["code"],
                    rec["report_date"],
                    rec["score"],
                    rec["sentiment"],
                    rec["trend"],
                    rec["operation_advice"],
                    rec["summary_md"],
                    rec["full_md"],
                    rec["data_snapshot_json"],
                    rec["score_breakdown_json"],
                    rec["decision_reason"],
                    rec["signal_score"],
                    rec["operation_advice"],  # llm_original_op (since not from LLM)
                    "static_import",  # llm_model
                ),
            )
            inserted += 1
            if rec["operation_advice"] == "買入":
                buy_count += 1
            elif rec["operation_advice"] == "賣出":
                sell_count += 1
            else:
                hold_count += 1
        except sqlite3.IntegrityError:
            failed += 1
        except Exception as e:
            print(f"  insert err {f.name}: {e}")
            failed += 1

    con.commit()

    # Verify
    n = con.execute(
        "SELECT COUNT(*) FROM daily_report WHERE report_date=?", (REPORT_DATE,)
    ).fetchone()[0]

    # Sample sub-score stats
    n_with_value = con.execute(
        """SELECT COUNT(*) FROM daily_report, json_each(score_breakdown_json)
           WHERE report_date=? AND json_each.key='value_score' AND json_each.value IS NOT NULL""",
        (REPORT_DATE,),
    ).fetchone()[0]

    print()
    print(f"Done: {inserted} inserted, {failed} failed, DB now has {n} records for {REPORT_DATE}")
    print(f"  Op distribution: 買入={buy_count}, 觀望={hold_count}, 賣出={sell_count}")
    print(f"  Records with value_score parsed: {n_with_value}")

    con.close()


if __name__ == "__main__":
    main()
