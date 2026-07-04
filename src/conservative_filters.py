"""Constants for backtest-validated high-WR filter presets.
Updated 2026-07-05 based on 6-day trace (26/6 - 2/7).

Evidence:
  - Conservative BUY (mean-reversion + non-tech + m 30-70 + score < 70):
      -1% to 0% prior-day-change bucket: +0.68% avg, 70.6% WR
      -3% to -1% bucket: +1.02% avg, 50% WR
  - Cyber BUY (DDOG/PANW/CRWD/FTNT/etc.):
      Backtest shows avg +1.5% to +3% on 6-day window, n>=2 signals
"""

# Cybersecurity / network-security tickers with positive backtest results
CYBER_TICKERS = {
    "DDOG",   # Datadog — observability
    "PANW",   # Palo Alto Networks — firewall
    "CRWD",   # CrowdStrike — endpoint security
    "FTNT",   # Fortinet — firewall/SD-WAN
    "OKTA",   # Okta — identity
    "ZS",     # Zscaler — zero-trust
    "NET",    # Cloudflare — edge
    "S",      # SentinelOne — endpoint
    "CYBR",   # CyberArk — privileged access
    "RBRK",   # Rubrik — data security
    "QLYS",   # Qualys — vulnerability
    "TENB",   # Tenable — vulnerability
    "VRNS",   # Varonis — data security
}

# Tech / communication-services sectors to AVOID for BUY
# (mean-reversion failed in 6-day trace: Technology -1.86%, Industrials -1.23%)
TECH_SECTORS_AVOID = {
    "Technology",
    "Communication Services",
    "Information Technology",
    "科技",
    "通訊服務",
    "軟件",
    "互聯網",
}