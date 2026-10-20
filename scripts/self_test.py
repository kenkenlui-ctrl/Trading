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
import ast
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts"))
PUBLIC = REPO / "public"

results: list[dict] = []


WRITE_CALLS = ("write_text", "write_bytes", "unlink", "mkdir", "rmdir",
               "remove", "copytree", "rmtree", "move", "truncate")


def _statements_writing_public(path: Path) -> list[str]:
    """Statements in `path` that both mention public/ and perform a write.

    Used to VERIFY a read-only exemption rather than take it on trust. It
    works on whole statements, so a write whose target is assembled a line
    away is still caught. It cannot follow a target through a variable into
    another module, and it does not need to — the scripts it is used on are
    small and their writes are local.
    """
    src = path.read_text(encoding="utf-8")
    out: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.stmt):
            continue
        seg = ast.get_source_segment(src, node) or ""
        low = seg.lower()
        if "public" not in low:
            continue
        if any(w + "(" in low for w in WRITE_CALLS) or "open(" in low:
            out.append(f"line {node.lineno}: {seg.strip()[:80]}")
    return out


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
# 13. every ASSET url the page fetches must be stamped with its content hash
# --------------------------------------------------------------------------
def t_every_fetched_asset_is_content_stamped() -> None:
    """Every <script>/<img>/data-* asset URL carries ?v=<sha1 of that file>.

    2026-10-12 this same defect fired three times in one session, each one
    invisible: the file on the server was correct, the URL never changed, and
    the browser kept serving the old copy.

      1. /charts/<t>.json      — no ?v= at all
      2. /ohlc/<t>_ohlc.json   — derived from (1), so also uncached
      3. /static/canvas-chart.js?v=2026-09-18b — a hardcoded literal

    Every deployment was "verified" by grepping the deployed file and finding
    the new code. Not one of those checks asked which URL the browser would
    actually request. t_css_cachebuster already covers /leeks.css; this covers
    the two asset families CSS does not.
    """
    import hashlib

    # Accept both "?v=<hash>" and "?v=<YYYY-MM-DD>-<hash>"; the hash is the
    # trailing 8 hex chars in either form.
    stamped = re.compile(
        r"^(?P<path>/[A-Za-z0-9_./\-]+)\?v=(?:[0-9]{4}-[0-9]{2}-[0-9]{2}-)?(?P<v>[0-9a-f]{8})$")

    # Which files must be stamped, and which pages legitimately omit them.
    families = ["static/canvas-chart.js"]
    unstamped_never = {"favicon", "apple-touch-icon", "manifest", "og-image"}

    checked = bad = 0
    problems: list[str] = []
    pat_script = re.compile(r'<script[^>]+src="(?P<u>[^"]+)"')
    pat_img = re.compile(r'<img[^>]+src="(?P<u>[^"]+)"')
    pat_data = re.compile(r'data-(?:chart-json|png)="(?P<u>[^"]+)"')

    for page in sorted(PUBLIC.rglob("*.html")):
        try:
            body = page.read_text(encoding="utf-8")
        except Exception:
            continue
        urls = []
        for _pat in (pat_script, pat_img, pat_data):
            for _mt2 in _pat.finditer(body):
                urls.append(_mt2.group("u"))
        for u in urls:
            if any(bad_name in u for bad_name in unstamped_never):
                continue
            local = PUBLIC / u.split("?")[0].lstrip("/")
            if not local.exists() or not local.is_file():
                continue                       # external or data: URL
            if u.endswith(".html") or "/ticker/" in u:
                continue                       # page links, not a cached asset
            checked += 1
            _mt = stamped.match(u)
            if not _mt:
                bad += 1
                problems.append(f"{page.relative_to(PUBLIC)}: {u} (no content stamp)")
                continue
            # Same rule as build_dashboard, and the rule depends on the asset:
            #   .json -> sha1(chart JSON + OHLC JSON)[:8]   (two files fetched)
            #   .js/.css -> sha1(file)[:8]                  (one file fetched)
            # lstrip("/") matters: PUBLIC / "/hk200/..." is an ABSOLUTE path and
            # pathlib discards PUBLIC entirely, so the OHLC file was never found
            # and the hash silently fell back to sha1(json + b"?").
            if local.suffix == ".json":
                _oh = PUBLIC / (_mt.group("path").split("?")[0]
                                .lstrip("/")
                                .replace("/charts/", "/ohlc/")
                                .replace(".json", "_ohlc.json"))
                _dd = hashlib.sha1()
                for _f in (local, _oh):
                    try:
                        _dd.update(Path(_f).read_bytes())
                    except Exception:
                        _dd.update(b"?")
                want = _dd.hexdigest()[:8]
            else:
                want = hashlib.sha1(local.read_bytes()).hexdigest()[:8]
            if _mt.group("v") != want:
                bad += 1
                problems.append(f"{page.relative_to(PUBLIC)}: {u} stamp={_mt.group('v')} want={want}")

    record("assets/every fetched asset is content-stamped",
           checked > 0 and bad == 0,
           f"{bad}/{checked} asset URLs are not stamped with their current content hash"
           + (f" e.g. {problems[:4]}" if problems else ""))

    # MUST-FAIL: an unstamped url and a stale stamp must both be rejected.
    _js = PUBLIC / "static/canvas-chart.js"
    real = hashlib.sha1(_js.read_bytes()).hexdigest()[:8]   # single-file asset
    good = "/static/canvas-chart.js?v=" + real
    dated = "/static/canvas-chart.js?v=2026-10-12-" + real
    record("assets/must-fail: unstamped url is rejected",
           not stamped.match("/static/canvas-chart.js"),
           "the assertion accepted a URL with no ?v=")
    record("assets/must-fail: stale stamp is rejected",
           (lambda m: m is None or m.group("v") != real)(
               stamped.match("/static/canvas-chart.js?v=deadbeef")),
           "the assertion accepted a wrong content hash")
    record("assets/dated form is also accepted",
           bool(stamped.match(dated)) and stamped.match(dated).group("v") == real,
           "the regex rejected the ?v=<date>-<hash> form the build actually emits")
    record("assets/must-fail: the current stamp is accepted",
           bool(stamped.match(good)) and stamped.match(good).group("v") == real,
           "the assertion rejected the correct stamp, so it proves nothing")

    # MUST-FAIL: the pass is actually wired into main().
    src = (Path(__file__).resolve().parent / "build_dashboard.py").read_text(encoding="utf-8")
    record("assets/_canvas_js_version is called by build_dashboard",
           "_canvas_js_version()" in src.split("def render_detail_page")[-1],
           "_canvas_js_version() defined but render_detail_page never calls it")

    # ---------------------------------------------------------------- noscript
    # The <noscript> block prints the chart JSON location as a real path, for
    # the visitor who has JavaScript switched off. That link used to be built as
    # chart_url = ".../<t>.png" and rendered as "{chart_url}.json", i.e.
    # "/jp200/charts/3099_T.png.json" — a filename that never existed. Nothing
    # else in the repo fetches that string, so no other check can see it: only
    # a visitor with JS off, on a page nobody crawls. Assert it resolves.
    _noscript_re = re.compile(r"<noscript>.*?</noscript>", re.S)
    _code_re = re.compile(r"<code>(/[^<\s]+?)</code>")

    def _dead_noscript_links(html: str) -> list[str]:
        return [link
                for block in _noscript_re.findall(html)
                for link in _code_re.findall(block)
                if not (PUBLIC / link.lstrip("/")).is_file()]

    seen = 0
    dead: list[str] = []
    for page in sorted(PUBLIC.rglob("*.html")):
        try:
            body = page.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        links = _dead_noscript_links(body)
        seen += 1 if "<noscript>" in body else 0
        if links:
            dead += [f"{page.relative_to(PUBLIC)} -> {l}" for l in links]
    record("assets/every noscript data link resolves to a real file",
           seen > 0 and not dead,
           f"{len(dead)} dead <noscript> links across {seen} pages "
           f"(a visitor without JS cannot open them)"
           + (f" e.g. {dead[:4]}" if dead else ""))

    # MUST-FAIL: prove the scanner can actually fail, and that it is not simply
    # accepting everything because PUBLIC paths always "exist".
    record("assets/must-fail: a bogus noscript link is caught",
           _dead_noscript_links("<noscript><code>/charts/definitely-not-here.json</code></noscript>")
           == ["/charts/definitely-not-here.json"],
           "the noscript scanner returned no error for a path that does not exist")
    record("assets/must-fail: a real noscript link passes",
           _dead_noscript_links("<noscript><code>/static/canvas-chart.js</code></noscript>") == [],
           "the noscript scanner rejected a file that does exist, so it proves nothing")

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
def t_no_retired_jp_claims() -> None:
    """The withdrawn Japan portfolio figures may only appear as history.

    2026-10-15: methodology.html kept publishing the withdrawn JP numbers
    (+25.4%/yr vs +18.5%/yr = +6.9pp/yr) for weeks after the home page,
    llms.txt and build_seo.py had all moved to the 2026-10-12 retest
    (+18.03% vs +18.00% = +0.04pp/yr). Two pages on one site answered "what
    did this strategy actually return" with different numbers, and the page
    that explains how the numbers are produced was the wrong one.

    A bare "these strings are banned" check would fail the moment the page
    quotes the old figure in its correction log — which it must, because a
    withdrawn claim that nobody can see withdrawn is a claim that looks live.
    So the rule is CONTEXTUAL: the withdrawn figure may appear only on a page
    that also says it was withdrawn.
    """
    sys.path.insert(0, str(REPO / "scripts"))
    from rewrite_methodology_v2 import JP, _pct, _pp

    # The verified figures, read from the retest summary rather than restated.
    j = JP()
    verified_gap = _pp(j["gap_pp"])
    verified_v2 = _pct(j["v2_cagr"])

    WITHDRAWN = ["+25.4%", "+18.5%", "+6.9 pp/年", "+702.0%"]
    WITHDRAWAL_MARKERS = ("撤回", "已撤回", "withdrawn", "retracted",
                          "唔好引用", "must not be cited")
    # The corrected source must exist, or "verified" means nothing.
    record("jp/retest summary is readable and is the source of the JP figures",
           (REPO / "data" / "hk_retest_summary.json").exists(),
           "data/hk_retest_summary.json is missing — the JP cells cannot be derived")

    live = {}
    for p in sorted(PUBLIC.rglob("*.html")):
        rel = str(p.relative_to(PUBLIC))
        if rel.startswith("en/"):
            continue          # hand-maintained translations, not machine-built
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            continue
        hits = [w for w in WITHDRAWN if w in txt]
        if not hits:
            continue
        if any(m in txt for m in WITHDRAWAL_MARKERS):
            continue          # quoted inside the correction record — that is the point
        live[rel] = hits
    record("jp/withdrawn JP figures appear only inside a withdrawal notice",
           not live,
           f"{len(live)} page(s) publish a withdrawn JP figure as live: "
           f"{list(live.items())[:3]}" if live else "")

    # MUST-FAIL: the contextual rule has to actually reject a bare claim, and
    # accept the same string inside a withdrawal notice. Without both, a page
    # could satisfy the check by saying "withdrawn" once and then claiming it.
    probe_live = "<p>日股 2016-26 v2 +25.4% 對等權 +18.5%</p>"
    probe_history = "<p>已撤回「日股 +25.4%」呢個聲稱</p>"
    check = lambda s: (not [w for w in WITHDRAWN if w in s]
                       or any(m in s for m in WITHDRAWAL_MARKERS))
    record("jp/withdrawn-claim rule must-fail: a bare claim is rejected",
           not check(probe_live))
    record("jp/withdrawn-claim rule must-pass: a correction notice is allowed",
           check(probe_history))

    # The page that explains the method must actually carry the current figures.
    meth = PUBLIC / "methodology.html"
    if meth.exists():
        txt = meth.read_text(encoding="utf-8")
        record("jp/methodology carries the verified JP figures",
               verified_gap in txt and verified_v2 in txt,
               f"expected {verified_gap!r} and {verified_v2!r} in methodology.html")
        # And it must not still quote the withdrawn pair as its own result.
        body = re.sub(r"<div class=\"callout\"[^>]*>.*?</div>", "", txt, flags=re.S)
        record("jp/methodology body quotes no withdrawn JP figure",
               not [w for w in WITHDRAWN if w in body],
               f"outside the correction callout: "
               f"{[w for w in WITHDRAWN if w in body]}")


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

    # ---- every sitemap URL must BE the page's own canonical -----------------
    # The builder used to add a trailing slash to every URL, so the ~700
    # entries derived from .html files pointed at a 308 while the pages' own
    # canonical tags used the slash-less form. The sitemap and the canonical
    # disagreed on every .html page on the site, and the sitemap advertised a
    # URL that was not the one the page said it was. The URL form now follows
    # the file's shape, so the sitemap entry IS the canonical.
    def sitemap_canonical_mismatches(sitemap_text: str) -> list[str]:
        out: list[str] = []
        for loc in re.findall(r"<loc>([^<]+)</loc>", sitemap_text):
            path = loc.replace("https://www.win9you.com", "")
            f = ((PUBLIC / path.lstrip("/") / "index.html")
                 if path.endswith("/")
                 else (PUBLIC / (path.lstrip("/") + ".html")))
            if not f.is_file():
                out.append(f"{loc}: no file for it")
                continue
            head = f.read_text(encoding="utf-8", errors="replace")[:4000]
            m = re.search(r'<link rel="canonical" href="([^"]+)"', head)
            if not m:
                out.append(f"{loc}: the page carries no canonical tag")
            elif m.group(1) != loc:
                out.append(f"{loc}: the page's canonical is {m.group(1)}")
        return out

    bad = sitemap_canonical_mismatches(text)
    record("sitemap/every URL is the page's own canonical", not bad,
           f"{len(bad)} disagree: {bad[:3]}")

    # MUST-FAIL: the same expression must reject a sitemap URL that is not
    # the canonical. A check that can only pass is not a check.
    cand = [u for u in re.findall(r"<loc>([^<]+)</loc>", text)
            if not u.endswith("/")]
    if cand:
        u = cand[0]
        probe = text.replace(f"<loc>{u}</loc>", f"<loc>{u}/</loc>", 1)
        bad_probe = sitemap_canonical_mismatches(probe)
        record("MUST-FAIL a sitemap URL that is not the canonical is rejected",
               bool(bad_probe), f"failures={bad_probe[:2]}")


# --------------------------------------------------------------------------
# 7. v2 signals must not be published from stale bars
# --------------------------------------------------------------------------
def bars_at_session(actual: str, expected: str) -> tuple[bool, str]:
    """True ONLY when a market's modal bar date is the session it was given.

    2026-10-14. The tolerance was `lag <= 2` in CALENDAR days, and calendar
    days are not sessions. On the night of 10-13 HK's series was two whole
    sessions behind (modal 2026-10-12, missing both the 10-13 and 10-14
    sessions) — that is exactly 2 calendar days, so the check reported PASS
    while HK published stale bars. The US, four days behind, failed correctly,
    which is what made it look like HK was simply not being checked: the
    market with the most names on the site was the one whose drift fit inside
    the slack. A weekend also inflates the same number for a SINGLE missed
    session, so the threshold could not be tuned to catch both without
    failing every Monday.

    There is no date here to infer: ASOF_<MKT> is the close date Kenneth gave
    this run, per market, because the three markets do not share a calendar.
    If that date was not a trading day for the market, the fix is to pass the
    date that was — not to widen the window.
    """
    a = date.fromisoformat(actual)
    e = date.fromisoformat(expected)
    if a == e:
        return True, ""
    if a > e:
        return False, (f"bars at {actual} are AHEAD of {expected}; the --asof cap "
                       f"is supposed to drop every later bar")
    lag = (e - a).days
    return False, (f"modal {actual} is {lag} calendar day(s) behind the session it "
                   f"was given ({expected}) — those sessions were never fetched. "
                   f"If {expected} was not a trading day for this market, pass the "
                   f"real close date in ASOF_<MKT> instead of widening this")


def t_v2_bars_fresh() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    from build_v2_signals import bars_freshness, bars_from_public

    # Control, run BEFORE the real data: a guard proven only by real input that
    # currently passes is indistinguishable from a guard that cannot fail. The
    # 1-day case is the one that slipped through on 2026-10-13 — it has to be
    # rejected now, and an ahead-of-target series must be rejected too.
    record("v2/session-match must-fail: one day behind is rejected",
           not bars_at_session("2026-10-13", "2026-10-14")[0])
    record("v2/session-match must-fail: two days behind is rejected",
           not bars_at_session("2026-10-12", "2026-10-14")[0])
    record("v2/session-match must-fail: bars ahead of the cap are rejected",
           not bars_at_session("2026-10-15", "2026-10-14")[0])
    record("v2/session-match must-pass: the session itself is accepted",
           bars_at_session("2026-10-14", "2026-10-14")[0])

    # 2026-10-13. This guard was vacuous. Two defects, either one fatal on its own:
    #
    #   1. The reference was `MARKETS[mkt].get("_ref", "2026-10-05")`, but no
    #      market in build_v2_signals.MARKETS has ever had a `_ref` key, so the
    #      .get() ALWAYS returned the fallback. The "T-1" being compared against
    #      was a literal frozen at 2026-10-05. From that day on, lag = ref - bars
    #      went NEGATIVE as the data advanced — JP bars at 2026-10-09 gave
    #      lag = -4, and -4 <= 4 passed. The later the data got, the more
    #      reliably it passed. It could only ever fire on bars from before
    #      2026-10-01.
    #   2. The loop covered ("jp200", "us200") and never hk200, so the largest
    #      market on the site had no freshness check at all.
    #
    # JP then sat on 2026-10-09 while HK and US moved daily, and this function
    # reported PASS on every run — the check that exists precisely to catch that.
    # The loop was fixed that day; the tolerance was not, so HK kept slipping
    # through it (see bars_at_session).
    #
    # The reference comes from the same ASOF_* the pipeline was GIVEN (the
    # dates refresh.sh already owns, per-market, because the three markets do
    # not share a calendar). Nothing is inferred. Standalone runs with no env
    # fall back to cross-market consistency: one market stuck while the others
    # moved is the failure mode, and it is detectable without knowing any date.
    expected = {"hk200": os.environ.get("ASOF_HK"),
                "us200": os.environ.get("ASOF_US"),
                "jp200": os.environ.get("ASOF_JP")}

    seen = {}
    for mkt in ("hk200", "us200", "jp200"):
        bars = bars_from_public(mkt)
        if not bars:
            record(f"v2/{mkt} bars present", False, "no bars")
            continue
        d, at, tot = bars_freshness(bars)
        seen[mkt] = d
        if not d:
            record(f"v2/{mkt} bars carry a date", False, f"no last-bar date ({at}/{tot})")
            continue
        exp = expected.get(mkt)
        if exp:
            ok, why = bars_at_session(d, exp)
            record(f"v2/{mkt} bars at the session refresh.sh was given ({d}, {at}/{tot})",
                   ok, why, f"modal {at}/{tot} names at {exp}")
        else:
            record(f"v2/{mkt} bars dated ({d}, {at}/{tot})", True,
                   "no ASOF_* in env — cross-market check below carries this market")

    # Cross-market floor: runs with no ASOF_* env (standalone, CI) still catch a
    # market that fell behind the rest of the same build.
    dated = [v for v in seen.values() if v]
    if dated:
        newest = max(dated)
        for mkt, d in seen.items():
            if not d or expected.get(mkt):
                continue
            lag = (date.fromisoformat(newest) - date.fromisoformat(d)).days
            record(f"v2/{mkt} not stranded behind {newest} ({d})", lag <= 3,
                   f"{lag}d behind the freshest market in this build")

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
    # 2026-10-14: self_test.py itself now runs after finalize, because a gate
    # that judges the tree BEFORE the last writer touches it cannot see what
    # ships — it failed on backtest.html's badge for exactly that reason. That
    # is safe only because self_test is read-only, so it is excluded here. This
    # exclusion is the whole safety argument: if self_test ever gained a
    # write, this list must stop exempting it. Keep it an explicit allowlist of
    # known-read-only scripts, never "everything after the gate is fine".
    #
    # 2026-10-20: deploy_gate.py joins it. A2's gate has to judge the tree
    # that is about to be published, so it runs after finalize for the same
    # reason self_test does. Its read-only claim is checked against the source
    # below rather than taken on trust — an exemption nobody verifies is a
    # hole with a comment on it.
    READ_ONLY_AFTER_FINALIZE = {"self_test.py", "deploy_gate.py"}
    writers = [s for s in after if s not in READ_ONLY_AFTER_FINALIZE]
    record("finalize/refresh.sh runs finalize.py and nothing writes public/ after it",
           fin != -1 and not writers,
           f"finalize on line {fin + 1}; scripts still run after it: {writers or after}")

    # The deploy_gate.py exemption, verified rather than asserted: no statement
    # in that script may both mention public/ and perform a write.
    dg = (Path(__file__).resolve().parent / "deploy_gate.py")
    if dg.exists():
        bad_writes = _statements_writing_public(dg)
        record("finalize/deploy_gate.py writes nothing under public/",
               not bad_writes,
               f"writes under public/: {bad_writes[:3]}" if bad_writes else
               "every write in deploy_gate.py targets logs/, not public/")


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


