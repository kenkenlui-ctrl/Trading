// 2026-09-06: detect user's region via CF-IPCountry (from /ip-route) + tz fallback.
(async function() {
  let market = 'US';
  try {
    const r = await fetch('/ip-route');
    if (r.ok) {
      const { suggested_market } = await r.json();
      if (suggested_market) market = suggested_market;
    }
  } catch (e) { /* fall through to tz */ }
  // Fallback to tz if edge not reachable
  if (market === 'US') {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    if (tz.startsWith('Asia/Hong_Kong') || tz.startsWith('Asia/Macau')) market = 'HK';
    else if (tz.startsWith('Asia/Tokyo')) market = 'JP';
    else if (tz.startsWith('Asia/Shanghai') || tz.startsWith('Asia/Chongqing') || tz.startsWith('Asia/Taipei')) market = 'HK';
    const lang = navigator.language || 'en';
    if (market === 'US' && /zh|hk|cmn|yue/.test(lang)) market = 'HK';
    if (market === 'US' && /ja/.test(lang)) market = 'JP';
  }
  document.documentElement.dataset.suggestedMarket = market;
  const el = document.querySelector('.region-suggest');
  if (el) {
    el.innerHTML = `<a href="/${market.toLowerCase()}200/" class="region-suggest-link">View ${market} Signals →</a>`;
    el.style.display = 'inline-block';
  }
})();
