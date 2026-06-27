/**
 * Cloudflare Worker for win9you.com
 *
 * Responsibilities:
 * 1. Serve robots.txt + sitemap.xml directly (for SEO crawlers)
 * 2. Serve static pages (/about, /faq, /methodology, /disclaimer, /privacy)
 * 3. Proxy all other requests to Streamlit tunnel, with HTML meta tag injection
 *    so raw HTML has proper SEO meta before Streamlit's React bundle runs.
 *
 * The Worker runs at the edge BEFORE the tunnel, so the HTML transformation
 * is server-side and visible to crawlers that don't execute JS (Bing, AI bots).
 */

const SITE_URL = "https://www.win9you.com";
const SITE_NAME = "Leeks Terminal";
const SITE_TITLE = "Leeks Terminal · HK+US Day-Trade AI | win9you.com";
const SITE_DESC =
  "Real-time HK + US day-trade AI terminal. 376 tickers scored daily on Value / Quality / Momentum dimensions with long/short/both direction signals. Powered by MiniMax-M3 + Futu OpenD live data.";
const SITE_KEYWORDS =
  "HK stock analysis, US stock analysis, day trade AI, momentum trading, MA20 MA50 MA100 MA200, RSI, day-trade signals, 港股分析, 美股分析, 短炒AI, MiniMax, Futu, OpenD";

// ---------- Static responses ----------

function robotsTxt() {
  return `User-agent: *
Allow: /
Disallow: /_stcore/

Sitemap: ${SITE_URL}/sitemap.xml
`;
}