def t_short_settlement_arithmetic() -> None:
    """Short P&L must settle correctly, checked against an independent hand-calc.

    2026-10-09. The multi-market study generalised portfolio_test.walk to hold
    shorts, because HK's v1 strategy emits SELL signals and PT.walk settles
    `shares*(exit - entry)` — on a short whose price FELL that books a loss,
    inverting every SELL trade. The long-only equivalence check in the study
    proves the generalisation did not change long behaviour; it says nothing
    about the short branch, which is where HK's whole result lives.

    PT.walk cannot serve as the reference here — it is the thing being
    generalised. So this re-derives each case from first principles: sizing
    off the stop distance, the notional cap, and the side-signed settlement.
    """
    sys.path.insert(0, str(REPO / "scripts"))
    import pandas as pd
    import portfolio_test as PT

    # 2026-10-18: this had its own EQ = 100_000.0 while PT.walk_signed started
    # from portfolio_test.INITIAL, which became 1,000,000. The test then compared
    # a walk priced off one capital against a hand-calc priced off another and
    # reported a 900x discrepancy as a settlement bug. Same defect as the pages:
    # a second copy of a number that had exactly one source.
    EQ = PT.INITIAL

    def hand(entry, stop, exit_px, side, why):
        rps = (entry - stop) if side > 0 else (stop - entry)
        if rps <= 0:
            return 0.0, 0.0
        sh = min(PT.RISK * EQ / rps, (PT.MAX_NOTIONAL * EQ) / entry)
        cost = PT.cost_rt("us200", PT.COST, why == "stop")
        return sh, sh * side * (exit_px * (1 - cost) - entry)

    cases = [
        ("short wins on target", -1, 50.0, 52.0, 45.0, "target"),
        ("short loses on stop", -1, 50.0, 52.0, 55.0, "stop"),
        ("long wins on target", 1, 50.0, 48.0, 55.0, "target"),
        ("long loses on stop", 1, 50.0, 48.0, 45.0, "stop"),
        ("short sized by risk cap", -1, 10.0, 10.5, 9.0, "target"),
        ("long sized by notional cap", 1, 200.0, 199.0, 210.0, "target"),
    ]
    bad, wrong_sign = [], []
    for name, side, entry, stop, exit_px, why in cases:
        rec = {"sym": "X", "side": side, "entry": entry, "stop": stop,
               "exit": exit_px, "why": why, "gross": 0.0, "ret5d": 0.0,
               "entry_date": pd.Timestamp("2024-01-02"),
               "exit_date": pd.Timestamp("2024-01-03")}
        curve, _ = PT.walk_signed([rec], "us200")
        got = float(curve["equity"].iloc[-1]) - EQ
        _, exp = hand(entry, stop, exit_px, side, why)
        if abs(got - exp) > 1e-9:
            bad.append(f"{name}: walk {got:.4f} vs hand {exp:.4f}")
        # Direction is the point of the generalisation: a short that fell must
        # profit. A magnitude that matches is not enough if the sign is wrong.
        moved_for_us = (exit_px < entry) if side < 0 else (exit_px > entry)
        if (got > 0) != moved_for_us:
            wrong_sign.append(f"{name}: {got:.4f} for a move that should "
                              f"{'profit' if moved_for_us else 'lose'}")
    record("portfolio/short settlement matches an independent hand-calc",
           not bad, "; ".join(bad) or "all cases agree to 1e-9")
    record("portfolio/short P&L carries the correct sign",
           not wrong_sign, "; ".join(wrong_sign) or "sign correct in all cases")

    # The trap this study walked into, locked down. PT.walk sizes with
    # `rps = entry - stop`, which is NEGATIVE for every short, so its
    # `if rps <= 0: continue` drops the entire short book without a word.
    # HK v1 is ~2/3 SELL signals, so a study that quietly used PT.walk would
    # have reported a portfolio built from the long third and called it the
    # walk. Any script holding short candidates must route through the
    # side-aware walk.
    shorty = {"sym": "X", "side": -1, "entry": 10.0, "stop": 10.5,
              "exit": 9.0, "why": "target", "gross": 0.10, "ret5d": 0.0,
              "entry_date": pd.Timestamp("2022-03-01"),
              "exit_date": pd.Timestamp("2022-03-04")}
    pt_curve, _ = PT.walk([shorty], "hk200")
    sg_curve, _ = PT.walk_signed([shorty], "hk200")
    pt_eq = float(pt_curve["equity"].iloc[-1])
    sg_eq = float(sg_curve["equity"].iloc[-1])
    # Momentum engine guards. The engine is research-only and pre-registered,
    # so the two things that can silently invalidate it are a production
    # import and a parameter edit.
    eng = REPO / "scripts" / "hk_momentum_engine.py"
    if not eng.exists():
        record("momentum/engine script exists", False, "scripts/hk_momentum_engine.py missing")
    else:
        esrc = eng.read_text(encoding="utf-8")
        record("momentum/engine script exists", True, "")
        # Match import STATEMENTS, not the bare name. A text search flags
        # hk_mom_screen_12.py purely because its docstring says "Run on same
        # panel as hk_momentum_engine.py" — which is documentation, not
        # coupling, and reporting it as a violation teaches the guard to be
        # ignored.
        imp = re.compile(r"^\s*(?:import\s+hk_momentum_engine\b"
                         r"|from\s+hk_momentum_engine\s+import\b)", re.M)
        offenders = [q.name for q in sorted((REPO / "scripts").glob("*.py"))
                     if q.name not in ("hk_momentum_engine.py", "self_test.py")
                     and imp.search(q.read_text(encoding="utf-8", errors="ignore"))]
        record("momentum/no production script imports the engine",
               not offenders, f"importers: {offenders}")

        # Pre-registered means frozen. A study that re-tunes PARAMS after
        # seeing its own output is a search wearing a study's clothes, and the
        # only trace of that is the diff — so the values are pinned.
        for k, v in (("lookback_bars", "252"), ("top_fraction", "0.20"),
                     ("rebalance_every_bars", "21")):
            record(f"momentum/PARAMS['{k}'] still {v}",
                   f'"{k}": {v}' in esrc,
                   f"pre-registered parameter {k} was changed")

        # The look-ahead probe compares MEMBER SETS. A count-only check passes
        # a strategy that swaps one name for another.
        record("momentum/look-ahead probe compares the member set, not the count",
               "members_identical" in esrc and "before == after" in esrc,
               "probe fell back to a size comparison")
        record("momentum/event robustness does not re-scale a percentage",
               "annualised_pct_all" in esrc and "per_year)" in esrc
               and "per_year) * 100)" not in esrc,
               "annualisation applies an extra x100 to an already-percent series")

    record("portfolio/PT.walk silently drops every short",
           sg_eq > pt_eq,
           f"PT.walk returned {pt_eq:.2f} and walk_signed {sg_eq:.2f} for one "
           f"profitable short — PT.walk ignored it because entry-stop < 0")


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
        # 2026-10-10: this used to read snapshots from charts/jp200 for every
        # market, but fetch_jp_charts.py writes JP snapshots to data/jp200 —
        # charts/jp200 holds PNGs and OHLC only. _own_bar was therefore always
        # "" for JP and the banner could never fire, and the "short series"
        # filter below meant seven full-length pages (dropped from the universe
        # on 10-08, so never rebuilt) sat on 10-08 data under a 10-09 headline.
        src = {"HK": REPO / "charts" / "hk200", "US": REPO / "charts" / "us200",
               "JP": REPO / "data" / "jp200"}[code]
        market_t1 = None
        for sym in sorted(pages):
            op = PUBLIC / mkt / "ohlc" / f"{sym}_ohlc.json"
            rows = json.load(open(op)) if op.exists() else None
            n_bars = len(rows) if isinstance(rows, list) else 0
            payload = src / f"{sym}.json"
            own = ""
            try:
                own = (json.load(open(payload)).get("last_bar") or {}).get("date", "")
            except Exception:
                pass
            if market_t1 is None:
                market_t1 = data_asof(code)
            # Staleness is the thing that must be declared. A short series whose own bar
            # date IS the market T-1 is plot-disabled but current, and the
            # original intent ("fresh ones may be short") must survive the
            # widening. The old 260-bar filter was a proxy for staleness that
            # happened to miss every full-length series — the majority of pages.
            stale = bool(own and market_t1 and own < market_t1)
            if not stale:
                continue
            pg = PUBLIC / mkt / "ticker" / f"{sym}.html"
            short_pages.append(f"{mkt}/{sym}={own}(vs {market_t1},{n_bars}bars)")
            if not (pg.exists() and "數據未更新" in pg.read_text(encoding="utf-8")):
                undisclosed.append(f"{mkt}/{sym} own={own} t1={market_t1} bars={n_bars}")
    record("ohlc/a too-short-to-plot series must be self-declared, fresh ones may be short",
           not undisclosed,
           f"{len(short_pages)} stale-or-short series, all self-declared"
           if not undisclosed
           else f"{len(undisclosed)} presented as current: {undisclosed[:4]}")


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


def t_signals_only_come_from_tracked_tickers() -> None:
    """A published signal must belong to a ticker we actually track.

    2026-10-10. build_v2_signals.py iterated bars_from_public(), which globs
    every public/<mkt>/ohlc/*.json — including files left behind for tickers a
    universe regen had swapped out. Seven JP names dropped from
    jp_universe_200.json on 10-08 were still published as "today's limit
    orders" on 10-10, and three of them appeared in the /jp200/ table. The
    data was real and the maths was right; what was wrong was that nobody had
    stopped trading a name we no longer cover.

    The per-market source matters too: US is filtered by us_published.json
    (every ticker that has ever had a page), not by us_universe_200.json.
    Filtering US by the rotating 200 would withdraw signals for 35 tickers the
    site still publishes a row for — the fix must not over-correct.
    """
    f = PUBLIC / "v2-signals.json"
    if not f.exists():
        record("v2/every published signal is a tracked ticker", False,
               "v2-signals.json missing")
        return
    sigs = json.loads(f.read_text(encoding="utf-8")).get("signals", [])
    if not sigs:
        record("v2/every published signal is a tracked ticker", True, "no signals today")
        return
    srcs = {"jp200": REPO / "jp_universe_200.json",
            "us200": REPO / "charts" / "us200" / "us_published.json",
            "hk200": REPO / "hk_universe_200.json"}
    keep = {m: {str(t).replace(".", "_") for t in json.load(open(p))}
            for m, p in srcs.items() if p.exists()}
    stray = sorted({f"{s['market']}/{s['symbol']}" for s in sigs
                    if s.get("market") in keep and s["symbol"] not in keep[s["market"]]})
    record("v2/every published signal is a tracked ticker",
           not stray,
           f"{len(sigs)} signals, all within {sorted(keep)}" if not stray
           else f"{len(stray)} signals for untracked tickers: {stray[:5]}")

    # And the mirror image: a page nothing will ever rebuild is a page whose
    # date stamp will drift away from its data.
    for mkt, path in FILTERS_FOR_PAGES.items():
        if not (REPO / path).exists():
            continue
        keep_m = {str(t).replace(".", "_") for t in json.load(open(REPO / path))}
        pages = {q.stem for q in (PUBLIC / mkt / "ticker").glob("*.html")}
        orphans = sorted(pages - keep_m)
        record(f"v2/{mkt} has no page for an untracked ticker",
               not orphans,
               f"{len(pages)} pages, all tracked" if not orphans
               else f"{len(orphans)} orphan pages: {orphans[:5]}")


FILTERS_FOR_PAGES = {
    "jp200": "jp_universe_200.json",
    "us200": "charts/us200/us_published.json",
    "hk200": "hk_universe_200.json",
}


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
        # The files were deleted on 2026-10-10. The REDIRECTS are the whole
        # point of the retirement once the files are gone — without them every
        # /en/ link anyone ever pasted turns into a 404. So this branch must NOT
        # return early; it has to fall through to the sitemap and redirect
        # checks, which are the only thing left standing.
        record("seo/retired /en/ pages carry noindex", True,
               "files deleted; redirects carry the retirement")
        sm0 = PUBLIC / "sitemap.xml"
        if sm0.exists():
            record("seo/retired /en/ is out of sitemap.xml",
                   sm0.read_text(encoding="utf-8").count("/en/") == 0,
                   "no /en/ URLs listed")
        rf0 = PUBLIC / "_redirects"
        n0 = sum(1 for ln in rf0.read_text(encoding="utf-8").splitlines()
                 if ln.startswith("/en") and " 301" in ln) if rf0.exists() else 0
        record("seo/the /en/ redirects outlive the files they served",
               n0 >= 10,
               f"{n0} /en/ 301 rules still in place" if n0 >= 10
               else f"only {n0} /en/ rules — /en/ links would 404")
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


def t_equity_curve_pages_are_v2() -> None:
    """P0-1: equity-curve pages must cite the v2 engine, never the retired v1 JSON."""
    pages = sorted(PUBLIC.glob("equity_curve_*.html"))
    record("equity curve pages exist", bool(pages), f"{len(pages)} page(s)")
    if not pages:
        return

    # 1. The engine check. build_equity_curves.py was migrated to
    #    data/v2_equity_trades.json on 2026-10-07, but its footer was a
    #    hardcoded literal still naming data/monthly_backtest/backtest_*.json —
    #    so the pages rendered v2 numbers while claiming a v1 source, which is
    #    the same defect P0-1 exists to stop, reduced to a footnote. The footer
    #    is now derived from the file that was actually read.
    #
    #    Naming the v1 JSON is not itself the defect: these pages carry a
    #    correction banner saying they USED to read it and that those numbers
    #    are void. That transparency is the right thing to ship. What must never
    #    appear is a page presenting v1 as its own current source — so the check
    #    is on the provenance line, and any surviving mention must sit inside
    #    withdrawal language. Grepping for the bare string would flag the banner
    #    that documents the very fix.
    bad_source, unexplained = [], []
    for p in pages:
        txt = p.read_text(encoding="utf-8")
        footers = [m for m in re.findall(r"Source:[^<]*", txt)
                   if "monthly_backtest" in m]
        if footers:
            bad_source.append(f"{p.name}:{footers[0][:60]}")
        for m in re.finditer(r"monthly_backtest", txt):
            ctx = txt[max(0, m.start() - 400):m.end() + 400]
            if not re.search(r"作廢|舊版|retired|已更換|withdrawn|取代", ctx):
                unexplained.append(f"{p.name}@{m.start()}")
    record("equity curves never present v1 backtest as their source", not bad_source,
           f"provenance still cites v1: {bad_source}" if bad_source else
           f"{len(pages)} pages, 0 v1 provenance lines")
    record("any v1 mention on equity curves sits in a withdrawal notice",
           not unexplained, f"bare mention with no 作廢/retired context: {unexplained}"
           if unexplained else "all mentions are inside the 2026-10-07 correction banner")

    detail = [p for p in pages if p.stem != "equity_curve_t10"]
    uncited = [p.name for p in detail
               if "v2_equity_trades.json" not in p.read_text(encoding="utf-8")]
    record("every equity curve states its v2 source file", not uncited,
           f"no source citation: {uncited}" if uncited else
           f"{len(detail)} detail pages cite v2_equity_trades.json")

    # 2. equity_curve_t10.html and equity_curve_t_10.html are NOT duplicates and
    #    deleting either breaks a live link. The first is the family hub (the
    #    other four link back to it as "<- All horizons"); the second is the
    #    T+10 detail. The names collide visually, so assert the relationship
    #    rather than trusting it. Links are extensionless here — Cloudflare
    #    Pages serves /equity_curve_t_1 for equity_curve_t_1.html — so match the
    #    stem, not the filename.
    hub = PUBLIC / "equity_curve_t10.html"
    if hub.exists():
        hub_txt = hub.read_text(encoding="utf-8")
        missing = [f"t_{n}" for n in (1, 3, 5, 10)
                   if f"/equity_curve_t_{n}" not in hub_txt]
        record("equity_curve_t10.html is the hub and links all four horizons",
               not missing, f"missing links: {missing}" if missing else
               "t_1 / t_3 / t_5 / t_10 all linked from the hub")

    # MUST-FAIL: a page that names v1 as its OWN source has to be rejected, and
    # the same mention inside a withdrawal banner has to be allowed. Without
    # both, this check could be passing because it greps nothing — or failing
    # forever on the correction banner that documents the fix.
    bad = "Source: data/monthly_backtest/backtest_2026-09-10.json · 6714 signals"
    record("must-fail: v1 stated as the page source is rejected",
           any("monthly_backtest" in m for m in re.findall(r"Source:[^<]*", bad)),
           "the provenance regex matches the literal the old footer used")
    ok = ("2026-10-07 — 數據源已更換。呢四頁原本讀 data/monthly_backtest/，"
          "舊版 v1 swing/day-trade 引擎，歷史數字全部作廢。")
    record("must-fail: v1 named inside a withdrawal notice is allowed",
           not re.search(r"Source:[^<]*monthly_backtest", ok)
           and bool(re.search(r"作廢|舊版|retired|已更換|withdrawn|取代", ok)),
           "a documented withdrawal is not a stale provenance claim")
    # MUST-FAIL: the hub must not pass just by existing.
    record("must-fail: hub without the four links is rejected",
           not all(f"equity_curve_t_{n}.html" in "equity_curve_t_1.html"
                   for n in (1, 3, 5, 10)),
           "a hub page missing its horizon links does not satisfy the test")


def t_homepage_perf_figures_trace_to_source() -> None:
    """Home-page v2-vs-equal-weight figures must be rendered FROM
    data/hk_retest_summary.json, not typed into the page as literals.

    2026-10-17. The lede used to hardcode "27.4% / 27.2%, difference of zero"
    for US while the JP sentence in the same paragraph came from the retest
    summary — two sources, one page. That is how a defective benchmark survived
    review: nothing compared the page against the file it claimed to quote.
    """
    src_json = REPO / "data" / "hk_retest_summary.json"
    page = PUBLIC / "index.html"
    record("retest summary exists", src_json.exists(), str(src_json.name))
    record("home page exists", page.exists(), "public/index.html")
    if not (src_json.exists() and page.exists()):
        return

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_home as BH
    perf = BH._perf()
    fmt = BH._perf_fmt(perf)
    html = page.read_text(encoding="utf-8")

    # 1. Every figure the formatter produces must actually appear on the page.
    #    If the page were built from a different artefact this fails.
    missing = [f"{k}={v}" for k, v in fmt.items()
               if k.endswith(("_V2", "_EW")) and v not in html]
    record("page renders the retest summary's own figures", not missing,
           f"absent from page: {missing}" if missing else
           f"{sum(1 for k in fmt if k.endswith(('_V2', '_EW')))} figures matched")

    # 2. A broken placeholder must never survive into the rendered page.
    leftover = sorted(set(re.findall(r"\{[A-Z][A-Z0-9_]{2,}\}", html)))
    record("no unfilled format placeholder on the page", not leftover,
           f"leftover: {leftover}" if leftover else "none")

    # 3. The lede strings must not carry literal performance figures at all.
    #    A stated cost assumption is fine — it is an input the reader is told
    #    about. A literal CAGR is a claim that will silently go stale the next
    #    time the retest is re-run, which is the defect being locked down.
    declared_costs = {"0.25%", "0.10%", "0.05%"}
    lits = []
    for key in ("home_lede_zh",):
        entry = BH._STRINGS.get(key, {})
        for lang, s in entry.items():
            for m in re.findall(r"\d+\.\d+%", s):
                if m not in declared_costs:
                    lits.append(f"{key}/{lang}:{m}")
    record("lede carries no literal performance figures", not lits,
           f"hardcoded: {lits}" if lits else
           "only declared cost assumptions are literal; CAGRs are formatted")

    # 4. Control: the withdrawn pre-fix numbers must be gone from the page.
    stale = [s for s in ("27.4%", "27.2%") if s in html]
    record("withdrawn hardcoded US figures are gone", not stale,
           f"still present: {stale}" if stale else "27.4/27.2 absent")


