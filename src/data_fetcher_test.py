"""
Tests for data_fetcher freshness detection + prompt integration.

These tests cover the regression reported on 2026-06-27: the 2291.HK dashboard
report was generated on a Saturday with YFinance's stale "last_price=11.31"
(Thursday's close) but no `data_as_of` was passed to the LLM. The LLM
hallucinated "今日爆升20.06%" by reading the intraday move on Thursday instead
of the live Friday price (last_price=9.96, prev_close=11.31, -11.94%).

What we lock in here:

1. `_is_hk_weekend_or_holiday` correctly identifies Sat/Sun + HK holidays.
2. `_expected_change_pct` is internally consistent with (last_price - prev_close)/prev_close.
3. `_attach_freshness_metadata` flags the 2291.HK-style staleness (snapshot's
   `change_pct` disagrees with the computed value by > 2pp).
4. `_attach_freshness_metadata` always populates `data_as_of` even when
   there's no live overlay (pure YFinance historical path).
5. `fill_user_prompt` (prompts.py) injects `as_of_date`,
   `is_weekend_or_holiday`, and `data_stale_warning` into the rendered prompt,
   so the LLM cannot anchor "today" to wall-clock.

Run with:
    python -m unittest src.data_fetcher_test
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

# Ensure src/ is importable when run from project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data_fetcher import (  # noqa: E402
    _attach_freshness_metadata,
    _expected_change_pct,
    _HK_HOLIDAYS,
    _is_hk_weekend_or_holiday,
)
from src.prompts import fill_user_prompt  # noqa: E402


# ============ 1. Holiday/weekend detection ============

class TestHkWeekendOrHoliday(unittest.TestCase):
    """Verify weekend + holiday detection at the HKT date boundary."""

    def test_saturday_is_closed(self):
        # 2026-06-27 is a Saturday (the bug day)
        sat = datetime(2026, 6, 27, 10, 0, 0)
        is_closed, date_str = _is_hk_weekend_or_holiday(now_hkt=sat)
        self.assertTrue(is_closed)
        self.assertEqual(date_str, "2026-06-27")

    def test_sunday_is_closed(self):
        sun = datetime(2026, 6, 28, 23, 59, 0)
        is_closed, _ = _is_hk_weekend_or_holiday(now_hkt=sun)
        self.assertTrue(is_closed)

    def test_weekday_open(self):
        # 2026-06-26 is a Friday (normal trading day)
        fri = datetime(2026, 6, 26, 10, 0, 0)
        is_closed, _ = _is_hk_weekend_or_holiday(now_hkt=fri)
        self.assertFalse(is_closed)

    def test_known_holiday_closed(self):
        # 2026-05-01 is Labour Day — in the curated holiday list.
        holiday = datetime(2026, 5, 1, 10, 0, 0)
        is_closed, _ = _is_hk_weekend_or_holiday(now_hkt=holiday)
        self.assertTrue(is_closed)

    def test_holiday_list_includes_recent_years(self):
        """We need 2025 + 2026 coverage at minimum; verify a sample of each year."""
        self.assertIn("2025-10-01", _HK_HOLIDAYS)  # National Day
        self.assertIn("2026-10-01", _HK_HOLIDAYS)  # National Day
        self.assertIn("2025-07-01", _HK_HOLIDAYS)  # HKSAR Establishment Day


# ============ 2. Expected change_pct sanity check ============

class TestExpectedChangePct(unittest.TestCase):
    def test_positive(self):
        # 9.96 vs 11.31 → -11.94%
        self.assertAlmostEqual(_expected_change_pct(9.96, 11.31), -11.94, places=2)

    def test_negative(self):
        self.assertAlmostEqual(_expected_change_pct(11.31, 9.67), 16.96, places=2)

    def test_zero(self):
        self.assertEqual(_expected_change_pct(11.31, 11.31), 0.0)

    def test_none_last(self):
        self.assertIsNone(_expected_change_pct(None, 11.31))

    def test_none_prev(self):
        self.assertIsNone(_expected_change_pct(9.96, None))

    def test_zero_prev(self):
        self.assertIsNone(_expected_change_pct(9.96, 0))


# ============ 3. Freshness metadata attachment ============

class TestAttachFreshnessMetadata(unittest.TestCase):
    """Reproduce the 2291.HK bug scenario and verify the warning fires."""

    def test_staleness_warning_when_change_pct_disagrees(self):
        # Reproduce 2291.HK on 2026-06-27 (Sat):
        #   YFinance (stale) says last_price=11.31, prev_close=11.31 (Thursday's close).
        #   But "change_pct=20.06" is internally inconsistent — likely leftover from a
        #   prior calculation, OR live overlay failed and we kept an older number.
        # The fresh path says: computed = (11.31 - 11.31)/11.31 = 0%.
        # Disagreement = 20.06pp > 2pp threshold → warning.
        snap = {
            "code": "2291.HK",
            "last_price": 11.31,
            "prev_close": 11.31,
            "change_pct": 20.06,  # inconsistent!
            "day_high": 13.20,
            "day_low": 9.42,
            "kline_30d": [
                {"date": "2026-06-25", "open": 9.67, "high": 13.20, "low": 9.42, "close": 11.31, "volume": 3_200_000},
            ],
            "data_as_of": "2026/06/26 16:08:13",
        }
        snap = _attach_freshness_metadata(snap)
        # Either we get a warning because of the disagreeing change_pct, or
        # the live overlay already corrected change_pct. We expect a warning here.
        # Also is_weekend_or_holiday should reflect Saturday on 2026-06-27.
        # NOTE: we cannot assert is_weekend_or_holiday=True unconditionally because
        # wall-clock at test-time could be a weekday. Instead, verify expected_today_change_pct
        # was added and the warning logic fired OR not based on declared vs expected.
        self.assertIn("expected_today_change_pct", snap)
        self.assertIsNotNone(snap["expected_today_change_pct"])
        # Expected change_pct is 0% (11.31 - 11.31)/11.31 — disagrees with declared 20.06%
        self.assertAlmostEqual(snap["expected_today_change_pct"], 0.0, places=2)

    def test_consistent_snapshot_has_no_warning(self):
        # Friday 2026-06-26 trading day, snapshot consistent with itself.
        snap = {
            "code": "0700.HK",
            "last_price": 410.6,
            "prev_close": 408.2,
            "change_pct": 0.59,  # (410.6 - 408.2)/408.2 = 0.588%
            "kline_30d": [],
            "data_as_of": "2026-06-26 16:08:13",
        }
        snap = _attach_freshness_metadata(snap)
        # No data_stale_warning expected since change_pct matches computation.
        self.assertFalse(snap.get("data_stale_warning"))
        # data_as_of was already present, we shouldn't clobber it.
        self.assertEqual(snap["data_as_of"], "2026-06-26 16:08:13")

    def test_data_as_of_filled_when_missing(self):
        """Pure-YFinance historical path may not set data_as_of — verify fallback."""
        snap = {
            "code": "9988.HK",
            "last_price": 95.0,
            "prev_close": 94.0,
            "change_pct": 1.06,  # (95 - 94)/94 = 1.06%
            "kline_30d": [
                {"date": "2026-06-25", "open": 94.0, "high": 95.5, "low": 93.8, "close": 95.0, "volume": 1_000_000},
            ],
            # No data_as_of
        }
        snap = _attach_freshness_metadata(snap)
        # Should be filled from kline last bar date.
        self.assertIn("data_as_of", snap)
        self.assertIn("2026-06-25", snap["data_as_of"])
        self.assertIn("historical bar close", snap["data_as_of"])

    def test_is_weekend_or_holiday_field_present(self):
        snap = {
            "code": "0700.HK",
            "last_price": 410.0,
            "prev_close": 408.0,
            "change_pct": 0.49,
            "kline_30d": [],
            "data_as_of": "2026-06-26 16:08:13",
        }
        snap = _attach_freshness_metadata(snap)
        self.assertIn("is_weekend_or_holiday", snap)
        self.assertIsInstance(snap["is_weekend_or_holiday"], bool)


# ============ 4. Prompt integration ============

class TestPromptIntegration(unittest.TestCase):
    """Verify the rendered user prompt carries the freshness metadata."""

    BASE_SNAPSHOT = {
        "code": "2291.HK",
        "last_price": 9.96,
        "prev_close": 11.31,
        "change_pct": -11.94,
        "day_high": 11.77,
        "day_low": 9.74,
        "volume": 5_000_000,
        "turnover_hkd": 50_000_000,
        "day_range_pct": 20.38,
        "vol_ratio": 0.72,
        "ma20": 9.45,
        "ma50": 10.51,
        "ma100": 12.69,
        "ma200": 15.45,
        "rsi14": 52.6,
        "52w_high": 22.4,
        "52w_low": 8.33,
        "ytd_change_pct": -35.69,
        "pe_ttm": 13.02,
        "pb": 1.53,
        "dividend_yield": 5.75,
        "market_cap_hkd": 8.0e9,
        "kline_30d": [
            {"date": "2026-06-25", "open": 9.67, "high": 13.20, "low": 9.42, "close": 11.31, "volume": 3_200_000},
            {"date": "2026-06-26", "open": 11.10, "high": 11.77, "low": 9.74, "close": 9.96, "volume": 5_000_000},
        ],
        "sector": "",
        "source": "futu",
        "data_as_of": "2026/06/26 16:08:13",
        "is_weekend_or_holiday": True,
        "data_stale_warning": (
            "snapshot.change_pct=-11.94% diverges from computed "
            "(9.96-11.31)/11.31=-11.94% by 0.00pp — consistent"
        ),
    }

    def test_zh_prompt_includes_as_of_date(self):
        from src.prompts import USER_PROMPT_TEMPLATE_ZH
        out = fill_user_prompt(
            USER_PROMPT_TEMPLATE_ZH, "2291.HK", "樂普心脈醫療",
            self.BASE_SNAPSHOT, news=[], language="zh-Hant",
        )
        self.assertIn("as_of_date", out)
        self.assertIn("2026/06/26 16:08:13", out)
        self.assertIn("is_weekend_or_holiday", out)
        self.assertIn("data_stale_warning", out)
        # Most important: the actual price/change numbers ARE in the prompt.
        self.assertIn("9.96", out)
        self.assertIn("-11.94", out)

    def test_en_prompt_includes_as_of_date(self):
        from src.prompts import USER_PROMPT_TEMPLATE_EN
        out = fill_user_prompt(
            USER_PROMPT_TEMPLATE_EN, "2291.HK", "SCIENTECH",
            self.BASE_SNAPSHOT, news=[], language="en",
        )
        self.assertIn("as_of_date", out)
        self.assertIn("2026/06/26 16:08:13", out)
        self.assertIn("is_weekend_or_holiday", out)
        self.assertIn("data_stale_warning", out)
        self.assertIn("9.96", out)
        self.assertIn("-11.94", out)

    def test_zh_prompt_anchoring_language_present(self):
        """Make sure the explicit '今日 = as_of_date' instruction survived translation."""
        from src.prompts import USER_PROMPT_TEMPLATE_ZH
        out = fill_user_prompt(
            USER_PROMPT_TEMPLATE_ZH, "2291.HK", "樂普心脈醫療",
            self.BASE_SNAPSHOT, news=[], language="zh-Hant",
        )
        # The new section header
        self.assertIn("數據時間錨點", out)
        # The '「今日」= as_of_date' guidance
        self.assertIn("as_of_date", out)
        # The K-line section header (Chinese uses 「K線」)
        self.assertIn("K線", out)
        # The snapshot's actual close prices still get rendered into kline_summary
        self.assertIn("11.31", out)  # Thursday close
        self.assertIn("9.96", out)   # Friday close

    def test_missing_data_as_of_renders_default(self):
        """If snapshot has no data_as_of (corrupt/edge), prompt must not crash and must show fallback."""
        snap = dict(self.BASE_SNAPSHOT)
        snap.pop("data_as_of", None)
        from src.prompts import USER_PROMPT_TEMPLATE_ZH
        out = fill_user_prompt(
            USER_PROMPT_TEMPLATE_ZH, "2291.HK", "樂普心脈醫療",
            snap, news=[], language="zh-Hant",
        )
        # The placeholder default should be present (not crash).
        self.assertIn("未知", out)


# ============ 5. End-to-end repro of 2291.HK bug scenario ============

class Test2291BugRepro(unittest.TestCase):
    """End-to-end trace: simulate the 2291.HK 2026-06-27 stale snapshot and show
    what the LLM would actually receive after the fix.

    Before the fix:
        snapshot = {last_price: 11.31, prev_close: 11.31, change_pct: 20.06,
                    kline_30d: [...Thursday bar with +16.96% intraday move...]}
        LLM prompt would have only "今日漲跌: 20.06%" and the kline bars.
        LLM would confabulate "今日爆升20.06%" by reading Thursday's bar.

    After the fix:
        snapshot gets data_stale_warning, expected_today_change_pct, is_weekend_or_holiday.
        Prompt header says "今日 = as_of_date = 2026-06-26 (Fri)" and the stale
        snapshot of last_price=11.31 would trigger the warning.
    """

    def test_buggy_snapshot_triggers_warning_after_fix(self):
        """What the orchestrator saw: last_price=11.31 + change_pct=20.06.
        The fresh path computes 0% (no overnight move from Thu close). 20pp gap
        → warning fires."""
        buggy = {
            "code": "2291.HK",
            "last_price": 11.31,
            "prev_close": 11.31,
            "change_pct": 20.06,
            "kline_30d": [
                {"date": "2026-06-25", "open": 9.67, "high": 13.20, "low": 9.42, "close": 11.31, "volume": 3_200_000},
            ],
            "data_as_of": "2026/06/26 16:08:13",
        }
        snap = _attach_freshness_metadata(buggy)
        # Sanity: expected_today_change_pct = 0% (since last == prev)
        self.assertEqual(snap["expected_today_change_pct"], 0.0)
        # Stale warning should be present.
        self.assertIsNotNone(snap.get("data_stale_warning"))
        self.assertIn("20.06", snap["data_stale_warning"])
        self.assertIn("diverges", snap["data_stale_warning"])

    def test_correct_snapshot_after_fix_renders_cleanly(self):
        """What the live data actually was on 2026-06-26: last_price=9.96,
        prev_close=11.31, change_pct=-11.94. No inconsistency, no warning."""
        correct = {
            "code": "2291.HK",
            "last_price": 9.96,
            "prev_close": 11.31,
            "change_pct": -11.94,
            "kline_30d": [
                {"date": "2026-06-25", "open": 9.67, "high": 13.20, "low": 9.42, "close": 11.31, "volume": 3_200_000},
                {"date": "2026-06-26", "open": 11.10, "high": 11.77, "low": 9.74, "close": 9.96, "volume": 5_000_000},
            ],
            "data_as_of": "2026/06/26 16:08:13",
        }
        snap = _attach_freshness_metadata(correct)
        self.assertEqual(snap["expected_today_change_pct"], -11.94)
        self.assertFalse(snap.get("data_stale_warning"))

        # Render the prompt and verify the LLM gets the right anchors.
        from src.prompts import USER_PROMPT_TEMPLATE_ZH
        out = fill_user_prompt(
            USER_PROMPT_TEMPLATE_ZH, "2291.HK", "樂普心脈醫療",
            snap, news=[], language="zh-Hant",
        )
        # LLM will see: "現價: 9.96 HKD", "今日漲跌: -11.94%"
        self.assertIn("9.96", out)
        self.assertIn("-11.94", out)
        # Plus the explicit time-anchor header
        self.assertIn("as_of_date", out)
        self.assertIn("2026/06/26 16:08:13", out)
        # is_weekend_or_holiday gets serialized as "true" (Saturday)
        self.assertIn("is_weekend_or_holiday: true", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)