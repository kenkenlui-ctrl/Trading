# DSA-HK — 港股 AI 智能分析系統

End-of-day AI analysis for Hong Kong stocks. Inspired by [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis), but tightly focused on HK-only with Futu OpenD + Cantonese-friendly reports.

## Features

- **每日 AI 分析報告** — LLM-generated score (0-100), sentiment, trend, entry/exit zones, stop-loss, target prices, catalysts, risks
- **44 HK tickers** — auto-loaded from your existing `curated-radar.json` (single source of truth)
- **HK data sources** — Futu OpenD primary, YFinance fallback (you already run Futu on `127.0.0.1:11111`)
- **News search** — Tavily + Bocha + Brave (auto-fallback)
- **Web dashboard** — Streamlit on port 8200, sortable by score, ticker detail pages, history
- **Telegram bot** — `/dashboard`, `/report 0700.HK`, `/analyze`, `/chat` with multi-turn Q&A
- **Daily scheduler** — APScheduler, runs at configurable HKT time, weekdays only
- **繁體中文 default** — Day-trade-focused prompts with strict no-overnight rule

## Quick start

```bash
# 1. Install deps
cd /Users/kenken/Documents/dsa-hk
pip3 install -r requirements.txt

# 2. Configure
cp .env.example .env
vim .env  # fill in at least one LLM key (Gemini free tier OK)

# 3. Initialize DB
python3 -m src.main init

# 4. Smoke test (3 tickers, end-to-end)
python3 -m src.main smoke

# 5. Run analysis for all 44 HK tickers
python3 -m src.main analyze

# 6. Start web UI
python3 -m src.main webui
# Open http://localhost:8200

# 7. (Optional) Start Telegram bot
python3 -m src.main bot

# 8. (Optional) Run scheduler + bot + web together
python3 -m src.main serve
```

## Architecture

```
dsa-hk/
├── src/
│   ├── config.py          # .env loader, validation
│   ├── ticker_loader.py   # Load HK tickers from curated-radar.json
│   ├── data_fetcher.py    # Futu OpenD + YFinance + indicators (MA, RSI)
│   ├── news_fetcher.py    # Tavily / Bocha / Brave news search
│   ├── prompts.py         # Cantonese + English LLM prompts
│   ├── analyzer.py        # litellm wrapper, structured JSON output
│   ├── db.py              # SQLite schema + CRUD
│   ├── pipeline.py        # Orchestration (fetch → analyze → save → render)
│   ├── web_ui.py          # Streamlit dashboard (port 8200)
│   ├── telegram_bot.py    # python-telegram-bot handlers
│   ├── scheduler.py       # APScheduler daily job
│   └── main.py            # CLI entry point
├── data/                  # SQLite DB (auto-created)
├── reports/               # Per-ticker daily markdown (auto-created)
├── logs/                  # Logs (auto-created)
├── requirements.txt
├── .env.example
└── README.md
```

## Commands

| Command | Description |
|---------|-------------|
| `python -m src.main init` | Initialize SQLite DB |
| `python -m src.main config` | Show config status + warnings |
| `python -m src.main smoke` | Smoke test (0700.HK, 9988.HK, 1810.HK) |
| `python -m src.main analyze` | Analyze all 44 HK tickers |
| `python -m src.main analyze 0700.HK,9988.HK` | Analyze specific tickers |
| `python -m src.main one 0700.HK` | Analyze single ticker with verbose output |
| `python -m src.main dashboard` | Print today's dashboard markdown |
| `python -m src.main webui` | Start Streamlit dashboard on :8200 |
| `python -m src.main bot` | Start Telegram bot (long-running) |
| `python -m src.main schedule` | Start scheduler (runs daily) |
| `python -m src.main serve` | Run web UI + bot + scheduler together |

## Configuration

All config in `.env` (copy from `.env.example`):

### LLM providers (pick at least one)

**MiniMax** (recommended if you have a MiniMax API key):

```bash
MINIMAX_API_KEY=your_key_here
# Optional overrides:
# MINIMAX_BASE_URL=https://api.minimax.io/v1
# MINIMAX_MODEL=MiniMax-M2.7    # or M2.7-highspeed for faster/cheaper
LITELLM_MODEL=openai/MiniMax-M2.7
```