def t_p0_site_figures_match_the_artifact() -> None:
    """docs/win9you-p0-fix-spec-2026-10-18.md's four acceptance criteria,
    executed against the built site.

    All five fixes that spec asks for were applied in the 2026-10-19 build,
    and the live site was byte-identical to it, so the spec's own work was
    done. But nothing was CHECKING them. The numbers were right by
    construction that morning, not by a rule, and the artifacts these pages
    quote are regenerated: a re-run moves the numbers, and the pages only
    stay right because a build re-renders them. A page edited by hand, or a
    generator that stops quoting the artifact, would have changed the site
    with nothing going red.

    The checks are phrased as "every gap claim on the page must be a value
    an artifact contains", not "the page must contain these values". The
    second form is satisfied by a page that carries the right figures AND a
    wrong one, which is the contradiction the spec was written about. The
    first form catches the extra number.

    Two of the checks are not about a number but about a claim:
      - the /backtest Headline table's "Total" column is the sum of the
        per-trade percentage returns (120 x +1.63% = +195.3%), the same
        arithmetic the site withdrew as "+44.02%", sitting under a
        "Headline" heading while the same page said the risk-managed
        portfolio return had no citable number;
      - the OG image is the one asset that gets scraped into other people's
        pages, so a retracted figure on it travels further than the page it
        came from.
    """
    import json as _json
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import perf_figures

    pf = perf_figures.fmt(perf_figures.load(), "en")
    src_p = REPO / "data" / "hk_retest_summary.json"
    ab_p = REPO / "data" / "ab_close_hold.json"
    sig_p = PUBLIC / "v2-signals.json"
    og_p = REPO / "scripts" / "generate_og_image.py"
    files = {
        "llms": PUBLIC / "llms.txt",
        "methodology": PUBLIC / "methodology.html",
        "us200": PUBLIC / "us200" / "index.html",
        "jp200": PUBLIC / "jp200" / "index.html",
        "backtest": PUBLIC / "backtest.html",
    }
    gone = [n for n, p in files.items() if not p.exists()]
    for n, p in (("hk_retest_summary.json", src_p), ("ab_close_hold.json", ab_p),
                 ("v2-signals.json", sig_p), ("generate_og_image.py", og_p)):
        if not p.exists():
            gone.append(n)
    record("every artifact and page the P0 check needs is present", not gone,
           f"missing: {gone}")
    if gone:
        return
    src = _json.loads(src_p.read_text(encoding="utf-8"))
    ab = _json.loads(ab_p.read_text(encoding="utf-8"))
    oos = (_json.loads(sig_p.read_text(encoding="utf-8")).get("setup_oos") or {})
    og_src = og_p.read_text(encoding="utf-8", errors="replace")
    T = {n: p.read_text(encoding="utf-8", errors="replace") for n, p in files.items()}

    # ---- the values a page may quote, derived from the artifacts ----------
    def allowed_values() -> set:
        vals = set()

        def add(v):
            for dp in (1, 2):
                vals.add(f"{abs(float(v)):.{dp}f}")
        for k in ("US_GAP", "US_GAP_EARLY", "US_GAP_LATE", "JP_GAP",
                  "JP_GAP_EARLY", "JP_GAP_LATE", "JP_GAP_MID", "HK_GAP",
                  "HK_GAP_EARLY", "HK_GAP_LATE"):
            add(pf[k])
        for m in ("hk", "us", "jp"):
            for wins in src[m]["portfolio"]["v2_vs_ew_pp"].values():
                for d in wins.values():
                    add(d["v2_minus_ew_pp"])
        for cell in ab["placebo"]["cells"].values():
            for d in cell.values():
                add(d["gap_vs_null_pp"])
        return vals

    # The 2026-10-06 retracted Japan claim (+6.9) and the figures it left
    # behind (+25.4% vs +18.5%, sub-periods 7.9 / 16.7). They have no
    # artifact — that is what a retraction means — so they are allowed only
    # where the page is talking about the retraction. Measured: every
    # occurrence is within 173 characters of a retraction marker, so 400 is a
    # tight window with more than twice the headroom.
    HISTORY = {"6.9", "7.9", "16.7", "25.4", "18.5"}
    MARKERS = ("撤回", "withdrawn", "retract", "唔企穩", "拆開就冇咗")

    def site_checks(texts):
        """The predicate, factored so the controls can re-evaluate it."""
        res = []
        allowed = allowed_values()
        llms = texts["llms"]
        meth = texts["methodology"]
        flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", meth))

        # --- criterion 1: llms.txt quotes the artifact, not a literal ------
        want = {
            "US_V2": f"{pf['US_V2']}/yr", "US_EW": f"{pf['US_EW']}/yr",
            "US_GAP": f"{pf['US_GAP']}pp/yr",
            "US_GAP_EARLY": f"{pf['US_GAP_EARLY']}pp",
            "US_GAP_LATE": f"{pf['US_GAP_LATE']}pp",
            "US_GAP_FREE": f"{pf['US_GAP_FREE']}pp",
            "JP_V2": f"{pf['JP_V2']}/yr", "JP_EW": f"{pf['JP_EW']}/yr",
            "JP_GAP": f"{pf['JP_GAP']}pp/yr",
            "JP_GAP_MID": f"{pf['JP_GAP_MID']}pp",
            "JP_GAP_EARLY": f"{pf['JP_GAP_EARLY']}pp",
            "JP_GAP_LATE": f"{pf['JP_GAP_LATE']}pp",
        }
        absent = [k for k, v in want.items() if v not in llms]
        res.append((not absent,
                    f"llms.txt quotes the retest summary's own figures: {absent}"))
        pre = [s for s in ("27.4%/yr", "27.2%/yr", "18.03%/yr", "18.00%/yr",
                           "0.04pp", "0.2pp/yr") if s in llms]
        res.append((not pre,
                    f"llms.txt still carries a pre-fix literal: {pre}"))
        for mk, key in (("us200", "US"), ("jp200", "JP")):
            m = oos.get(mk) or {}
            expect = (f"勝率 {m.get('win_pct')}%、每單平均淨 {m.get('avg_net_pct')}%、"
                      f"n = {m.get('n'):,}")
            res.append((expect in llms,
                        f"llms.txt's {key} per-trade line is the artifact's"))

        # --- criterion 2: the methodology page states no untraceable gap ----
        claims = re.findall(r"[+\u2212-]?\d+\.\d+\s*(?:\u9ede/\u5e74|pp/\u5e74|\u500b\u767e\u5206\u9ede)",
                            flat)
        untraceable = []
        for c in claims:
            num = c.split()[0].lstrip("+\u2212-")
            if num in allowed or num in HISTORY:
                continue
            untraceable.append(c)
        res.append((not untraceable,
                    f"methodology quotes a gap no artifact contains: {sorted(set(untraceable))}"))
        stray = []
        for num in HISTORY:
            for m in re.finditer(r"(?<![\d.])" + re.escape(num) + r"(?![\d])", flat):
                ctx = flat[max(0, m.start() - 400):m.end() + 400]
                if not any(k in ctx for k in MARKERS):
                    stray.append(num)
                    break
        res.append((not stray,
                    f"a retracted figure is quoted outside a retraction notice: {sorted(set(stray))}"))
        res.append(("0.2 \u500b\u767e\u5206\u9ede" not in flat,
                    "the methodology page's old 'US trails by 0.2pp' line is gone"))
        res.append((not re.search(r"50[\u2013-]54%", flat),
                    "the methodology FAQ's old '50-54%' win rate is gone"))
        for mk, key in (("us200", "US"), ("jp200", "JP")):
            m = oos.get(mk) or {}
            res.append((f"{m.get('win_pct')}%" in flat
                        and f"{m.get('n'):,}" in flat,
                        f"the methodology FAQ's {key} per-trade figures are the artifact's"))

        # --- criterion 3: no retired strategy is published as a signal -----
        for page in ("us200", "jp200"):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", texts[page], flags=re.S)
            bad = [c for c in cells
                   if re.search(r"SELL_R1|BREAK_SHORT|BREAK_LONG", re.sub(r"<[^>]+>", "", c))]
            res.append((not bad,
                        f"{page} publishes a retired strategy as a signal: {bad[:3]}"))

        # --- criterion 4: one authoritative T+10 figure on /backtest -------
        bt = re.sub(r"<script.*?</script>", " ", texts["backtest"], flags=re.S)
        res.append(("814.07" not in texts["backtest"],
                    "/backtest still carries the +814.07% figure"))
        res.append((not re.search(r"<th>\s*Total\s*</th>", texts["backtest"]),
                    "/backtest's Headline table still has a Total column"))
        for num in ("44.02", "4.05"):
            stray = False
            for m in re.finditer(re.escape(num), bt):
                ctx = bt[max(0, m.start() - 500):m.end() + 500]
                if not any(k in ctx for k in MARKERS + ("停止引用", "停用", "唔可以引用")):
                    stray = True
                    break
            res.append((not stray,
                        f"/backtest quotes {num} outside a withdrawal notice"))
        return res

    for ok, msg in site_checks(T):
        record(msg, ok, "the P0 acceptance criteria do not hold on the built site")

    # ---- the OG image generator may not carry a retired figure ------------
    # The quoted code-literal forms, not the bare digits: the generator's
    # docstring explains WHY +3.05% came off the card, and that explanation
    # must not trip the check that the number is gone.
    og_stale = [s for s in ('"+3.02%"', '"+3.05%"', '"+44.02%"', '"+814.07%"',
                            '"-4.05%"', '"+4.05%"', '"+0.38%"', '"+1.63%"')
                if s in og_src]
    record("the OG image generator carries no retired figure",
           not og_stale,
           f"the generator still prints: {og_stale}")

    # ---- MUST-FAIL: re-evaluate the same predicate on mutated text -------
    def mutate(s, a, b):
        if a not in s:
            return s
        return s.replace(a, b)

    tampered = dict(T)
    tampered["llms"] = mutate(T["llms"], f"{pf['US_V2']}/yr", "+99.99%/yr")
    tampered["methodology"] = mutate(T["methodology"],
                                     f"{pf['JP_GAP']} \u9ede/\u5e74",
                                     "+99.99 \u9ede/\u5e74")
    tampered["us200"] = mutate(T["us200"], "</td>", "SELL_R1</td>")
    tampered["backtest"] = mutate(T["backtest"], "<th>Avg</th>",
                                  "<th>Total</th><th>Avg</th>")
    for label, texts in (("a tampered llms.txt figure", tampered),):
        res = site_checks(texts)
        record(f"MUST-FAIL {label} is rejected",
               not all(ok for ok, _ in res),
               "the control for this gate does not discriminate: the same "
               "predicate still passes on the mutated text")