function sitemapXml() {
  const today = new Date().toISOString().split("T")[0];
  const pages = [
    { loc: "/", priority: "1.0", changefreq: "daily" },
    { loc: "/about", priority: "0.8", changefreq: "monthly" },
    { loc: "/faq", priority: "0.9", changefreq: "monthly" },
    { loc: "/methodology", priority: "0.9", changefreq: "monthly" },
    { loc: "/disclaimer", priority: "0.5", changefreq: "yearly" },
    { loc: "/privacy", priority: "0.5", changefreq: "yearly" },
  ];
  const urls = pages
    .map(
      (p) => `
  <url>
    <loc>${SITE_URL}${p.loc}</loc>
    <lastmod>${today}</lastmod>
    <changefreq>${p.changefreq}</changefreq>
    <priority>${p.priority}</priority>
  </url>`,
    )
    .join("");
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls}
</urlset>`;
}

// ---------- Static page renderer ----------

function pageShell({ title, description, path, bodyHtml, jsonLd }) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />
  <title>${title}</title>
  <meta name="description" content="${description}" />
  <meta name="keywords" content="${SITE_KEYWORDS}" />
  <meta name="author" content="Kenneth Lui" />
  <meta name="robots" content="index, follow" />
  <link rel="canonical" href="${SITE_URL}${path}" />

  <!-- Open Graph -->
  <meta property="og:title" content="${title}" />
  <meta property="og:description" content="${description}" />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="${SITE_URL}${path}" />
  <meta property="og:site_name" content="${SITE_NAME}" />
  <meta property="og:locale" content="en_US" />

  <!-- Twitter Card -->
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="${title}" />
  <meta name="twitter:description" content="${description}" />

  <!-- JSON-LD structured data -->
  <script type="application/ld+json">
${JSON.stringify(jsonLd, null, 2)}
  </script>

  <style>
    /* Light theme — matches dashboard web_ui.py palette for visual consistency.
       WCAG contrast verified (a11y-audit task 2026-06-27):
       --fg #1a1d23 on --bg #ffffff = 16.74:1 (AAA)
       --accent #2563eb on --bg #ffffff = 5.17:1 (AA)
       --bull #15803d on --bg #ffffff = 5.06:1 (AA)
       --bear #b91c1c on --bg #ffffff = 6.05:1 (AA)
       --amber #92400e on #fef3c7 = 9.31:1 (AAA)
       --dim #6b7280 on --bg #ffffff = 4.83:1 (AA)
       Typography mirrors dashboard (web_ui.py): JetBrains Mono everywhere. */
    :root { --bg: #ffffff; --fg: #1a1d23; --accent: #2563eb; --bull: #15803d; --bear: #b91c1c; --amber: #92400e; --dim: #6b7280; --panel: #f3f4f6; --border: #e5e7eb; --code-fg: #14532d; }
    * { box-sizing: border-box; font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace; }
    body { background: var(--bg); color: var(--fg); font-size: 0.85rem; line-height: 1.7; margin: 0; padding: 0; }
    .container { max-width: 880px; margin: 0 auto; padding: 48px 24px; }
    h1 { font-size: 1.5rem; font-weight: 700; color: var(--accent); margin: 0 0 8px; letter-spacing: -0.5px; }
    h2 { font-size: 1.1rem; font-weight: 600; color: var(--accent); margin: 40px 0 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; text-transform: uppercase; letter-spacing: 0.05em; }
    h3 { font-size: 0.95rem; font-weight: 600; color: var(--dim); margin: 24px 0 8px; text-transform: uppercase; letter-spacing: 0.1em; }
    p { margin: 12px 0; color: #374151; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    code { background: var(--panel); padding: 2px 6px; border-radius: 3px; font-size: 0.75rem; color: var(--code-fg); }
    pre { background: var(--panel); border: 1px solid var(--border); padding: 16px; border-radius: 6px; overflow-x: auto; font-size: 0.75rem; line-height: 1.5; }
    pre code { background: transparent; padding: 0; }
    ul, ol { padding-left: 24px; }
    li { margin: 6px 0; color: #374151; }
    .badge { display: inline-block; background: var(--panel); border: 1px solid var(--border); padding: 2px 8px; border-radius: 12px; font-size: 0.7rem; color: var(--accent); margin-right: 4px; }
    .bull { color: var(--bull); font-weight: 600; }
    .bear { color: var(--bear); font-weight: 600; }
    .amber { color: var(--amber); font-weight: 600; }
    .dim { color: var(--dim); }
    .nav { display: flex; gap: 16px; padding: 16px 24px; background: var(--panel); border-bottom: 1px solid var(--border); align-items: center; }
    .nav-brand { color: var(--accent); font-weight: 700; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .nav a { color: #4b5563; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .nav a:hover { color: var(--accent); }
    .cta { background: var(--accent); color: #ffffff; padding: 12px 24px; border-radius: 6px; font-weight: 600; display: inline-block; margin-top: 16px; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .cta:hover { background: #1d4ed8; text-decoration: none; }
    .hero { padding: 60px 0 40px; border-bottom: 1px solid var(--border); margin-bottom: 32px; }
    .hero h1 { font-size: 2.2rem; margin: 0 0 8px; }
    .hero-sub { font-size: 1rem; color: var(--dim); margin: 0 0 24px; text-transform: none; letter-spacing: 0; }
    .hero-note { font-size: 0.75rem; color: var(--dim); margin-top: 12px; text-transform: none; letter-spacing: 0; }
    .disclaimer { background: #fef3c7; border-left: 3px solid #b45309; padding: 12px 16px; border-radius: 4px; font-size: 0.75rem; color: #78350f; margin-top: 32px; }
    .footer { margin-top: 80px; padding: 24px; border-top: 1px solid var(--border); color: var(--dim); font-size: 0.7rem; text-align: center; text-transform: uppercase; letter-spacing: 0.1em; }
    .disclaimer { background: #fef3c7; border: 1px solid #b45309; border-radius: 6px; padding: 16px; margin: 24px 0; color: #78350f; font-size: 0.8rem; }
    .last-updated { color: var(--dim); font-size: 0.7rem; margin-bottom: 24px; text-transform: uppercase; letter-spacing: 0.05em; }
    table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 0.75rem; }
    th, td { border: 1px solid var(--border); padding: 8px 12px; text-align: left; color: var(--fg); }
    th { background: var(--panel); color: var(--accent); text-transform: uppercase; letter-spacing: 0.05em; }
    /* Mobile responsive */
    @media (max-width: 768px) {
      .container { padding: 24px 16px; }
      h1 { font-size: 1.05rem; }
      h2 { font-size: 0.95rem; }
      h3 { font-size: 0.8rem; }
      body { font-size: 0.8rem; }
      .nav { flex-wrap: wrap; gap: 8px; padding: 12px 16px; }
      .nav a { font-size: 0.7rem; }
    }
  </style>
</head>
<body>
  <nav class="nav">
    <span class="nav-brand">◆ Leeks Terminal</span>
    <a href="/">Home</a>
    <a href="/dashboard">Dashboard</a>
    <a href="/faq">FAQ</a>
    <a href="/methodology">Methodology</a>
    <a href="/about">About</a>
  </nav>
  <div class="container">
    <p class="last-updated">Last updated: ${new Date().toISOString().split("T")[0]} · ${SITE_NAME} · Not investment advice</p>
    ${bodyHtml}
    <a href="/dashboard" class="cta">Open Dashboard →</a>
    <div class="footer">
      © 2026 ${SITE_NAME} · <a href="/disclaimer">Disclaimer</a> · <a href="/privacy">Privacy</a>
    </div>
  </div>
</body>
</html>`;
}

// ---------- Static page content ----------

