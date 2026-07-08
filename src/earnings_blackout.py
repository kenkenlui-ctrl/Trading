"""Earnings calendar blackout config (2026-07-09).

Prevents BUY signals for tickers with earnings announcement within
configurable days. Manually maintained (no API integration yet).

Format (JSON file at data/earnings_blackout.json):
  {
    "blackout_days": 2,  # skip BUY for tickers with earnings within N days
    "events": [
      {"ticker": "AAPL", "date": "2026-08-01", "type": "earnings", "note": "Q3 FY2026"},
      ...
    ]
  }

How to update:
  - Manually add entries before market open on earnings day
  - Or integrate with yfinance calendar (TODO): yf.Ticker(t).calendar
"""