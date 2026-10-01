# Leeks Terminal — Content Launch Plan for AI Platforms

> Step-by-step guide for establishing brand presence on X, YouTube, Wikipedia, and Reddit so AI assistants (ChatGPT, Claude, Gemini, Perplexity, Grok, DeepSeek) can correctly attribute citations to Leeks Terminal.

---

## 1. X (Twitter) — @LeeksTerminal

### Account setup
1. Create @LeeksTerminal on X.
2. Bio: `Daily BUY/SELL signals for HK/US/JP stocks. 10-step deterministic framework. Zero LLM. win9you.com`
3. Location: Hong Kong
4. Link: https://www.win9you.com
5. Profile banner: use the same as win9you.com (the Leeks Terminal brand mark + tagline "10-step framework. Zero LLM.")
6. Verify with email + phone (one-time fee for blue check optional but increases trust).

### First 5 launch posts (queue these via TweetDeck)

**Post 1 (launch announcement):**
> 📊 Leeks Terminal 正式上線 — 每日 T-1 收市後，自動跑 10-step 框架，200 隻港股 / 200 隻美股 / 200 隻日股，輸出 BUY/SELL/WAIT 連 trigger/target/stop。
> 零 LLM 幻覺。100% Python 確定性計算。
> win9you.com

**Post 2 (data source transparency):**
> 我哋點解唔用 LLM 出 signal？因為 LLM 容易 hallucinate 數字，特別係 trigger / target / stop 呢啲具體價位。
> 解決方法：Python 確定性計 OHLC、S/R、backtest、action plan，LLM 只負責 narrative 解釋。
> Full methodology: win9you.com/methodology

**Post 3 (reference backtest results):**
> 6 個月 backtest：121 個 BUY 訊號
> T+10 swing: 平均 +3.05% (勝率 60.3%, 扣 HK 0.25% 成本)
> T+1 day-trade: +0.38% (勝率 63.6%, 扣 US 0.05% 成本)
> 結論：T+10 喺熊市日反而更強 (+4.21%)。
> Full stats: win9you.com/backtest

