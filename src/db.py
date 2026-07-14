"""SQLite storage for ticker data, reports, market reviews, and chat sessions."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from .config import get_config


SCHEMA = """
CREATE TABLE IF NOT EXISTS ticker (
    code TEXT PRIMARY KEY,
    name_zh TEXT,
    name_en TEXT,
    sector TEXT,
    last_price REAL,
    last_updated TEXT
);

CREATE TABLE IF NOT EXISTS daily_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    report_date TEXT NOT NULL,
    score INTEGER,
    sentiment TEXT,           -- 樂觀 / 中性 / 悲觀
    trend TEXT,                -- 看多 / 震盪 / 看空
    operation_advice TEXT,     -- 買入 / 觀望 / 賣出 (RULE-BASED since 2026-07-10)
    llm_original_op TEXT,     -- LLM's original op before rule override (for audit)
    decision_reason TEXT,      -- Why rule chose this op (e.g. "ANTI-CHASE: 樂觀+m=78+chg=+5.8%")
    signal_score INTEGER,      -- 0-100, rule-based edge confidence (NOT LLM narrative)
    score_breakdown_json TEXT, -- {"value_score":N,"quality_score":N,"momentum_score":N}
    trade_direction TEXT,      -- long / short / both
    support_zone TEXT,         -- 支持區 e.g. "385.00-392.00"
    resistance_zone TEXT,      -- 阻力區 e.g. "411.50 (MA20) / 425.00"
    key_levels_json TEXT,      -- JSON: {ma20_value, ma50_value, day_low_value, day_high_value, support_floor, support_ceiling, resistance_target}
    summary_md TEXT,
    full_md TEXT,
    news_json TEXT,
    data_snapshot_json TEXT,
    llm_model TEXT,
    generated_at TEXT,
    UNIQUE(code, report_date)
);

CREATE INDEX IF NOT EXISTS idx_report_date ON daily_report(report_date DESC);
CREATE INDEX IF NOT EXISTS idx_report_code ON daily_report(code, report_date DESC);

CREATE TABLE IF NOT EXISTS market_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_date TEXT UNIQUE,
    hsi REAL,
    hscei REAL,
    hsi_chg REAL,
    hscei_chg REAL,
    advancers INTEGER,
    decliners INTEGER,
    sectors_json TEXT,
    summary_md TEXT,
    generated_at TEXT
);

CREATE TABLE IF NOT EXISTS chat_session (
    id TEXT PRIMARY KEY,
    code TEXT,
    messages_json TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    ended_at TEXT,
    status TEXT,           -- running / success / failed
    tickers_total INTEGER,
    tickers_done INTEGER,
    tickers_failed INTEGER,
    error TEXT,
    trigger TEXT           -- cli / schedule / telegram
);