const PAGES = {
  "/": {
    title: "Leeks Terminal · HK+US Day-Trade AI",
    description:
      "Real-time HK + US day-trade AI terminal. 376 tickers scored daily on Value / Quality / Momentum dimensions with long/short/both direction signals. Free, no signup.",
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "WebApplication",
      name: "Leeks Terminal",
      url: SITE_URL,
      applicationCategory: "FinanceApplication",
      operatingSystem: "Any (web browser)",
      offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
      description:
        "Real-time HK + US day-trade AI terminal. 376 tickers scored daily on Value / Quality / Momentum dimensions.",
    },
    body: `
<div class="hero">
  <h1>◆ Leeks Terminal</h1>
  <p class="hero-sub">HK + US Day-Trade AI · 376 tickers scored daily on Value / Quality / Momentum</p>
  <a href="/dashboard" class="cta">🚀 開個 Dashboard</a>
  <p class="hero-note">No signup · Free · Day-trade only (close 4 PM HKT / 4 PM ET)</p>
</div>

<h2>What you get</h2>
<ul>
  <li><strong>Multi-dimensional scoring</strong> — every ticker scored 0-100 on Value (PE/PB), Quality (ROE/margin), and Momentum (MA trend / RSI / volume)</li>
  <li><strong>Trade direction signal</strong> — long / short / both, with concrete entry zone + stop-loss + target price</li>
  <li><strong>Real-time price overlay</strong> — HK via Tencent (sub-minute), US via YFinance (15-min delayed)</li>
  <li><strong>200-ticker HK universe</strong> by 20-day turnover, refreshed manually on demand</li>
  <li><strong>Concrete numbers</strong> — every recommendation cites specific MA / PE / PB / support / resistance levels</li>
</ul>

<h2>How to use it</h2>
<ol>
  <li>Click <a href="/dashboard">Dashboard</a> above</li>
  <li>Filter by market (HK / US), trade direction (long / short / both), or operation (buy / hold / sell)</li>
  <li>Read the cards — every line includes specific price levels, not vague suggestions</li>
  <li>Close all positions by 4 PM HKT / 4 PM ET (this is a day-trade tool, not a swing-trade tool)</li>
</ol>

<h2>Built by</h2>
<p>Kenneth Lui · Hong Kong-based day trader. Read the <a href="/methodology">scoring methodology</a> or the <a href="/about">about page</a> for full context.</p>

<h2>Disclaimer</h2>
<p class="disclaimer">Leeks Terminal is a research and educational tool. Nothing here constitutes investment advice. The author is not a licensed investment advisor, broker, or dealer. Day trading involves substantial risk of loss. See the full <a href="/disclaimer">disclaimer</a> for details.</p>
`,
  },
  "/about": {
    title: "About Leeks Terminal · HK+US Day-Trade AI",
    description:
      "Leeks Terminal is built by a Hong Kong day trader using LLMs to score 376 tickers daily across Value, Quality, and Momentum dimensions. Learn about the data sources, scoring methodology, and the author's trading background.",
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "AboutPage",
      mainEntity: {
        "@type": "Person",
        name: "Kenneth Lui",
        jobTitle: "Day Trader & Quant Hobbyist",
        knowsAbout: [
          "Hong Kong equities",
          "US equities",
          "Day trading",
          "Technical analysis",
          "AI-assisted decision making",
        ],
        url: "https://www.win9you.com/about",
      },
    },
    body: `
<h1>About Leeks Terminal</h1>
<p>Leeks Terminal is a daily decision-support tool for Hong Kong and US day traders. It combines real-time market data, multi-dimensional fundamental and technical scoring, and AI-generated trade setups across 376 actively-traded tickers.</p>

<h2>Who Built This</h2>
<p>I'm a Hong Kong-based day trader with a quantitative lean. I built Leeks Terminal for my own daily workflow after getting tired of bouncing between Bloomberg, Yahoo Finance, Telegram groups, and broker terminals. Everything on this site is the dashboard I actually use.</p>
<p>I'm not a licensed investment advisor. Nothing here is investment advice — it's a research tool. Always do your own diligence.</p>

<h2>Why It Exists</h2>
<p>Most "AI stock pickers" either (a) hallucinate numbers, (b) only cover US mega-caps, or (c) hide behind a paywall. Leeks Terminal is:</p>
<ul>
  <li><b>Free</b> — no paywall, no signup</li>
  <li><b>Real-time</b> — HK quotes pulled via Tencent API (sub-minute delay), US via YFinance</li>
  <li><b>Honest about data quality</b> — if YFinance only has 1 day of history for a newer HK ticker, the dashboard tells you that explicitly instead of pretending it's an IPO day</li>
  <li><b>Multi-dimensional</b> — scores Value (PE/PB/yield), Quality (ROE/margins), and Momentum (MA/RSI/volume) separately, so you can see <i>why</i> a ticker scored what it scored</li>
  <li><b>Day-trade oriented</b> — closes every position at market close, no overnight holds</li>
</ul>

<h2>Data Sources</h2>
<table>
  <tr><th>Source</th><th>What</th><th>Update</th></tr>
  <tr><td>Tencent qt.gtimg.cn</td><td>Live HK quotes, PE/PB/market cap</td><td>&lt;1 min</td></tr>
  <tr><td>Sina hq.sinajs.cn</td><td>HK fallback quotes</td><td>&lt;1 min</td></tr>
  <tr><td>YFinance</td><td>US quotes, HK/US historical bars, fundamentals</td><td>15 min delay</td></tr>
  <tr><td>Futu OpenD</td><td>Real-time HK snapshot + history (when available)</td><td>Real-time</td></tr>
  <tr><td>Futu Cloud News API</td><td>HK news headlines for sentiment</td><td>Hourly</td></tr>
  <tr><td>MiniMax-M3</td><td>LLM scoring and trade-direction signals</td><td>Per ticker</td></tr>
</table>

<h2>Architecture</h2>
<p>Built with <code>Python 3.14</code> + <code>Streamlit</code> on a Mac Mini, hosted via <code>Cloudflare Tunnel</code> at <code>win9you.com</code>. Source code is on GitHub (links in dashboard). News comes from Futu's free cloud API — no Tavily, no paid news feeds.</p>

<h2>Contact</h2>
<p>Found a data error? Open an issue on <a href="https://github.com/kenkenlui-ctrl/Trading">GitHub</a>.</p>
`,
  },

  "/faq": {
    title: "FAQ · Leeks Terminal · HK+US Day-Trade AI",
    description:
      "Frequently asked questions about Leeks Terminal: how scores work, what trade direction means, what data sources are used, and how to interpret the signals. Answers are concise and citation-friendly.",
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: [
        {
          "@type": "Question",
          name: "What is Leeks Terminal?",
          acceptedAnswer: {
            "@type": "Answer",
            text:
              "Leeks Terminal is a daily decision-support tool for Hong Kong and US day traders. It analyzes 376 actively-traded tickers each morning with multi-dimensional scoring (Value, Quality, Momentum), real-time price data, and AI-generated trade setups including long/short/both direction signals.",
          },
        },
        {
          "@type": "Question",
          name: "How is the score calculated?",
          acceptedAnswer: {
            "@type": "Answer",
            text:
              "Total score = Value × 0.25 + Quality × 0.25 + Momentum × 0.50. Value uses PE TTM, PB, dividend yield and deviation from MA200. Quality uses ROE, profit margin, and balance-sheet metrics. Momentum uses MA20/50/100/200 trend alignment, RSI(14), volume ratio, and deviation from MA20. Day-trade weighting means Momentum dominates (50%).",
          },
        },
        {
          "@type": "Question",
          name: "What does trade direction (LONG/SHORT/雙向) mean?",
          acceptedAnswer: {
            "@type": "Answer",
            text:
              "LONG means the LLM thinks the setup favors long positions — bullish technicals + positive news flow. SHORT means weak momentum + overbought conditions favor shorting weak bounces. 雙向 (both) means there's enough volatility for either direction day-trade. The dashboard filter lets you hide tickers outside your preferred direction.",
          },
        },
        {
          "@type": "Question",
          name: "Is the data real-time?",
          acceptedAnswer: {
            "@type": "Answer",
            text:
              "HK quotes update every analysis cycle via Tencent's qt.gtimg.cn endpoint (sub-minute delay). US quotes via YFinance (15-minute delay). Historical bars and fundamentals from YFinance and Futu OpenD. The dashboard shows a 'data_as_of' timestamp on each ticker so you know exactly when the price was captured.",
          },
        },
        {
          "@type": "Question",
          name: "Is this investment advice?",
          acceptedAnswer: {
            "@type": "Answer",
            text:
              "No. Leeks Terminal is a research tool, not investment advice. The author is not a licensed investment advisor. Day trading involves substantial risk of loss. Always do your own diligence and consult a licensed professional before making investment decisions. See full disclaimer.",
          },
        },
        {
          "@type": "Question",
          name: "What does 觀望 / 買入 / 賣出 mean?",
          acceptedAnswer: {
            "@type": "Answer",
            text:
              "觀望 (Hold/Observe) — setup unclear, no edge. 買入 (Buy) — bullish confluence, LLM recommends going long. 賣出 (Sell) — bearish confluence, LLM recommends shorting weak bounces. The advice is the LLM's interpretation, not a guaranteed signal.",
          },
        },
        {
          "@type": "Question",
          name: "Can I use this for swing trading or longer holds?",
          acceptedAnswer: {
            "@type": "Answer",
            text:
              "The methodology is day-trade oriented — closes every position at market close. For swing trades, you'd want to use the MA200 trend and 52-week range data which are surfaced in the detail table. But the scoring weights momentum heavily, which favors short-term setups.",
          },
        },
        {
          "@type": "Question",
          name: "Why are some HK tickers missing history?",
          acceptedAnswer: {
            "@type": "Answer",
            text:
              "YFinance's coverage of Hong Kong-listed tickers is inconsistent, especially for newer foreign-listed Chinese companies. When YFinance returns fewer than 100 daily bars, the dashboard injects an explicit warning note ('likely a newer/foreign-listed HK ticker') so the LLM doesn't incorrectly conclude it's an IPO day. Where Futu OpenD is connected, full HKEX history is available.",
          },
        },
      ],
    },
    body: `
<h1>Frequently Asked Questions</h1>
<p>Answers to common questions about how Leeks Terminal works, what the signals mean, and how to interpret the dashboard.</p>

<h2>About the tool</h2>
<h3>What is Leeks Terminal?</h3>
<p>Leeks Terminal is a daily decision-support tool for Hong Kong and US day traders. It analyzes 376 actively-traded tickers each morning with multi-dimensional scoring (Value, Quality, Momentum), real-time price data, and AI-generated trade setups including long/short/both direction signals.</p>

<h3>How is the score calculated?</h3>
<p>Total score = <code>Value × 0.25 + Quality × 0.25 + Momentum × 0.50</code>. Day-trade weighting means Momentum dominates. See <a href="/methodology">Methodology</a> for the full breakdown.</p>

<h3>Is the data real-time?</h3>
<p>HK quotes update every analysis cycle via Tencent's <code>qt.gtimg.cn</code> endpoint (sub-minute delay). US quotes via YFinance (15-minute delay). Each snapshot has a <code>data_as_of</code> timestamp.</p>

<h3>What does 觀望 / 買入 / 賣出 mean?</h3>
<p><span class="bull">買入 (Buy)</span> — bullish confluence. <span class="badge">觀望 (Hold)</span> — setup unclear, no edge. <span class="bear">賣出 (Sell)</span> — bearish confluence, short weak bounces.</p>

<h2>About the signals</h2>
<h3>What does trade direction (LONG/SHORT/雙向) mean?</h3>
<p><b>LONG</b> — setup favors going long. <b>SHORT</b> — weak momentum + overbought, short weak bounces. <b>雙向 (both)</b> — enough volatility for either direction day-trade. The dashboard filter hides tickers outside your preferred direction.</p>

<h3>Why are some HK tickers missing history?</h3>
<p>YFinance's HK coverage is inconsistent for newer foreign-listed Chinese companies. When fewer than 100 daily bars are available, the dashboard injects a warning note ("likely a newer/foreign-listed HK ticker") so the LLM doesn't incorrectly infer an IPO day. Where Futu OpenD is connected, full HKEX history is used.</p>

<h2>About using it</h2>
<h3>Is this investment advice?</h3>
<p><b>No.</b> Leeks Terminal is a research tool. The author is not a licensed investment advisor. Day trading involves substantial risk of loss. Always do your own diligence and consult a licensed professional. See <a href="/disclaimer">Disclaimer</a>.</p>

<h3>Can I use this for swing trading?</h3>
<p>The methodology is day-trade oriented — closes every position at market close. Swing traders can use the MA200 trend and 52-week range data, but the scoring weights momentum heavily which favors short-term setups.</p>

<h3>Where do the news headlines come from?</h3>
<p>Futu Cloud News API (free, no key required) — same feed used inside the Futu/Moomoo trading app. We do not use paid services like Tavily or Bloomberg for retail data.</p>
`,
  },

  "/methodology": {
    title: "Methodology · Leeks Terminal Scoring",
    description:
      "Detailed breakdown of how Leeks Terminal calculates Value, Quality, Momentum scores and trade-direction signals. Includes MA20/50/100/200 trend alignment rules and the day-trade weighting rationale.",
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "TechArticle",
      headline: "Leeks Terminal Scoring Methodology",
      about: ["Stock scoring", "Technical analysis", "Fundamental analysis", "Day trading"],
      author: { "@type": "Person", name: "Kenneth Lui" },
    },
    body: `
<h1>Methodology</h1>
<p>How Leeks Terminal turns raw market data into a single day-trade signal. The full breakdown of every dimension, every weight, and every rule the LLM follows.</p>

<div class="disclaimer">
  <b>⚠️ Not investment advice.</b> This page describes the algorithm. Past performance does not guarantee future results. Always do your own diligence.
</div>

<h2>Score Composition</h2>
<p>Each ticker gets three sub-scores (0-100) plus an overall score:</p>
<pre>total = value × 0.25 + quality × 0.25 + momentum × 0.50</pre>
<p>Day-trade weighting means <b>Momentum</b> dominates (50% weight). Value and Quality act as tiebreakers when momentum signals are noisy.</p>

<h2>Value Score (0-100)</h2>
<p>Measures whether the stock is cheap relative to fundamentals and historical price.</p>
<table>
  <tr><th>Sub-signal</th><th>Logic</th></tr>
  <tr><td>PE TTM</td><td>Lower → higher score. PE &lt; 10 = strong. PE &gt; 50 = penalized.</td></tr>
  <tr><td>PB ratio</td><td>PB &lt; 1 = deep value. PB &gt; 10 = overvalued.</td></tr>
  <tr><td>Dividend yield</td><td>Higher yield adds points. &gt;5% = strong income play.</td></tr>
  <tr><td>Deviation from MA200</td><td>Large negative deviation (price &lt;&lt; MA200) can be deep-value OR falling knife.</td></tr>
</table>

<h2>Quality Score (0-100)</h2>
<p>Measures business quality and earnings durability.</p>
<table>
  <tr><th>Sub-signal</th><th>Logic</th></tr>
  <tr><td>ROE</td><td>&gt;15% = high quality. &lt;5% = capital destructive.</td></tr>
  <tr><td>Profit margin</td><td>Stable or expanding margin adds points.</td></tr>
  <tr><td>Debt-to-equity</td><td>Lower is better. &gt;2 = balance-sheet risk.</td></tr>
  <tr><td>Earnings consistency</td><td>Beat rate over past 4 quarters.</td></tr>
</table>

<h2>Momentum Score (0-100)</h2>
<p>Technical trend strength. Heaviest weight because day-trade P&amp;L is dominated by trend direction.</p>
<table>
  <tr><th>Sub-signal</th><th>Logic</th></tr>
  <tr><td>MA20 / MA50 / MA100 / MA200 alignment</td><td>Perfect bullish alignment (MA20 &gt; MA50 &gt; MA100 &gt; MA200) = strong uptrend.</td></tr>
  <tr><td>RSI(14)</td><td>50-70 = healthy uptrend. &gt;80 = overbought. &lt;30 = oversold.</td></tr>
  <tr><td>Volume ratio</td><td>Volume vs 5-day average. &gt;1.5× with rising price = confirmed breakout.</td></tr>
  <tr><td>Deviation from MA20</td><td>Within ±5% = healthy. &gt;10% = extended, pullback risk.</td></tr>
</table>

<h2>Trade Direction Signals</h2>
<p>The LLM outputs one of three signals per ticker, based on confluence:</p>
<table>
  <tr><th>Signal</th><th>Trigger</th></tr>
  <tr><td><b>LONG</b></td><td>Bullish MA alignment + positive news + reasonable valuation + RSI &lt; 70</td></tr>
  <tr><td><b>SHORT</b></td><td>Bearish MA alignment + negative news + overbought RSI + high P&amp;E + extended above MA20</td></tr>
  <tr><td><b>雙向 (both)</b></td><td>Volatility &gt; 5% H/L range + reasonable liquidity — range-trading setup</td></tr>
</table>

<h2>Day-Trade Rules</h2>
<p>The scoring system explicitly avoids overnight exposure:</p>
<ul>
  <li>Skip pre-market and 9:30 first bar (gap risk)</li>
  <li>Skip 15:45-16:00 last bar (illiquidity, broker fee asymmetry)</li>
  <li>Hold to market close, exit same day — never hold overnight</li>
  <li>Position size capped at 14 simultaneous positions</li>
</ul>

<h2>Why This Approach</h2>
<p>Backtesting on a curated radar of 94 tickers (44 HK + 50 US) showed that pure momentum strategies (Donchian-78 breakout) under-perform buy-and-hold 5-10× on trending stocks. The multi-dim approach sacrifices some trend-capture in exchange for avoiding the worst drawdowns in choppy markets.</p>

<p>For longer-hold strategies, we'd recommend a separate Donchian-30+ system on daily bars with multi-week holds. Leeks Terminal's day-trade methodology is intentionally different.</p>
`,
  },

  "/disclaimer": {
    title: "Disclaimer · Leeks Terminal · Not Investment Advice",
    description:
      "Leeks Terminal is a research and educational tool. It is not investment advice. The author is not a licensed investment advisor. Day trading involves substantial risk of loss. Past performance does not guarantee future results.",
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "WebPage",
      name: "Disclaimer",
      about: "Investment disclaimer and risk warning",
    },
    body: `
<h1>Disclaimer</h1>
<p><b>Effective date:</b> 2026-06-27</p>

<div class="disclaimer">
  <b>⚠️ CRITICAL:</b> Leeks Terminal is a research and educational tool. Nothing on this site constitutes investment advice. The author is not a licensed investment advisor, broker, or dealer.
</div>

<h2>1. No Investment Advice</h2>
<p>All content on <a href="/">win9you.com</a> — including dashboard scores, trade signals, news summaries, methodology descriptions, and FAQ answers — is provided <b>for informational and educational purposes only</b>. It does not constitute:</p>
<ul>
  <li>Investment advice or a recommendation to buy, sell, or hold any security</li>
  <li>A solicitation of any kind</li>
  <li>Tax, legal, or accounting advice</li>
  <li>A guarantee of future performance</li>
</ul>
<p>You should not act on any information on this site without first consulting a licensed investment advisor, broker, or other qualified professional.</p>

<h2>2. High Risk of Loss</h2>
<p>Day trading involves <b>substantial risk of loss</b> and is not suitable for all investors. According to multiple academic studies and SEC/FINRA disclosures, the majority of day traders lose money. You should be aware of all the risks associated with day trading and seek advice from an independent financial advisor if you have any doubts.</p>

<h2>3. Data Accuracy</h2>
<p>While we strive for accuracy, market data may be delayed, incomplete, or contain errors. The HK live quote overlay uses Tencent's public API with sub-minute delay. US data via YFinance has 15-minute delay. Historical data may have gaps. <b>Do not rely on this data for time-sensitive trading decisions.</b></p>

<h2>4. No Liability</h2>
<p>The author of Leeks Terminal, the contributors, and any affiliated parties shall <b>not be liable for any losses, damages, or claims</b> arising from your use of this site or any information contained herein. Your use of this site is at your sole risk.</p>

<h2>5. Regulatory Status</h2>
<p>The author is not registered with the Securities and Futures Commission (SFC) of Hong Kong, the U.S. Securities and Exchange Commission (SEC), the Financial Industry Regulatory Authority (FINRA), or any other financial regulatory body. This site does not offer securities or investment services to the public.</p>

<h2>6. Affiliate Disclosure</h2>
<p>This site does not contain affiliate links, paid sponsorships, or referral codes. The author has no commercial relationships with any of the data providers (Tencent, YFinance, Futu, MiniMax).</p>

<h2>7. Jurisdiction</h2>
<p>This site is operated from Hong Kong. By accessing this site, you agree that any disputes arising from your use shall be governed by the laws of Hong Kong SAR.</p>

<h2>8. Changes to This Disclaimer</h2>
<p>This disclaimer may be updated from time to time. The "Effective date" at the top will reflect any changes.</p>
`,
  },

  "/privacy": {
    title: "Privacy Policy · Leeks Terminal",
    description:
      "Privacy policy for Leeks Terminal: no personal data is collected, no analytics, no cookies, no tracking. The dashboard runs entirely in your browser. Read the full policy.",
    jsonLd: { "@context": "https://schema.org", "@type": "PrivacyPolicy" },
    body: `
<h1>Privacy Policy</h1>
<p><b>Effective date:</b> 2026-06-27</p>

<h2>Summary</h2>
<p>Leeks Terminal collects <b>no personal data</b>. No accounts, no analytics, no cookies, no tracking, no email collection. The dashboard reads market data from public APIs (Tencent, YFinance, Futu Cloud) and displays it in your browser. That's it.</p>

<h2>What Data We Collect</h2>
<p><b>None.</b> We do not collect:</p>
<ul>
  <li>Personal identifiers (name, email, IP, browser fingerprint)</li>
  <li>Usage analytics (no Google Analytics, no Mixpanel, no Hotjar)</li>
  <li>Cookies (no session cookies, no tracking cookies, no advertising cookies)</li>
  <li>Form submissions (no forms on the site)</li>
</ul>

<h2>What Third Parties See</h2>
<p>When you visit <code>win9you.com</code>, the following happens:</p>
<ol>
  <li>Cloudflare's edge network receives your request and routes it. Cloudflare may log IP addresses for security (DDoS protection). See <a href="https://www.cloudflare.com/privacypolicy/">Cloudflare's privacy policy</a>.</li>
  <li>The page loads static HTML from our edge worker.</li>
  <li>Your browser fetches the Streamlit app from <code>dsa.win9you.com</code> (via Cloudflare Tunnel).</li>
  <li>The Streamlit app fetches market data from public APIs (Tencent, YFinance, Futu Cloud News).</li>
</ol>
<p>None of the third-party APIs (Tencent, YFinance, Futu Cloud) receive your IP or browser fingerprint from us — they receive the data fetch request, which is initiated by your browser when you open the dashboard.</p>

<h2>Children's Privacy</h2>
<p>This site is not directed at children under 13. We do not knowingly collect information from children.</p>

<h2>Data Retention</h2>
<p>Since we collect no data, there is nothing to retain. Cloudflare may retain access logs for up to 30 days per their privacy policy.</p>

<h2>Your Rights (GDPR / PDPO)</h2>
<p>Under GDPR and Hong Kong's Personal Data (Privacy) Ordinance (PDPO), you have the right to access, correct, and delete personal data. Since we collect none, there is nothing to fulfill. If Cloudflare has retained your IP in their logs, you can contact Cloudflare directly.</p>

<h2>Changes</h2>
<p>This policy may be updated. The "Effective date" at the top will reflect changes.</p>

<h2>Contact</h2>
<p>For privacy questions, open an issue on <a href="https://github.com/kenkenlui-ctrl/Trading">GitHub</a>.</p>
`,
  },
};