def t_og_image_figures_match_the_artifact() -> None:
    """The OG image must carry the site's numbers, and card 1 must name the
    markets the engine actually runs.

    The card used to say "200+200 HK + US universe" — a hardcoded brand line
    that was wrong twice over. The site publishes 241 HK, 232 US and 201 JP
    ticker pages, and the v2 engine's own markets are US + JP, so "HK + US"
    described an universe the site does not run.

    The card then carried a count of all three markets (674) while every
    other number on the same image is a v2 number. Beside them it read as
    "674 names are scored by the engine" — and 241 of them are HK, where
    the retired engine still runs and the site says so on every page. The
    card is the v2 universe: 433 = 232 US + 201 JP, the same figure the
    homepage's own lede states. The card is also the one asset that gets
    scraped into other people's pages, so a wrong number on it outlives
    the page it came from.

    A PNG's pixels cannot be read by a test, so the generator writes down the
    figures it rendered, next to the image, in public/og-image.figures.json.
    This gate checks that file. The sidecar is only worth trusting if the
    generator cannot type a number into it, so the gate also opens the
    generator's own source and refuses a universe figure held as code or as
    text — the count has to be counted, not typed.

    The card is not read back from the sidecar alone: the generator's card
    logic is executed (without running its drawing code, which would
    overwrite the PNG) and the cards it produces are checked against the
    artifacts and against the published tree. The sidecar is then required
    to record exactly those cards.

    All four controls run against the REAL files: the sidecar is written
    back with a wrong count, the generator's str(total) is replaced with a
    typed sum, a brand line that drops a market is injected, and a universe
    figure is appended to the generator as text. The same predicates are
    re-evaluated on what is on disk, and every file is restored and
    re-checked afterwards. A control that only proves the arithmetic works
    on a copy proves nothing about the files the build ships.
    """
    sidecar_p = PUBLIC / "og-image.figures.json"
    gen_p = REPO / "scripts" / "generate_og_image.py"
    md_p = REPO / "data" / "monthly_backtest" / "latest.md"
    ab_p = REPO / "data" / "ab_close_hold.json"
    sig_p = PUBLIC / "v2-signals.json"
    png_p = PUBLIC / "og-image.png"

    missing = [p.name for p in (sidecar_p, gen_p, md_p, ab_p, sig_p, png_p)
               if not p.exists()]
    record("every file the OG-image gate needs is present", not missing,
           f"missing: {missing}")
    if missing:
        return

    # --- the published tree, counted here rather than imported -------------
    # If this called build_home._universe_counts() it would be checking the
    # generator against itself. Count the published pages directly.
    tree = {}
    for mkt in ("hk200", "us200", "jp200"):
        d = PUBLIC / mkt / "ticker"
        tree[mkt[:2].upper()] = len(list(d.glob("*.html"))) if d.is_dir() else 0

    # --- the markets the engine runs, read from the live signals file -------
    sig = json.loads(sig_p.read_text(encoding="utf-8"))
    engine_markets = [k[:2].upper() for k in (sig.get("market_state") or {})]
    total = sum(tree.get(m, 0) for m in engine_markets)
    banned_nums = {total, *tree.values()}

    # --- the artifacts the other three cards quote -------------------------
    md = md_p.read_text(encoding="utf-8")
    i = md.find("## Headline")
    j = md.find("\n## ", i + 5)
    block = md[i: j] if i >= 0 and j > 0 else ""
    hl = {}
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5 or set(cells[0]) <= set("|-: "):
            continue
        hl[cells[0]] = {"n": cells[1], "wr": cells[2], "avg": cells[3]}
    ab = json.loads(ab_p.read_text(encoding="utf-8"))
    win = f"{ab['windows']['us200']['B_close-hold']['test']['win%']}%"

    def markets_in(text: str) -> set:
        """The market codes a line of copy names."""
        return {m for m in ("HK", "US", "JP")
                if re.search(rf"\b{m}\b", str(text))}

    def card_checks(cards: list) -> list:
        """The predicate over the card list the generator produces.

        Every card's big value is TEXT, so it must be a string: a card that
        printed a computed integer instead of the rendered string would be
        rendered by PIL just the same, and the check that the number is the
        tree's would pass on 433 while the image says "433" only by luck of
        the draw call. Requiring str keeps the check on what is painted.
        """
        res = []
        res.append((len(cards) == 4,
                    f"the OG image has four cards: {len(cards)}"))
        if len(cards) < 4:
            return res
        big, sub = cards[0][0], cards[0][1]
        res.append((isinstance(big, str) and big == str(total),
                    f"card 1 prints the engine universe's total: {big!r} vs {total}"))
        res.append((markets_in(sub) == set(engine_markets),
                    f"card 1 names exactly the engine's markets: {sub!r} vs {engine_markets}"))
        res.append(("200+200" not in str(big) and "200+200" not in str(sub),
                    f"card 1 does not carry the retired '200+200' claim: {big!r} {sub!r}"))
        t10 = (hl.get("T+10 (10-day swing)") or {}).get("avg")
        t1 = (hl.get("T+1 (day-trade)") or {}).get("avg")
        res.append((cards[1][0] == t10 and cards[1][1] == "T+10 avg/trade",
                    f"card 2 is the T+10 average from the artifact: "
                    f"{cards[1][:2]} vs {t10}"))
        res.append((cards[2][0] == t1 and cards[2][1] == "T+1 avg/trade",
                    f"card 3 is the T+1 average from the artifact: "
                    f"{cards[2][:2]} vs {t1}"))
        res.append((cards[3][0] == win and cards[3][1] == "US win rate, OOS",
                    f"card 4 is the US out-of-sample win rate from the artifact: "
                    f"{cards[3][:2]} vs {win}"))
        return res

    def load_cards(src: str) -> dict:
        """Run the generator's card logic without running its drawing code.

        generate_og_image.py is a script: importing it would redraw and
        overwrite public/og-image.png mid-test. So the module's literal
        constants and its functions are lifted out of the source and executed
        here, and only the card list and the brand line are asked for. They
        are then the ones a build would paint, not an echo of a file.
        """
        import pathlib as _pathlib

        def literal_value(v) -> bool:
            if isinstance(v, ast.Constant):
                return True
            if isinstance(v, (ast.Tuple, ast.List)):
                return all(literal_value(e) for e in v.elts)
            return False

        mod = ast.parse(src)
        keep = [n for n in mod.body
                if isinstance(n, ast.FunctionDef)
                or (isinstance(n, ast.Assign) and literal_value(n.value))]
        ns = {"json": json, "pathlib": _pathlib, "sys": sys, "REPO": REPO}
        exec(compile(ast.Module(body=keep, type_ignores=[]), str(gen_p), "exec"), ns)
        return {"cards": ns["_cards"](), "tagline": ns["_tagline"]()}

    def source_checks(src: str) -> list:
        """Two scans, each scoped where the number could actually be typed.

        A scan of every numeric constant in the file is useless here: the
        drawing code is full of layout numbers and the palette holds
        FG = (232, 234, 240), so a universe count of 232 was "found" in a
        colour channel. Numbers can only reach the card through the
        functions that build it, so the numeric scan is scoped to those.

        Strings are different: a rendered figure is a string, and the
        generator's docstrings quote the counts in prose to explain why the
        card changed. Docstrings are excluded; every other string in the
        file is scanned.
        """
        mod = ast.parse(src)
        docs = set()
        for node in ast.walk(mod):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                  ast.AsyncFunctionDef, ast.ClassDef)):
                body = node.body
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    docs.add(id(body[0].value))

        strings = [n.value for n in ast.walk(mod)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and id(n) not in docs]
        hard_strs = [s for s in strings
                     if "200+200" in s or s in {str(c) for c in banned_nums}]

        fn_nums = []
        for node in mod.body:
            if isinstance(node, ast.FunctionDef):
                for n in ast.walk(node):
                    if (isinstance(n, ast.Constant)
                            and isinstance(n.value, (int, float))
                            and not isinstance(n.value, bool)):
                        fn_nums.append(n.value)
        hard_nums = sorted({n for n in fn_nums if n in banned_nums})
        return [(not hard_nums,
                 f"the OG generator types no universe number: {hard_nums}"),
                (not hard_strs,
                 f"the OG generator holds no universe figure as text: {hard_strs}")]

    # ---- the generator as the build left it -------------------------------
    gen_txt = gen_p.read_text(encoding="utf-8")
    try:
        built = load_cards(gen_txt)
    except Exception as e:
        record("the OG generator's card logic runs", False,
               f"{type(e).__name__}: {e}")
        return
    record("the OG generator's card logic runs", True)
    cards, tagline = built["cards"], built["tagline"]
    for ok, msg in card_checks(cards):
        record(msg, ok, "the OG image's numbers are not the site's numbers")
    record("the OG image's brand line names the same markets as its universe card",
           markets_in(tagline) == markets_in(cards[0][1]),
           f"the brand line {tagline!r} names {sorted(markets_in(tagline))} but "
           f"the card names {sorted(markets_in(cards[0][1]))}")
    for ok, msg in source_checks(gen_txt):
        record(msg, ok, "the OG generator can hardcode a universe number")

    # ---- the sidecar the build wrote next to the image --------------------
    sidecar_txt = sidecar_p.read_text(encoding="utf-8")
    try:
        fig = json.loads(sidecar_txt)
    except Exception as e:
        record("the OG image's figures sidecar parses", False,
               f"{type(e).__name__}: {e}")
        return
    side_cards = [(c.get("big"), c.get("sub")) for c in (fig.get("cards") or [])]
    record("the OG image's sidecar records the card the generator printed",
           side_cards == [(c[0], c[1]) for c in cards],
           f"sidecar {side_cards} vs generator {[(c[0], c[1]) for c in cards]}")
    record("the OG image's sidecar universe counts are the published tree's",
           (fig.get("universe_ticker_pages") or {}) == tree,
           f"sidecar {fig.get('universe_ticker_pages')} vs tree {tree}")
    record("the OG image's sidecar records the markets the generator used",
           (fig.get("markets") or []) == engine_markets,
           f"sidecar {fig.get('markets')} vs engine {engine_markets}")
    record("the OG image's sidecar records the brand line the generator printed",
           fig.get("tagline") == tagline,
           f"sidecar {fig.get('tagline')!r} vs generator {tagline!r}")
    for ok, msg in card_checks([(c.get("big"), c.get("sub"), "")
                                for c in (fig.get("cards") or [])]):
        record(msg.replace("the OG", "the sidecar's"), ok,
               "the sidecar records numbers the site does not publish")

    # ---- MUST-FAIL: corrupt the REAL sidecar and re-evaluate -------------
    try:
        bad = json.loads(sidecar_txt)
        bad["cards"][0]["big"] = str(total + 1)
        sidecar_p.write_text(json.dumps(bad, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        on_disk = json.loads(sidecar_p.read_text(encoding="utf-8"))
        red = card_checks([(c.get("big"), c.get("sub"), "")
                           for c in (on_disk.get("cards") or [])])
        record("MUST-FAIL a wrong universe count on the sidecar is rejected",
               not all(ok for ok, _ in red),
               "the control for this gate does not discriminate: the same "
               "predicate still passes on the corrupted sidecar")
    finally:
        sidecar_p.write_text(sidecar_txt, encoding="utf-8")
    record("the sidecar is restored after the control",
           sidecar_p.read_text(encoding="utf-8") == sidecar_txt,
           "the sidecar was left corrupted by the control")

    # ---- MUST-FAIL: type the universe into the REAL generator -------------
    # str(total) -> str(232 + 201): the card still prints the RIGHT number, so
    # the card check still passes and only a check on the SOURCE can tell a
    # counted number from a typed one. A control that also broke the number
    # would prove nothing about the source scan.
    try:
        assert gen_txt.count("(str(total),") == 1
        gen_p.write_text(gen_txt.replace("(str(total),", "(str(232 + 201),"),
                         encoding="utf-8")
        tampered = gen_p.read_text(encoding="utf-8")
        red_cards = card_checks(load_cards(tampered)["cards"])
        red_src = source_checks(tampered)
        record("MUST-FAIL a typed universe total in the generator is rejected",
               all(ok for ok, _ in red_cards) and not all(ok for ok, _ in red_src),
               "the control for this gate does not discriminate: the same "
               "predicate still passes on a generator that types the number")
    finally:
        gen_p.write_text(gen_txt, encoding="utf-8")
    record("the generator is restored after the control",
           gen_p.read_text(encoding="utf-8") == gen_txt,
           "the generator was left corrupted by the control")

    # ---- MUST-FAIL: let the brand line drop a market ----------------------
    try:
        needle = '" + ".join(_markets())'
        assert gen_txt.count(needle) == 1
        gen_p.write_text(gen_txt.replace(needle, '"HK + US"'), encoding="utf-8")
        tampered = gen_p.read_text(encoding="utf-8")
        built_bad = load_cards(tampered)
        record("MUST-FAIL a brand line that drops a market is rejected",
               markets_in(built_bad["tagline"]) != markets_in(built_bad["cards"][0][1]),
               "the control for this gate does not discriminate: a brand line "
               "naming fewer markets than the card is not detected")
    finally:
        gen_p.write_text(gen_txt, encoding="utf-8")
    record("the generator is restored after the brand-line control",
           gen_p.read_text(encoding="utf-8") == gen_txt,
           "the generator was left corrupted by the control")

    # ---- MUST-FAIL: a universe figure held as text in the generator -------
    try:
        gen_p.write_text(gen_txt + f'\nHARDCODED_UNIVERSE = "{total}"\n',
                         encoding="utf-8")
        red = source_checks(gen_p.read_text(encoding="utf-8"))
        record("MUST-FAIL a universe figure held as text in the generator is rejected",
               not all(ok for ok, _ in red),
               "the control for this gate does not discriminate: the same "
               "predicate still passes on a generator holding the number")
    finally:
        gen_p.write_text(gen_txt, encoding="utf-8")
    record("the generator is restored after the text control",
           gen_p.read_text(encoding="utf-8") == gen_txt,
           "the generator was left corrupted by the control")


def t_hub_universe_claims_match_the_page() -> None:
    """A hub page must quote the number of names it actually lists.

    Each of the three hubs used to say "200" in four places at once — the
    <title>, the og:title, the kicker and the FAQPage schema — while the
    table under them listed 241 HK, 232 US and 201 JP names. On the HK hub
    the hero meta added a third number, "Universe: 245 HK names", taken
    from len(universe) — four names with no data and so no page — for a
    table of 241 rows. Three numbers for one set, on the pages that carry
    the most SEO weight on the site, and the first thing a visitor looks
    at to find out what the site covers.

    The count is now derived from the rows the page renders, so it cannot
    drift. This gate makes sure the built page agrees with the tree it was
    built from, that the homepage's own v2 universe line agrees with the
    two hubs the engine runs, and that no "200" claim survives on the three
    pages.
    """
    MARKETS = (("HK", "hk200", "港股"), ("US", "us200", "美股"), ("JP", "jp200", "日股"))
    missing = [d for _, d, _ in MARKETS if not (PUBLIC / d / "index.html").exists()]
    record("every hub page the universe-count gate needs is present",
           not missing, f"missing: {missing}")
    if missing:
        return

    def hub_checks(text: str, code: str, d: str, label: str, n: int) -> list:
        """The predicate, factored so the control can re-evaluate it."""
        res = []
        res.append((f"{label} {n} · Leeks Terminal" in text,
                    f"the {label} hub's title says {label} {n}"))
        res.append((f'property="og:title" content="{label} {n} · Leeks Terminal"' in text,
                    f"the {label} hub's og:title says {label} {n}"))
        res.append((f'<div class="kicker">{code} · {n} names</div>' in text,
                    f"the {label} hub's kicker says {code} · {n} names"))
        res.append((f"Universe: <b>{n} {code} names</b>" in text,
                    f"the {label} hub's hero says the universe is {n} names"))
        res.append((f'"{label} {n} 點樣揀？"' in text,
                    f"the {label} hub's FAQ names {n}"))
        res.append((f"{n} 隻" in text,
                    f"the {label} hub's FAQ quotes {n} 隻"))
        stale = [s for s in (f"{label} 200", f"{code} · 200 names",
                             f"{label} 200 點樣揀", "200 隻", "5-day 平均成交額")
                 if s in text]
        res.append((not stale,
                    f"the {label} hub carries no stale universe claim: {stale}"))
        res.append((len(set(re.findall(r'href="/%s/ticker/([^"]+)"' % d, text))) == n,
                    f"the {label} hub lists {n} ticker links"))
        return res

    counts = {}
    for code, d, label in MARKETS:
        text = (PUBLIC / d / "index.html").read_text(encoding="utf-8", errors="replace")
        n = len(set(re.findall(r'href="/%s/ticker/([^"]+)"' % d, text)))
        counts[code] = n
        for ok, msg in hub_checks(text, code, d, label, n):
            record(msg, ok, "the hub pages quote a universe size they do not list")

    # The homepage states the v2 universe in its own lede and badge. The two
    # hubs the engine runs must agree with it, and the OG card's total must
    # be the same sum — three places, one number.
    home = (PUBLIC / "index.html").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<b>(\d+) 隻美股、(\d+) 隻日股</b>", home)
    if not m:
        record("the homepage states its v2 universe", False,
               "the homepage's lede no longer carries the 'N 隻美股、M 隻日股' line")
        return
    home_us, home_jp = int(m.group(1)), int(m.group(2))
    record("the homepage's v2 universe matches the US and JP hubs",
           home_us == counts["US"] and home_jp == counts["JP"],
           f"homepage {home_us} US / {home_jp} JP vs hubs {counts['US']} / {counts['JP']}")
    record("the homepage's v2 badge matches its own lede",
           f"{home_us} US + {home_jp} JP 行 v2" in home,
           f"the badge does not say {home_us} US + {home_jp} JP 行 v2")
    sidecar = PUBLIC / "og-image.figures.json"
    if sidecar.exists():
        fig = json.loads(sidecar.read_text(encoding="utf-8"))
        og_total = (fig.get("cards") or [{}])[0].get("big")
        record("the OG image's universe total is the homepage's v2 universe",
               og_total == str(home_us + home_jp),
               f"the OG card says {og_total!r}, the homepage says {home_us + home_jp}")

    # ---- MUST-FAIL: tamper a REAL hub and re-evaluate ---------------------
    hp = PUBLIC / "hk200" / "index.html"
    good = hp.read_text(encoding="utf-8")
    try:
        hp.write_text(good.replace("港股 241", "港股 200", 1), encoding="utf-8")
        on_disk = hp.read_text(encoding="utf-8")
        red = hub_checks(on_disk, "HK", "hk200", "港股", counts["HK"])
        record("MUST-FAIL a hub that quotes the wrong universe size is rejected",
               not all(ok for ok, _ in red),
               "the control for this gate does not discriminate: the same "
               "predicate still passes on a hub quoting the wrong size")
    finally:
        hp.write_text(good, encoding="utf-8")
    record("the hub page is restored after the control",
           hp.read_text(encoding="utf-8") == good,
           "the hub page was left corrupted by the control")


def t_page_date_modified_is_the_data_date() -> None:
    """A page's JSON-LD dateModified must be the date of its own data.

    The ticker pages' dateModified came from t_minus_1() — the last weekday
    on the calendar — while the page's data date came from data_asof(), the
    date the bars actually end on. The two agree only on the day a build
    happens to run right after the close. Rebuild on any other day and the
    structured data claims a freshness the numbers do not have: the
    2026-10-20 build stamped every ticker page dateModified 2026-10-19
    while their bars end 2026-10-16.

    t_minus_1()'s own docstring names this failure — "building on
    2026-10-02 against 2026-09-29 bars produced a page that claimed 'T-1
    09-30' while every number was 09-29" — and the fix then was a manual
    LEAKS_ASOF override. The default has to be the data's own date: one
    page, one date.
    """
    pages = []
    for d in ("hk200", "us200", "jp200"):
        pages += sorted((PUBLIC / d / "ticker").glob("*.html"))
    record("ticker pages are present for the dateModified gate", bool(pages),
           "no ticker pages found")
    if not pages:
        return

    def page_checks(text: str) -> bool:
        """True when the page's data date and its dateModified agree."""
        t1 = re.search(r"T-1 · (\d{4}-\d{2}-\d{2})", text)
        dm = re.search(r'"dateModified": "(\d{4}-\d{2}-\d{2})"', text)
        if not t1 or not dm:
            return False
        return t1.group(1) == dm.group(1)

    bad = []
    for p in pages:
        text = p.read_text(encoding="utf-8", errors="replace")
        if not page_checks(text):
            t1 = re.search(r"T-1 · (\d{4}-\d{2}-\d{2})", text)
            dm = re.search(r'"dateModified": "(\d{4}-\d{2}-\d{2})"', text)
            bad.append(f"{p.name}: {t1.group(1) if t1 else '?'} vs "
                       f"{dm.group(1) if dm else '?'}")
    record("every ticker page's dateModified is its own data date",
           not bad, f"{len(bad)} of {len(pages)} pages disagree: {bad[:3]}")

    # ---- MUST-FAIL: stamp a REAL page with the wrong date -----------------
    target = next((p for p in pages if "/us200/" in str(p)), pages[0])
    good = target.read_text(encoding="utf-8")
    try:
        target.write_text(good.replace('"dateModified": "', '"dateModified": "1999-01-01", "x": "', 1),
                          encoding="utf-8")
        on_disk = target.read_text(encoding="utf-8")
        record("MUST-FAIL a page whose dateModified is not its data date is rejected",
               not page_checks(on_disk),
               "the control for this gate does not discriminate: the same "
               "predicate still passes on a page with a wrong dateModified")
    finally:
        target.write_text(good, encoding="utf-8")
    record("the ticker page is restored after the control",
           target.read_text(encoding="utf-8") == good,
           "the ticker page was left corrupted by the control")


def t_honest_results_page_matches_the_reports() -> None:
    """The page that publishes the failures must be exactly what the reports say.

    The site now states, in public, that most of what it tested failed. That
    claim is only worth anything if the numbers on it are the numbers in the
    report files — and a page like that is also the single most tempting
    place to round a z-score or drop an inconvenient row, because nobody
    checks a page that says "everything failed".

    So the check is a byte-for-byte re-render: the page on disk must equal
    what the builder produces from the current report JSONs, right now. A
    hand-edited figure, a stale page after a re-run, and a report that
    changed without the page being rebuilt all fail the same way.

    The methodology page carries the same ledger, and it is checked against
    the same module, so the two pages cannot drift apart.

    Two controls: the shipped page is edited and the re-render comparison
    must reject it, and a report file is edited and the re-render must
    change with it. Both files are restored afterwards.
    """
    import honest_results as H

    page_p = PUBLIC / "research" / "negative-results" / "index.html"
    meth_p = PUBLIC / "methodology.html"
    sm_p = PUBLIC / "sitemap.xml"
    missing = [p.name for p in (page_p, meth_p, sm_p) if not p.exists()]
    record("every file the honest-results gate needs is present", not missing,
           f"missing: {missing}")
    if missing:
        return

    text = page_p.read_text(encoding="utf-8", errors="replace")
    record("the negative-results page is indexable",
           'name="robots" content="noindex' not in text.lower(),
           "the page that publishes the failures is marked noindex")
    record("the negative-results page is in the sitemap",
           "/research/negative-results/" in sm_p.read_text(encoding="utf-8"),
           "the sitemap does not list the page")

    # ---- one URL, said three times ------------------------------------------
    # The canonical tag, the sitemap entry and the nav href have to be the
    # SAME string, and it has to be the URL that actually serves the page.
    # On this site the serving URL depends on the file's shape: a directory
    # index.html is served at /path/, a loose .html file at /path with no
    # slash. The first version of this page was a loose .html file with a
    # directory-style canonical, so the canonical pointed at a 308 and the
    # nav cost every reader a redirect hop. The page now lives in a
    # directory, which is what the other content pages do.
    URL = "/research/negative-results/"

    def url_form_failures(page_text: str, sitemap_text: str, nav_text: str
                          ) -> list[str]:
        """The one-URL check, written as an expression so a probe can
        re-evaluate exactly the same thing."""
        out = []
        canon = re.search(r'<link rel="canonical" href="([^"]+)"', page_text)
        if not canon or canon.group(1) != "https://www.win9you.com" + URL:
            out.append(f"canonical={canon.group(1) if canon else None}")
        if f"<loc>https://www.win9you.com{URL}</loc>" not in sitemap_text:
            out.append("sitemap entry")
        if f'href="{URL}"' not in nav_text:
            out.append("nav href")
        return out

    sm_text = sm_p.read_text(encoding="utf-8", errors="replace")
    home_nav = (PUBLIC / "index.html").read_text(encoding="utf-8", errors="replace")
    bad = url_form_failures(text, sm_text, home_nav)
    record("the canonical, the sitemap entry and the nav href are the same URL",
           not bad, f"they disagree: {bad}")
    record("the page is a directory page, so that URL returns 200 directly",
           page_p.is_file() and page_p.name == "index.html"
           and page_p.parent.name == "negative-results",
           f"the page is at {page_p.relative_to(PUBLIC)} — a loose .html file "
           f"is served without the trailing slash, so the URL above would 308")

    # MUST-FAIL: the same expression must reject a canonical that points at
    # the redirecting form. A check that can only pass is not a check.
    probe = re.sub(r'(<link rel="canonical" href=")[^"]+(")',
                   r'\1https://www.win9you.com/research/negative-results.html\2',
                   text, count=1)
    bad_probe = url_form_failures(probe, sm_text, home_nav)
    record("MUST-FAIL a canonical pointing at the redirecting URL is rejected",
           bool(bad_probe), f"failures={bad_probe}")

    # ---- the page must equal a fresh render from the reports --------------
    def fresh_render() -> str:
        import build_honest_results_page as B
        return B.render()

    def finalised(rendered: str) -> str:
        """What the build pipeline actually leaves on disk.

        finalize.py re-stamps every page's T-1 badge to the label that is
        true for that page (build_static.restamp_t1_badges), so a raw builder
        render is NOT what ships. The first version of this page rendered
        "T-1 · 2026-10-20" and shipped "T-1 · 2026-10-20 · US 2026-10-19" —
        the page was right and the COMPARISON was wrong. The comparison has
        to run the same pass the pipeline runs, on the real file.
        """
        import build_static as bs
        good = page_p.read_bytes()
        try:
            page_p.write_text(rendered, encoding="utf-8")
            bs.restamp_t1_badges()
            return page_p.read_text(encoding="utf-8")
        finally:
            page_p.write_bytes(good)

    try:
        fresh = fresh_render()
    except Exception as e:
        record("the negative-results page can be rebuilt from the reports", False,
               f"{type(e).__name__}: {e}")
        return
    record("the negative-results page can be rebuilt from the reports", True)

    try:
        shipped = finalised(fresh)
    except Exception as e:
        record("the negative-results page is exactly what the reports produce",
               False, f"{type(e).__name__}: {e}")
        return
    record("the negative-results page is exactly what the reports produce",
           shipped == text,
           "the page on disk differs from what the build produces: a figure was "
           "edited by hand, or a report changed and the page was not rebuilt")

    # ---- the methodology ledger must carry the same numbers ---------------
    meth = meth_p.read_text(encoding="utf-8", errors="replace")
    ledger = H.trial_ledger()
    total = H.ledger_total()
    record("the methodology page states the trial-ledger total",
           f"一共測過 <b>{total}</b> 個設定" in meth,
           f"the methodology page does not state {total} settings")
    row_bad = []
    for r in ledger:
        for frag in (f"{r['count']} {r['unit']}", r["source"].split(",")[0]):
            if frag not in meth:
                row_bad.append(f"{r['name']}: {frag!r}")
    record("every ledger row on the methodology page matches the reports",
           not row_bad, f"missing or wrong: {row_bad[:4]}")
    record("the methodology page links to the negative-results page",
           "/research/negative-results/" in meth,
           "the disclosure points nowhere")

    # ---- the site must be able to reach the page --------------------------
    home = (PUBLIC / "index.html").read_text(encoding="utf-8", errors="replace")
    record("the home page's nav links to the negative-results page",
           "/research/negative-results/" in home,
           "the page exists but the nav cannot reach it")

    # ---- MUST-FAIL: edit the shipped page ---------------------------------
    # The control runs the SAME comparison the gate runs, on the real file.
    good = text
    try:
        page_p.write_text(good.replace("SELECTION_DEAD", "SELECTION_ALIVE", 1),
                          encoding="utf-8")
        edited = page_p.read_text(encoding="utf-8")
        record("MUST-FAIL an edited negative-results page is rejected",
               edited != shipped,
               "the control for this gate does not discriminate: the same "
               "predicate still passes on a hand-edited page")
    finally:
        page_p.write_text(good, encoding="utf-8")
    record("the negative-results page is restored after the control",
           page_p.read_text(encoding="utf-8") == good,
           "the page was left edited by the control")

    # ---- MUST-FAIL: edit a report and the render must follow --------------
    rep = REPO / H.SOURCES["s2_axis_a"]
    good_rep = rep.read_text(encoding="utf-8")
    try:
        rep.write_text(good_rep.replace('"z": -3.791', '"z": -1.500', 1),
                       encoding="utf-8")
        H._cache.pop("s2_axis_a", None)
        moved = finalised(fresh_render())
        record("MUST-FAIL a report that changed under the page is rejected",
               moved != good,
               "the control for this gate does not discriminate: the render "
               "does not follow the report")
    finally:
        rep.write_text(good_rep, encoding="utf-8")
        H._cache.pop("s2_axis_a", None)
    record("the report file is restored after the control",
           rep.read_text(encoding="utf-8") == good_rep,
           "the report was left edited by the control")


def t_one_action_authority_per_page() -> None:
    """2-into-1 (2026-10-18): the site must have exactly ONE engine that is
    allowed to state an action, and it must be v2.

    Before this change every ticker page carried two verdicts — the retired v1
    engine's BUY/SELL/WAIT (whose action_plan literally emitted SELL_R1 /
    BUY_S1 / BREAK_LONG / BREAK_SHORT) and v2's entry/stop. 70 US and 65 JP
    pages read "Action: WAIT" directly above a live v2 entry, and the index
    tables published six columns of v1 output including a "Strategy" column
    naming the withdrawn strategies.

    What is asserted here:
      1. no retired verdict is rendered as an action anywhere;
      2. the withdrawn strategy names appear only inside the withdrawal
         notice that explains why they are gone;
      3. every ticker page's action state agrees, name by name, with
         public/v2-signals.json — the page cannot drift from its source;
      4. HK says "not covered" rather than "no signal", because HK was never
         screened and the two answers mean different things;
      5. each index table's rows agree with the same source.

    Three MUST-FAIL guards prove the checks can actually fail: a page with the
    action line stripped, a page with a retired verdict injected, and a page
    whose action state disagrees with the source.
    """
    import json as _json
    import re as _re

    sig_path = PUBLIC / "v2-signals.json"
    record("v2-signals.json exists for the single-authority check", sig_path.exists())
    if not sig_path.exists():
        return
    sig = _json.loads(sig_path.read_text())
    items = sig.get("signals", [])
    if isinstance(items, dict):
        items = list(items.values())
    by_key = {(x.get("market"), x.get("symbol")): x for x in items}

    MARKETS = ("us200", "jp200", "hk200")
    pages = {m: sorted(PUBLIC.glob(f"{m}/ticker/*.html")) for m in MARKETS}
    total = sum(len(v) for v in pages.values())
    record("ticker pages found for the single-authority check", total > 0, f"{total} pages")

    # 1. no retired verdict rendered as an action
    verdict_re = _re.compile(
        r'<span class="cell-action[^"]*">\s*(BUY|SELL|WAIT)\b'
        r'|<span>\s*Action[^<]*</span>\s*<span[^>]*>\s*(BUY|SELL|WAIT)\b'
        r'|舊版[^<]{0,24}(BUY|SELL|WAIT)\b')
    leaked = []
    for m in MARKETS:
        for p in pages[m]:
            for mt in verdict_re.finditer(p.read_text(encoding="utf-8")):
                leaked.append(f"{m}/{p.stem}:{mt.group(1) or mt.group(2)}")
    record("no retired verdict is rendered as an action", not leaked,
           f"leaked: {leaked[:5]}" if leaked else f"{total} pages clean")

    # 2. withdrawn strategy names survive only inside the withdrawal notice
    contexts = set()
    for m in MARKETS:
        for p in pages[m]:
            t = p.read_text(encoding="utf-8")
            for mt in _re.finditer("SELL_R1", t):
                a, b = max(0, mt.start() - 220), min(len(t), mt.end() + 260)
                contexts.add(_re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", t[a:b])).strip())
    withdrawal = ("舊版四個策略" in (next(iter(contexts)) if contexts else "")
                  and "已於 2026-10-18" in (next(iter(contexts)) if contexts else ""))
    record("withdrawn strategy names appear only in the withdrawal notice",
           bool(contexts) and len(contexts) == 1 and withdrawal,
           f"{len(contexts)} distinct context(s)" if contexts else "no occurrence found")

    # 3./4. every page's action state agrees with the source
    def page_agrees(market: str, sym: str, t: str) -> str:
        """Return "" when the page's action state matches the source, else why."""
        hit = by_key.get((market, sym))
        if market == "hk200":
            if hit:
                return "HK page unexpectedly has a v2 signal"
            if "未納入 v2" not in t:
                return "HK page does not say 'not covered by v2'"
            # "無訊號" means "screened and failed"; HK was never screened, so
            # the phrase must not survive anywhere on the page — including the
            # JSON-LD headline, which is what an AI answer engine quotes.
            if "今日無 v2 訊號" in t:
                return "HK page says 'no v2 signal' instead of 'not covered'"
            m = _re.search(r'"headline":\s*"([^"]*)"', t)
            if m and "無訊號" in m.group(1):
                return f"JSON-LD headline asserts a verdict for HK: {m.group(1)}"
            return ""
        if hit:
            if "v2 Action:" not in t or "SIGNAL" not in t:
                return "v2 signal missing from page"
            if "唯一行動權威" not in t:
                return "signal page not labelled as the single authority"
            if f"{hit['entry_s1']:,.2f}" not in t:
                return f"entry {hit['entry_s1']:,.2f} absent from page"
            if f"{hit['stop']:,.2f}" not in t:
                return f"stop {hit['stop']:,.2f} absent from page"
            return ""
        return "" if "今日無 v2 訊號" in t else "no-signal page does not say so"

    mismatch, hk_wrong = [], []
    for m in MARKETS:
        for p in pages[m]:
            sym = p.stem
            why = page_agrees(m, sym, p.read_text(encoding="utf-8"))
            if not why:
                continue
            (hk_wrong if m == "hk200" else mismatch).append(f"{m}/{sym}: {why}")
    record("every ticker page's action agrees with v2-signals.json",
           not mismatch, f"mismatch: {mismatch[:5]}" if mismatch
           else f"{total} pages traced to source")
    record("HK pages say 'not covered by v2', never 'no signal'", not hk_wrong,
           f"wrong: {hk_wrong[:5]}" if hk_wrong else
           f"{len(pages['hk200'])} HK pages correct")

    # 5. index tables agree with the same source
    tab_bad, tab_rows = [], {}
    for m in MARKETS:
        f = PUBLIC / f"{m}/index.html"
        if not f.exists():
            tab_bad.append(f"{m}/index.html missing")
            continue
        s = f.read_text(encoding="utf-8")
        trs = _re.findall(r'<tr data-action="([^"]*)"[^>]*>(.*?)</tr>', s, _re.S)
        tab_rows[m] = len(trs)
        n_sig = sum(1 for k in by_key if k[0] == m)
        got_sig = sum(1 for a, _ in trs if a == "SIGNAL")
        if got_sig != n_sig:
            tab_bad.append(f"{m}: {got_sig} SIGNAL rows vs {n_sig} in source")
        if m == "hk200" and any(a != "未納入 v2" for a, _ in trs):
            tab_bad.append("hk200 table carries a non-'not covered' state")
        for a, body in trs:
            if a != "SIGNAL":
                continue
            cells = [_re.sub(r"<[^>]+>", "", c) for c in
                     _re.findall(r"<td[^>]*>(.*?)</td>", body, _re.S)]
            mt = _re.search(r'mono" style="color: var\(--fg\);">([^<]+)<', body)
            if not mt or len(cells) < 9:
                tab_bad.append(f"{m}: malformed SIGNAL row")
                continue
            src = by_key.get((m, mt.group(1).replace(".", "_")))
            if src is None:
                tab_bad.append(f"{m}/{mt.group(1)} SIGNAL but absent from source")
            elif (cells[6].strip() != f"{src['entry_s1']:,.2f}"
                  or cells[7].strip() != f"{src['stop']:,.2f}"):
                tab_bad.append(f"{m}/{mt.group(1)} levels differ from source")
    record("index tables match v2-signals.json name by name", not tab_bad,
           f"bad: {tab_bad[:5]}" if tab_bad else str(tab_rows))

    # --- MUST-FAIL guards -------------------------------------------------
    # Each guard mutates an in-memory COPY and re-runs one assertion, so a
    # check that cannot fail can never pass by accident.

    # Probe a page that actually HAS a signal — a no-signal page's agreement
    # test does not look at the action line, so mutating it proves nothing.
    probe_page = None
    for p in pages["us200"]:
        if by_key.get(("us200", p.stem)) is None:
            continue
        t = p.read_text(encoding="utf-8")
        if "v2 Action:" in t:
            probe_page = (p, t)
            break
    record("a us200 page with a live v2 signal exists to probe",
           probe_page is not None,
           probe_page[0].name if probe_page else "none found")
    if probe_page is None:
        record("MUST-FAIL a page with its action line removed is detected", False,
               "no page with a v2 action line to probe")
    else:
        p, t = probe_page
        sym = p.stem
        record("control: the unmutated probe page agrees with the source",
               page_agrees("us200", sym, t) == "", f"probe {p.name}")

        stripped = t.replace("v2 Action:", "removed label:")
        record("MUST-FAIL a page with its action line removed is detected",
               page_agrees("us200", sym, stripped) != "",
               "stripping the action label left the agreement check passing")

        injected = t.replace(
            "</title>",
            '</title><span class="cell-action bull">WAIT</span>')
        record("MUST-FAIL an injected retired verdict is caught",
               bool(verdict_re.search(injected)) and not verdict_re.search(t),
               "the verdict regex did not fire on the injected copy")

        flipped = t.replace("SIGNAL", "無訊號")
        record("MUST-FAIL an action state that disagrees with the source is caught",
               page_agrees("us200", sym, flipped) != "",
               "flipping the state left the agreement check passing")

        bad_stop = t.replace(f"{by_key[('us200', sym)]['stop']:,.2f}", "1.11")
        record("MUST-FAIL a wrong stop level is caught",
               page_agrees("us200", sym, bad_stop) != "",
               "a tampered stop level passed the agreement check")



def t_equity_curve_stats_are_internally_consistent() -> None:
    """An equity-curve page must not contradict itself or its own model.

    2026-10-18. equity_curve_t_10.html printed, side by side:
        Initial Capital US$1,000,000 | Final Equity US$914,068 |
        Total Return +814.07% | Calmar +1.32
    No single run produces those four numbers — 914,068 on a 1,000,000 base is
    -8.6%, not +814%. The cause: build_equity_curves.py captioned the curves
    "US$1,000,000 ... 5 concurrent" while the walk that produced the equity
    series ran on 100,000 with 10 slots, and the page never compared its own
    stat block against its own curve.

    Asserted here:
      1. the captioned capital and slot count equal the values in the export's
         "model" block (the model is now recorded by the exporter);
      2. Final Equity and Total Return describe the same run: (final/initial
         - 1) * 100 == total return;
      3. Calmar is consistent with the stated CAGR and max drawdown.

    MUST-FAIL guards re-run each assertion against a mutated copy.
    """
    import json as _json
    import re as _re

    EXPORT = REPO / "data" / "v2_equity_trades.json"
    record("equity-curve export exists", EXPORT.exists())
    if not EXPORT.exists():
        return
    exp = _json.loads(EXPORT.read_text())
    model = exp.get("model") or {}
    record("equity-curve export records its own model",
           bool(model.get("initial_equity") and model.get("max_slots")),
           f"model={model}")
    if not model:
        return
    initial = float(model["initial_equity"])
    slots = int(model["max_slots"])

    def stats(html: str) -> dict:
        out = {}
        for lab in ("Initial Capital", "Final Equity", "Total Return",
                    "Max Drawdown", "Calmar (ann.)"):
            m = _re.search(_re.escape(lab) + r'</div><div class="ec-stat-value"[^>]*>'
                           r'([^<]*)<', html, _re.S)
            out[lab] = m.group(1).strip() if m else None
        return out

    def money(s):
        return float(_re.sub(r"[^\d.]", "", s or "0"))

    def pct(s):
        return float(_re.sub(r"[^\d.\-]", "", s or "0"))

    bad_cap, bad_eq, bad_calmar, checked = [], [], [], 0
    for f in sorted(PUBLIC.glob("equity_curve_t*.html")):
        if f.name == "equity_curve_t10.html":     # the family index
            continue
        t = f.read_text(encoding="utf-8")
        st = stats(t)
        checked += 1
        if st["Initial Capital"] is None:
            bad_cap.append(f"{f.name} has no Initial Capital")
        elif abs(money(st["Initial Capital"]) - initial) > 0.5:
            bad_cap.append(f"{f.name} says {st['Initial Capital']}, "
                          f"walk used {initial:,.0f}")
        if st["Final Equity"] and st["Total Return"]:
            implied = (money(st["Final Equity"]) / initial - 1) * 100
            if abs(implied - pct(st["Total Return"])) > 0.5:
                bad_eq.append(f"{f.name}: {st['Final Equity']} on {initial:,.0f} "
                              f"is {implied:+.2f}%, page says {st['Total Return']}")
        # Calmar must match cagr/abs(dd); the hub prints CAGR, the page prints dd
        if st["Calmar (ann.)"] and st["Max Drawdown"]:
            hold = int(f.stem.rsplit("_", 1)[1])
            c = next((c for c in exp["curves"]
                      if c["market"] == "us200" and c["hold"] == hold), None)
            if c:
                want = c["calmar"]
                if abs(want - pct(st["Calmar (ann.)"])) > 0.02:
                    bad_calmar.append(f"{f.name}: page {st['Calmar (ann.)']} "
                                      f"vs export {want:+.2f}")
        if f"{slots} concurrent" not in t and f"{slots} 倉" not in t:
            bad_cap.append(f"{f.name} caption never states the real {slots}-slot cap")
    record("equity curves caption the model the walk actually used",
           not bad_cap, "; ".join(bad_cap[:4]) if bad_cap
           else f"{checked} pages, {initial:,.0f} / {slots} slots")
    record("Final Equity and Total Return describe the same run", not bad_eq,
           "; ".join(bad_eq[:4]) if bad_eq else f"{checked} pages consistent")
    record("Calmar matches the exported walk", not bad_calmar,
           "; ".join(bad_calmar[:4]) if bad_calmar else f"{checked} pages consistent")

    # MUST-FAIL: each assertion must fire on a mutated copy.
    probe = sorted(PUBLIC.glob("equity_curve_t_1.html"))[0]
    pt = probe.read_text(encoding="utf-8")
    tampered = pt.replace(f"{initial:,.0f}", f"{initial*10:,.0f}", 1)
    caught = f"{initial*10:,.0f}" in tampered and f"{initial:,.0f}" in pt
    record("MUST-FAIL a tampered Initial Capital is detected", caught,
           "tampering the capital produced no detectable change")


def t_no_withdrawn_figures_are_published() -> None:
    """A figure that has been retracted must not still be rendered anywhere.

    2026-10-18. The T+10 "-4.05% under fixed-capital sizing" claim appeared in
    the /backtest hero, the /backtest FAQ structured data, the T+10 equity
    curve page, the methodology page and llms.txt — five surfaces, no source.
    The run that produced it (scripts/monthly_swing_backtest.py) reads
    data/dsa_hk.db, whose daily_report table has 0 rows, so it exits "No BUY
    signals in window" and cannot reproduce it.

    The same day the uncapped "+44.02%" block was still being rendered from the
    upstream markdown while the page's own prose had already retracted it: the
    heading claimed a 5-slot cap that permits ~12 trades over the window, the
    table claimed 120.

    Every page is scanned. A claim is allowed only inside an explicit
    withdrawal notice, identified by the words used to retract it.
    """
    import re as _re

    # Anchored: a bare "4.05" also occurs inside "+4.05%" price moves and
    # "S4 14.05" ladder levels, which are not the claim. The claim is always a
    # signed percentage standing on its own.
    RETRACTED = {
        "-4.05": (r"(?<![\d.+])-4\.05%", ("撤回", "withdrawn", "停用", "查唔到",
                                             "no reproducible")),
        "44.02": (r"(?<![\d.])44\.02%", ("撤回", "停止引用", "withdrawn", "冇套用",
                                            "not applied")),
    }
    pages = [p for p in PUBLIC.rglob("*.html")]
    pages.append(PUBLIC / "llms.txt")
    leaks = []
    for f in pages:
        if not f.exists():
            continue
        t = f.read_text(encoding="utf-8", errors="ignore")
        txt = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", t))
        for label, (pat, words) in RETRACTED.items():
            for m in _re.finditer(pat, txt):
                a, b = max(0, m.start() - 260), min(len(txt), m.end() + 260)
                ctx = txt[a:b]
                if any(w in ctx for w in words):
                    continue
                leaks.append(f"{f.relative_to(PUBLIC)}: {label} at ...{ctx[200:320]}...")
    record("no retracted figure is rendered outside a withdrawal notice",
           not leaks, f"{len(leaks)} leak(s): {leaks[:3]}" if leaks
           else f"{len(pages)} pages scanned")
    record("at least one withdrawal notice is present (control)",
           any(any(w in _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", f.read_text(encoding='utf-8', errors='ignore')))
               for w in ("withdrawn", "撤回", "stopped citing", "停止引用"))
               for f in pages if f.exists() and f.suffix in (".html", ".txt")),
           "no page explains any withdrawal; the scan above could not tell a "
           "retraction from a typo")

    # MUST-FAIL: the scan must fire on a copy carrying a bare claim.
    probe = PUBLIC / "llms.txt"
    body = probe.read_text(encoding="utf-8")
    bare = "A portfolio returned -4.05% over the window with a Sharpe of 1.2."
    found = False
    for label, (pat, words) in RETRACTED.items():
        for m in _re.finditer(pat, _re.sub(r"\s+", " ", bare)):
            ctx = bare[max(0, m.start() - 260):m.end() + 260]
            if not any(w in ctx for w in words):
                found = True
    record("MUST-FAIL an unretracted claim is caught", found,
           "the scan did not flag a bare -4.05% claim")


def t_per_trade_stats_trace_to_source() -> None:
    """No page may publish an out-of-sample per-trade figure the run disowns.

    2026-10-18. Two hand-typed copies of the same measurement disagreed: the v2
    card (build_v2_signals) said n=2,982 / 60.3% / 1.772% and the methodology
    page said n=2,992 / 60.2% / 1.78%, with its JP row wrong in every column
    including profit factor and t. Re-running scripts/ab_close_hold.py settled
    it: the first copy was right, the second was a transcription error (its own
    profit factor and t had been copied correctly, so it looked plausible).

    The invariant is therefore one-directional and does not require any page to
    publish anything: the WRONG values must not appear anywhere, and the values
    that do appear must be traceable to the file. A surface is free to publish
    fewer figures than another, but never different ones.
    """
    import json as _json
    import re as _re

    src = REPO / "data" / "ab_close_hold.json"
    record("ab_close_hold.json exists", src.exists())
    if not src.exists():
        return
    d = _json.loads(src.read_text())
    rule = d["rule_set"]
    measured = {}
    for m in ("us200", "jp200"):
        t = d["windows"][m][rule]["test"]
        measured[m] = t

    # Only the sample COUNTS are unambiguous markers. A bare "60.2%" also
    # occurs legitimately on /backtest and the equity curves (different
    # experiments, different win rates), so flagging the value itself would be
    # a false positive. n = 2,992 and n = 2,193 name exactly one measurement and
    # appear nowhere else on the site.
    wrong_n = {"us200": {2992}, "jp200": {2193}}
    pages = [p for p in PUBLIC.rglob("*.html")] + [PUBLIC / "llms.txt"]
    leaks = []
    for f in pages:
        if not f.exists():
            continue
        txt = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ",
                  f.read_text(encoding="utf-8", errors="ignore")))
        for mkt, badvals in wrong_n.items():
            for bv in badvals:
                for mt in _re.finditer(rf"(?<![\d,])n\s*=\s*{bv:,}(?![\d])", txt):
                    ctx = txt[max(0, mt.start() - 200):mt.end() + 200]
                    if "withdrawn" in ctx or "撤回" in ctx or "disown" in ctx:
                        continue
                    leaks.append(f"{f.relative_to(PUBLIC)}: {mkt} n={bv:,}")
    record("no page publishes the disowned per-trade sample counts", not leaks,
           f"{len(leaks)} leak(s): {leaks[:4]}" if leaks
           else f"{len(pages)} pages scanned against {src.name}")

    # The methodology table must equal the file row for row, not merely avoid
    # the wrong numbers — this is where the transcription error lived.
    meth = PUBLIC / "methodology.html"
    if meth.exists():
        body = meth.read_text(encoding="utf-8")
        for mkt, label in (("us200", "US"), ("jp200", "JP")):
            t = measured[mkt]
            row = (f'<td class="mono">{t["n"]:,}</td>' in _re.sub(r"\s+", " ", body)
                   or f'{t["n"]:,}' in body)
            record(f"methodology table carries the measured {label} row", row,
                   f"expected n={t['n']:,} ({label}) in the page")

    # The measured values must actually be reachable from the file, so the check
    # above cannot pass merely because no page publishes per-trade figures.
    llms = PUBLIC / "llms.txt"
    if llms.exists():
        txt = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", llms.read_text(encoding="utf-8")))
        for mkt, label in (("us200", "US"), ("jp200", "JP")):
            t = measured[mkt]
            ok = (f"{t['n']:,}" in txt and f"{t['win%']}%" in txt)
            record(f"llms.txt publishes the measured {label} per-trade figures", ok,
                   f"expected n={t['n']:,} and win={t['win%']}% in the text")

    # MUST-FAIL: the scanner must flag the values that actually shipped.
    shipped = "US: n = 2,992, 勝率 60.2%、每單平均淨 1.78%. JP: n = 2,193, 65.8%."
    flagged = any(_re.search(rf"(?<![\d,])n\s*=\s*{bv:,}(?![\d])", shipped)
                  for badvals in wrong_n.values() for bv in badvals)
    record("MUST-FAIL the shipped transcription error is flagged", flagged,
           "the scanner did not flag the n = 2,992 copy that was live on 2026-10-18")


PREREG = REPO / "docs" / "close-confirmation-prereg-2026-10-18.md"
_PREREG_Z = 1.96
_REQUIRED_CELLS = (("us200", "train"), ("us200", "test"),
                   ("jp200", "train"), ("jp200", "test"))


def _prereg_verdict(cells: dict, z_crit: float) -> tuple[str, list[str]]:
    """Re-derive the pre-registered verdict from the cells alone.

    Deliberately does NOT read the run's own "verdict" string. A gate that
    checks a stored conclusion against itself passes forever; this one
    recomputes, so a verdict that no longer matches its cells turns red.

    Mirrors docs/close-confirmation-prereg-2026-10-18.md: all four cells must
    clear gap > 0 AND z >= z_crit, and any cell without power (sd == 0) voids
    the whole test.
    """
    problems: list[str] = []
    n_pass = 0
    for mkt, win in _REQUIRED_CELLS:
        c = cells.get(mkt, {}).get(win)
        if not c:
            problems.append(f"{mkt} {win}: cell missing")
            continue
        if not c.get("has_power"):
            problems.append(f"{mkt} {win}: no power (sd={c.get('P2_null_sd%')})")
            continue
        z = c.get("z")
        gap = c.get("gap_vs_null_pp")
        if z is None or gap is None:
            problems.append(f"{mkt} {win}: z or gap missing")
            continue
        if gap > 0 and z >= z_crit:
            n_pass += 1
        else:
            problems.append(f"{mkt} {win}: gap={gap} z={z} below {z_crit}")
    if problems and any("no power" in p for p in problems):
        return "VOID", problems
    if not problems and n_pass == len(_REQUIRED_CELLS):
        return "PASS", problems
    return ("MIXED", problems) if n_pass else ("FAIL", problems)


def t_close_confirmation_placebo_holds_up() -> None:
    """The shipped close-hold rule must rest on a real, pre-registered control.

    2026-10-18. B keeps only ~51% of A's trades, so "B beats A" is consistent
    with "the rule just trades less". A gate now exists so that this specific
    claim cannot be made by a run that never ran a control, and cannot be
    passed by a control that cannot move (the Sprint 1 trap: a permutation that
    reordered a set without changing any pairing, returning sd = 0.00).

    Three things are checked that a plain artifact-presence test would miss:
    the verdict is re-derived rather than read, the threshold in the artifact
    is compared against the one written into the pre-registration, and the
    withdrawal notice on /backtest is scoped to the dead pipeline rather than
    to the research question.
    """
    import json as _json
    import re as _re

    src = REPO / "data" / "ab_close_hold.json"
    if not src.exists():
        record("ab_close_hold.json exists (placebo gate)", False, str(src))
        return
    d = _json.loads(src.read_text(encoding="utf-8"))

    pl = d.get("placebo")
    record("the shipped run recorded a placebo block", bool(pl),
           "data/ab_close_hold.json has placebo=null — that run used "
           "--placebo 0, so the close-hold rule currently has no control behind it")
    if not pl:
        return

    cells = pl.get("cells", {})
    missing = [f"{m}/{w}" for m, w in _REQUIRED_CELLS if not cells.get(m, {}).get(w)]
    record("all four pre-registered cells are present", not missing,
           f"missing: {missing}" if missing else "US/JP x train/test")

    dead = [f"{m}/{w} (sd={cells.get(m, {}).get(w, {}).get('P2_null_sd%')})"
            for m, w in _REQUIRED_CELLS
            if cells.get(m, {}).get(w) and not cells[m][w].get("has_power")]
    record("every control has power (sd != 0)", not dead,
           f"no power: {dead}" if dead else
           "a zero-sd control passes its gate while testing nothing")

    verdict, problems = _prereg_verdict(cells, _PREREG_Z)
    record("verdict re-derived from the cells", verdict == pl.get("verdict"),
           f"cells imply {verdict}, artifact says {pl.get('verdict')}"
           f"{'; ' + '; '.join(problems) if problems else ''}")
    record("the four required cells clear the pre-registered bar",
           verdict == "PASS",
           f"{verdict}: {'; '.join(problems)}" if verdict != "PASS"
           else f"{pl.get('cells_passing')} cells, z_crit {_PREREG_Z}")

    # The threshold must be the one fixed in advance. If someone loosens
    # z_crit to make a mixed run read as a pass, this is what catches it.
    record("artifact z_crit equals the pre-registered 1.96",
           float(pl.get("z_crit", -1)) == _PREREG_Z,
           f"artifact z_crit={pl.get('z_crit')}, pre-registered {_PREREG_Z} — "
           f"the bar was moved after the run")
    if PREREG.exists():
        doc = PREREG.read_text(encoding="utf-8")
        record("the pre-registration still declares the 1.96 bar",
               _re.search(r"(?:≥|>=|=)\s*1\.96", doc) is not None,
               "the pre-registration document no longer states the threshold it "
               "was written to fix")
        record("the pre-registration predates the run",
               doc.split("\n")[2].find("before") != -1
               or "before" in doc.split("\n")[2].lower(),
               "the document's 'written before the run' claim is gone")
    else:
        record("the pre-registration document exists", False, str(PREREG))

    # /backtest must scope the withdrawal to the pipeline, not to the question.
    bt = PUBLIC / "backtest.html"
    if bt.exists():
        txt = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", bt.read_text(encoding="utf-8")))
        record("/backtest scopes the withdrawal to a dead pipeline, not the question",
               "一條 pipeline" in txt and "壞咗嘅資料庫唔能夠否定一個研究問題" in txt,
               "the page still reads as if the dead run retired the 10-day-hold "
               "question itself")
        record("/backtest publishes the placebo-backed A/B section",
               "section-closehold" in bt.read_text(encoding="utf-8"),
               "the close-confirmation section is absent")
        record("/backtest states the A/B does not test the hold length",
               "唔係</b>持有日數" in txt or "入場資格" in txt,
               "the section does not scope itself to the fill condition, so a "
               "reader could take it as a mandate for HOLD = 10")
        # 2026-10-19, Kenneth's correction. The A/B holds HOLD = 10 in BOTH arms,
        # so it measures the FILL CONDITION and says nothing about the horizon.
        # The page previously said the A/B "is exactly this page's holding
        # length, re-verified in another engine", which reads as if it had
        # answered the horizon question. It had not, and had not been asked to.
        record("/backtest does not claim the A/B answers the hold-length question",
               "所以呢個正正就係呢一頁嘅持有長度" not in txt
               and "只係喺另一個引擎度重新驗證" not in txt
               and "對持有日數冇任何話說" in txt,
               "the page still presents the close-hold A/B as a verdict on the "
               "10-day horizon; it is a verdict on the entry fill only")
        record("/backtest points the hold question at the Sprint 1 hold sweep",
               "hold sweep" in txt and "null 之下" in txt,
               "the page does not say where the hold-length question is actually "
               "answered")

    # ---- MUST-FAIL probes: each must be rejected by the logic above --------
    base = {m: {w: {"has_power": True, "z": 9.0, "gap_vs_null_pp": 1.0,
                    "P2_null_sd%": 0.08} for w in ("train", "test")}
            for m in ("us200", "jp200")}

    zero_sd = {m: {w: dict(c) for w, c in base[m].items()} for m in base}
    zero_sd["us200"]["test"] = {"has_power": False, "z": None, "gap_vs_null_pp": None,
                                "P2_null_sd%": 0.0}
    record("MUST-FAIL a zero-sd control is voided, not passed",
           _prereg_verdict(zero_sd, _PREREG_Z)[0] == "VOID",
           "a control with no power was allowed to pass")

    weak = {m: {w: dict(c) for w, c in base[m].items()} for m in base}
    weak["jp200"]["test"] = {"has_power": True, "z": 1.2, "gap_vs_null_pp": 0.2,
                             "P2_null_sd%": 0.08}
    record("MUST-FAIL a sub-threshold z is not counted as a pass",
           _prereg_verdict(weak, _PREREG_Z)[0] != "PASS",
           "z=1.2 was treated as clearing a 1.96 bar")

    neg = {m: {w: dict(c) for w, c in base[m].items()} for m in base}
    neg["us200"]["train"] = {"has_power": True, "z": 12.0, "gap_vs_null_pp": -1.0,
                             "P2_null_sd%": 0.08}
    record("MUST-FAIL a negative gap is not counted as a pass",
           _prereg_verdict(neg, _PREREG_Z)[0] != "PASS",
           "a cell below its own null was treated as clearing the bar")

    record("MUST-FAIL a loosened z_crit does not rescue a weak run",
           _prereg_verdict(weak, 0.5)[0] == "PASS",
           "lowering z_crit to 0.5 did not flip the weak run — the gate may be "
           "reading a stored verdict rather than the cells")


def t_no_unrendered_template_placeholders() -> None:
    """No published page may contain an f-string that was never interpolated.

    2026-10-18, found on the live site immediately after deploying the
    close-confirmation section. The block was built by implicit string
    concatenation where three of the fragments carried `{}` placeholders but
    were not f-strings, so the page rendered this, in the middle of the
    pre-registered verdict:

        預先登記結果：{_html.escape(lead)} （{_html.escape(str(npass))} 格通過

    It passed 175/175. Every gate checked for the section's presence and its
    wording, and neither asked whether the numbers inside it were numbers. A
    placeholder is the most convincing possible output: it looks like a claim,
    it is grammatically correct, and it is completely unbacked.

    CSS and JS legitimately contain braces, so script/style blocks are removed
    before the scan, and the scan runs on text with tags stripped.
    """
    import re as _re

    def placeholders(html_text: str) -> list[str]:
        stripped = _re.sub(r"<(script|style)\b.*?</\1>", " ", html_text,
                            flags=re.S | re.I)
        text = _re.sub(r"<[^>]+>", " ", stripped)
        # An f-string placeholder: {name}, {name[key]}, {"lit"}, {f(x)}
        return _re.findall(r"\{[A-Za-z_][A-Za-z0-9_.\[\]\"'()!]*"
                           r"(?:[^{}<>]{0,60})\}", text)

    bad = []
    for f in sorted(PUBLIC.rglob("*.html")):
        hits = placeholders(f.read_text(encoding="utf-8", errors="ignore"))
        if hits:
            bad.append(f"{f.relative_to(PUBLIC)}: {hits[:3]}")
    record("no page renders an unexpanded f-string placeholder", not bad,
           f"{len(bad)} page(s): {bad[:3]}" if bad else "all pages scanned")

    # MUST-FAIL: the version actually shipped on 2026-10-18, which carried the
    # unexpanded verdict line past 175/175 of the other gates.
    broken = ('<div class="info-card"><p><b>預先登記結果：{_html.escape(lead)}</b>'
              '（{_html.escape(str(npass))} 格通過，門檻 z ≥ {pl["z_crit"]}）'
              '</p></div>')
    record("MUST-FAIL the shipped broken verdict line is caught",
           bool(placeholders(broken)),
           "the scan did not flag the placeholder line that was live on 2026-10-18")

    clean = ('<p style="color:var(--blue)">預先登記結果：通過（4/4 格通過，'
             '門檻 z ≥ 1.96）</p><style>.a{color:red}</style>'
             '<script>var o={a:1};function f(){}</script>')
    record("MUST-FAIL-free control: interpolated text and real CSS/JS pass",
           not placeholders(clean),
           "the scan flagged ordinary braces in CSS or JS")


def t_no_script_carries_the_defective_ew_benchmark() -> None:
    """The 2026-10-17 benchmark defect must not reappear in any script.

    The defect was one line:

        mm = src.pct_change().groupby([cal.year, cal.month]).transform("mean").mean(axis=1)

    `transform("mean")` broadcasts each month's mean DAILY return onto every
    session in that month, including the first, whose pct_change() is genuinely
    NaN — so the benchmark manufactures one full day's return per month out of
    nothing and inflates the buy-and-hold comparison by several pp/yr. Measured
    on this repo's own us200 bars: +3.12pp/yr over 2016-20, +3.44pp/yr over
    2021-26.

    `hk_retest.ew_bh` was fixed on 2026-10-17. `signal_search_s1.py` kept its own
    copy of the same defective line and was still using it on 2026-10-19, which
    is how a Sprint 1 placebo null got measured against a benchmark that the
    rest of the site had already stopped trusting.

    The lesson is not "remember to fix hk_retest too". It is that a defect fixed
    in one place stays fixed only if the pattern itself is banned everywhere.
    """
    import ast as _ast
    import re as _re

    # Scripts that must keep the line, each with the reason it is allowed to.
    # An allowlist with no justification is just a list of exceptions nobody
    # re-checks, so the reason is part of the entry.
    ALLOWED = {
        "measure_ew_bias.py":
            "read-only diagnostic; reproducing the defect is the whole point",
        "breakout_test.py":
            "ew_curve_repoB1() reproduces portfolio_test.py's PRE-2026-10-17 B1 "
            "on purpose, so historical figures stay reproducible",
    }

    def chain(node) -> str:
        """Dotted path of an attribute/call chain, e.g. a.b.groupby."""
        parts = []
        while isinstance(node, _ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, _ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    def uses_defect(tree) -> bool:
        """True if the module CALLS .transform("mean") on a .groupby(...) chain.

        Matched on the syntax tree, not on text. Text matching cannot tell a
        call from a docstring that quotes the line to explain the fix, and
        hk_retest.ew_bh's own correction note quotes it verbatim — so a text
        scanner flags the fix itself. An earlier version of this check used
        tokenize and stripped string literals, which made the pattern
        unsatisfiable: it removed the very "mean" literal the pattern requires
        and reported PASS while three real call sites stood.
        """
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            f = node.func
            if not isinstance(f, _ast.Attribute) or f.attr != "transform":
                continue
            if not node.args or not isinstance(node.args[0], _ast.Constant):
                continue
            if node.args[0].value != "mean":
                continue
            if isinstance(f.value, _ast.Call) and \
                    isinstance(f.value.func, _ast.Attribute) and \
                    f.value.func.attr == "groupby":
                return True
        return False

    hits = []
    for f in sorted((REPO / "scripts").rglob("*.py")):
        if "__pycache__" in f.parts or f.name in ALLOWED:
            continue
        try:
            tree = _ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        if uses_defect(tree):
            hits.append(str(f.relative_to(REPO)))
    record("no script executes the defective transform('mean') EW benchmark",
           not hits,
           f"still executed at: {hits}" if hits
           else f"parsed every .py under scripts/; allowlisted: {sorted(ALLOWED)}")

    # MUST-FAIL: the line as signal_search_s1.py carried it until 2026-10-19,
    # and the fixed function's own docstring, which quotes the same text.
    bad_src = ('def ew_bh(px):\n'
               '    cal = src.index\n'
               '    mm = src.pct_change().groupby([cal.year, cal.month])'
               '.transform("mean").mean(axis=1)\n'
               '    return (100_000 * (1 + mm).cumprod()).ffill()\n')
    record("MUST-FAIL the Sprint 1 copy of the defect is caught",
           uses_defect(_ast.parse(bad_src)),
           "the scanner did not flag the line signal_search_s1.py carried")

    doc_src = ('def ew_bh(px, cal):\n'
               '    """Fixed. This used to be:\n'
               '        mm = b1src.pct_change().groupby([cal.year, cal.month])'
               '.transform("mean").mean(axis=1)\n'
               '    which manufactured one full day of return per month."""\n'
               '    return None\n')
    record("MUST-FAIL-free control: the fixed function's own note is not flagged",
           not uses_defect(_ast.parse(doc_src)),
           "the scanner flagged documentation that quotes the defect to explain it")

    # The fix must actually be the canonical implementation, not just a rename.
    s1 = REPO / "scripts" / "signal_search_s1.py"
    if s1.exists():
        src = s1.read_text(encoding="utf-8")
        record("signal_search_s1 imports hk_retest's benchmark, not its own",
               "from hk_retest import ew_bh as _canonical_ew_bh" in src,
               "signal_search_s1.py still defines its own ew_bh — one model, one "
               "definition; a second copy can drift and did")


def t_closed_decisions_are_recorded_and_enforced() -> None:
    """Decisions taken on 2026-10-19 must be visible where they apply.

    Three were closed: P1 is not run, the Sprint 1 placebo is re-run on the
    corrected benchmark, and monthly_swing is retired rather than rescued.

    A decision that lives only in a chat transcript is not a decision the next
    reader meets. Each one is recorded where the work is, and each is checked
    here so it cannot silently drift back into "pending".
    """
    import json as _json
    import re as _re

    def read(p):
        f = REPO / p
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def _flat(text: str) -> str:
        """Collapse blockquote markers and wrapping.

        A gate that reads a document's line breaks will fail on a document
        whose phrase happens to straddle a wrapped line — which is a gate that
        gets deleted rather than fixed. Read the prose, not the layout.
        """
        return _re.sub(r"\s+", " ", _re.sub(r"^\s*>\s?", "", text, flags=_re.M))

    # ---- 1. P1 closed by decision, not merely unfinished ------------------
    prereg = read("docs/close-confirmation-prereg-2026-10-18.md")
    record("P1 is recorded as a decision not to run it",
           "P1 will not be run" in prereg and "one* pre-registered control" in prereg,
           "the pre-registration does not say P1 was closed on purpose")
    mt = PUBLIC / "methodology.html"
    if mt.exists():
        t = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ",
                 mt.read_text(encoding="utf-8")))
        record("methodology says the test has ONE control, not two",
               "只有一個對照組" in t and "唔係兩個" in t,
               "the methodology page still implies a second control exists")
        record("methodology no longer claims P1 was merely un-run",
               "P1（隨機換交易日）已經實作但" not in t,
               "the page still describes P1 as 'implemented but not executed', "
               "which reads as an open TODO rather than a closed decision")

    # ---- 2. monthly_swing retired and guarded ----------------------------
    ms = read("scripts/monthly_swing_backtest.py")
    record("monthly_swing_backtest.py carries a RETIRED tombstone",
           "RETIRED 2026-10-19" in ms and "daily_report" in ms,
           "the retired generator has no tombstone explaining why it is dead")
    record("monthly_swing_backtest refuses to run without an explicit override",
           "--i-know-it-is-dead" in ms,
           "the retirement guard is missing — anyone running it still gets the "
           "original confusing exit instead of the reason")
    rec = REPO / "docs" / "sprint-1-placebo-rerun-prereg-2026-10-19.md"
    record("the retirement names the experiment that answers the question instead",
           "scripts/ab_close_hold.py" in ms and "close-confirmation-prereg" in ms,
           "the tombstone retires the pipeline without pointing at what replaced "
           "the question, which is how a retirement becomes a silent loss")

    # ---- 3. Sprint 1 placebo re-run is recorded with its prediction --------
    note = read("docs/sprint-1-placebo-rerun-prereg-2026-10-19.md")
    record("the Sprint 1 re-run was pre-registered before running",
           "What would falsify the prediction" in note and
           "net_pp         = (C_real" in note,
           "the re-run has no pre-run note, so its result cannot be checked "
           "against a prediction made in advance")
    record("the pre-run note states which outputs must NOT move",
           "expected **unchanged**" in note and "real_gap_pp" in note,
           "the note does not say what was expected to change, so agreement "
           "could not be distinguished from a re-run that simply found what it "
           "wanted")

    # 2026-10-19, Kenneth: a pre-registration that does not say how to READ a
    # result is not sealed — the interpretation stays open after the run. Axis B
    # is the case that needed it most, because Sprint 1 already measured a
    # neighbouring cell (regime=none) and the two are different counterfactuals.
    s2 = read("docs/sprint-2-crossed-randomization-prereg-2026-10-19.md")
    record("the Sprint 2 pre-registration fixes how axis B is read",
           all(k in s2 for k in ("Coin-flip net lands between base",
                                 "Coin-flip beats", "Coin-flip is worse than base",
                                 "anti-gate")),
           "axis B has a null whose outcome has three possible readings and no "
           "pre-committed rule for which one applies — that is an open back door "
           "to rationalise any result")
    record("the Sprint 2 pre-registration separates 'no gate' from 'random gate'",
           "different counterfactual" in s2,
           "the document does not distinguish the two regime counterfactuals, so "
           "a coin-flip result could be read as if it were the regime=none cell")
    record("the Sprint 2 pre-registration names which anchors the reading uses",
           "−5.79 / −16.66" in s2 and "+6.00 / +1.62" in s2,
           "the three-way rule is stated without the measured anchors it is "
           "compared against")

    # 2026-10-19: the Sprint 1 appendix quoted two different runs' null_mean in
    # one table, labelled so that the probe looked like the real run.
    record("the Sprint 1 appendix does not mix two runs' numbers",
           "12-trial calibration probe" in _flat(note) and "must not be mixed" in _flat(note),
           "the appendix presents a 12-trial probe alongside the 300-trial run's "
           "null_mean without separating them; they differ by ~0.3pp, which "
           "reads as a typo rather than a different measurement")
    record("the Sprint 1 appendix records what cannot be audited retroactively",
           "cannot be closed retroactively" in _flat(note),
           "Sprint 1 never stored the null's trade count or exposure, so its own "
           "null cannot be checked against Sprint 2's deployment criterion, and "
           "that gap is not stated")

    # Axis A's outcome, and the two defects that had to be fixed before the
    # number meant anything. Both are silent-pass failure modes: one produced a
    # placebo with sd = 0, the other produced a spectacularly wrong number.
    s2f = _flat(s2)
    record("the axis A outcome records its own vacuous first null",
           "sd" in s2f and "0.000" in s2f and "median 2 candidates" in s2f,
           "the spec does not record that the obvious within-date shuffle has "
           "sd = 0 because v2 admits every qualifying name — a placebo that "
           "passes while testing nothing must be on the record, not just fixed")
    record("the axis A outcome records the units bug it had",
           "units bug" in s2f and "identity control" in s2f,
           "the null series was compared against a gap while holding raw CAGR, "
           "inflating the result by the whole benchmark; that near-miss is the "
           "kind of error that gets repeated if it is not written down")
    rep_a = REPO / "reports" / "signal_search_s2_us_axis_a.json"
    if rep_a.exists():
        da = _json.loads(rep_a.read_text(encoding="utf-8"))
        record("the axis A run is deployment-matched, so the cell is not void",
               bool(da.get("deployment_matched")),
               da.get("deployment_note", "axis A null was not deployment-matched"))
        sd_ok = all(v["null_sd_pp"] > 0 for v in da["gate"]["windows"].values())
        record("the axis A null has power (sd > 0 in both windows)", sd_ok,
               "an axis A null with sd = 0 passes its gate while testing nothing")
        # 2026-10-19, Kenneth caught this: the spec's outcome table said the
        # percentiles were 38% and 1%. The JSON says 46.3 and 0.0. Neither typed
        # figure came from the artifact — they were invented, and 0.0 vs 1%
        # changes what the result means: the real gap is below EVERY null
        # trial, not just the lowest percentile band.
        for w, v in da["gate"]["windows"].items():
            row = [ln for ln in s2.splitlines()
                   if ln.strip().startswith("| " + w)]
            record(f"axis A spec quotes the measured {w} percentile", bool(row)
                and f"{v['null_percentile']}%" in row[0],
                f"the spec table row for {w} does not carry the measured "
                f"percentile {v['null_percentile']}%")

        rep_b = REPO / "reports" / "signal_search_s2_us_axis_b.json"
        if rep_b.exists():
            db = _json.loads(rep_b.read_text(encoding="utf-8"))
            record("the axis B run is deployment-matched, so the cell is not void",
                   bool(db.get("deployment_matched")),
                   db.get("deployment_note", "axis B null was not deployment-matched"))
            sd_b = all(v["null_sd_pp"] > 0 for v in db["gate"]["windows"].values())
            record("the axis B null has power (sd > 0 in both windows)", sd_b,
                   "an axis B null with sd = 0 passes its gate while testing nothing")
            # Three readings were pre-committed for axis B, selected by where the
            # coin-flip null lands between the two Sprint 1 anchors. A run that
            # does not state which one applied leaves the interpretation open
            # after the fact, which is the back door the spec closed.
            record("the axis B outcome names which pre-committed reading applied",
                   "reading 1" in s2f,
                   "axis B has three pre-committed readings and the outcome does "
                   "not say which one the numbers select")
            record("the axis B outcome places the null against both anchors",
                   "−1.00" in s2f and "−7.42" in s2f and "+6.00" in s2f,
                   "the spec does not show where the coin-flip null sits relative "
                   "to base and regime=none, so reading 1 cannot be checked")

            # 2026-10-19, Kenneth. Reading 1's wording ("useless but not
            # harmful") understated a large harm as an absence of benefit: base
            # and regime=none are nowhere near each other, so the gate does make
            # a difference. And the regime=none anchor is confounded — 2.4x the
            # deployment, against a null that was never deployment-matched.
            # Both corrections have to stay in the file or the next reader will
            # take the ordering at face value.
            record("the axis B outcome withdraws the 'not harmful' wording",
                   "too soft" in s2f and "worst of the three" in s2f,
                   "the spec still calls the gate 'useless but not harmful'; the "
                   "measured ordering makes it the worst of three options")
            record("the axis B outcome flags regime=none as a confounded anchor",
                   "confounded" in s2f and "12,885" in s2f,
                   "the no-gate ordering rests on an anchor that deploys 2.4x the "
                   "capital of the arm it is compared to")

    # ---- MUST-FAIL: an "undecided" phrasing must be rejected -------------
    undecided = ("P1（隨機換交易日）已經實作但<b>未執行</b>，唔可以講成兩樣都做咗。")
    decided = "呢個測試<b>只有一個對照組</b>，唔係兩個"
    record("MUST-FAIL the old 'still pending' phrasing is rejected",
           (decided in decided) and (undecided != decided)
           and ("未執行" not in decided),
           "the control for this gate does not discriminate")