CREATE TABLE IF NOT EXISTS chanlun_signal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    signal_date TEXT NOT NULL,         -- ISO date of breakout bar
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    target REAL NOT NULL,
    confidence INTEGER NOT NULL,
    central_zg REAL NOT NULL,
    central_zd REAL NOT NULL,
    central_gg REAL NOT NULL,
    central_dd REAL NOT NULL,
    had_pullback INTEGER NOT NULL,     -- 0/1
    rationale TEXT,
    llm_score INTEGER,                 -- MiniMax-M3 secondary score (1-10), NULL if not scored
    llm_conviction TEXT,               -- high / medium / low
    llm_reasoning TEXT,                -- 1-2 sentence explanation
    llm_risks_json TEXT,               -- JSON array of risk strings
    status TEXT DEFAULT 'active',       -- active / filled / expired / cancelled / llm_rejected
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_chanlun_date ON chanlun_signal(signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_chanlun_code ON chanlun_signal(code, signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_chanlun_status ON chanlun_signal(status);

-- Idempotent migration for older deployments (pre-LLM schema).
-- Each ALTER ignores error if column already exists.
ALTER TABLE chanlun_signal ADD COLUMN llm_score INTEGER;
ALTER TABLE chanlun_signal ADD COLUMN llm_conviction TEXT;
ALTER TABLE chanlun_signal ADD COLUMN llm_reasoning TEXT;
ALTER TABLE chanlun_signal ADD COLUMN llm_risks_json TEXT;

-- Idempotent migration for support_zone / resistance_zone / key_levels (2026-06-27).
-- Forces prompt-engineering: concrete numbers in LLM summaries.
ALTER TABLE daily_report ADD COLUMN support_zone TEXT;
ALTER TABLE daily_report ADD COLUMN resistance_zone TEXT;
ALTER TABLE daily_report ADD COLUMN key_levels_json TEXT;

-- Idempotent migration for entry/stop/target (2026-07-09)
ALTER TABLE daily_report ADD COLUMN entry_zone TEXT;
ALTER TABLE daily_report ADD COLUMN stop_loss TEXT;
ALTER TABLE daily_report ADD COLUMN target_price TEXT;

-- Idempotent migration for rule-based decision engine (2026-07-10)
-- Phase 2 of WHY_LLM_IS_DUMB fix: LLM op_advice is no longer trusted.
-- Rule-based decide() sets operation_advice. LLM's original is preserved.
ALTER TABLE daily_report ADD COLUMN llm_original_op TEXT;
ALTER TABLE daily_report ADD COLUMN decision_reason TEXT;

-- Idempotent migration for Signal Score (2026-07-11)
-- Separates LLM narrative confidence (LLM 評分) from rule-based edge
-- confidence (訊號強度). User complained 02208 (LLM 評分 58 + 買入)
-- and 00992 (LLM 評分 77 + 觀望) feel contradictory.
ALTER TABLE daily_report ADD COLUMN signal_score INTEGER;
"""


def get_db() -> sqlite3.Connection:
    cfg = get_config()
    db_path = Path(cfg.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables and indexes per SCHEMA. Tolerates idempotent migrations
    (ALTER TABLE ... ADD COLUMN on existing tables returns 'duplicate column'
    which we ignore)."""
    conn = get_db()
    try:
        # Split schema into individual statements; execute one-by-one so that
        # ALTER TABLE ... ADD COLUMN fails silently on already-existing columns.
        for stmt in SCHEMA.split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e):
                    raise
        conn.commit()
    finally:
        conn.close()


# ============ Ticker ============

def upsert_ticker(code: str, name_zh: Optional[str] = None, name_en: Optional[str] = None,
                  sector: Optional[str] = None, last_price: Optional[float] = None) -> None:
    conn = get_db()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        # Get existing
        existing = conn.execute("SELECT * FROM ticker WHERE code=?", (code,)).fetchone()
        if existing:
            updates = []
            params = []
            if name_zh is not None:
                updates.append("name_zh=?")
                params.append(name_zh)
            if name_en is not None:
                updates.append("name_en=?")
                params.append(name_en)
            if sector is not None:
                updates.append("sector=?")
                params.append(sector)
            if last_price is not None:
                updates.append("last_price=?")
                params.append(last_price)
            updates.append("last_updated=?")
            params.append(now)
            params.append(code)
            conn.execute(f"UPDATE ticker SET {', '.join(updates)} WHERE code=?", params)
        else:
            conn.execute(
                """INSERT INTO ticker (code, name_zh, name_en, sector, last_price, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (code, name_zh, name_en, sector, last_price, now),
            )
        conn.commit()
    finally:
        conn.close()


def get_ticker(code: str) -> Optional[dict]:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM ticker WHERE code=?", (code,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ============ Daily Report ============

def save_report(
    code: str,
    report_date: str,
    score: Optional[int],
    sentiment: Optional[str],
    trend: Optional[str],
    operation_advice: Optional[str],
    summary_md: Optional[str],
    full_md: Optional[str],
    news: list[dict],
    data_snapshot: dict,
    llm_model: Optional[str],
    score_breakdown_json: Optional[str] = None,
    trade_direction: Optional[str] = None,
    support_zone: Optional[str] = None,
    resistance_zone: Optional[str] = None,
    key_levels_json: Optional[str] = None,
    entry_zone: Optional[str] = None,
    stop_loss: Optional[str] = None,
    target_price: Optional[str] = None,
    llm_original_op: Optional[str] = None,
    decision_reason: Optional[str] = None,
    signal_score: Optional[int] = None,
) -> int:
    """Insert or replace today's report for code. Returns row id.

    Phase 2 (2026-07-10): operation_advice is RULE-BASED (overrides LLM).
    LLM's original op is preserved in llm_original_op for audit.
    decision_reason explains why the rule was applied.
    signal_score (0-100) is the rule-based edge confidence (Phase 3 UX fix
    to separate LLM narrative confidence from trade edge).

    If llm_original_op is None, save_report auto-applies the rule using
    the passed operation_advice as the LLM's op. This means existing
    callers get the new behavior without code changes.
    """
    from .signal_decision import (
        apply_to_snapshot, extract_matched_rule,
        predict_win_probability,
    )

    matched_rule = ""
    # If caller didn't pre-apply the rule, apply it here
    if llm_original_op is None and operation_advice:
        try:
            sb = json.loads(score_breakdown_json or "{}") if score_breakdown_json else {}
        except Exception:
            sb = {}
        sector = (data_snapshot.get("sector") or "").strip() if data_snapshot else ""
        decision = apply_to_snapshot(
            llm_op=operation_advice,
            llm_sentiment=sentiment or "",
            llm_trend=trend or "",
            score_breakdown=sb,
            data_snapshot=data_snapshot or {},
            sector=sector,
        )
        operation_advice = decision.op
        llm_original_op = decision.original_op
        decision_reason = f"[{decision.matched_rule}] {decision.reason}"
        matched_rule = decision.matched_rule

    # Compute Signal Score if not provided
    # Note: parameter renamed to avoid shadowing the imported function name.
    if signal_score is None:
        # If we just applied the rule, use the matched_rule directly
        if not matched_rule and decision_reason:
            matched_rule = extract_matched_rule(decision_reason)
        # Phase 4 (2026-07-11): use logistic-regression-based win probability
        # instead of static mapping. Falls back to static for missing features.
        try:
            sb = json.loads(score_breakdown_json or "{}") if score_breakdown_json else {}
        except Exception:
            sb = {}
        signal_score_val = predict_win_probability(
            m=sb.get("momentum_score") or 0,
            of=sb.get("order_flow_score") or 0,
            v=sb.get("value_score") or 0,
            q=sb.get("quality_score") or 0,
            chg=(data_snapshot or {}).get("change_pct") or 0,
            sentiment=sentiment or "",
            matched_rule=matched_rule,
        )
    else:
        # Use the explicit signal_score passed by caller
        signal_score_val = signal_score

    conn = get_db()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        # Replace existing for (code, report_date)
        conn.execute(
            """INSERT OR REPLACE INTO daily_report
               (code, report_date, score, sentiment, trend, operation_advice,
                score_breakdown_json, trade_direction,
                support_zone, resistance_zone, key_levels_json,
                entry_zone, stop_loss, target_price,
                summary_md, full_md, news_json, data_snapshot_json, llm_model, generated_at,
                llm_original_op, decision_reason, signal_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                code, report_date, score, sentiment, trend, operation_advice,
                score_breakdown_json, trade_direction,
                support_zone, resistance_zone, key_levels_json,
                entry_zone, stop_loss, target_price,
                summary_md, full_md, json.dumps(news, ensure_ascii=False),
                json.dumps(data_snapshot, ensure_ascii=False), llm_model, now,
                llm_original_op, decision_reason, signal_score_val,
            ),
        )
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        return row_id
    finally:
        conn.close()


def get_report(code: str, report_date: Optional[str] = None) -> Optional[dict]:
    """Get report for code (latest if date not specified)."""
    conn = get_db()
    try:
        if report_date:
            row = conn.execute(
                "SELECT * FROM daily_report WHERE code=? AND report_date=?",
                (code, report_date),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM daily_report WHERE code=? ORDER BY report_date DESC LIMIT 1",
                (code,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("news_json"):
            d["news"] = json.loads(d["news_json"])
        if d.get("data_snapshot_json"):
            d["data_snapshot"] = json.loads(d["data_snapshot_json"])
        return d
    finally:
        conn.close()


def list_reports(report_date: Optional[str] = None, limit: int = 100) -> list[dict]:
    """List reports for a date (default today), sorted by signal_score DESC (下日勝率).

    Phase 4 (2026-07-11): sort by 下日勝率 (signal_score) instead of LLM 評分 (score).
    User wanted "越高分等於越大機會 next day 贏" + cards should be ranked by win rate.
    """
    conn = get_db()
    try:
        if report_date:
            rows = conn.execute(
                """SELECT * FROM daily_report WHERE report_date=?
                   ORDER BY signal_score DESC, score DESC, code ASC LIMIT ?""",
                (report_date, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM daily_report
                   ORDER BY report_date DESC, signal_score DESC, score DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_report_dates(limit: int = 30) -> list[str]:
    """Get distinct report dates, most recent first."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT DISTINCT report_date FROM daily_report ORDER BY report_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["report_date"] for r in rows]
    finally:
        conn.close()


def report_history(code: str, limit: int = 30) -> list[dict]:
    """Get historical reports for a ticker."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT report_date, score, sentiment, trend, operation_advice, summary_md
               FROM daily_report WHERE code=? ORDER BY report_date DESC LIMIT ?""",
            (code, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============ Market Review ============

def save_market_review(
    review_date: str,
    hsi: Optional[float], hscei: Optional[float],
    hsi_chg: Optional[float], hscei_chg: Optional[float],
    advancers: Optional[int], decliners: Optional[int],
    sectors: list[dict],
    summary_md: str,
) -> None:
    conn = get_db()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """INSERT OR REPLACE INTO market_review
               (review_date, hsi, hscei, hsi_chg, hscei_chg, advancers, decliners,
                sectors_json, summary_md, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                review_date, hsi, hscei, hsi_chg, hscei_chg, advancers, decliners,
                json.dumps(sectors, ensure_ascii=False), summary_md, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_market_review(review_date: Optional[str] = None) -> Optional[dict]:
    conn = get_db()
    try:
        if review_date:
            row = conn.execute(
                "SELECT * FROM market_review WHERE review_date=?", (review_date,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM market_review ORDER BY review_date DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("sectors_json"):
            d["sectors"] = json.loads(d["sectors_json"])
        return d
    finally:
        conn.close()


# ============ Chat Sessions ============

def save_chat_session(
    session_id: str,
    code: Optional[str],
    messages: list[dict],
) -> None:
    conn = get_db()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        existing = conn.execute(
            "SELECT created_at FROM chat_session WHERE id=?", (session_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE chat_session SET messages_json=?, updated_at=? WHERE id=?""",
                (json.dumps(messages, ensure_ascii=False), now, session_id),
            )
        else:
            conn.execute(
                """INSERT INTO chat_session (id, code, messages_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, code, json.dumps(messages, ensure_ascii=False), now, now),
            )
        conn.commit()
    finally:
        conn.close()


def get_chat_session(session_id: str) -> Optional[dict]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM chat_session WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("messages_json"):
            d["messages"] = json.loads(d["messages_json"])
        return d
    finally:
        conn.close()


# ============ Run Log ============

def start_run(trigger: str, tickers_total: int) -> int:
    conn = get_db()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            """INSERT INTO run_log (started_at, status, tickers_total, tickers_done, tickers_failed, trigger)
               VALUES (?, 'running', ?, 0, 0, ?)""",
            (now, tickers_total, trigger),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def finish_run(run_id: int, status: str, tickers_done: int, tickers_failed: int, error: Optional[str] = None) -> None:
    conn = get_db()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """UPDATE run_log SET ended_at=?, status=?, tickers_done=?, tickers_failed=?, error=?
               WHERE id=?""",
            (now, status, tickers_done, tickers_failed, error, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_run_progress(run_id: int, tickers_done: int, tickers_failed: int) -> None:
    """Update progress counters for an in-flight run. Safe to call frequently
    (e.g. after each ticker completes in a parallel pipeline)."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE run_log SET tickers_done=?, tickers_failed=? WHERE id=?",
            (tickers_done, tickers_failed, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_running_run() -> Optional[dict]:
    """Return the most recent ACTIVE analysis run, or None.

    An "active" run is one that:
      - has status='running' AND
      - was started within the last 4 hours AND
      - has progress (tickers_done > 0 or < 60s old — avoid zombies)
    """
    conn = get_db()
    try:
        # First try: fresh + making progress
        row = conn.execute(
            """SELECT * FROM run_log
               WHERE status='running'
                 AND started_at >= datetime('now', '-4 hours')
                 AND (
                   started_at >= datetime('now', '-60 seconds')
                   OR tickers_done > 0
                 )
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_recent_runs(limit: int = 20) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    print("Initializing DB...")
    init_db()
    print(f"DB ready at: {get_config().database_path}")


# =============================================================================
# Chanlun signal storage (2026-06-26)
# =============================================================================

def save_chanlun_signal(sig_dict: dict) -> int:
    """
    Insert a Chanlun 3rd-class BUY signal. Idempotent on (code, signal_date).
    Returns signal id. Now supports LLM secondary scoring fields.
    """
    import json as _json
    conn = get_db()
    try:
        # Determine status: llm_rejected if LLM scored below threshold
        status = sig_dict.get("status", "active")
        if status == "llm_rejected":
            pass  # use as-is
        elif sig_dict.get("llm_score") is not None:
            status = "active"  # LLM-scored signals stay active regardless of score

        cur = conn.execute(
            """
            INSERT OR REPLACE INTO chanlun_signal
                (code, signal_date, entry_price, stop_loss, target,
                 confidence, central_zg, central_zd, central_gg, central_dd,
                 had_pullback, rationale,
                 llm_score, llm_conviction, llm_reasoning, llm_risks_json,
                 status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sig_dict["code"],
                sig_dict["signal_date"],
                sig_dict["entry_price"],
                sig_dict["stop_loss"],
                sig_dict["target"],
                sig_dict["confidence"],
                sig_dict["central_zg"],
                sig_dict["central_zd"],
                sig_dict["central_gg"],
                sig_dict["central_dd"],
                1 if sig_dict.get("had_pullback") else 0,
                sig_dict.get("rationale", ""),
                sig_dict.get("llm_score"),
                sig_dict.get("llm_conviction"),
                sig_dict.get("llm_reasoning"),
                _json.dumps(sig_dict.get("llm_risks", []), ensure_ascii=False),
                status,
            ),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def list_chanlun_signals(
    days: int = 7,
    code: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """
    List recent Chanlun signals. Default: last 7 days, all codes, all statuses.
    """
    from datetime import datetime, timedelta
    conn = get_db()
    try:
        sql = "SELECT * FROM chanlun_signal WHERE 1=1"
        params: list = []
        if days:
            cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
            sql += " AND signal_date >= ?"
            params.append(cutoff)
        if code:
            sql += " AND code = ?"
            params.append(code)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY signal_date DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_chanlun_signals(code: Optional[str] = None, days: Optional[int] = None) -> int:
    """Count Chanlun signals (lifetime or filtered)."""
    conn = get_db()
    try:
        sql = "SELECT COUNT(*) FROM chanlun_signal WHERE 1=1"
        params: list = []
        if code:
            sql += " AND code = ?"
            params.append(code)
        if days:
            from datetime import datetime, timedelta
            cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
            sql += " AND signal_date >= ?"
            params.append(cutoff)
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


# Update save_chanlun_signal to support llm fields
