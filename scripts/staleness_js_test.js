/* staleness_js_test.js — runs the REAL decision code (public/static/
 * staleness-core.js) against the cases that matter.
 *
 * The point of this file is that it loads the shipped code, not a copy of
 * it. A test that mirrors the logic tests the mirror: the day somebody
 * edits the mirror to match a new behaviour, the test still passes while
 * the site does something else.
 *
 *   node scripts/staleness_js_test.js [path/to/staleness-core.js]
 *
 * Exit 0 = every case behaved as expected.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const corePath = process.argv[2] ||
  path.join(__dirname, '..', 'public', 'static', 'staleness-core.js');
const src = fs.readFileSync(corePath, 'utf8');
const sandbox = { globalThis: {} };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const W9 = sandbox.W9Freshness || sandbox.globalThis.W9Freshness;
if (!W9 || typeof W9.decide !== 'function') {
  console.error('FAIL: staleness-core.js did not export W9Freshness.decide');
  process.exit(1);
}

const HK28 = { market: 'HK', session: '2026-10-28', close: '2026-10-28T16:00:00+08:00', next: '2026-10-29T16:00:00+08:00', limit: 36 };
const HKFRI = { market: 'HK', session: '2026-10-24', close: '2026-10-24T16:00:00+08:00', next: '2026-10-27T16:00:00+08:00', limit: 36 };
const US28 = { market: 'US', session: '2026-10-28', close: '2026-10-29T04:00:00+08:00', next: '2026-10-30T04:00:00+08:00', limit: 36 };
const NOCAL = { market: 'HK', session: '2026-10-28', close: '2026-10-28T16:00:00+08:00', next: '', limit: 36 };

const cases = [
  ['fresh data, same morning, before the close', '2026-10-29T09:00:00+08:00', [HK28], false],
  ['fresh data, same afternoon, before the close', '2026-10-29T15:00:00+08:00', [HK28], false],
  ['one session missed', '2026-10-30T09:00:00+08:00', [HK28], true],
  ['weekend: Friday data on Saturday morning', '2026-10-25T10:00:00+08:00', [HKFRI], false],
  ['Monday morning, before the market opened', '2026-10-27T10:00:00+08:00', [HKFRI], false],
  ['Monday after the close', '2026-10-27T17:00:00+08:00', [HKFRI], true],
  ['two sessions missed', '2026-10-29T10:00:00+08:00', [HKFRI], true],
  ['US: fresh', '2026-10-29T09:00:00+08:00', [US28], false],
  ['US: one session missed', '2026-10-30T09:00:00+08:00', [US28], true],
  ['no calendar: the age limit is the backstop', '2026-10-30T09:00:00+08:00', [NOCAL], true],
];

let bad = 0;
for (const [name, nowISO, facts, wantStale] of cases) {
  const now = Date.parse(nowISO);
  const stale = W9.decide(facts, now);
  const got = stale.length > 0;
  const ok = got === wantStale;
  if (!ok) bad++;
  console.log(`${ok ? '  ok  ' : 'FAIL  '}${name} -> ${got ? 'STALE' : 'FRESH'}` +
    (stale.length ? ` (${stale[0].reason})` : ''));
}
if (bad) {
  console.error(`\n${bad} case(s) failed`);
  process.exit(1);
}
console.log('\nALL PASS');