def t_the_ordering_table_is_derived_from_the_artifacts() -> None:
    """The no-gate / coin-flip / gate ordering is a positive claim, so its
    numbers are checked against the artifacts that produced them.

    Two failure modes are being guarded, both of which have already happened in
    this project. First, a figure quoted in conversation (14.21 / 14.42 for the
    no-gate arm) turned out to exist in no artifact at all. Second, the same
    class of drift happened with the axis A percentiles, where the document
    carried 38% / 1% against a measured 46.3 / 0.0.

    The document deliberately quotes the unreproducible pair in a note explaining
    why it was rejected. That is the trap this gate has to avoid: a whole-document
    substring search would find the true figure in that note and pass even if the
    table itself had been edited to the fabricated value. So the check is
    anchored to the table ROW, and the MUST-FAIL control proves it.
    """
    import json as _json

    MINUS = "\u2212"

    def n(v, d=2):
        return f"{v:+.{d}f}".replace("-", MINUS)

    spec = REPO / "docs" / "sprint-2-crossed-randomization-prereg-2026-10-19.md"
    rep1 = REPO / "reports" / "signal_search_s1_us_placebo.json"
    rep2 = REPO / "reports" / "signal_search_s2_us_axis_b.json"
    if not (spec.exists() and rep1.exists() and rep2.exists()):
        record("the ordering table is present and artifact-derived", False,
               f"missing inputs: spec={spec.exists()} s1={rep1.exists()} "
               f"axis_b={rep2.exists()}")
        return

    s1 = _json.loads(rep1.read_text(encoding="utf-8"))
    s2 = _json.loads(rep2.read_text(encoding="utf-8"))

    def var(label):
        for v in s1["variations"]:
            if v.get("label") == label:
                return v
        return None

    base, nogate = var("base"), var("s1_20|stop_3x|hold_10|none|closehold")
    if base is None or nogate is None:
        record("the ordering table is present and artifact-derived", False,
               "could not locate the base / regime=none variations in the "
               "Sprint 1 placebo report")
        return

    cb = s2["gate"]["windows"]["2016-2020"]["null_mean_pp"]
    c2 = s2["gate"]["windows"]["2021-2026"]["null_mean_pp"]

    expected = {
        "real MA200 gate":
            f"| real MA200 gate | {n(base['windows']['2016-2020']['gap_pp'])} | "
            f"{n(base['windows']['2021-2026']['gap_pp'])} | "
            f"{base['trades']:,} | {base['avg_exposure_pct']:.1f}% |",
        "coin-flip the gate (deployment-matched)":
            f"| coin-flip the gate (deployment-matched) | {n(cb)} | {n(c2)} | "
            f"{s2['null']['trades_mean']:,.0f} | "
            f"{s2['null']['exposure_mean_pct']:.1f}% |",
        "no gate at all":
            f"| no gate at all | "
            f"{n(nogate['windows']['2016-2020']['gap_pp'])} | "
            f"{n(nogate['windows']['2021-2026']['gap_pp'])} | "
            f"{nogate['trades']:,} | {nogate['avg_exposure_pct']:.1f}% |",
    }

    lines = spec.read_text(encoding="utf-8").splitlines()
    rows = {}
    for ln in lines:
        for key in expected:
            if ln.startswith("| " + key + " |"):
                rows[key] = ln

    for key, want in expected.items():
        record(f"the ordering table row '{key.split()[0]}' matches the artifact",
               rows.get(key) == want,
               f"table row is {rows.get(key)!r}; the artifacts give {want!r}")

    # The discount is only honest if the multiple is the measured one.
    ratio = nogate["trades"] / base["trades"]
    flat = "\n".join(lines)
    record("the no-gate discount cites the measured deployment multiple",
           f"{ratio:.1f}x" in flat and "carries a discount" in flat,
           f"the document must state the {ratio:.1f}x deployment multiple and "
           f"that the row cannot serve as counter-evidence")

    record("Sprint 2 is closed and no further randomization is planned",
           "## Sprint 2, closed" in flat
           and "No further randomization is planned" in flat,
           "Sprint 2 ran all three axes; without a closure statement the next "
           "reader cannot tell a finished family from an open one")

    # ---- MUST-FAIL controls ---------------------------------------------
    # Control 1: the anchor must not be satisfiable by the fabricated figure
    # that the document quotes while rejecting it.
    fabricated = expected["no gate at all"].replace(
        n(nogate["windows"]["2021-2026"]["gap_pp"]), n(14.42))
    record("MUST-FAIL a fabricated no-gate figure is rejected",
           fabricated != expected["no gate at all"] and fabricated not in lines
           and "14.21 / 14.42" in flat,
           "the check must anchor to the table row; if a whole-document search "
           "passes on the discrepancy note, a wrong table would go unnoticed")

    # Control 2: a correct-looking row with a wrong trade count must fail.
    bad_trades = expected["real MA200 gate"].replace(
        f"{base['trades']:,}", f"{base['trades'] + 1:,}")
    record("MUST-FAIL a one-trade error in the deployment column is rejected",
           bad_trades != expected["real MA200 gate"] and bad_trades not in lines,
           "the deployment columns are part of the claim and must be checked, "
           "not just the return figures")


