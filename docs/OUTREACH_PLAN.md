# SEO Outreach Plan — win9you.com (Leeks Terminal)

**Created**: 2026-07-11
**Goal**: +5 SEO points (78/100 → 83/100) within 2 weeks via 5-10 quality backlinks
**Constraint**: All outreach from win9you.com user (kenken) account, not from Mavis/MiniMax

---

## Target Sites (Prioritized by leverage/effort)

### Tier 1 — High Volume + High Authority (do these first)

| # | Site | Type | Est. Traffic | Effort | Est. Score Impact |
|---|------|------|---|---|---|
| 1 | **ProductHunt** | Product launch | 100k+ DAU | 2 hours submit | +2-3 |
| 2 | **HackerNews (Show HN)** | Tech community | 50k+ daily | 30 min post | +1-2 |
| 3 | **r/algotrading** | Reddit 380k members | 380k subs | 30 min post | +1-2 |
| 4 | **r/stocks** | Reddit 3.5M members | 3.5M subs | 30 min post | +1-2 |
| 5 | **r/HKStocks** | Reddit 12k members | 12k subs | 30 min post | +1 |

### Tier 2 — Niche but Authoritative

| # | Site | Type | Notes |
|---|------|------|------|
| 6 | QuantStart (quantstart.com) | Algo trading blog | Guest post on rule-based signals |
| 7 | Elite Trader (elitetrader.com) | Trading forum | Forum signature with link |
| 8 | Seeking Alpha | Finance | Free contributor submission |
| 9 | Seeking Alpha HK |  |  |
| 10 | Stockq.com | Chinese finance | Submit as research article |

### Tier 3 — Knowledge Graph (Biggest leverage, hardest)

| # | Site | Type | Notes |
|---|------|------|------|
| 11 | Wikidata | Structured data | Add Q-id for Leeks Terminal |
| 12 | Wikipedia | Encyclopedia | Submit entry via Articles for Creation |
| 13 | Crunchbase | Company profile | Free tier sufficient |
| 14 | AngelList/Wellfound | Startup profile | Free |

---

## Pre-Submit Prep (do once)

### Assets to create:
- [ ] Hero image 1200x630 (already have og-image.png)
- [ ] 3-5 product screenshots (dashboard cards, paper-trade, insights page)
- [ ] 1 short demo video (60-90s) — use mavis-team to generate
- [ ] Tagline variations: "AI Trading Signal Audit" / "Rule-Based Day Trade" / "LLM vs Rule Audit"
- [ ] Founder photo (K. Chan placeholder) — needs user input

### Draft posts (Reddit + HN ready):

**Reddit r/algotrading post** (draft below):
```
Title: We backtested 1913 AI trading signals. LLM was wrong 62% of the time.

Body:
I run a day-trade dashboard that uses an LLM (MiniMax-M3) + rule-based overlay
to generate BUY/SELL signals for 200 HK + 200 US tickers.

Ran a 10-day audit. Here's what I found:

LLM signals (raw):
- 38.6% hit rate on BUY (worse than HOLD's 40.2%)
- 樂觀 sentiment BUY: 30.4% hit rate (反指 reality — stocks fell)
- m_score ≥ 80 BUY: 16.7% hit rate
- 悲觀 SELL: 37.7% (bounced 0.24% on average — catching knife)

Rule-based signals (Anti-Chase, Anti-Knife, Conservative BUY, Bounce BUY):
- Conservative BUY (mean-reversion): 61.5% hit rate, +0.92% avg
- Bounce BUY: 51.7% hit rate

The LLM is trained on investing content (trend-following), which is the
OPPOSITE of day-trade 1D mean-reversion. The pattern: LLM says 樂觀 → buy
at top → lose. LLM says 悲觀 → sell at bottom → miss bounce.

The fix: treat LLM as a feature extractor (narrative, catalysts, levels) and
let deterministic rules make the BUY/SELL call.

Dashboard is public: https://www.win9you.com/insights.html

Full audit data + 4 rules in the insights page. Curious what others
have found. Are any of you running LLM-based signals and seeing similar
inversion?
```

