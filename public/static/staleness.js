/* staleness.js — the visitor-visible freshness check, evaluated at REQUEST time.
 *
 * WHY THIS IS CLIENT-SIDE
 *   The A1 contract computes the state at BUILD time, and that is right for a
 *   build: the page is built from the data of that moment. But the failure
 *   this exists for is a site that was built once and then sat there for four
 *   days while the market moved on. A build-time banner cannot catch that —
 *   it was computed when the data was still fresh, and it is frozen into the
 *   shipped HTML.
 *
 *   On 2026-10-29 the live site was exactly that: built 2026-10-25, still
 *   saying "Live", four sessions out of date, and nothing on the page said so.
 *
 *   So the page carries the facts (which session the data covers, when that
 *   session closed, when the NEXT session was due to close) and the visitor's
 *   own clock decides. The server-rendered banner is still there for the
 *   build-time state; this adds the case the server cannot see.
 *
 * The decision itself lives in staleness-core.js, which has no DOM in it and
 * is tested by scripts/staleness_js_test.js on the server.
 */
(function () {
  'use strict';

  var meta = document.querySelector('meta[name="w9-freshness"]');
  if (!meta || !window.W9Freshness) return;
  var facts;
  try {
    facts = JSON.parse(meta.getAttribute('content') || 'null');
  } catch (e) {
    return;
  }
  if (!facts || !facts.length) return;

  var LABEL = { HK: '港股', US: '美股', JP: '日股' };
  var stale = window.W9Freshness.decide(facts, Date.now());
  if (!stale.length) return;

  /* ---- the banner ---------------------------------------------------- */
  var rows = stale.map(function (f) {
    var name = LABEL[f.market] || f.market;
    var why = f.reason === 'session-missed'
      ? '數據截至 ' + f.session + '，但市場其後應該已經開過市（應有新 session）'
      : '數據已過 ' + f.ageH.toFixed(0) + ' 小時（上限 ' + f.limit + ' 小時）';
    return '<li><b>' + name + '</b>：' + why +
      '（數據截至 ' + f.session + '，已過 ' + f.ageH.toFixed(0) + ' 小時）' +
      '受影響 session：' + (f.expected || f.session) + '。</li>';
  }).join('');

  var banner = document.createElement('div');
  banner.className = 'container';
  banner.style.margin = '18px 0 0';
  banner.innerHTML =
    '<div class="info-card" style="border-left:3px solid var(--amber)">' +
    '<h2 style="margin-top:0;font-size:1rem">⚠ 數據新鮮度提示</h2>' +
    '<ul style="font-size:0.85rem;margin:6px 0 0;padding-left:1.1rem">' + rows + '</ul>' +
    '<p style="font-size:0.8rem;opacity:0.75;margin:8px 0 0">' +
    '頁面下面嘅數字係截至上面列出嘅日期，唔係最新。' +
    '落單前請以券商或官方 App 嘅報價為準。</p></div>';

  var main = document.querySelector('main');
  if (main && main.firstChild) {
    main.insertBefore(banner, main.firstChild);
  } else {
    var hdr = document.querySelector('header');
    if (hdr && hdr.parentNode) hdr.parentNode.insertBefore(banner, hdr.nextSibling);
  }

  /* ---- the two claims that must not stand alone ---------------------- */
  var kicker = document.querySelector('.kicker');
  if (kicker && /Live/.test(kicker.textContent)) {
    kicker.textContent = kicker.textContent.replace(/\s*·\s*Live\s*$/, ' · 數據未更新');
  }
  var heads = document.querySelectorAll('h2');
  for (var i = 0; i < heads.length; i++) {
    var h = heads[i];
    if (h.textContent && /actionable/i.test(h.textContent) &&
        !/未更新/.test(h.textContent)) {
      var parts = stale.map(function (f) {
        return (LABEL[f.market] || f.market) + '數據截至 ' + f.session;
      }).join('；');
      h.textContent = h.textContent.replace(/\s*$/, '') +
        '（' + parts + '，未更新）';
      break;
    }
  }
})();