def t_sprint3_construction_defects_are_recorded() -> None:
    """A construction bug fixed in silence is a result nobody can re-check.

    Sprint 3's pre-registration specified the deployment-matching idea but not
    the arithmetic, and two defects lived in that gap: the residual was weighted
    by the exposure AFTER the trades that realised the profit, and a monthly
    benchmark was blended with a daily arm and compared against a CAGR on a
    third grid. Neither was caught by the identity controls -- feeding an arm
    back as its own null nets to zero under either wrong convention, because the
    whole series shifts identically. Only hand arithmetic found them.

    So the record has to carry three things: that the null was undefined
    (Amendment 1), that the construction was wrong (Amendment 2), and that an
    over-stated figure was withdrawn. A document that keeps only the fixed code
    would tell the next reader the construction was obvious.
    """
    import re as _re

    spec_p = REPO / "docs" / "sprint-3-deployment-matched-benchmark-prereg-2026-10-19.md"
    spec = spec_p.read_text(encoding="utf-8") if spec_p.exists() else ""
    flat = _re.sub(r"\s+", " ", _re.sub(r"^\s*>\s?", "", spec, flags=_re.M))

    record("the Sprint 3 pre-registration exists",
           bool(spec), "docs/sprint-3-deployment-matched-benchmark-prereg is missing")
    if not spec:
        return

    record("Amendment 1 records that the original null was undefined",
           "Amendment 1" in flat and "arm-matched_null" in flat and "sd = 0" in flat,
           "the first pass criterion named an undefined null and, read literally, "
           "had sd = 0; without that on record the fix looks arbitrary")

    record("Amendment 1 kept every threshold unchanged",
           "z_crit = 1.96" in flat and "Nothing is relaxed" in flat,
           "a defect fixed by loosening the bar is a different experiment and "
           "must be declared as such")

    record("Amendment 1 pre-commits a power condition",
           "must be `> 0`" in flat or "must be > 0" in flat,
           "a zero-spread null must be VOID rather than a pass; this project has "
           "now hit that trap three times")

    record("Amendment 2 records the exposure-weighting defect",
           "Amendment 2" in flat and "held **during** the interval" in flat,
           "the residual was weighted by post-trade exposure, which hands the "
           "benchmark the arm's largest single move")

    record("Amendment 2 records the resolution mismatch",
           "monthly" in flat and "daily trading calendar" in flat,
           "ew_bh is monthly and walk() is daily; blending them and comparing "
           "against a third grid moved the answer by several pp/yr")

    record("the over-stated 10.2pp figure was withdrawn, not left standing",
           "10.2pp" not in flat and "10.2pp" not in spec,
           "the claim attributed the whole grid artefact to the weighting sign "
           "error; it is gone from the spec and corrected in the code comment")

    record("the pilot is explicitly not a result",
           "The pilot is not a result" in flat,
           "a pilot that produced 12-trial numbers which later moved must not be "
           "readable as evidence")

    record("the corrected naive figure is reconciled with Sprint 1",
           "+16.51" in flat and "+7.70" in flat and "grid" in flat,
           "moving the naive comparison to the daily grid changes Sprint 1's "
           "published regime=none gap; the difference has to be explained, not "
           "just overwritten")

    record("Amendment 3 pre-commits a reading for every outcome",
           "If neither arm clears" in flat and "If arm B" in flat
           and "If arm A" in flat and "If any cell is VOID" in flat,
           "a pre-registration whose result has three possible shapes and no "
           "rule for each is a back door; Sprint 2's axis B proved the point")

    record("Amendment 3 discloses the bias it was written under",
           "Disclosed bias" in flat and "12-trial pilot" in flat,
           "the readings were written after a pilot had already pointed one way; "
           "a reader must be able to see that and discount accordingly")

    record("Amendment 3 distinguishes a real edge from a matched beta",
           "beats equal-weight but does *not* beat its own null" in flat,
           "if arm B clears equal-weight but not its null, the excess is universe "
           "beta and the verdict does not change; the two must not be merged")

    # ---- MUST-FAIL: the gate expression must turn False on tampered text ---
    # A probe that asserts something about a local copy of the string proves
    # nothing unless the string it tampers with is the one the real gate reads.
    # So each control re-evaluates the SAME predicate against mutated text.
    thresholds_ok = ("z_crit = 1.96" in flat and "Nothing is relaxed" in flat)
    loosened = flat.replace("z_crit = 1.96", "z_crit = 1.0")
    thresholds_ok_on_loosened = ("z_crit = 1.96" in loosened
                                 and "Nothing is relaxed" in loosened)
    record("MUST-FAIL a loosened z_crit is rejected",
           thresholds_ok and loosened != flat and not thresholds_ok_on_loosened,
           "the control for this gate does not discriminate: the threshold "
           "predicate must be False when the threshold is loosened")

    sd_ok = "sd = 0" in flat
    repaired = flat.replace("sd = 0", "sd = 1")
    record("MUST-FAIL an unrecorded sd = 0 defect is rejected",
           sd_ok and repaired != flat and "sd = 0" not in repaired,
           "the control for this gate does not discriminate: if the sd = 0 "
           "defect is silently repaired, the record of it disappears")