**HackerNews Show HN** (draft):
```
Title: Show HN: Leeks Terminal – AI trading signal audit found 62% LLM miss-rate

Hi HN — I built a day-trade dashboard that uses an LLM + rule-based overlay
to generate signals for 200 HK + 200 US tickers.

I ran a 10-day audit of 1,913 signals and was surprised to find:

- Raw LLM BUY: 38.6% hit rate (worse than just HOLD)
- LLM 樂觀 sentiment BUY: 30.4% (反指 — stocks actually fell)
- LLM BUY on m_score≥80 (strongest momentum): 16.7% WR
- LLM SELL on panic days: 37.7% (bounced — caught falling knife)

vs rule-based (mean-reversion entry, anti-chase, anti-knife):
- Conservative BUY: 61.5% WR / +0.92% avg

Root cause: LLMs are trained on investing content (trend-following) which
is the OPPOSITE of day-trade 1D mean-reversion.

The fix: don't let LLM decide BUY/SELL. Use it for narrative only. Apply
deterministic rules for the call.

Live dashboard + full audit data: https://www.win9you.com/
Insight writeup: https://www.win9you.com/insights.html

Stack: MiniMax-M3 via LiteLLM, Futu OpenD (HK), Yahoo Finance (US),
Cloudflare Pages, Python.

Happy to discuss the architecture — looking for feedback on the rule
design and any other edges I should test.
```

**Reddit r/stocks** (draft, simpler):
```
Title: Built a free day-trade signal dashboard. After 10 days the LLM
was wrong more than right.

Body:
I've been running an LLM-powered day-trade signal dashboard (200 HK + 200 US
tickers, signals at 9:30 HKT / 9:30 ET). After 10 days I ran an audit on
the 1,913 signals.

The LLM is essentially an inverted indicator:
- LLM 樂觀 BUY: stocks fell 0.21% on average next day
- LLM 悲觀 SELL: stocks bounced 0.15% on average next day

The fix: anti-chase + anti-knife rules now override the LLM. Audit
shows +23 percentage points improvement in hit rate.

Wrote up the full audit and rule design here:
https://www.win9you.com/insights.html

If you've worked with LLM-based trading signals, curious if you saw
similar patterns. The data is published, feel free to critique.
```

---

## Execution Timeline (2 weeks)

### Day 1-2: Prep
- Generate demo video (mavis-team, parallel)
- Create 3-5 product screenshots
- Get founder photo from user

### Day 3-4: Tier 1 push
- ProductHunt launch
- HackerNews Show HN
- r/algotrading post

### Day 5-7: Tier 1 amplification
- r/stocks post (if HN did well)
- r/HKStocks post
- Reply to comments on all platforms

### Day 8-10: Tier 2 outreach
- QuantStart guest post pitch
- Seeking Alpha submission
- Stockq.com research article

### Day 11-14: Tier 3 (Knowledge Graph)
- Wikidata Q-id application
- Crunchbase profile
- Wikipedia Articles for Creation

---

## Tracking

Create `docs/OUTREACH_TRACKER.md` with:
- Date posted
- Site
- URL
- Upvotes/replies (daily check)
- Backlink confirmed? (Y/N)
- DA of linking page
- Score impact estimate

---

## Risk Mitigation

- **Avoid self-promotion flags on Reddit**: Genuine discussion tone, no "check out my site" spam. Post a real question + share data.
- **HackerNews Show HN**: Title must be technical/curious, not "Launch HN". "Show HN" prefix is fine.
- **Wikipedia**: Must cite reliable sources. Don't write the entry yourself — use Articles for Creation process.
- **Backup plan**: If ProductHunt fails, pivot to a BetaList or similar launchpad.

---

## Cost

- All time investment: ~10-15 hours over 2 weeks
- No monetary cost (free tier on all platforms)
- Optional: $29/month for ProductHunt "shipped" badge boost (probably not needed)

---

## Expected Score Impact

| Source | Est. Score Impact |
|---|---|
| ProductHunt (DA 91) | +2-3 |
| HackerNews (DA 90+ if front page) | +1-2 |
| Reddit r/algotrading (DA 70) | +1 |
| Reddit r/stocks (DA 95) | +1-2 |
| Reddit r/HKStocks (DA 30) | +0.5 |
| QuantStart guest post | +0.5 |
| Seeking Alpha | +1 |
| Wikipedia (if accepted) | +3-5 |
| **Total** | **+10-15** |

**Final score: 88-93/100 (Excellent)**

---

## Next Steps

1. Get user approval on this plan
2. Generate demo video + screenshots (mavis-team, parallel)
3. Draft Reddit + HN posts (already drafted above)
4. Schedule submission (1 platform per day to avoid spam flags)
5. Track results in `docs/OUTREACH_TRACKER.md`

要不要而家 launch 呢個 plan？可以用 mavis-team parallel 做晒 prep + 第一波 outreach。