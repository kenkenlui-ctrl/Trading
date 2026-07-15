"""ETP / leveraged product detection and support/resistance filtering.

Owner complaint 2026-07-15: 07709.HK (CSOP SK Hynix 2x Leveraged ETP) report
showed `support_zone: 58.78 / 8.42 (52週低位)` — the 8.42 is from a year ago when
the underlying SK Hynix was at a totally different price level. For 2x/3x
leveraged ETPs, old 52-week extreme values are MEANINGLESS because:

  1. Daily-reset leverage compounds decay (path-dependent)
  2. Old ETP prices were on different underlying levels
  3. Volatility drag means current 60 HKD ≠ old 60 HKD

This module detects ETP/leveraged products and provides a post-processor
that strips 52-week extreme values from support/resistance strings.
"""

from __future__ import annotations

import re
from typing import Optional

# Hardcoded known leveraged/ETP codes (the universe's core leveraged products).
# Add to this list as new leveraged products are added to the radar.
KNOWN_LEVERAGED_ETP: set[str] = {
    "07200.HK",  # FL二南方恒指 (CSOP HSI Daily 2x Leveraged)
    "07700.HK",  # (placeholder for future HSI 2x inverse)
    "07709.HK",  # XL二南方海力士 (CSOP SK Hynix 2x Leveraged)
    "07747.HK",  # XL二南方三星 (CSOP Samsung 2x Leveraged)
    "07500.HK",  # (placeholder for HSI 2x inverse)
    # Add US leveraged ETFs (TQQQ, SQQQ, SOXL, SOXS, etc.) as they appear in the radar
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UVXY", "SVXY", "TNA", "TZA",
    "FAS", "FAZ", "ERX", "ERY", "DPST", "JNUG", "JDST",
    "LABU", "LABD", "NUGT", "DUST", "GUSH", "DRIP",
}

# Pattern keywords in name_zh / name_en to detect leveraged / ETP.
# Match: "2x", "3x", "倍", "槓桿", "Leveraged", "Inverse", "ETP", "Daily Reset"
# Also: CSOP product prefixes (FL2 = Future Long 2x, FI2 = Future Inverse 2x, XL2 = eXtra Leveraged 2x)
# Also: Chinese 「二」 (2) when paired with leveraged context
_NAME_LEVERAGE_PATTERN = re.compile(
    r"("
    r"\b\d+x\b|"                                # 2x, 3x, 4x, 5x as whole words
    r"倍|槓桿|leveraged|inverse|etp|"
    r"daily\s*reset|double\s*(short|long)|"
    r"triple\s*leveraged|ultra\s*(pro|qqq|soxl|tsla)|"
    r"\bFL\d|\bFI\d|\bXL\d|\bFL2|\bFI2|\bXL2"  # CSOP naming convention
    r")",
    re.IGNORECASE,
)


def is_leveraged_etp(code: str, name_zh: str = "", name_en: str = "") -> bool:
    """Return True if the product is a leveraged ETP / ETF (e.g. 2x, 3x, inverse).

    Detection priority:
      1. Hardcoded known list (KNOWN_LEVERAGED_ETP) — definitive
      2. Name pattern match (Leveraged/倍/槓桿/2x/3x/ETP) — covers unlisted ones
    """
    if code in KNOWN_LEVERAGED_ETP:
        return True
    if name_zh and _NAME_LEVERAGE_PATTERN.search(name_zh):
        return True
    if name_en and _NAME_LEVERAGE_PATTERN.search(name_en):
        return True
    return False


# Patterns that match 52-week references in support/resistance strings.
# Matches: "8.42 (52週低位)", "193.65 (52週高位)", "8.42 (52w low)", "193.65 (52-week high)"
# Also matches Chinese fullwidth parens: "8.42（52週低位）"
# Also matches comma/slash-separated entries: ", 8.42 (52週低位)" or "/ 8.42 (52週低位)"
# Also matches descriptive 52-week ranges: "52週區間 11.34-243.10 HKD"
# Also matches table format: "52週高/低 | 243.1 / 11.34"
#
# Strategy: catch any "52" within 1-2 chars of "週"/"周"/"w" (case-insensitive) — anything
# in the 52-week family, in any context.
_WEEK52_PATTERN = re.compile(
    r"52\s*[週周]\s*[^\s,，。)）]{0,8}"
    r"|"
    r"52\s*[-‑]?\s*week"
    r"|"
    r"\b52w\b"
    r"|"
    r"\b52W\b"
    r"|"
    # Number before "52週": "/ 8.42 (52週低)" or ", 8.42 (52週高)"
    r"[,，/]\s*\d+(?:\.\d+)?\s*\(?(?:52\s*[週周]|52-week|52w|52W)\s*(?:高位|低位|high|low)\)?"
    r"|"
    # Number after "52週": "52週低 8.42"
    r"(?:52\s*[週周]|52-week|52w|52W)\s*(?:高位|低位|high|low)\s*[\$]?\s*\d+(?:\.\d+)?",
    re.IGNORECASE,
)


def strip_52w_levels(text: Optional[str]) -> Optional[str]:
    """Remove 52-week extreme values from a support/resistance string.

    Example:
        strip_52w_levels('58.78 (今日低) / 8.42 (52週低)')
            -> '58.78 (今日低)'
        strip_52w_levels('76.00 / 89.40 / 193.65 (52週高)')
            -> '76.00 / 89.40'  (note: clean up trailing separator)
    """
    if not text:
        return text
    cleaned = _WEEK52_PATTERN.sub("", text)
    # Tidy up trailing punctuation
    cleaned = re.sub(r"\s*[/,，]+\s*$", "", cleaned)
    cleaned = re.sub(r"\s*[\(（]\s*[\)）]", "", cleaned)  # remove empty parens
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    # If the cleaned result is empty (was only 52-week refs), return a placeholder
    if not cleaned:
        return "N/A (leveraged ETP — 52-week levels invalid)"
    return cleaned


def filter_etp_support_resistance(
    code: str,
    name_zh: str,
    name_en: str,
    support_zone: Optional[str],
    resistance_zone: Optional[str],
    summary_md: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Filter 52-week values from support/resistance strings for ETP/leveraged products.

    Returns (support_zone, resistance_zone, summary_md) — unchanged if not ETP.
    """
    if not is_leveraged_etp(code, name_zh, name_en):
        return support_zone, resistance_zone, summary_md

    new_support = strip_52w_levels(support_zone)
    new_resistance = strip_52w_levels(resistance_zone)
    # Also strip from summary_md text body (52-week text usually appears there too)
    new_summary = summary_md
    if summary_md:
        new_summary = _WEEK52_PATTERN.sub("", summary_md)
        new_summary = re.sub(r"\s*[/,，]+\s*\)", ")", new_summary)
        new_summary = re.sub(r"\s{2,}", " ", new_summary).strip()

    return new_support, new_resistance, new_summary
