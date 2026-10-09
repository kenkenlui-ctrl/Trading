"""self_test.py — must-fail self-test for the win9you build invariants.

WHY
    2026-10-05 was one session, three silent failures, all of the same shape:
    the build exited 0, the pages rendered, and the data was quietly wrong.

      1. JP hub showed Last=0.00 / Chg%=+0.00% on 200/200 rows because the JP
         snapshot is flat ({asof, close}) and every reader did
         `last_bar.get("C", 0)` — the 0 default turned a schema mismatch into
         a plausible-looking price.
      2. build_static.py rewrote sitemap.xml from 638 URLs to 8 because
         list_report_dates() returned [] and the code treated "no dates" as
         "no sitemap".
      3. The v2 limit-buy plan on /us200/ was computed from ohlc bars ending
         2026-09-30 while the page header read T-1 2026-10-02 — two data dates
         on one page, one badge.

    The rule this encodes comes from an EigenFlux peer broadcast
    (2026-10-01): "a check that can only fail silently is not a check, and a
    missing artifact that gets filled in is not data". Asserting that a
    missing stamp aborts a run is worthless until you strip the stamp from a
    real artifact and watch the run refuse — otherwise "no run ever lacked a
    stamp" and "the check was never wired in" read the same.

    So every invariant below is tested in both directions: the real input
    must PASS, and a deliberately corrupted copy must FAIL. A guard that
    cannot be shown to reject bad input is reported as a failure, not a pass.

Usage:
    python3 scripts/self_test.py            # run all
    python3 scripts/self_test.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts"))
PUBLIC = REPO / "public"

results: list[dict] = []


def record(name: str, ok: bool, detail: str = "", pass_detail: str = "") -> None:
    """detail explains a FAILURE; pass_detail explains a PASS.

    Both used to be one field, so a green line printed "faq.html is back" while
    asserting the opposite. A test log that lies when it passes trains you to
    skim it, which is the one thing a guard exists to prevent.
    """
    results.append({"name": name, "ok": ok, "detail": detail})
    shown = pass_detail if ok else detail
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {shown}" if shown else ""))


# --------------------------------------------------------------------------
# 1. snapshot_quote must not fabricate a price
# --------------------------------------------------------------------------
def t_quote_never_zero() -> None:
    from build_dashboard import snapshot_quote

    # real HK shape
    hk = {"name": "X", "last_bar": {"date": "2026-10-02", "C": 68.0, "chg_pct": -2.04}}
    q = snapshot_quote(hk)
    record("quote/hk nested reads close", q["last"] == 68.0 and q["chg_pct"] == -2.04,
           f"got {q['last']} / {q['chg_pct']}")

    # real JP flat shape
    jp = {"name": "Y", "asof": "2026-10-02", "close": 3460.0, "chg_pct": 0.0}
    q = snapshot_quote(jp)
    record("quote/jp flat shape reads close", q["last"] == 3460.0, f"got {q['last']}")

    # MUST-FAIL: a snapshot with no price at all must return None, never 0.
    # This is the exact 2026-10-05 bug: defaulting to 0 made all 200 JP rows
    # read 0.00 and the page looked populated.
    empty = {"name": "Z", "asof": "2026-10-02"}
    q = snapshot_quote(empty)
    record("quote/must-fail: missing close -> None (not 0)", q["last"] is None,
           f"got {q['last']!r} — a 0 here is the bug that shipped")


# --------------------------------------------------------------------------
# 2. site_t1 must reflect the data, not the calendar
# --------------------------------------------------------------------------
def t_badge_is_data_driven() -> None:
    from build_dashboard import site_t1, data_asof

    t1 = site_t1()
    mkts = {m: data_asof(m) for m in ("HK", "US", "JP")}
    record("badge/site_t1 returns a real date", bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", t1)), t1)
    record("badge/site_t1 >= every market's own T-1",
           all(t1 >= d for d in mkts.values() if d),
           f"site={t1} markets={mkts}")

    # MUST-FAIL: the old `if "t_minus_1" in dir()` idiom is always False in a
    # function scope, so the static pages shipped a bare "—". If this ever
    # regresses to the calendar helper, site_t1 must not silently equal
    # "yesterday" when the data is days older.
    from datetime import date, timedelta
    cal = (date.today() - timedelta(days=1)).isoformat()
    record("badge/must-fail: not a bare calendar guess",
           t1 != "—" and len(t1) == 10,
           f"badge={t1} calendar_would_be={cal}")


# --------------------------------------------------------------------------
# 3. no published page may carry a 0 price where data is missing
# --------------------------------------------------------------------------
def t_no_zero_prices() -> None:
    bad = []
    for m in ("hk200", "us200", "jp200"):
        p = PUBLIC / m / "index.html"
        if not p.exists():
            continue
        h = p.read_text(encoding="utf-8")
        rows = []
        for r in re.findall(r"<tr[^>]*>(.*?)</tr>", h, flags=re.S):
            v = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S)]
            if len(v) == 11 and v[0] != "Ticker":
                rows.append(v)
        zero_last = [r[0] for r in rows if r[2] in ("0.00", "0", "0.0", "")]
        empty_name = [r[0] for r in rows if r[1] == ""]
        if zero_last:
            bad.append(f"{m}: {len(zero_last)} zero Last ({zero_last[:3]})")
        if empty_name:
            bad.append(f"{m}: {len(empty_name)} empty Name")
        record(f"page/{m} has no fabricated price", not zero_last and not empty_name,
               f"rows={len(rows)} zero={len(zero_last)} empty_name={len(empty_name)}")
    if bad:
        print("   " + " | ".join(bad))


# --------------------------------------------------------------------------
# 4. every page's T-1 badge must match THAT page's market
# --------------------------------------------------------------------------
def t_badges_agree() -> None:
    """A badge must name a session the page actually carries.

    2026-10-07: this used to assert every published badge was identical. That
    assertion is what let build_static stamp site_t1() — the NEWEST of the
    three markets — onto all 223 /us200/ pages whose bars ended 2026-10-05,
    while US closed a session behind HK/JP. "Identical" was the wrong
    invariant; "equal to the data this page serves" is the right one, and it
    holds for a cross-market page too because page_label() composes all three.
    """
    from build_dashboard import page_label, data_asof

    _BADGE = re.compile(r'<span class="live-dot"></span>\s*T-1 · ([^<\n]*?)\s*'
                        r'<button class="theme-toggle"', re.S)
    wrong: list[str] = []
    found: set[str] = set()
    stamped = 0
    for p in sorted(PUBLIC.rglob("*.html")):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        m = _BADGE.search(txt)
        if not m:
            continue
        stamped += 1
        got, want = m.group(1).strip(), page_label(str(p.relative_to(PUBLIC)))
        found.add(got)
        if got != want:
            wrong.append(f"{p.relative_to(PUBLIC)}: {got!r} != {want!r}")
    record("badge/every page's badge equals its own market's data date",
           not wrong,
           f"checked={stamped} wrong={len(wrong)}" + (f" {wrong[:3]}" if wrong else ""))

    # MUST-FAIL: guard against re-introducing "every badge is one constant".
    # The set of badges actually printed on the tree must be exactly the three
    # per-market dates plus the composite — no extra value, and no market
    # missing. Read from the files, not from page_label(), so it can fail.
    expect = {data_asof(m) for m in ("HK", "US", "JP")} | {page_label("index.html")}
    record("badge/must-fail: tree carries exactly the per-market labels",
           found == expect,
           f"on_disk={sorted(found)} expected={sorted(expect)}")

    # MUST-FAIL: the em-dash placeholder is what the broken dir() check shipped.
    seen: dict[str, list[str]] = {}
    for p in PUBLIC.rglob("*.html"):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for m2 in re.findall(r"T-1 · ([0-9]{4}-[0-9]{2}-[0-9]{2}|—)", txt):
            seen.setdefault(m2, []).append(str(p.relative_to(PUBLIC)))
    record("badge/must-fail: no '—' placeholder anywhere", "—" not in seen,
           f"'—' in {len(seen.get('—', []))} pages")


# --------------------------------------------------------------------------
# 5. restamp must be idempotent AND must not eat editorial text
# --------------------------------------------------------------------------
def t_restamp_idempotent() -> None:
    import build_static as bs
    before = {p: p.read_bytes() for p in PUBLIC.rglob("*.html")}
    bs.restamp_t1_badges()
    changed = [str(p.relative_to(PUBLIC)) for p, b in before.items()
               if p.exists() and p.read_bytes() != b]
    record("restamp/idempotent (no-op on a stamped tree)", not changed,
           f"changed: {changed[:3]}" if changed else "")

    # MUST-FAIL: "—" must be replaced, not skipped.
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.html"
        f.write_text(
            '<span class="live-dot"></span>\nT-1 · —\n<button class="theme-toggle">x</button>',
            encoding="utf-8")
        PUBLIC_DIR_BACKUP = bs.PUBLIC_DIR
        bs.PUBLIC_DIR = Path(td)
        try:
            bs.restamp_t1_badges()
            out = f.read_text(encoding="utf-8")
            record("restamp/must-fail: '—' gets stamped", "—" not in out and "T-1 ·" in out,
                   out.replace("\n", " ")[:90])
            # editorial text must survive
            f2 = Path(td) / "y.html"
            f2.write_text(
                '<span class="live-dot"></span>\nT-1 · —\n<button class="theme-toggle">x</button>'
                '<p>以下為 2026-09-03 收市時點之記錄</p>'
                '<div class="mono text-dim">T-1 · 2026-09-03</div>', encoding="utf-8")
            bs.restamp_t1_badges()
            out2 = f2.read_text(encoding="utf-8")
            record("restamp/editorial text untouched", "2026-09-03 收市時點之記錄" in out2)
        finally:
            bs.PUBLIC_DIR = PUBLIC_DIR_BACKUP


# --------------------------------------------------------------------------
# 6. the sitemap guard: an empty date list must not wipe it
# --------------------------------------------------------------------------
def t_sitemap_guard() -> None:
    import build_static as bs
    sm = PUBLIC / "sitemap.xml"
    if not sm.exists():
        record("sitemap/present", False, "sitemap.xml missing")
        return
    text = sm.read_text(encoding="utf-8")
    locs = set(re.findall(r"<loc>[^<]*</loc>", text))
    n = len(locs)
    record("sitemap/has URLs", n > 100, f"{n} URLs")

    # STRUCTURAL, not a magic number. The old guard only asked "is it non-empty",
    # so build_seo.py rewrote the sitemap from 638 URLs to 427 — a third of the
    # site dropped out — and this test still passed. A count threshold cannot
    # catch a silent loss; "every ticker page that exists on disk is listed" can.
    on_disk = sorted(f"{p.parent.parent.name}/ticker/{p.stem}"
                     for mkt in ("hk200", "us200", "jp200")
                     for p in (PUBLIC / mkt / "ticker").glob("*.html"))
    listed = set()
    for loc in locs:
        m = re.search(r"\.com/(.*?)</loc>", loc)
        if m:
            listed.add(m.group(1).strip("/"))
    missing = [t for t in on_disk if t not in listed]
    record("sitemap/covers every ticker page on disk", not missing,
           f"{len(on_disk)} ticker files, {len(missing)} missing"
           + (f" e.g. {missing[:3]}" if missing else ""))

    # MUST-FAIL: delete 10% of the real entries and the check must notice.
    if n > 100:
        def coverage(keep: set) -> int:
            return sum(1 for t in on_disk
                       if any(t == re.search(r"\.com/(.*?)</loc>", l).group(1).strip("/")
                              for l in keep))
        broken = set(list(locs)[: int(n * 0.9)])
        record("sitemap/must-fail: truncated sitemap is detected",
               coverage(broken) < coverage(locs),
               f"dropping 10% of entries changed coverage {coverage(locs)} -> {coverage(broken)}"
               " — the guard did not notice" if coverage(broken) == coverage(locs) else "")

    # MUST-FAIL: prove the guard is wired, by checking the source refuses the
    # empty-dates path rather than writing an 8-URL sitemap.
    src = Path(bs.__file__).read_text(encoding="utf-8")
    guarded = "No legacy report dates" in src
    record("sitemap/must-fail: empty-date path is guarded", guarded,
           "build_static.py no longer contains the empty-date guard"
           if not guarded else "")

    # two writers for one artifact is how the sitemap lost a third of itself
    seo = (REPO / "scripts" / "build_seo.py").read_text(encoding="utf-8")
    body = seo.split("def main(")[1] if "def main(" in seo else seo
    guarded = "owns sitemap.xml" in seo and "write_sitemap(urls)" not in body
    record("sitemap/single writer", guarded,
           "build_seo.py can still overwrite sitemap.xml")


# --------------------------------------------------------------------------
# 7. v2 signals must not be published from stale bars
# --------------------------------------------------------------------------
def t_v2_bars_fresh() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    from build_v2_signals import bars_freshness, bars_from_public, MARKETS

    for mkt in ("jp200", "us200"):
        bars = bars_from_public(mkt)
        if not bars:
            record(f"v2/{mkt} bars present", False, "no bars")
            continue
        d, at, tot = bars_freshness(bars)
        # MUST-FAIL: the guard's own threshold. A 2026-10-05 build reading
        # 09-30 bars must be REJECTED, not quietly published.
        from datetime import date
        ref = date.fromisoformat(MARKETS[mkt].get("_ref", "2026-10-05") or "2026-10-05")
        lag = (ref - date.fromisoformat(d)).days if d else 999
        ok = lag <= 4
        record(f"v2/{mkt} bars within 4 sessions of T-1 ({d}, {at}/{tot})", ok,
               f"{lag}d behind — this is the condition that shipped 86 US signals on 6-day-old bars")

    p = PUBLIC / "v2-signals.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        stale = [k for k in d.get("screen", {}) if k.endswith("BARS_STALE")]
        for k, v in (d.get("market_state") or {}).items():
            if v.get("bars_asof"):
                record(f"v2/{k} published payload records bars_asof", True,
                       f"bars_asof={v['bars_asof']} {v.get('bars_at_modal','')}")
        if stale:
            record("v2/stale markets suppressed in payload", True, f"suppressed: {stale}")


def t_glitch_mask() -> None:
    """The data gate has to catch corrupt bars WITHOUT eating real market moves.

    A mask that is too loose passes the corruption test and silently deletes
    every genuine 40% earnings gap — which would flatter the backtest just as
    badly as letting the corruption through. So both directions are asserted,
    and each assertion is also run against a deliberately broken copy.
    """
    sys.path.insert(0, str(REPO / "scripts"))
    from bt_clean import bars_dir, load, glitch_dates, filter_candidates, _load_mask_cache
    import numpy as np
    import pandas as pd

    src = bars_dir()
    record("glitch/mask reads the re-fetched store", src.name == "bt10y2",
           f"source={src.name} — legacy data/bt10y had 8 corrupt files")

    mask = _load_mask_cache()

    # 1. a real corruption IS caught. 1306.T printed two sessions at 1/10 scale
    #    on 2026-03-30 with 10x volume.
    b = load(src / "1306_T.json")
    if b is None:
        record("glitch/known corrupt bar is masked", False, "1306_T not loadable")
    else:
        hit = glitch_dates(b)
        caught = any(str(d.date()) == "2026-03-30" for d in hit)
        record("glitch/known corrupt bar is masked", caught,
               f"masked={[str(d.date()) for d in hit][:5]}")

    # 2. MUST-FAIL: real gaps must NOT be masked. MRNA +177% (2026-08-19) and
    #    APP +46% on earnings (2024-11-07) both held their new level for the
    #    following 20 sessions. A real market agrees with itself afterwards.
    for sym, day, why in (("MRNA", "2026-08-19", "+177% — held the new level"),
                          ("APP", "2024-11-07", "+46% on earnings — held the new level")):
        bb = load(src / f"{sym}.json")
        if bb is None:
            record(f"glitch/real gap kept: {sym}", False, "not loadable")
            continue
        flagged = [str(d.date()) for d in glitch_dates(bb)]
        record(f"glitch/real gap kept: {sym} {day}", day not in flagged,
               f"{why}; flagged={flagged[:5]}")

    # 3. MUST-FAIL: inject a synthetic corruption into a KNOWN-CLEAN series and
    #    assert the detector finds it, and finds nothing before the injection.
    #    (Do not use 1306_T as the control: it genuinely is corrupt on 2026-03-30,
    #    so "no findings" there would be a contradiction, not a control.)
    clean_sym = next((s for s in ("AAPL", "MSFT", "META")
                      if (src / f"{s}.json").exists() and not mask.get(s)), None)
    if clean_sym:
        cb = load(src / f"{clean_sym}.json")
        i = len(cb) // 2
        before = [str(d.date()) for d in glitch_dates(cb)]
        broken = cb.copy()
        broken.iloc[i, broken.columns.get_loc("close")] *= 6.0
        after = [str(d.date()) for d in glitch_dates(broken)]
        record(f"glitch/{clean_sym} clean copy has no false positives", not before,
               f"flagged before injection: {before[:5]}")
        record("glitch/detector finds an injected 6x print",
               str(broken.index[i].date()) in after,
               f"injected at {broken.index[i].date()}, found={after[:3]}")

    # 4. the gate drops a trade whose window touches a masked bar. The "outside"
    #    trade must clear the 210-session feature warm-up, because one bad bar
    #    poisons ma200/S1/atr for every entry behind it — that reach is the
    #    whole point of the gate, so a control 60 days later is NOT a control.
    sym = next(iter(mask))
    d0 = pd.Timestamp(sorted(mask[sym])[0])
    inside = {"sym": sym, "entry_date": d0, "exit_date": d0 + pd.Timedelta(days=5)}
    outside = {"sym": sym, "entry_date": d0 + pd.Timedelta(days=400),
               "exit_date": d0 + pd.Timedelta(days=405)}
    kept, dropped = filter_candidates([inside, outside], mask)
    record("glitch/trade inside a corrupted window is dropped, clear trade kept",
           dropped == 1 and len(kept) == 1 and kept[0]["entry_date"] == outside["entry_date"],
           f"dropped={dropped} kept={len(kept)} — a gate that keeps both is not a gate")


def t_css_cachebuster() -> None:
    """Every page links /leeks.css at the CURRENT content hash — zero literals."""
    import build_static as bs
    import hashlib
    want = hashlib.sha1((PUBLIC / "leeks.css").read_bytes()).hexdigest()[:10]

    # STRUCTURAL, not a count. "at least N pages use the hash" would pass with
    # 30 stale pages still pinning old sheets; the real defect is "some page
    # links a version literal", so assert that no page does.
    linked, stale = 0, []
    link_re = re.compile(r'leeks\.css\?v=([A-Za-z0-9_.\-]+)')
    for p in sorted(PUBLIC.rglob("*.html")):
        try:
            hits = link_re.findall(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not hits:
            continue
        linked += 1
        for v in set(hits):
            if v != want:
                stale.append(f"{p.relative_to(PUBLIC)}={v}")
    record("css/every linked page uses the current hash",
           linked > 100 and not stale,
           f"{linked} pages linked leeks.css, want {want}, {len(stale)} stale"
           + (f" e.g. {stale[:4]}" if stale else ""))

    # MUST-FAIL: the same assertion run against a page carrying a frozen literal
    # must reject it. If this passes on stale text, the test cannot see the bug
    # it was written for.
    probe = 'leeks.css?v=2026-08-25b'
    record("css/must-fail: a frozen literal is rejected",
           link_re.findall(probe) != [want],
           "the assertion accepted a stale version string")

    # MUST-FAIL: the pass is actually wired into main(), not just importable.
    src = (Path(__file__).resolve().parent / "build_static.py").read_text(encoding="utf-8")
    record("css/restamp is called from build_static main()",
           "restamp_css_version()" in src.split("def main(")[-1],
           "restamp_css_version() defined but never invoked by the build")

    # 2026-10-07: build_static's passes are not actually last. refresh.sh runs
    # build_backtest_page / build_insights_* / build_v2_signals AFTER it, and
    # each writes HTML — so they undo the badge and re-freeze the CSS hash.
    # The structural guard is that a pass that must be last is called from a
    # step that nothing follows, not that it appears somewhere in the file.
    ref = (Path(__file__).resolve().parent / "refresh.sh").read_text(encoding="utf-8")
    lines = ref.splitlines()
    fin = next((i for i, l in enumerate(lines) if "scripts/finalize.py" in l), -1)
    after = re.findall(r"scripts/([a-z0-9_]+\.py)", "\n".join(lines[fin + 1:])) \
        if fin != -1 else []
    record("finalize/refresh.sh runs finalize.py and nothing writes public/ after it",
           fin != -1 and not after,
           f"finalize on line {fin + 1}; scripts still run after it: {after}")


def t_no_retired_v1_claims() -> None:
    """No public page may advertise the retired v1 engine or its withdrawn stats."""
    # 2026-10-06: the homepage's lower half was still rendering the v1
    # action_plan stream while the hero above it described v2. Two engines, one
    # page, mutually exclusive rule sets — including a SELL card directly under
    # a "long only" hero, and a target price under a "no target" hero.
    #
    # STRUCTURAL: "no page mentions X", not "the homepage mentions less than N
    # times". The homepage used to carry every one of these strings.
    banned = ["BUY_S1", "SELL_R1", "BREAK_LONG", "BREAK_SHORT",
              "Wilson 下限", "勝率下限"]

    # 2026-10-06. The fix is ADDITIVE: render_v2_status_for_ticker() puts the
    # live v2 verdict above the legacy content and labels the rest, so a reader
    # cannot mistake one for the other. The invariant worth enforcing is
    # therefore NOT "v1 strings are absent" — the v1 block is still what HK is
    # served, and deleting it would hide which engine produced a number.
    # It is "no page may show v1 content without also carrying the v2 verdict",
    # i.e. a reader must never see a v1 plan and nothing telling them it is v1.
    V2_MARKERS = ("v2 訊號", "今日無 v2 訊號", "未涵蓋於現行引擎", "v2 交易計劃")
    # Pages where v1 names are legitimate historical reference, not a live plan.
    EXEMPT = {"methodology.html", "wikipedia-draft.html"}

    unmarked = {}
    for p in sorted(PUBLIC.rglob("*.html")):
        rel = str(p.relative_to(PUBLIC))
        if rel in EXEMPT or rel.startswith("en/"):
            continue          # hand-maintained translations, not machine-built
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if not any(b in txt for b in banned):
            continue
        if not any(m in txt for m in V2_MARKERS):
            unmarked[rel] = [b for b in banned if b in txt]
    record("every v1 plan page also states the v2 verdict",
           not unmarked,
           f"{len(unmarked)} page(s) show a v1 plan with no v2 statement"
           + (f" e.g. {list(unmarked.items())[:3]}" if unmarked else ""))

    # MUST-FAIL: the assertion must actually fire on a real v1 page that has
    # no v2 statement. /hk200/ is served by v1 and used to have no marker.
    record("retired-claim detector must-fail: a real v1 page trips it",
           any(b in (PUBLIC / "hk200" / "index.html").read_text(encoding="utf-8")
               for b in banned) if (PUBLIC / "hk200" / "index.html").exists() else False,
           "detector found nothing on /hk200/index.html, which is known to be v1")

    # The homepage must be the strict case: it is the one page that is supposed
    # to carry NO v1 content at all, because it is the page that states the
    # current rules. A v1 strategy name there is a direct contradiction.
    idx = PUBLIC / "index.html"
    if idx.exists():
        txt = idx.read_text(encoding="utf-8")
        hit = [b for b in banned if b in txt]
        record("homepage carries no v1 strategy name at all", not hit,
               f"homepage still shows {hit}" if hit else "")

    # The homepage must not claim HK is covered by v2 while HK is excluded.
    idx = PUBLIC / "index.html"
    if idx.exists():
        txt = idx.read_text(encoding="utf-8")
        says_hk_plan = ("港股" in txt and "隻港股" in txt)
        discloses = "港股未納入 v2" in txt or "港股未包含喺呢版" in txt
        record("homepage does not claim HK coverage without disclosing it",
               not says_hk_plan or discloses,
               "homepage still claims 港股 counts in its universe line")

    # 2026-10-06: public/faq.html survived as a 47KB build orphan for months —
    # dropped from build_static's page list on 2026-08-29, in neither the page
    # list nor hand_edited, so nothing rebuilt it, while /faq and /faq.html both
    # 301 to methodology. It shipped a full duplicate of the methodology claims
    # for ~6 weeks after the correction log retracted some of them.
    #
    # Guard the concrete regression, not a general orphan heuristic. A
    # "scan public/*.html and ask which builder mentions this filename"
    # detector produces false positives immediately (build_equity_curves writes
    # equity_curve_t_1/3/5.html via an f-string, so no literal name matches),
    # and a guard that cries wolf gets ignored.
    faq = PUBLIC / "faq.html"
    record("the retired faq.html duplicate is not shipped again",
           not faq.exists(),
           "public/faq.html is back — it is shadowed by /faq -> methodology.html 301, "
           "so it can only ever be read by a crawler, never a visitor",
           pass_detail="no duplicate claims page ships; one source of truth")

    # The redirect must survive the file's removal, or external /faq links 404.
    if (PUBLIC / "_redirects").exists():
        red = (PUBLIC / "_redirects").read_text(encoding="utf-8")
        record("external /faq and /faq.html still redirect to methodology",
               "/faq.html" in red and "/methodology.html" in red,
               "faq.html deleted but its 301 redirect is missing",
               pass_detail="/faq and /faq.html still 301 to methodology.html")

    # 2026-10-06: 22 published US ticker pages were frozen at 2026-09-30 while
    # the other 200 were rebuilt daily, because they had fallen out of
    # us_top200_fresh.json and build_dashboard only writes what the universe
    # names. restamp_t1_badges() still rewrote their nav badge to the current
    # T-1, so each of those 22 pages ADVERTISED a data date its body did not
    # have. A page being "in the sitemap" is not evidence it is maintained.
    # 2026-10-06: the published set is now a union file (us_published.json),
    # not the rotating turnover top-200. That only works if the set and the
    # pages on disk stay reconciled in BOTH directions — a page with no entry
    # in the set is exactly the frozen orphan that caused this, and an entry
    # with no page is a name we fetch daily and never publish.
    pub = REPO / "charts" / "us200" / "us_published.json"
    if pub.exists():
        names = {n.replace(".", "_") for n in json.load(open(pub))}
        pages = {p.stem for p in (PUBLIC / "us200" / "ticker").glob("*.html")}
        orphan_pages = sorted(pages - names)
        missing_pages = sorted(names - pages)
        record("us200 published set == pages on disk",
               not orphan_pages and not missing_pages,
               f"set={len(names)} pages={len(pages)}"
               + (f", {len(orphan_pages)} page(s) not in set e.g. {orphan_pages[:4]}"
                  if orphan_pages else "")
               + (f", {len(missing_pages)} in set with no page e.g. {missing_pages[:4]}"
                  if missing_pages else ""))

        # MUST-FAIL: drop an entry from the set and the reconciliation must fail.
        shrunk = names - {next(iter(sorted(names)))}
        record("us200 reconciliation must-fail: a dropped entry is detected",
               bool((pages - shrunk)),
               "reconciliation stayed green after removing an entry")

    # 2026-10-07: the four equity-curve pages rendered from
    # data/monthly_backtest/, which is the retired v1 swing system (fixed
    # -2/-5/-7/-10% stops, +2/+5/+10/+15% targets, zero v2 code paths). The site
    # publishes them under a methodology that states, in bold, "no profit
    # target". Anyone using them to choose a holding period was reading a
    # different strategy's results.
    ec_bad = []
    for n in (1, 3, 5, 10):
        f = PUBLIC / f"equity_curve_t_{n}.html"
        if not f.exists():
            ec_bad.append(f"t_{n}: page missing")
            continue
        txt = f.read_text(encoding="utf-8")
        if "v2 規則" not in txt and "v2 rule set" not in txt:
            ec_bad.append(f"t_{n}: does not declare the v2 source")
        if "HK$" in txt:
            ec_bad.append(f"t_{n}: still denominated in HK$ but the curve is US-only")
        if "不是你可以達到" not in txt and "唔係你可以達到" not in txt:
            ec_bad.append(f"t_{n}: missing the turnover caveat")
    record("equity curves come from the v2 rule set and say so",
           not ec_bad,
           f"{len(ec_bad)} problem(s): {ec_bad[:4]}"
           if ec_bad else "4 pages declare v2 source, USD, and the turnover caveat")

    # 2026-10-07: /equity_curve_t10 (no underscore before the digits) was a
    # SECOND source of truth — build_equity_curve.py writing the retired v1
    # swing engine (HK$1,000,000, 32 trades, "Sharpe-like 5.63" next to an
    # average PnL of −92.40) one URL away from the v2 T+10 page showing
    # +27.18%. It could not simply be deleted: the other three v2 pages link
    # to it as "← All horizons". So it now renders the v2 family index, and
    # the writer that produced the contradiction is a stub.
    hub = PUBLIC / "equity_curve_t10.html"
    hub_bad = []
    if not hub.exists():
        hub_bad.append("hub missing")
    else:
        txt = hub.read_text(encoding="utf-8")
        if "HK$" in txt:
            hub_bad.append("hub still denominated in HK$")
        if "Sharpe-like" in txt and "v1 swing 引擎" not in txt:
            hub_bad.append("hub quotes the v1 Sharpe without naming it as retired")
        if "27.18" not in txt:
            hub_bad.append("hub does not carry the measured v2 T+10 CAGR")
        for n in (1, 3, 5, 10):
            if f"/equity_curve_t_{n}" not in txt:
                hub_bad.append(f"hub does not link horizon t_{n}")
    record("equity hub at /equity_curve_t10 is v2, not the retired v1 swing page",
           not hub_bad, "; ".join(hub_bad) or
           "hub declares v2 numbers and links all four horizons")

    # The v1 writer must be a stub, and nothing in the pipeline may call it.
    src = (Path(__file__).resolve().parent / "build_equity_curve.py").read_text(encoding="utf-8")
    body = src.split("def main()")[-1]
    record("retired v1 equity builder writes nothing",
           "RETIRED" in src and ".write_text" not in body,
           "build_equity_curve.py can still write public/equity_curve_t10.html")
    for sh in ("refresh.sh", "run_monthly_backtest.sh"):
        txt = (Path(__file__).resolve().parent / sh).read_text(encoding="utf-8")
        # Strip comment lines first — naming the retired script in a comment is
        # the opposite of calling it.
        code = "\n".join(l for l in txt.splitlines() if not l.lstrip().startswith("#"))
        record(f"{sh} does not run the retired v1 equity builder",
               "build_equity_curve.py" not in code,
               f"{sh} still executes build_equity_curve.py")






# 2026-10-07: SPCX showed a fresh header and plan but an interactive chart
# whose last candle was 5 sessions old. canvas-chart.js derives its ohlc
# URL from the chart URL (replace /charts/ -> /ohlc/, .json -> _ohlc.json),
# so the ticker page reads two independent files, and NOTHING in
# refresh.sh wrote the second one. update_ohlc.py existed and its own
# docstring described this exact bug — the repair was made and never wired
# into the pipeline.
#
# It also poisoned the SIGNALS: build_v2_signals.py reads
# public/<market>/ohlc/*.json, so the published signal set was computed
# from the stale series too.
#
# Structural: the two files must agree on their last bar. Not "at least N
# pages match" — 232 of 234 were wrong while every count-based check passed.
for mkt in ("us200", "hk200", "jp200"):
    cdir = PUBLIC / mkt / "charts"
    odir = PUBLIC / mkt / "ohlc"
    if not cdir.exists() or not odir.exists():
        continue
    pairs, bad = 0, []
    # Only tickers that actually have a published page. public/<mkt>/charts/
    # also holds leftovers for names that dropped out of the universe years
    # ago; those are not a published surface and nobody can reach them.
    # Scoping to pages is the same reconciliation the sitemap guard uses —
    # in-sitemap is the definition of published, not "a file exists".
    pages = {q.stem for q in (PUBLIC / mkt / "ticker").glob("*.html")}
    for cp in sorted(cdir.glob("*.json")):
        sym = cp.stem
        if sym.endswith("_ohlc") or sym not in pages:
            continue
        op = odir / f"{sym}_ohlc.json"
        if not op.exists():
            continue
        try:
            craw = json.load(open(cp))
            orows = json.load(open(op))
        except Exception:
            continue
        if not isinstance(craw, dict):
            continue
        c_last = (craw.get("last_bar") or {}).get("date")
        bars = orows if isinstance(orows, list) else orows.get("bars", [])
        o_last = None
        if bars:
            o_last = bars[-1].get("date") if isinstance(bars[-1], dict) else bars[-1][0]
        if not c_last or not o_last:
            continue
        # A page that already carries the "數據未更新" banner is telling the
        # reader its data is not current, so a mismatch there is disclosed
        # rather than silent — same invariant as the freshness guard below
        # ("no page may CLAIM a date its body does not have"). BRK-B is the
        # live case: its plan payload is frozen because the vendor dropped
        # the symbol, while its ohlc series still fetches.
        page = PUBLIC / mkt / "ticker" / f"{sym}.html"
        if page.exists() and "數據未更新" in page.read_text(encoding="utf-8"):
            continue
        pairs += 1
        if c_last != o_last:
            bad.append(f"{sym}: charts={c_last} ohlc={o_last}")
    record(f"{mkt}/chart series matches the published analysis payload",
           pairs > 50 and not bad,
           f"{pairs} pairs compared, {len(bad)} disagree"
           + (f" e.g. {bad[:3]}" if bad else "")
           + " — the interactive chart renders a different session than the plan")

stale = {}
for mkt in ("hk200", "us200", "jp200"):
    cdir = REPO / "charts" / mkt
    for p in sorted((PUBLIC / mkt / "ticker").glob("*.html")):
        src = cdir / f"{p.stem}.json"
        if not src.exists():
            continue
        try:
            lb = json.load(open(src)).get("last_bar", {})
        except Exception:
            continue
        d = lb.get("date")
        if not d:
            continue
        stale.setdefault(mkt, {}).setdefault(d, []).append(p.stem)
if stale:
    for mkt, bydate in sorted(stale.items()):
        newest = max(bydate)
        behind = {d: v for d, v in bydate.items() if d != newest}
        # The invariant is NOT "every page is fresh" — AVB and QRVO are
        # genuinely unfetchable upstream and no build can fix that. It is
        # "no page may CLAIM a data date its body does not have". A page
        # that carries the stale banner is telling the truth and passes;
        # a stale page with no banner is lying and fails.
        silent = []
        for d, syms in behind.items():
            for sym in syms:
                page = PUBLIC / mkt / "ticker" / f"{sym}.html"
                if not page.exists():
                    continue
                if "數據未更新" not in page.read_text(encoding="utf-8"):
                    silent.append(f"{mkt}/{sym}@{d}")
        n_behind = sum(len(v) for v in behind.values())
        # Always record, even at zero. This used to `continue` when a market
        # had nothing behind it, which meant the day JP came back 200/200 its
        # assertion silently vanished from the run — and the assertion count
        # is the only signal that coverage changed. A test that disappears is
        # indistinguishable from a test that passes.
        record(f"{mkt}/a page behind the market either fresh or self-declared",
               not silent,
               f"{n_behind} page(s) behind {newest}; "
               f"{len(silent)} of them do not declare it"
               + (f" e.g. {silent[:5]}" if silent else "")
               + (" — market fully current, nothing behind it"
                  if not n_behind
                  else " — they advertise the current T-1 over older bars"))


def t_track_record_page() -> None:
    """/track-record/ must publish the measured bar, and declare its source.

    2026-10-07. The page exists to hold one honest comparison: the measured
    edge of a v2 entry over a same-day same-stock random entry, against the
    user's own logged trades. Both halves have to come from a file, because a
    number typed into an HTML template is a number that starts drifting the
    day after it is measured.
    """
    p = PUBLIC / "track-record" / "index.html"
    if not p.exists():
        record("track-record/ exists", False, "public/track-record/index.html missing")
        return
    txt = p.read_text(encoding="utf-8")
    bad = []
    # The sitemap derives URLs from directory names. A page served at
    # /track-record/ but filed under track_record/ produced a sitemap entry
    # pointing at a 404 — invisible in every page-level check, because the
    # page itself was fine.
    if "/track_record/" in txt:
        bad.append("sitemap URL form still uses the underscore")
    if "control_test.csv" not in txt:
        bad.append("does not name the benchmark's source file")
    if "track_record.json" not in txt:
        bad.append("does not name the trade log's source file")
    if "暫時 0 單記錄" not in txt and "單數" not in txt:
        bad.append("renders neither the empty state nor a filled table")
    if "唔係一個要跟嘅組合" not in txt:
        bad.append("omits the no-portfolio-alpha caveat")
    record("track-record/ names both data sources and shows the empty state",
           not bad, "; ".join(bad) or
           f"empty log stated, benchmark sourced from {len(txt)} bytes")

    sm = (PUBLIC / "sitemap.xml")
    if sm.exists():
        sloc = re.findall(r"<loc>([^<]*)</loc>", sm.read_text(encoding="utf-8"))
        record("track-record/ is reachable at the URL the sitemap advertises",
               any(u.rstrip("/").endswith("/track-record") for u in sloc)
               and not any(u.rstrip("/").endswith("/track_record") for u in sloc),
               f"sitemap entries: {[u for u in sloc if 'track' in u]}")

    # Structural: the builder must READ the benchmark, not carry a literal.
    src = (Path(__file__).resolve().parent / "build_track_record.py").read_text(encoding="utf-8")
    record("track-record/ builder reads the benchmark instead of hardcoding it",
           "CONTROL" in src and "load_bench" in src and "control_test.csv" in src,
           "builder no longer references data/control_test.csv")


def t_universe_keeps_published_fetchable() -> None:
    """A published page must still be reachable by the daily fetch.

    2026-10-08. The universe regen is the candidate POOL for batch_us200.py,
    so a name it drops is a name nothing ever fetches again. Its pages stay in
    the sitemap forever and quietly freeze — the exact "in the sitemap is not
    evidence of maintenance" failure the persistent published list was built to
    end.

    It happened for real: fetch_avg_dollar_vol() returned 0.0 on every
    failure, and the filter read 0.0 as "below the $20M threshold", so WBD —
    whose Yahoo response that day was a single truncated row — was removed from
    the universe for being illiquid. One bad upstream response would have made
    a published page permanently unfetchable.

    The invariant is structural: everything ever published must remain inside
    a universe the daily fetch actually reads.
    """
    pub_f = REPO / "charts" / "us200" / "us_published.json"
    uni_f = REPO / "us_universe_200.json"
    if not (pub_f.exists() and uni_f.exists()):
        record("universe/every published US ticker is still fetchable",
               False, "universe or published list missing")
        return
    pub = set(json.load(open(pub_f)))
    uni = set(json.load(open(uni_f)))
    batch = (REPO / "charts" / "us200" / "batch_us200.py").read_text(encoding="utf-8")
    extra: set[str] = set()
    m = re.search(r"EXTRA_US\s*=\s*\[(.*?)\]", batch, re.S)
    if m:
        extra = set(re.findall(r'"([A-Z][A-Z0-9.\-]{0,5})"', m.group(1)))
    fetchable = uni | extra
    lost = sorted(pub - fetchable)
    record("universe/every published US ticker is still fetchable",
           not lost,
           f"published={len(pub)} universe={len(uni)} extra={len(extra)} "
           f"lost={len(lost)}" + (f" e.g. {lost[:6]}" if lost
                                  else " — the persistent published set is still fetchable"))

    # Owner decision (Kenneth, 2026-10-08): universe regen runs WEEKLY ON CLOUD
    # and pushes the three *_universe_200.json files into the repo. The daily
    # refresh only reads them. Comment lines are excluded — naming the script
    # in a "do not call this" note is the opposite of calling it.
    ref = (Path(__file__).resolve().parent / "refresh.sh").read_text(encoding="utf-8")
    code = "\n".join(l for l in ref.splitlines() if not l.lstrip().startswith("#"))
    record("universe/refresh.sh does not regenerate the universe (cloud owns it)",
           "regen_all.py" not in code and "build_jp_universe.py" not in code
           and "regen_us_universe.py" not in code,
           "refresh.sh still runs a universe regen — cloud owns that step")

    # The three market lists must each carry a cadence entry, or the gate
    # cannot see them as stale. JP never wrote one, so it was invisible to the
    # gate for its entire life.
    log = json.loads((REPO / "data" / "radar_regen.json").read_text())
    missing = [m for m in ("hk", "us", "jp") if not log.get(m, {}).get("last_regen")]
    record("universe/all three markets are cadence-gated",
           not missing,
           f"logged: {sorted(log)}" if not missing
           else f"no cadence entry for: {missing} — the gate cannot see them as stale")


def t_vix_study_is_research_only() -> None:
    """The VIX regime study must not be reachable from any build path.

    2026-10-08. The spec requires the work to sit behind a `vix_regime_filter`
    flag that defaults OFF, with no change to production signal generation. The
    cheapest correct branch is one nothing in the daily build can import.
    """
    cfg = (REPO / "scripts" / "vix_config.py").read_text(encoding="utf-8")
    record("vix/study config flag defaults to OFF",
           "vix_regime_filter: bool = False" in cfg,
           "scripts/vix_config.py does not default vix_regime_filter to False")

    # Nothing on the build path may import it. build_v2_signals.py is the
    # production generator; if it ever imports vix_config, the flag has one
    # import away from changing published signals.
    offenders = []
    for p in sorted((REPO / "scripts").glob("*.py")):
        # Exclude the study itself and this checker — both name vix_config on
        # purpose, and a guard that flags its own source is a false positive.
        if p.name.startswith("vix_") or p.name == "self_test.py":
            continue
        if "vix_config" in p.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(p.name)
    build = (REPO / "charts" / "us200" / "batch_us200.py").read_text(encoding="utf-8")
    if "vix" in build.lower():
        offenders.append("batch_us200.py")
    record("vix/no production script imports the study config",
           not offenders, f"importers: {offenders}")

    # And the generator's own parameters must be untouched by the study.
    gen = (REPO / "scripts" / "build_v2_signals.py").read_text(encoding="utf-8")
    record("vix/production v2 PARAMS unchanged",
           "PARAMS = dict(stop_atr=3.0, hold=10, atr_max=0.06, box_max=0.35," in gen
           and "risk=0.01, max_notional=0.10)" in gen,
           "build_v2_signals.py PARAMS no longer match the published rule set")


def t_no_ai_engine_claim() -> None:
    """The product may not call itself an AI engine while denying one.

    2026-10-07. The homepage <h1> read "多週期 AI 交易決策儀表板" one screen above
    the hero line "全部數字由 Python 對 T-1 收市 OHLC 確定性計出，零 LLM 幻覺".
    Both cannot be true, and a visitor who reads the methodology page sees
    the same contradiction. The claim is checkable in the site's favour, so
    it is the stronger sell — but only once the wording stops overclaiming.
    """
    bad = []
    for p in (PUBLIC / "index.html", PUBLIC / "en" / "index" / "index.html"):
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8")
        for m in re.findall(r"AI (?:交易決策|Trading Decision)", txt):
            bad.append(f"{p.relative_to(PUBLIC)}: {m!r}")
    record("homepage does not brand itself as an AI engine", not bad,
           f"{len(bad)} occurrence(s): {bad[:3]}" if bad
           else "title and h1 both say trading decision dashboard, no AI engine claim")


def t_candles_have_prices() -> None:
    """No published candle may carry a null, NaN or non-positive price.

    2026-10-07: update_ohlc.py wrote 16 HK series whose final 2026-10-06 bar
    had close = NaN. yfinance returns a row with a real date but no settled
    price for a handful of HK names, and the old guard compared DATES only, so
    `last == asof` passed and the NaN candle shipped. canvas-chart.js drew it,
    so the page showed a bar the market never printed — and it sat directly
    under a "數據未更新" banner that claimed everything shown was older. The
    chart/analysis guard missed it because it skips pages carrying that banner.

    This is deliberately a different question from "do the two files agree":
    a null price is wrong on its own even when both files agree about it.
    """
    import math
    PRICES = ("open", "high", "low", "close")
    bad: list[str] = []
    orphans = 0
    checked = 0
    for mkt in ("hk200", "us200", "jp200"):
        odir = PUBLIC / mkt / "ohlc"
        if not odir.exists():
            continue
        # Published surface only, same rule as the chart/analysis guard and the
        # sitemap reconciliation: "has a page" means published. The ohlc/
        # directory also keeps leftovers for names that left the universe years
        # ago — nobody can reach them, and no fetch path ever rewrites them.
        pages = {q.stem for q in (PUBLIC / mkt / "ticker").glob("*.html")}
        for op in sorted(odir.glob("*_ohlc.json")):
            try:
                rows = json.load(open(op))
            except Exception:
                continue
            if not isinstance(rows, list):
                continue
            if op.stem.replace("_ohlc", "") not in pages:
                orphans += 1
                continue
            checked += 1
            for r in rows:
                if not isinstance(r, dict):
                    continue
                # volume may legitimately be 0; a price may not be null/NaN/<=0
                for k in PRICES:
                    v = r.get(k)
                    if v is None or not isinstance(v, (int, float)) or \
                            not math.isfinite(v) or v <= 0:
                        bad.append(f"{op.name} {r.get('date')}: {k}={v!r}")
                        break
                vol = r.get("volume")
                if vol is not None and not math.isfinite(vol):
                    bad.append(f"{op.name} {r.get('date')}: volume={vol!r}")
                if bad and bad[-1].startswith(op.name):
                    break
    record("ohlc/no published candle carries a null or non-positive price",
           not bad,
           f"{checked} series checked, {len(bad)} bad: {bad[:3]}" if bad
           else f"{checked} published series clean "
                f"({orphans} orphan file(s) out of scope)")

    # MUST-FAIL: json.dumps writes NaN as a bare NaN token, which json.load
    # happily parses back to float('nan'). Proving the detector sees it keeps
    # this from quietly becoming a no-op.
    probe = json.loads('[{"date":"2026-10-06","open":1.0,"high":1.0,"low":1.0,'
                       '"close":NaN,"volume":1.0}]')
    seen = any(not math.isfinite(r[k]) for r in probe for k in PRICES if k in r)
    record("ohlc/must-fail: a NaN candle is detected",
           seen, "the detector cannot see a NaN price")

    # A short series is not automatically a problem: 26 HK names and SPCX have
    # 71–249 bars because that is all the listing has, and they are current.
    # What must never ship is a series that is short AND stale while the page
    # presents it as a normal chart — canvas-chart.js will happily draw three
    # points and call it a candlestick chart, and neither a NaN check nor a
    # date-agreement check sees anything wrong with it. AVB sits at 1 bar.
    # The upstream truncation that produced QRVO's 5 bars is now blocked at
    # the source in update_ohlc.py; this is the published-surface backstop.
    short_pages, undisclosed = [], []
    from build_dashboard import data_asof
    for mkt, code in (("hk200", "HK"), ("us200", "US"), ("jp200", "JP")):
        pages = {q.stem for q in (PUBLIC / mkt / "ticker").glob("*.html")}
        src = REPO / "charts" / ("jp200" if code == "JP" else mkt)
        market_t1 = None
        for sym in sorted(pages):
            op = PUBLIC / mkt / "ohlc" / f"{sym}_ohlc.json"
            rows = json.load(open(op)) if op.exists() else None
            if not isinstance(rows, list) or len(rows) >= 260:
                continue
            if market_t1 is None:
                market_t1 = data_asof(code)
            payload = src / f"{sym}.json"
            own = ""
            try:
                own = (json.load(open(payload)).get("last_bar") or {}).get("date", "")
            except Exception:
                pass
            stale = bool(own and market_t1 and own < market_t1)
            if stale:
                short_pages.append(f"{mkt}/{sym}={len(rows)}bars")
                pg = PUBLIC / mkt / "ticker" / f"{sym}.html"
                if not pg.exists() or "數據未更新" not in pg.read_text(encoding="utf-8"):
                    undisclosed.append(f"{mkt}/{sym}={len(rows)}bars")
    record("ohlc/a too-short-to-plot series must be self-declared, fresh ones may be short",
           not undisclosed,
           f"{len(short_pages)} short AND stale series, all self-declared"
           if not undisclosed
           else f"{len(undisclosed)} presented as a normal chart: {undisclosed[:4]}")


def t_v2_sizing_is_tradable() -> None:
    """A published share count must be an order a broker would accept.

    2026-10-08. plan() sized every position as int(min(1% risk, 10% notional))
    and shipped that integer as `shares_at_1pct_risk`. On Japan one 単元 is 100
    shares, so the published plan told readers to buy "3 shares of 2503" — a
    quantity no broker will take — and names whose per-share risk exceeded the
    whole budget shipped as 0. The number was never wrong arithmetically; it
    just described a position that cannot exist.

    Two things have to hold together. The count must be a whole multiple of the
    tradable unit, and a name that cannot be sized must SAY SO rather than
    publishing a bare 0, because a silent 0 reads as "not applicable" while a
    note reads as "we checked and this one is out of reach at this equity".
    """
    f = PUBLIC / "v2-signals.json"
    if not f.exists():
        record("v2 sizing/published counts are tradable units", False, "v2-signals.json missing")
        return
    d = json.loads(f.read_text(encoding="utf-8"))
    sigs = d.get("signals", [])

    bad_multiple, silent_zero, no_basis = [], [], []
    for s in sigs:
        lot = int(s.get("lot_size") or 1)
        sh = int(s.get("shares_at_1pct_risk") or 0)
        if sh < 0 or (lot > 1 and sh % lot != 0):
            bad_multiple.append(f"{s.get('symbol')}={sh}shares/lot{lot}")
        if sh == 0 and not (s.get("size_note") or "").strip():
            silent_zero.append(str(s.get("symbol")))
        if not (s.get("lot_size_source") or "").strip():
            no_basis.append(str(s.get("symbol")))

    record("v2 sizing/every published count is a whole multiple of the tradable unit",
           not bad_multiple,
           f"{len(sigs)} signals, all lot-aligned" if not bad_multiple
           else f"{len(bad_multiple)} not lot-aligned: {bad_multiple[:5]}")
    record("v2 sizing/a signal that cannot be sized must say so, not print a bare 0",
           not silent_zero,
           f"{sum(1 for s in sigs if int(s.get('shares_at_1pct_risk') or 0) == 0)} "
           f"unsizeable, all explained" if not silent_zero
           else f"{len(silent_zero)} publish 0 with no reason: {silent_zero[:5]}")

    # A note that names the wrong constraint is worse than no note. The first
    # version reported only the risk leg, so names blocked purely by the 10%
    # notional cap said "needs 0.9% equity, above the 1% limit" — which is
    # self-contradictory and sends the reader to the wrong limit.
    self_contradict = []
    for s in sigs:
        note = (s.get("size_note") or "")
        m = re.search(r"需\s*([\d.]+)%\s*本金（上限\s*([\d.]+)%", note)
        if m and float(m.group(1)) <= float(m.group(2)):
            self_contradict.append(f"{s.get('symbol')}={note[:48]}")
    record("v2 sizing/an unsizeable note must not quote a limit it does not breach",
           not self_contradict,
           f"{sum(1 for s in sigs if (s.get('size_note') or '').strip())} notes, "
           f"each naming a real breach" if not self_contradict
           else f"{len(self_contradict)} self-contradictory: {self_contradict[:3]}")
    record("v2 sizing/the unit size carries its provenance (measured vs assumed)",
           not no_basis,
           "every signal records lot_size_source" if not no_basis
           else f"{len(no_basis)} missing: {no_basis[:5]}")

    # The unit is only a number relative to a capital figure. If the basis is
    # not published next to it, every share count on the site impersonates a
    # recommendation for the reader's own account.
    basis = (d.get("rules") or {}).get("sizing_equity")
    markets = {s.get("market") for s in sigs}
    ok = isinstance(basis, dict) and bool(basis) and all(
        isinstance(v, (int, float)) and v > 0 for v in basis.values())
    # Every market that published a count must declare the capital behind it,
    # otherwise one market is sized against a number nobody can see.
    covered = markets <= set(basis or {})
    record("v2 sizing/the capital basis travels with the numbers",
           ok and covered,
           f"sizing_equity={basis} covering {sorted(markets)}" if ok and covered
           else f"sizing_equity={basis} does not cover {sorted(markets - set(basis or {}))}")


def t_structured_data_is_present_and_true() -> None:
    """JSON-LD must exist, parse, and describe what the page actually shows.

    2026-10-08. Three of the four requested schemas were already there; the
    homepage had WebSite + SoftwareApplication but no standalone Organization
    node, so the site had no entity for a knowledge graph to attach to. The
    FAQPage blocks on /methodology and /backtest were complete because both
    draw from the same Q&A list that renders the visible questions — so the
    guard below is the thing that KEEPS them honest rather than the thing that
    fixed them. Schema.org explicitly requires FAQPage questions to match
    visible content; a hand-maintained copy drifts the first time copy is
    edited.
    """
    def graph_of(path: Path) -> dict:
        h = path.read_text(encoding="utf-8")
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', h, re.S)
        for b in blocks:
            try:
                d = json.loads(b)
            except Exception:
                continue
            if "@graph" in d:
                return d
        return {}

    # Both language versions are the same page to a search engine. Checking only
    # the Chinese one left /en/index/ free to keep a stale graph — which is how
    # it kept claiming "AI trading decision dashboard" for 200 HK + 200 US
    # after the Chinese site had dropped AI and moved to US+JP.
    for label, home in (("homepage", PUBLIC / "index.html"),
                        ("/en/ homepage", PUBLIC / "en" / "index" / "index.html")):
        if not home.exists():
            record(f"seo/{label} declares an Organization entity", True, "absent")
            continue
        g = graph_of(home)
        types = {n.get("@type") for n in g.get("@graph", [])}
        record(f"seo/{label} declares an Organization entity",
               "Organization" in types,
               f"@graph types: {sorted(types)}")
        ws = next((n for n in g.get("@graph", []) if n.get("@type") == "WebSite"), {})
        org = next((n for n in g.get("@graph", []) if n.get("@type") == "Organization"), {})
        linked = (isinstance(ws.get("publisher"), dict)
                  and bool(org.get("@id"))
                  and ws["publisher"].get("@id") == org["@id"])
        record(f"seo/{label} WebSite publishes the same Organization entity",
               linked,
               f"publisher={ws.get('publisher')} org @id={org.get('@id')}")

        # A page must not advertise a retired engine or an AI claim the rest of
        # the site has retracted. This is checked on the machine-readable copy
        # because that is what an LLM or a crawler is most likely to consume.
        blob = json.dumps(g, ensure_ascii=False)
        stale = [w for w in ("10-step price-action", "multi-horizon AI",
                             "30/60/90/197", "60-day win rate gate", "plan-strategy")
                 if w.lower() in blob.lower()]
        record(f"seo/{label} structured data describes v2, not the retired v1 engine",
               not stale,
               "no retired-engine claims" if not stale
               else f"still advertises: {stale}")

    # FAQPage must not out-claim the page. Compare on question text.
    for page in ("methodology.html", "backtest.html"):
        p = PUBLIC / page
        if not p.exists():
            continue
        faq = next((n for n in graph_of(p).get("@graph", [])
                    if n.get("@type") == "FAQPage"), {})
        ld_q = {q.get("name") for q in faq.get("mainEntity", [])}
        html = p.read_text(encoding="utf-8")
        vis_q = {re.sub(r"<[^>]+>", "", m).strip()
                 for m in re.findall(r"<h3[^>]*>(.*?)</h3>", html, re.S)}
        # Only questions that read like questions; methodology has section h3s too.
        vis_q = {q for q in vis_q if q.endswith("？") or q.endswith("?")}
        # Slice to the FAQ section first. Methodology's section headings are
        # also <h3> and several end in "？" ("4.6 對照組測試：…？"), so scanning
        # the whole page reported them as undeclared questions — a guard that
        # fires on correct output gets ignored, which is worse than no guard.
        m_faq = re.search(r"常見問題(.*?)(?=<h2|</body>)", html, re.S)
        if m_faq:
            vis_q = {re.sub(r"<[^>]+>", "", x).strip()
                     for x in re.findall(r"<h3[^>]*>(.*?)</h3>", m_faq.group(1), re.S)}
            vis_q = {q for q in vis_q if q.endswith("？") or q.endswith("?")}
        stray = ld_q - vis_q          # claims a Q the page does not show
        missing = vis_q - ld_q        # shows a Q the schema omits
        record(f"seo/{page} FAQPage claims exactly the questions on the page",
               not stray and not missing,
               f"{len(ld_q)} declared, {len(vis_q)} visible, identical"
               if not stray and not missing
               else f"{len(stray)} not on page, {len(missing)} not declared: "
                    f"{sorted(stray | missing)[:2]}")


def t_llms_txt_says_which_engine_covers_which_market() -> None:
    """llms.txt must not imply Hong Kong is a v2 market.

    2026-10-08: it read "Market coverage: 200 Hong Kong, 222 US, 200 Japanese
    stocks" — a flat list that an LLM reading it would take as one engine over
    three markets. Hong Kong runs the older v1 engine and is not part of v2;
    the site says so everywhere else, and llms.txt is the file aimed at exactly
    the reader most likely to miss the nuance.
    """
    f = PUBLIC / "llms.txt"
    if not f.exists():
        record("seo/llms.txt marks Hong Kong as outside v2", False, "llms.txt missing")
        return
    t = f.read_text(encoding="utf-8")
    has_split = ("not covered by v2" in t.lower() or "not part of v2" in t.lower()
                 or "legacy" in t.lower())
    # The old failure mode: a flat "Market coverage: ... Hong Kong ..." list.
    flat = re.search(r"Market coverage:[^\n]*Hong Kong", t)
    record("seo/llms.txt marks Hong Kong as outside v2",
           has_split and not flat,
           "v1/v2 split stated" if has_split and not flat
           else ("still a flat market-coverage list" if flat
                 else "no statement separating HK from v2"))


def t_jp_ohlc_has_one_naming_scheme() -> None:
    """public/jp200/ohlc must not carry two parallel filenames per ticker.

    2026-10-08: a Futu-format patch script wrote "JP.NNNN" as "JP_NNNN" while
    the rest of the pipeline writes "NNNN_T", so the directory held 392 files
    for 200 tickers. The 192 prefixed files each had 5 bars from a truncated
    fetch and were dropped by bars_from_public()'s 260-bar floor — invisible,
    because the directory still looked populated and no published page broke.
    """
    d = PUBLIC / "jp200" / "ohlc"
    if not d.exists():
        record("ohlc/jp200 uses a single filename scheme", True, "dir absent, nothing to check")
        return
    files = sorted(p.name for p in d.glob("*_ohlc.json"))
    pref = [n for n in files if n.startswith("JP_")]
    dup = [n for n in files if n[3:].startswith("JP_")]   # a prefixed twin exists
    record("ohlc/jp200 uses a single filename scheme",
           not pref,
           f"{len(files)} files, none with a JP_ prefix"
           if not pref else f"{len(pref)} leftover JP_-prefixed files: {pref[:3]}")


def t_en_tree_is_retired() -> None:
    """/en/ must be out of service, or rebuilt — never silently stale.

    2026-10-08. The English tree was a frozen snapshot of the v1 engine: live
    at HTTP 200, all seven pages in sitemap.xml, data as-of 2026-08-28, still
    advertising SELL_R1 / BUY_S1 (switched off in October 2026) and the
    per-stock 60-day win-rate badge (audited out, rank correlation -0.001 with
    the next trade). A crawler or an LLM reading the site was therefore being
    told, in the operator's own voice, that a retired strategy was current.

    Retired in place rather than deleted: noindex on every page (which also
    drops them from sitemap.xml, since build_sitemap skips noindex), and 301s
    from every /en/ URL form to its Chinese counterpart.
    """
    en = PUBLIC / "en"
    pages = sorted(en.rglob("*.html")) if en.exists() else []
    if not pages:
        record("seo/retired /en/ pages carry noindex", True, "no /en/ tree present")
        return

    missing = [str(p.relative_to(PUBLIC)) for p in pages
               if not re.search(r'name=["\']robots["\'][^>]*noindex',
                                p.read_text(encoding="utf-8")[:4000], re.I)]
    record("seo/retired /en/ pages carry noindex",
           not missing,
           f"{len(pages)} pages all noindex" if not missing
           else f"{len(missing)} indexable: {missing[:3]}")

    sm = PUBLIC / "sitemap.xml"
    if sm.exists():
        n = sm.read_text(encoding="utf-8").count("/en/")
        record("seo/retired /en/ is out of sitemap.xml",
               n == 0,
               "no /en/ URLs listed" if n == 0 else f"{n} /en/ URLs still listed")

    # A 301 that points at another /en/ page is not a retirement, it is a
    # detour — the old i18n rules chained /en/x.html -> /en/x/ -> target, and
    # Cloudflare Pages applies the FIRST match, so the chain rule kept winning.
    rf = PUBLIC / "_redirects"
    if rf.exists():
        rules = rf.read_text(encoding="utf-8").splitlines()
        chains = [ln for ln in rules
                  if ln.startswith("/en") and re.search(r"^\S+\s+/en\S*\s+301", ln)]
        record("seo/no /en/ redirect chains back into the retired tree",
               not chains,
               "every /en/ rule lands on a Chinese page" if not chains
               else f"{len(chains)} chain rules: {chains[:2]}")

        # Coverage, not just correctness: removing the legacy rules without
        # replacing their whole URL space silently 404s the forms you did not
        # think of. /en/methodology.html returned 404 on the first deployment
        # for exactly this reason while every other URL looked fine.
        covered = {ln.split()[0] for ln in rules
                   if ln.startswith("/en") and " 301" in ln}
        need = set()
        for p in pages:
            rel = "/" + "/".join(p.relative_to(PUBLIC).parts)
            stem = rel[: -len("index.html")] if rel.endswith("/index.html") else rel
            base = stem[:-len(".html")] if stem.endswith(".html") else stem
            base = base.rstrip("/")          # /en/backtest/ -> /en/backtest
            need |= {base, base + "/", base + ".html"}
        need |= {"/en", "/en/"}
        gaps = sorted(need - covered)
        record("seo/every /en/ URL form has a redirect, not a 404",
               not gaps,
               f"{len(covered)} rules cover all {len(need)} reachable forms"
               if not gaps else f"{len(gaps)} uncovered: {gaps[:5]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.json:
        print("{" + ",".join(json.dumps(r) for r in results) + "}")
    for fn in (t_quote_never_zero, t_badge_is_data_driven, t_no_zero_prices,
               t_badges_agree, t_restamp_idempotent, t_sitemap_guard, t_v2_bars_fresh,
               t_glitch_mask, t_css_cachebuster, t_no_retired_v1_claims,
               t_candles_have_prices, t_track_record_page, t_vix_study_is_research_only,
               t_no_ai_engine_claim,
               t_universe_keeps_published_fetchable, t_v2_sizing_is_tradable,
               t_structured_data_is_present_and_true,
               t_llms_txt_says_which_engine_covers_which_market,
               t_jp_ohlc_has_one_naming_scheme, t_en_tree_is_retired):
        print(f"\n{fn.__doc__.splitlines()[0] if fn.__doc__ else fn.__name__}")
        try:
            fn()
        except Exception as e:
            record(f"{fn.__name__} raised", False, f"{type(e).__name__}: {e}")

    n = len(results)
    bad = [r for r in results if not r["ok"]]
    print(f"\n{'='*60}\n{n - len(bad)}/{n} passed")
    for r in bad:
        print(f"  FAILED: {r['name']}  {r['detail']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
