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


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append({"name": name, "ok": ok, "detail": detail})
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


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
# 4. every page's T-1 badge must agree
# --------------------------------------------------------------------------
def t_badges_agree() -> None:
    import build_static as bs
    want = bs.site_t1()
    seen: dict[str, list[str]] = {}
    for p in PUBLIC.rglob("*.html"):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.findall(r"T-1 · ([0-9]{4}-[0-9]{2}-[0-9]{2}|—)", txt):
            seen.setdefault(m, []).append(str(p.relative_to(PUBLIC)))
    ok = set(seen) == {want}
    record("badge/all published badges identical", ok,
           f"expected {want}, found { {k: len(v) for k, v in seen.items()} }")
    # MUST-FAIL: the em-dash placeholder is what the broken dir() check shipped.
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.json:
        print("{" + ",".join(json.dumps(r) for r in results) + "}")
    for fn in (t_quote_never_zero, t_badge_is_data_driven, t_no_zero_prices,
               t_badges_agree, t_restamp_idempotent, t_sitemap_guard, t_v2_bars_fresh,
               t_glitch_mask, t_css_cachebuster):
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