MiniMax is routed via the OpenAI-compatible protocol at `https://api.minimax.io/v1`.

**Gemini** (free tier, good for getting started):

```bash
GEMINI_API_KEY=your_key_here
LITELLM_MODEL=gemini/gemini-2.5-flash
```

**DeepSeek** (cheap, good for Chinese):

```bash
DEEPSEEK_API_KEY=your_key_here
LITELLM_MODEL=deepseek/deepseek-chat
```

**OpenAI**:

```bash
OPENAI_API_KEY=your_key_here
LITELLM_MODEL=openai/gpt-4o-mini
```

### HK Data

```bash
# Futu OpenD (primary, must be running locally)
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
# YFinance auto-fallback if Futu unavailable
```

### News search (recommended)

```bash
TAVILY_API_KEY=your_key    # 1000 free queries/month
BOCHA_API_KEY=your_key     # Chinese-optimized
```

### Ticker universe

```bash
# Default: read from your trading-platform radar
RADAR_PATH=/Users/kenken/Documents/Gstack/trading-platform/docs/curated-radar.json

# Override with specific list
# HK_TICKERS_OVERRIDE=0700.HK,9988.HK,1810.HK

# Test with just first 5
# MAX_TICKERS=5
```

### Telegram bot (optional)

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

## Daily report example

```
🟢 0700.HK 騰訊控股

🚀 評分 78/100 · 樂觀 · 看多 · 買入 · 信心 中高

📋 核心結論
今日恆指走強帶動科技股，騰訊突破 410 阻力位，成交量配合放大。
短線趨勢轉強，建議回調至 408-410 區間吸納，目標 425，止損 398。

🎯 操作建議
- 入場區間: 408-410
- 止損位: 398
- 目標價: 425
- 風險回報比: 2.0

✨ 利好催化
- 恆指科技指數創近期新高
- 公司回購計劃持續
- AI 業務收入增速放緩但仍雙位數

🚨 風險警報
- 今晚美股若大幅波動，明日或有缺口風險
- 4 PM HKT 前必須平倉，不持倉過夜

📊 技術數據
| 指標 | 數值 |
|---|---|
| MA5 / MA10 / MA20 / MA50 | 405.30 / 402.10 / 398.70 / 392.50 |
| RSI14 | 58.2 |
| 52週高/低 | 425.0 / 295.6 |
| PE (TTM) / PB | 18.5 / 4.2 |

*數據來源: futu · LLM: gemini/gemini-2.5-flash*
```

## Cost estimates

| LLM | Cost per ticker | 44 tickers/day |
|-----|-----------------|----------------|
| Gemini 2.5 Flash (free tier) | $0 | $0 |
| Gemini 2.5 Pro | ~$0.01 | ~$0.50 |
| DeepSeek | ~$0.001 | ~$0.05 |
| GPT-4o-mini | ~$0.01 | ~$0.50 |
| GPT-4o | ~$0.05 | ~$2.20 |

News search: $0 with free tier (Tavily 1000/month = 30+ days of 44 tickers).

## Differences from upstream daily_stock_analysis

Cut from the original to stay focused on HK:
- A-share / CN mainland modules (Tushare, AkShare, Efinance, Pytdx, Baostock)
- JP/KR markets
- DingTalk / Feishu / WeChat / Discord / Slack / Pushover / Email / ntfy / Gotify / PushPlus / Server酱 (kept Telegram only)
- AlphaSift, AlphaEvo integrations
- Backtest service (you have your own)
- Feishu cloud doc generation

Added for HK:
- Futu OpenD primary data source
- 繁體中文 default + Cantonese trading rules baked into prompts
- Day-trade focus (no overnight, must close by 4 PM HKT)
- Tickers auto-loaded from your existing curated radar
- Intraday 15m bar enrichment (when available in trading-platform cache)

## Disclaimer

This tool is for educational and research purposes only. AI-generated analysis does not constitute investment advice. Stock trading involves risk; you are solely responsible for your own trading decisions.