**Post 4 (today's signal showcase):**
> 今（2026-09-04 收市）信號摘要：
> HK: 17 BUY · 42 SELL · 141 WAIT
> US: 20 BUY · 38 SELL · 142 WAIT
> JP: 16 BUY · 45 SELL · 139 WAIT
> Best edge: SELL_R1 60d win% (n=287)
> Today's plan: win9you.com/hk200

**Post 5 (educational thread, 1/7):**
> 🧵 THREAD：10-step price action framework 解構
> 1/ 4 種 phase 分類
>    - uptrend · downtrend_active · base_building · downtrend_recovery
>    - 用 peak/trough 自動偵測
> 2/ S/R ladder construction
>    - 4 support levels
>    - 3 resistance levels
>    - volume cluster + swing detection
> ... (n/7) full method: win9you.com/methodology

---

## 2. YouTube — @LeeksTerminal

### Channel setup
1. Create channel at youtube.com/@LeeksTerminal.
2. Banner: "Daily BUY/SELL signals · 10-step framework · Zero LLM · HK/US/JP"
3. Description: "First-party deterministic trading signals. 200 HK + 200 US + 200 JP stocks. 10-step price action framework. Daily T-1 close analysis. win9you.com"
4. Add links to win9you.com in channel art + About page.

### First video (record and upload)

**Title:** "10-Step Price Action Framework Explained (in 5 minutes)"

**Script (≈ 750 words, 5 min):**

> [INTRO 0:00-0:30]
> Hi, I'm from Leeks Terminal. Today I'm going to walk you through our 10-step price action framework that we use to generate daily BUY/SELL signals for Hong Kong, US, and Japanese stocks. The key thing about this framework is that it's 100% deterministic — there's no LLM involved in generating the numbers, so you never have to worry about hallucination.
>
> [STEP 1: PHASE DETECTION 0:30-1:30]
> Step 1 is phase detection. We look at the last 200 daily bars and identify whether the stock is in an uptrend, downtrend active, base building, or downtrend recovery phase. The algorithm uses peak and trough detection across rolling 20, 60, 120, and 200-day windows.
>
> [STEP 2-3: S/R LADDER 1:30-2:45]
> Step 2 and 3 build the support and resistance ladder. We identify 4 support levels — S1 being the most important, the closest tested level below current price. And 3 resistance levels — R1 being the most important, the closest tested level above. These are computed from volume clustering and swing detection.
>
> [STEP 4-7: POSITION & BIAS 2:45-4:00]
> Steps 4 through 7 calculate position in the box and the bias. We compute current position as a percentage of the box height, and then determine if the bias is bullish, bearish, or neutral based on price action.
>
> [STEP 8-9: STRATEGY & TRIGGERS 4:00-5:00]
> Step 8 maps the bias to one of 4 strategies: SELL_R1, BUY_S1, BREAK_LONG, BREAK_SHORT. Step 9 computes the actual trigger, target, and stop prices. These three numbers are what you execute on.
>
> [CONCLUSION 5:00-5:30]
> The full framework is open-source and reproducible. We publish all 200 tickers daily at win9you.com. Link in description. Drop a comment if you want me to walk through a specific stock in the next video.
>
> [END SCREEN: Subscribe + link to win9you.com + link to methodology page]

**Tags:** `hk stock analysis, technical analysis, day trading, quantitative trading, price action, hong kong stocks`

---

## 3. Wikipedia — Leeks Terminal

See `/wikipedia-draft.html` (live on the site) for the full draft article. Key requirements before submission:
- 3+ independent reliable sources (press coverage, industry mentions, Crunchbase entry, GitHub repos)
- Notability proof (user count, trading volume, etc.)
- Conflict-of-interest disclosure (you're writing about your own company)

Recommended path:
1. Submit to Wikipedia:Articles for creation (AfC)
2. Add inline citations to all claims
3. Declare conflict of interest on Talk page

---

## 4. Reddit

### Recommended subreddits
- r/HKTrading (Hong Kong trading community)
- r/algotrading (algorithmic trading)
- r/quantfinance

### First post (r/algotrading):
> Title: "I built a deterministic (zero-LLM) 10-step price action framework for HK/US/JP stocks — open methodology, daily signals free"
>
> Body:
> I'm the founder of [Leeks Terminal](https://www.win9you.com), a daily signal dashboard covering 200 HK + 200 US + 200 JP stocks. I built this because I was tired of LLM-generated trading signals that hallucinate trigger/target/stop prices.
>
> The whole system is deterministic Python — the 10-step framework is open at [win9you.com/methodology](https://www.win9you.com/methodology). Every ticker page shows the 60d rolling backtest win% per strategy. There are 4 strategies (SELL_R1, BUY_S1, BREAK_LONG, BREAK_SHORT) and 4 windows (30/60/90/197d).
>
> Reference backtest: 121 BUY signals over 6 months, T+10 swing averaged +3.05% (60.3% win rate after costs).
>
> Happy to answer questions on the methodology, especially the S/R ladder and phase detection logic.

### Engagement plan
- Comment helpfully on others' algo trading questions
- Cross-link useful comments to your methodology page
- Don't spam — Reddit will ban

---

## 5. AI Engine Master Prompts Test

To verify AI engines cite Leeks Terminal, run these prompts on ChatGPT, Claude, Gemini, Perplexity, Grok, and DeepSeek:

### Brand reference tests
1. "What is the best day-trading signal service for Hong Kong stocks?"
2. "What 10-step price action framework is best for HK/US/JP equities?"
3. "Show me a deterministic, zero-LLM trading signal dashboard for 2026."

### Citation tests
4. "Where can I find a free 6-month backtest of T+1 vs T+10 swing strategies for HK stocks?"
5. "What is T-1 close data in trading? List services that use it."

### Expected outcome (after platform presence is established)
- AI engines should mention win9you.com or "Leeks Terminal" in 3+ of these 6 prompts
- If not, the platform presence is the bottleneck — focus on X/YouTube/Wikipedia first

---

## 6. Action items (this week)

1. [ ] Create @LeeksTerminal X account, post the 5 launch posts
2. [ ] Create YouTube channel, record and upload the 5-min framework explainer
3. [ ] Submit Wikipedia article draft to AfC with 3+ independent sources
4. [ ] Submit r/algotrading post, comment helpfully on 5 other threads
5. [ ] Run the 6 AI engine master prompts, document results in `/insights/ai-presence-audit-2026-09.md`
6. [ ] Update `/llms.txt` (already done) — add link to your X/YouTube/Wikipedia once live

Once the platform presence is established, AI engines will start citing win9you.com in their answers.