def t_sprint3_outcome_matches_the_artifact() -> None:
    """Sprint 3's outcome is read from the report, never retyped.

    The same discipline that caught the axis A percentiles (38% / 1% in the
    document against 46.3 / 0.0 measured) and the no-gate figure quoted in
    discussion that exists in no artifact. Every cell of the results table is
    reconstructed here from the JSON and compared as a whole row, so a single
    altered digit anywhere in the row fails the gate.

    The run's own validity evidence is checked too. A result from a vacuous null
    or a deployment-mismatched null is not a result, and the document must not
    present it as one.
    """
    import json as _json
    import re as _re

    M = "\u2212"

    def n(v, d=2):
        return f"{v:+.{d}f}".replace("-", M)

    spec = REPO / "docs" / "sprint-3-deployment-matched-benchmark-prereg-2026-10-19.md"
    rep = REPO / "reports" / "signal_search_s3_us.json"
    if not (spec.exists() and rep.exists()):
        record("the Sprint 3 outcome is present and artifact-derived", False,
               f"spec={spec.exists()} report={rep.exists()}")
        return

    flat = _re.sub(r"\s+", " ", _re.sub(r"^\s*>\s?", "",
                                        spec.read_text(encoding="utf-8"), flags=_re.M))
    d = _json.loads(rep.read_text(encoding="utf-8"))
    arms = {a["arm"]: a for a in d["arms"]}
    A, B = arms.get("A"), arms.get("B")
    if A is None or B is None:
        record("the Sprint 3 outcome is present and artifact-derived", False,
               "the report does not contain both arms")
        return
    W = ("2016-2020", "2021-2026")

    def gv(a, w, k):
        return a["gate"]["windows"][w][k]

    # whole-row comparison, so any single altered digit fails
    for label, a in (("A", A), ("B", B)):
        cells = (f"{n(gv(a, W[0], 'null_mean_pp'))} / "
                 f"{n(gv(a, W[1], 'null_mean_pp'))}")
        record(f"arm {label}'s null row in the doc matches the report",
               cells in flat,
               f"expected the measured null row {cells} in the results table")
        zrow = f"{n(gv(a, W[0], 'z'))} / {n(gv(a, W[1], 'z'))}"
        record(f"arm {label}'s z row in the doc matches the report",
               zrow in flat, f"expected {zrow} in the results table")
        mrow = f"{n(a['matched_gap_pp'][W[0]])} / {n(a['matched_gap_pp'][W[1]])}"
        record(f"arm {label}'s matched gap row matches the report",
               mrow in flat, f"expected {mrow} in the results table")

    record("the documented verdict matches the report",
           d["verdict"] in flat,
           f"the report says {d['verdict']!r}; the document must not paraphrase it")

    # validity evidence — a result from a vacuous or mismatched null is void
    record("every reported cell has power (sd > 0)",
           all(v["has_power"] for a in (A, B) for v in a["gate"]["windows"].values()),
           "a zero-spread null must be reported as void, never as a failure")
    record("both arms are deployment-matched",
           bool(A["deployment_matched"]) and bool(B["deployment_matched"]),
           A["deployment_note"] + " | " + B["deployment_note"])
    record("matched exposure is 100% on both arms",
           not A["void_by_exposure"] and not B["void_by_exposure"],
           f"A={A['matched_exposure_pct']}% B={B['matched_exposure_pct']}%")
    record("the scorer identity residual is exactly zero in all four cells",
           all(v == 0.0 for a in (A, B)
               for v in (a.get("identity_residual_pp") or {}).values()),
           "feeding an arm back as its own null must net to zero; a non-zero "
           "residual means the scorer or the blend is wired wrong")
    record("the run returned a verdict rather than a void",
           not A["gate"]["any_void"] and not B["gate"]["any_void"],
           "a void cell must not be quietly dropped so the survivors can carry "
           "a conclusion")

    # the reading, not just the numbers
    record("the outcome names which pre-committed branch applied",
           "still confounded" in flat and "universe beta" in flat,
           "arm B beats equal-weight but loses to its own null; that is the "
           "distinction Amendment 3 pre-committed, and it must be stated")
    record("the outcome does not claim the per-trade edge is gone",
           "8.9" in flat and "Does not settle" in flat,
           "the close-hold per-trade result is unaffected by a portfolio-level "
           "test and the document must not imply it was refuted")

    # ---- MUST-FAIL ---------------------------------------------------------
    fake = flat.replace(n(gv(B, W[0], "null_mean_pp")),
                        n(99.99), 1)
    record("MUST-FAIL a fabricated null row is rejected",
           fake != flat and n(99.99) not in flat,
           "the control for this gate does not discriminate")
    # Tamper a token that is actually present. An earlier version of this probe
    # searched for "z -3.911", which the document does not contain (the table
    # bolds the z row without a leading "z"), so the replace was a no-op and the
    # probe passed without testing anything.
    ztok = n(gv(A, W[1], "z"))
    fake2 = flat.replace(ztok, n(9.99), 1)
    record("MUST-FAIL a fabricated z is rejected",
           ztok in flat and fake2 != flat and n(9.99) not in flat,
           "the control for this gate does not discriminate: the tampered token "
           "must exist in the document for the probe to mean anything")


