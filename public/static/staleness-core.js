/* staleness-core.js — the freshness DECISION, with no DOM in it.
 *
 * Split out from staleness.js so it can be tested by the same test file on
 * the server (scripts/staleness_js_test.js) as by a browser. A test that
 * mirrors the logic instead of running it tests the mirror.
 *
 * The rule:
 *   stale if the next session that was DUE to close has closed and the page
 *   still carries the session before it.
 *
 * Why not "the data is older than N hours": that version cries wolf every
 * weekend and every Monday morning. Friday's close is more than 36 hours
 * old by Monday morning, but Monday has not opened yet, so nothing is
 * missing and nothing is stale. The server-side contract (integrity/
 * freshness.py) applies its 36-hour limit only when it cannot confirm that
 * no session was missed; this is the same idea with the "next due close"
 * standing in for that confirmation.
 *
 * The age limit is kept as a backstop for a page that carries no calendar at
 * all — then there is nothing better to go on.
 */
(function (root) {
  'use strict';

  function dueClosePassed(f, now) {
    if (!f || !f.next) return false;
    var due = Date.parse(f.next);
    if (isNaN(due)) return false;
    return now >= due;
  }

  /* facts: [{market, session, close, next, limit}], now: epoch millis */
  function decide(facts, now) {
    var stale = [];
    (facts || []).forEach(function (f) {
      if (!f || !f.close) return;
      var close = Date.parse(f.close);
      if (isNaN(close)) return;
      var ageH = (now - close) / 3600000;
      var missed = dueClosePassed(f, now);
      var noCalendar = !f.next || isNaN(Date.parse(f.next));
      var overLimit = noCalendar && f.limit && ageH > f.limit;
      if (missed || overLimit) {
        stale.push({
          market: f.market,
          session: f.session,
          expected: f.next ? String(f.next).slice(0, 10) : '',
          ageH: ageH,
          limit: f.limit,
          reason: missed ? 'session-missed' : 'age-limit'
        });
      }
    });
    return stale;
  }

  root.W9Freshness = { dueClosePassed: dueClosePassed, decide: decide };
})(typeof window !== 'undefined' ? window : globalThis);