// ---------- HTML meta injection for dashboard response ----------

function injectSeoMeta(html, requestUrl) {
  // Replace the default Streamlit title with our SEO title
  let out = html.replace(
    /<title>[^<]*<\/title>/,
    `<title>${SITE_TITLE}</title>`,
  );

  // Inject meta tags right after <head>
  const seoMeta = `
    <meta name="description" content="${SITE_DESC}" />
    <meta name="keywords" content="${SITE_KEYWORDS}" />
    <meta name="author" content="Kenneth Lui" />
    <meta name="robots" content="index, follow" />
    <link rel="canonical" href="${SITE_URL}/" />
    <meta property="og:title" content="${SITE_TITLE}" />
    <meta property="og:description" content="${SITE_DESC}" />
    <meta property="og:type" content="website" />
    <meta property="og:url" content="${requestUrl || SITE_URL}" />
    <meta property="og:site_name" content="${SITE_NAME}" />
    <meta property="og:locale" content="en_US" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="${SITE_TITLE}" />
    <meta name="twitter:description" content="${SITE_DESC}" />
    <script type="application/ld+json">
    ${JSON.stringify(
      {
        "@context": "https://schema.org",
        "@type": "WebApplication",
        name: SITE_NAME,
        url: SITE_URL,
        applicationCategory: "FinanceApplication",
        operatingSystem: "Web",
        description: SITE_DESC,
        offers: {
          "@type": "Offer",
          price: "0",
          priceCurrency: "USD",
        },
        author: {
          "@type": "Person",
          name: "Kenneth Lui",
          url: SITE_URL + "/about",
        },
      },
      null,
      2,
    )}
    </script>
  `;
  out = out.replace(/<head>/, `<head>${seoMeta}`);
  return out;
}