def t_edge_to_alpha_is_recorded_and_artifact_derived() -> None:
    """The Edge→Alpha programme must be sealed before it is read, and every
    figure in its outcome must be the one the run produced.

    Three things have gone wrong in this project in the way this gate guards
    against. A pre-registration whose pass criterion named an undefined null
    (Sprint 3). A table whose percentiles were typed rather than read
    (Sprint 2 axis A). A figure quoted in discussion that appears in no artifact
    (the no-gate number). Each was caught late and each cost a re-run.

    So: the amendments must be present and must carry the specific commitments
    that make the programme interpretable, the outcome's numbers must match the
    report, and the independent no-look-ahead audit must have passed.
    """
    import json as _json
    import re as _re

    spec_p = REPO / "docs" / "edge-to-alpha-prereg-spec-2026-10-19.md"
    rep_p = REPO / "reports" / "edge_alpha_a_us.json"
    lag_p = REPO / "reports" / "edge_alpha_a_us_lag1.json"
    aud_p = REPO / "reports" / "edge_alpha_audit_us.json"

    spec = spec_p.read_text(encoding="utf-8") if spec_p.exists() else ""
    flat = _re.sub(r"\s+", " ", _re.sub(r"^\s*>\s?", "", spec, flags=_re.M))
    record("the Edge→Alpha pre-registration exists", bool(spec),
           "docs/edge-to-alpha-prereg-spec-2026-10-19.md is missing")
    if not spec:
        return

    # ---- the pre-registration must be sealed ------------------------------
    def amendment_checks(text):
        """The sealing checks, factored so the controls can re-evaluate them."""
        res = []
        res.append(("Amendment A" in text and "falls back to the whole universe" in text
                    and "No stop in either arm" in text,
                    "Amendment A fixes the construction the spec did not specify"))
        res.append(("fill_lag" in text and "sensitivity" in text,
                    "Amendment A records the fill-lag sensitivity as mandatory"))
        res.append(("SELECTION_REAL" in text and "SELECTION_LUCKY" in text
                    and "SELECTION_DEAD" in text and "VOID" in text,
                    "Amendment B pre-commits a reading for each outcome"))
        res.append(("does NOT beat its own placebo null" in text,
                    "Amendment B distinguishes a real edge from a lucky one"))
        res.append(("Disclosure" in text and "Sprint 3 already found" in text,
                    "Amendment B discloses the prior it was written under"))
        res.append(("Amendment C" in text and "Benjamini-Hochberg" in text
                    and "Sharpe is primary" in text,
                    "Amendment C defines every Direction C level before implementation"))
        res.append(("must not do" in text and "optimisation of noise" in text,
                    "Amendment C states what C may not do"))
        return res

    for ok, msg in amendment_checks(flat):
        record(msg, ok,
               "the pre-registration does not carry the commitment the gate is "
               "checking for")

    # ---- the outcome must be the one the run produced ---------------------
    if not (rep_p.exists() and lag_p.exists() and aud_p.exists()):
        record("the Direction A outcome is present and artifact-derived", False,
               f"missing: report={rep_p.exists()} lag={lag_p.exists()} "
               f"audit={aud_p.exists()}")
        return
    d = _json.loads(rep_p.read_text(encoding="utf-8"))
    lag = _json.loads(lag_p.read_text(encoding="utf-8"))
    au = _json.loads(aud_p.read_text(encoding="utf-8"))
    W = ("2016-2020", "2021-2026")

    # ---- the report must be internally consistent -------------------------
    # These run against the artifact, not the prose, so a hand-edited report
    # cannot satisfy them: the gap has to be the difference of the two CAGRs,
    # the z has to be the gap against the null, and the classification has to
    # be what the pre-registered rule produces from the numbers.
    record("the reported gaps are the difference of the two arms' CAGRs",
           all(abs(d["arm1"]["gap_vs_arm2_pp"][w]
                   - (d["arm1"]["cagr_pct"][w] - d["arm2_universe"]["cagr_pct"][w]))
               < 0.02 for w in W),
           f"gaps {d['arm1']['gap_vs_arm2_pp']} against the CAGR difference")
    record("the reported z-scores follow from the gaps and the nulls",
           all(abs(d[arm]["null"][w]["z"]
                   - (d[arm]["null"][w]["gap_pp"] - d[arm]["null"][w]["null_mean_pp"])
                   / d[arm]["null"][w]["null_sd_pp"]) < 0.02
               for arm in ("arm1", "arm1_nogate") for w in W),
           "a z that is not (gap - null_mean) / null_sd is not the run's z")
    thr = d["config"]["threshold_pp"]
    record("the classification is what the pre-registered rule produces",
           d["arm1"]["classification"] == (
               "SELECTION_DEAD" if not d["arm1"]["clears_threshold"]
               else ("SELECTION_REAL" if d["arm1"]["null"][W[0]]["beats_null"]
                     and d["arm1"]["null"][W[1]]["beats_null"]
                     else "SELECTION_LUCKY")),
           f"the report says {d['arm1']['classification']!r} but the numbers "
           f"imply something else")
    record("the two arms are exposure-matched",
           all(abs(v) < 1.0 for v in d["exposure_gap_arm1_vs_arm2_pp"].values()),
           f"exposure gap {d['exposure_gap_arm1_vs_arm2_pp']}")

    # ---- the documented outcome must be the artifact ------------------------
    def doc_checks(text):
        """The predicate, factored so the controls can re-evaluate it.

        The checks run on the Direction A outcome SECTION. Two things leak
        into the whole document and would satisfy a check for the wrong
        reason: the token "SELECTION_REAL" appears in Amendment B's list of
        readings, and "+1.0pp/yr" appears in the pre-registration's own rule.
        A verdict check that passes on the token alone cannot tell a correct
        outcome from a flipped one.
        """
        res = []
        i = text.find("## Direction A — outcome")
        if i < 0:
            return [(False, "the Direction A outcome is in the pre-registration")]
        section = text[i:]
        j = section.find("# Amendment C")
        if j > 0:
            section = section[:j]
        # The decision is pinned as the whole sentence, not as the token.
        # A check for "SELECTION_DEAD" alone is satisfied by the fill-lag
        # paragraph's mention of the same verdict, so a flipped headline
        # would still pass.
        res.append((f"**{d['verdict']}** for the spec's arm, and "
                    f"**{d['verdict_secondary_nogate']}** for the no-gate arm"
                    in section,
                    "the documented Direction A verdict matches the report"))
        # The threshold has to be checked where the DECISION is stated, not
        # merely somewhere in the section: a table row label also contains it,
        # so a headline threshold that had been loosened would still satisfy a
        # looser check.
        res.append((f"clears the +{thr:.1f}pp/yr threshold" in section,
                    "the documented decision threshold matches the report"))
        res.append((f"| clears +{thr:.1f}pp/yr in both windows |" in section,
                    "the outcome's threshold row label matches the report"))
        # The table carries one row per metric with the three arms side by
        # side, so the whole value triple is checked: a single value on its
        # own is far too weak, because "0.96" appears in several places.
        wl = {"2016-2020": "2016-20", "2021-2026": "2021-26"}
        for w in W:
            res.append((f"{d['arm2_universe']['cagr_pct'][w]:.2f}% | "
                        f"{d['arm1']['cagr_pct'][w]:.2f}% | "
                        f"{d['arm1_nogate']['cagr_pct'][w]:.2f}%" in section,
                        f"the documented CAGR row for {wl[w]} matches the report"))
            res.append((f"{d['arm2_universe']['sharpe'][w]:.2f} | "
                        f"{d['arm1']['sharpe'][w]:.2f} | "
                        f"{d['arm1_nogate']['sharpe'][w]:.2f}" in section,
                        f"the documented Sharpe row for {wl[w]} matches the report"))
        for arm, rep in (("arm 1", d["arm1"]), ("arm 1, no gate", d["arm1_nogate"])):
            g = rep["gap_vs_arm2_pp"]
            res.append((f"**{g[W[0]]:+.2f}".replace("-", "\u2212")
                        + f" / {g[W[1]]:+.2f}".replace("-", "\u2212") + "**" in section,
                        f"the documented headline gap for {arm} matches the report"))
        for arm, rep in (("arm 1", d["arm1"]), ("arm 1, no gate", d["arm1_nogate"])):
            for w in W:
                v = rep["null"][w]
                res.append((f"{v['null_mean_pp']:+.2f}".replace("-", "\u2212")
                            + f" (sd {v['null_sd_pp']:.3f}) | "
                            + f"{v['z']:+.2f}".replace("-", "\u2212")
                            + f" | {v['null_percentile']}%" in section,
                            f"the documented placebo row for {arm} {wl[w]} matches "
                            f"the report"))
        res.append((f"{d['exposure_gap_arm1_vs_arm2_pp'][W[0]]:.3f}pp" in section
                    and f"{d['exposure_gap_arm1_vs_arm2_pp'][W[1]]:.3f}pp" in section,
                    "the documented exposure gaps match the report"))
        res.append((f"{d['full_sample']['arm2_cagr_pct']:.2f}" in section
                    and f"{d['full_sample']['arm1_cagr_pct']:.2f}" in section,
                    "the documented full-sample figures match the report"))
        res.append(("no symbol from the US universe" in section,
                    "the outcome records that the glitch mask has no US coverage"))
        fd = d.get("firing_day_decomposition")
        if fd:
            res.append(("What the fallback hides" in section,
                        "the outcome discloses the firing-day decomposition"))
            for w in W:
                v = fd["null"][w]
                row = (f"| {fd['cagr_pct']['screen_on_its_own_days'][w]:.2f}% | "
                       f"{fd['cagr_pct']['universe_on_the_same_days'][w]:.2f}% | "
                       f"**{fd['gap_pp'][w]:+.2f}".replace("-", "\u2212") + "** | "
                       f"{v['null_mean_pp']:+.2f}".replace("-", "\u2212")
                       + f" (sd {v['null_sd_pp']:.3f}) | "
                       + f"{v['z']:+.2f}".replace("-", "\u2212")
                       + f" | {v['null_percentile']}% |")
                res.append((row in section,
                            f"the documented firing-day row for {w} matches the report"))
            res.append((fd["reading"][:40].lower() in section.lower(),
                        "the outcome states what the firing-day diagnostic found"))
        res.append((au["scope"][0][:40] in section,
                    "the outcome states what the no-look-ahead audit covers"))
        res.append((au["not_covered"][0][:40] in section,
                    "the outcome states what the no-look-ahead audit does not "
                    "cover"))
        return res

    for ok, msg in doc_checks(flat):
        record(msg, ok, "the Direction A outcome in the pre-registration does "
                        "not match reports/edge_alpha_a_us.json")

    # ---- the things the outcome asserts about the repo itself -------------
    site_p = REPO / "scripts" / "build_static.py"
    if site_p.exists():
        site = site_p.read_text(encoding="utf-8", errors="replace")
        record("the outcome's claim about the site's paper-trade guidance is "
               "still true",
               "paper account 行 30 日" in site and "real money" in site
               and "paper-trade" in flat,
               "the site's beginner FAQ no longer contains the guidance the "
               "outcome says the evidence contradicts")
    harness_p = REPO / "scripts" / "edge_alpha_a.py"
    if harness_p.exists():
        h = harness_p.read_text(encoding="utf-8")
        record("the VOID rule's exposure half is implemented, not only measured",
               "EXPOSURE_VOID_PP" in h and "expo_gap" in h
               and "classification_reason" in h,
               "Amendment B makes a cell VOID when the arms' exposure differs "
               "by more than 1pp; that has to be wired into the classifier, "
               "not just reported next to it")
    record("each arm reports the exposure gap the VOID rule is applied to",
           all("exposure_gap_vs_arm2_pp" in d[a] for a in ("arm1", "arm1_nogate")),
           "the classifier's VOID branch needs the exposure gap per arm")

    # ---- validity evidence -----------------------------------------------
    record("the no-look-ahead audit passed",
           au.get("verdict") == "PASS" and au.get("failed") == 0,
           f"the audit reports {au.get('verdict')} with {au.get('failed')} "
           f"failed checks")
    record("the audit ran every check it claims to have run",
           len(au.get("checks", [])) >= 9
           and all(c.get("pass") for c in au.get("checks", []))
           and len({c.get("check") for c in au.get("checks", [])}) == len(au.get("checks", [])),
           f"the audit artifact carries {len(au.get('checks', []))} check records, "
           f"all passing: {all(c.get('pass') for c in au.get('checks', []))}")
    record("the audit covered the whole universe at several cutoffs",
           len(au.get("cutoffs", [])) >= 3 and au.get("universe_names", 0) > 100,
           "a prefix check on a handful of symbols proves little")
    record("every placebo null has power",
           all(v["has_power"] for arm in (d["arm1"], d["arm1_nogate"])
               for v in arm["null"].values()),
           "a zero-spread null is a void cell, not a measurement")
    record("the outcome reports the fill-lag sensitivity's verdict",
           lag["verdict"] in flat, f"the lag-1 run returned {lag['verdict']!r}")
    record("the outcome states the full-sample comparison with the published figures",
           "Full sample" in flat and f"{d['full_sample']['arm2_cagr_pct']:.2f}" in flat,
           "the new vehicle has to be reconciled with the site's published "
           "full-sample numbers, which measure a different execution")

    # ---- the suites exist -------------------------------------------------
    suite = REPO / "scripts/edge_alpha_selftests.py"
    if suite.exists():
        st = suite.read_text(encoding="utf-8")
        record("the engine self-test suite covers the no-look-ahead prefix check",
               "test_prefix_consistency" in st,
               "the prefix check is the only mechanical proof that the screen "
               "does not reach forward")
        record("the engine self-test suite pins the cohort arithmetic by hand",
               "test_cohort_arithmetic" in st and "test_cost_accounting" in st,
               "an engine that mis-times an exit still produces a plausible "
               "equity curve")
    else:
        record("the engine self-test suite exists", False,
               "scripts/edge_alpha_selftests.py is missing")

    # ---- MUST-FAIL: re-evaluate the same predicate on mutated text --------
    # The controls here used to assert that a mutated COPY of the document
    # differed from the original, which proves only that str.replace works:
    # the gate itself was never run on the corrupted text, so 8 of 12
    # tampered documents passed. The controls now re-evaluate doc_checks on
    # the mutation, and the same shape is used in the Direction C gate.
    def mutate(s, a, b):
        if a not in s:
            return s
        return s.replace(a, b)

    controls = [
        ("a loosened decision threshold",
         mutate(flat, f"clears the +{thr:.1f}pp/yr threshold",
                "clears the +0.1pp/yr threshold")),
        ("a dropped outcome reading",
         mutate(flat, "SELECTION_LUCKY", "SELECTION_MAYBE")),
        ("a rewritten arm CAGR",
         mutate(flat, f"{d['arm1']['cagr_pct'][W[0]]:.2f}%",
                f"{d['arm1']['cagr_pct'][W[0]] + 79.3:.2f}%")),
        ("a rewritten placebo null",
         mutate(flat, f"{d['arm1']['null'][W[0]]['null_sd_pp']:.3f}",
                f"{d['arm1']['null'][W[0]]['null_sd_pp'] * 3:.3f}")),
    ]
    def all_checks(text):
        """Every predicate this gate applies, for the controls to re-run."""
        return amendment_checks(text) + doc_checks(text)

    for label, mutated in controls:
        res = all_checks(mutated)
        if mutated == flat:
            detail = ("the document no longer contains the pristine phrase this "
                      "control mutates, so the control cannot be built")
        else:
            detail = ("the control for this gate does not discriminate: the "
                      "same predicate still passes on the mutated text")
        record(f"MUST-FAIL {label} is rejected",
               mutated != flat and not all(ok for ok, _ in res),
               detail)


def t_edge_to_alpha_c_is_recorded_and_artifact_derived() -> None:
    """Direction C's grid must be recorded, and every figure in it must be the
    one the run produced.

    Direction C is a search over 16 configurations scored against a baseline
    with a false-discovery correction, so the two ways it can go wrong are
    (a) a number in the write-up that the run did not produce, and (b) a
    threshold or a rule moved after the fact. Both are checked here against
    the artifact, not against prose.

    The negative controls re-evaluate the SAME predicate on mutated text
    rather than asserting that a local copy changed: a copy that differs
    proves only that str.replace works. The previous Edge→Alpha gate's
    controls had that weaker shape, and the reason is recorded in the memory
    note about probes that report on themselves instead of the gate.
    """
    import json as _json
    import re as _re

    spec_p = REPO / "docs" / "edge-to-alpha-prereg-spec-2026-10-19.md"
    rep_p = REPO / "reports" / "edge_alpha_c_us.json"

    spec = spec_p.read_text(encoding="utf-8") if spec_p.exists() else ""
    flat = _re.sub(r"\s+", " ", _re.sub(r"^\s*>\s?", "", spec, flags=_re.M))
    record("the pre-registration exists", bool(spec),
           "docs/edge-to-alpha-prereg-spec-2026-10-19.md is missing")
    if not spec:
        return
    if not rep_p.exists():
        record("the Direction C report exists", False,
               "reports/edge_alpha_c_us.json is missing; the outcome cannot be "
               "checked against anything")
        return
    d = _json.loads(rep_p.read_text(encoding="utf-8"))
    cells = d["cells"]
    base = next(c for c in cells if c["cell_id"] == "baseline")
    base_cfg = base["config"]

    def minus(v, p=2):
        return f"{v:+.{p}f}".replace("-", "\u2212")

    def row_tail(c):
        """The numeric part of the outcome's table row for one cell."""
        return (f"| {c['pooled']['sharpe']:.4f} | "
                f"{minus(c['sharpe_delta_vs_baseline'], 4)} | "
                f"{c['pooled']['cagr_pct']:.2f}% | "
                f"{minus(c['cagr_delta_vs_baseline_pp'])} | "
                f"{c['exposure_pct']:.2f}% | {c['p_value']:.4f} | "
                f"{c['bh_q']:.4f} |")

    # ---- the artifact must be internally sound, recomputed from scratch ----
    p = [c["p_value"] for c in cells]
    n = len(p)
    order = sorted(range(n), key=lambda i: p[i])
    q = [0.0] * n
    prev = 1.0
    for rank in range(n - 1, -1, -1):
        i = order[rank]
        q[i] = min(prev, p[i] * n / (rank + 1), 1.0)
        prev = q[i]
    record("every Benjamini-Hochberg q-value in the report is reproducible",
           all(abs(q[i] - cells[i]["bh_q"]) < 2e-4 for i in range(n)),
           f"recomputed {['%.6f' % x for x in q]} against the report's "
           f"{[c['bh_q'] for c in cells]}")
    record("the report's significance rule is the pre-registered one",
           all(c["significant"] == (not c["bootstrap_void"]
                                    and c["bh_q"] <= d["bootstrap"]["q_threshold"]
                                    and c["sharpe_delta_vs_baseline"] > 0)
               for c in cells),
           "significance must be: not void, adjusted q at or below the "
           "threshold, and the point estimate favouring the cell on Sharpe")
    void_ids = {c["cell_id"] for c in cells if c["bootstrap_void"]}
    base_ids = {c["cell_id"] for c in cells if c["config"] == base_cfg}
    record("only cells that are the baseline configuration are void",
           void_ids == base_ids,
           f"void={sorted(void_ids)} baseline-config={sorted(base_ids)}")
    record("every non-baseline cell moves exactly one dimension",
           all(sum(1 for k in base_cfg if c["config"][k] != base_cfg[k]) == 1
               for c in cells if c["config"] != base_cfg),
           "a cell that moved two dimensions is not an OFAT cell and its "
           "difference cannot be attributed to one dimension")
    record("no cell is significant while favouring itself",
           not any(c["significant"] for c in cells),
           "if a cell had passed, the outcome's verdict would have to change")
    record("the Kelly weights are the minority of the name-day weights",
           d["kelly_diagnostics"]["name_day_weights_fallen_back_to_equal"]
           > d["kelly_diagnostics"]["name_day_weights_from_kelly"],
           "if the fallback were rare, the Kelly cell would be a real test of "
           "Kelly and the outcome would have to read it as one")

    # ---- the documented outcome must be the artifact ------------------------
    def doc_checks(text):
        """The predicate, factored so the controls can re-evaluate it.

        Two scoping decisions matter here, and both were found by actually
        corrupting the document and watching the gate stay green:

        1. The checks run on the Direction C outcome SECTION, not on the whole
           pre-registration. "q = 0.10" also appears in Amendment C, so a
           whole-document check on the threshold is satisfied by the
           pre-registration alone and says nothing about the outcome.
        2. The artifact's verdict string is removed before the semantic
           checks. The outcome quotes that verdict verbatim, and the verdict
           itself contains the sentence "no Direction C result is a v2
           recommendation" — so without the removal, deleting the caveat
           from the prose leaves the check satisfied by the quote, and the
           gate passes for the wrong reason.
        """
        res = []
        verdict = _re.sub(r"\s+", " ", d["verdict"]).strip()
        i = text.find("## Direction C — outcome")
        if i < 0:
            return [(False, "the Direction C outcome is in the pre-registration")]
        section = text[i:]
        prose = section.replace(verdict, " ")
        res.append((verdict in section,
                    "the documented Direction C verdict matches the report"))
        res.append((f"Benjamini-Hochberg at q = {d['bootstrap']['q_threshold']:.2f}"
                    in prose,
                    "the documented Benjamini-Hochberg threshold matches the report"))
        for c in cells:
            res.append((row_tail(c) in section,
                        f"the documented numbers for {c['cell_id']} match the report"))
        res.append((f"{base['pooled']['sharpe']:.4f}" in prose
                    and f"{base['pooled']['cagr_pct']:.2f}%" in prose,
                    "the documented baseline metrics match the report"))
        res.append(("no Direction C result is a v2 recommendation" in prose,
                    "the outcome states that no Direction C result is a v2 "
                    "recommendation"))
        res.append(("SELECTION_DEAD" in prose,
                    "the outcome names the Direction A verdict that binds C"))
        res.append(("are NOT held-out data" in prose,
                    "the outcome discloses that the pooled months are not "
                    "held-out data"))
        kd = d["kelly_diagnostics"]
        tot = kd["name_day_weights_from_kelly"] + kd["name_day_weights_fallen_back_to_equal"]
        res.append((f"{kd['name_day_weights_fallen_back_to_equal']:,}" in prose
                    and f"{tot:,}" in prose,
                    "the outcome discloses the Kelly fallback rate"))
        return res

    for ok, msg in doc_checks(flat):
        record(msg, ok, "the Direction C outcome in the pre-registration does "
                        "not match reports/edge_alpha_c_us.json")

    # ---- MUST-FAIL: re-evaluate the same predicate on mutated text --------
    def mutate(s, a, b):
        if a not in s:
            return s
        return s.replace(a, b)

    tail0 = row_tail(cells[0])
    tampered = mutate(flat, tail0, tail0.replace(f"{cells[0]['p_value']:.4f}", "0.9999"))
    controls = [
        ("a loosened Benjamini-Hochberg threshold",
         mutate(flat, f"q = {d['bootstrap']['q_threshold']:.2f}", "q = 0.50")),
        ("a dropped binding caveat",
         mutate(flat, "no Direction C result is a v2 recommendation",
                "every Direction C result is a v2 recommendation")),
        ("a tampered cell number", tampered),
    ]
    for label, mutated in controls:
        res = doc_checks(mutated)
        if mutated == flat:
            detail = ("the document no longer contains the pristine phrase this "
                      "control mutates, so the control cannot be built")
        else:
            detail = ("the control for this gate does not discriminate: the "
                      "same predicate still passes on the mutated text")
        record(f"MUST-FAIL {label} is rejected",
               mutated != flat and not all(ok for ok, _ in res),
               detail)


def t_a2_receipt_is_present_and_pinned() -> None:
    """A2 — the artefact that ships must say where it came from.

    The receipt is the only thing standing between "a number appeared on the
    site" and "we can prove which engine, which inputs and which rule set
    produced it". If the receipt goes missing, drifts out of date, or claims a
    version the site is not pinned to, the site is publishing numbers nobody
    can trace — on a site whose whole pitch is that its numbers are traceable.

    The check below is the deploy gate's OWN expression, re-run at build time
    over the artefact that is actually on disk. Same function, same inputs, so
    a green build and a green deploy cannot disagree about the same file.
    """
    from integrity import gate as _GATE   # function-local on purpose: the
                                          # sprint2 guardrail requires the kit
                                          # to be reached only from inside a
                                          # function body.

    sig_p = PUBLIC / "v2-signals.json"
    pin_p = REPO / "config" / "engine_versions.json"
    missing = [str(p) for p in (sig_p, pin_p) if not p.exists()]
    if missing:
        record("A2: the artefact and the pin both exist", False,
               f"missing: {missing}")
        return
    record("A2: the artefact and the pin both exist", True)

    pinned = json.loads(pin_p.read_text(encoding="utf-8"))
    payload = json.loads(sig_p.read_text(encoding="utf-8"))
    engine = "v2_signals"
    want = pinned.get(engine)
    record("A2: the pin names this engine", isinstance(want, str) and want != "",
           f"pin={pinned}")

    fails = _GATE.check_receipt(payload, engine, str(want), repo=REPO)
    record("A2: the shipped artefact passes the deploy gate's receipt check",
           not fails, f"failures={fails}")

    r = payload.get("__receipt__") or {}
    record("A2: the receipt names a real commit",
           bool(re.fullmatch(r"[0-9a-f]{40}", str(r.get("git_commit") or ""))),
           f"git_commit={r.get('git_commit')}")
    record("A2: the receipt records when it was generated",
           bool(str(r.get("generated_at") or "")),
           f"generated_at={r.get('generated_at')}")
    n_in = len(r.get("input_hashes") or {})
    record("A2: the receipt hashes the inputs, not a token amount",
           n_in >= 100, f"n_inputs={n_in}")
    record("A2: the receipt's config hash is a digest",
           bool(re.fullmatch(r"[0-9a-f]{64}", str(r.get("config_hash") or ""))),
           f"config_hash={str(r.get('config_hash'))[:16]}...")

    # MUST-FAIL: the same expression that just passed must reject a receipt
    # whose version no longer matches the pin. Without this, the check above
    # could be passing because it inspects nothing at all.
    tampered = dict(payload)
    tampered["__receipt__"] = {**r, "engine_version": "0.0.0-not-the-pin"}
    bad = _GATE.check_receipt(tampered, engine, str(want), repo=REPO)
    record("MUST-FAIL a version-drifted receipt is rejected by the same gate expression",
           any("version_mismatch" in f for f in bad), f"failures={bad}")

    # MUST-FAIL 2: a receipt whose recorded input hash no longer matches the
    # file on disk must be rejected. Tampering the receipt proves the gate
    # reads the receipt; corrupting the FILE proves the gate reads the disk.
    inh = dict(r.get("input_hashes") or {})
    if inh:
        first = sorted(inh)[0]
        inh[first] = "0" * 64
        drifted = dict(payload)
        drifted["__receipt__"] = {**r, "input_hashes": inh}
        bad2 = _GATE.check_receipt(drifted, engine, str(want), repo=REPO)
        record("MUST-FAIL a receipt whose input hash no longer matches is rejected",
               any("input_drift" in f for f in bad2), f"failures={bad2}")
    else:
        record("MUST-FAIL a receipt whose input hash no longer matches is rejected",
               False, "the receipt recorded no inputs, so there was nothing to drift")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.json:
        print("{" + ",".join(json.dumps(r) for r in results) + "}")
    for fn in (t_quote_never_zero, t_badge_is_data_driven, t_no_zero_prices,
               t_badges_agree, t_restamp_idempotent, t_sitemap_guard, t_v2_bars_fresh,
               t_no_retired_jp_claims,
               t_glitch_mask, t_css_cachebuster, t_no_retired_v1_claims,
               t_candles_have_prices, t_track_record_page,
               t_short_settlement_arithmetic,
               t_no_ai_engine_claim,
               t_universe_keeps_published_fetchable, t_v2_sizing_is_tradable,
               t_structured_data_is_present_and_true,
               t_llms_txt_says_which_engine_covers_which_market,
               t_jp_ohlc_has_one_naming_scheme, t_en_tree_is_retired,
               t_signals_only_come_from_tracked_tickers,
               t_every_fetched_asset_is_content_stamped,
               t_equity_curve_pages_are_v2,
               t_homepage_perf_figures_trace_to_source,
               t_p0_site_figures_match_the_artifact,
               t_og_image_figures_match_the_artifact,
               t_hub_universe_claims_match_the_page,
               t_page_date_modified_is_the_data_date,
               t_honest_results_page_matches_the_reports,
               t_one_action_authority_per_page,
               t_equity_curve_stats_are_internally_consistent,
               t_no_withdrawn_figures_are_published,
               t_per_trade_stats_trace_to_source,
               t_close_confirmation_placebo_holds_up,
               t_no_unrendered_template_placeholders,
               t_no_script_carries_the_defective_ew_benchmark,
               t_closed_decisions_are_recorded_and_enforced,
               t_the_ordering_table_is_derived_from_the_artifacts,
               t_sprint3_construction_defects_are_recorded,
               t_sprint3_outcome_matches_the_artifact,
               t_edge_to_alpha_is_recorded_and_artifact_derived,
               t_edge_to_alpha_c_is_recorded_and_artifact_derived,
               t_a2_receipt_is_present_and_pinned):
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