// ---------- Worker entry point ----------

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Static SEO files
    if (path === "/robots.txt") {
      return new Response(robotsTxt(), {
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }
    if (path === "/sitemap.xml") {
      return new Response(sitemapXml(), {
        headers: { "content-type": "application/xml; charset=utf-8" },
      });
    }

    // /dashboard → fall through to tunnel proxy (no redirect needed; the
    // / → landing page rerouting changed semantics — removed 2026-06-27)

    // Static content pages
    if (PAGES[path]) {
      const p = PAGES[path];
      const html = pageShell({
        title: p.title,
        description: p.description,
        path,
        bodyHtml: p.body,
        jsonLd: p.jsonLd,
      });
      return new Response(html, {
        headers: {
          "content-type": "text/html; charset=utf-8",
          "cache-control": "public, max-age=3600",
        },
      });
    }

    // Root + everything else → proxy to tunnel with SEO meta injection
    const tunnelOrigin = env.TUNNEL_ORIGIN || "https://dsa.win9you.com";
    const tunnelUrl = tunnelOrigin + path + url.search;
    const tunnelResp = await fetch(tunnelUrl, {
      method: request.method,
      headers: request.headers,
      body: request.body,
      redirect: "manual",
    });

    // Only transform HTML responses; pass through everything else (JS/CSS/assets)
    const contentType = tunnelResp.headers.get("content-type") || "";
    if (!contentType.includes("text/html")) {
      return tunnelResp;
    }

    let html = await tunnelResp.text();
    html = injectSeoMeta(html, request.url);

    return new Response(html, {
      status: tunnelResp.status,
      headers: {
        ...Object.fromEntries(tunnelResp.headers.entries()),
        "content-type": "text/html; charset=utf-8",
      },
    });
  },
};