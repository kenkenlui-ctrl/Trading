"""build_i18n.py — Full EN subpath build via SITE_LANG=en.

Strategy: For each public-facing page, run the appropriate build script with
SITE_LANG=en to produce the English version, then copy to public/en/<subpath>/.

The build scripts already support SITE_LANG=en:
- build_dashboard.py: hub + ticker detail pages (T() + _STRINGS)
- build_home.py: home page (T() + _STRINGS)
- build_backtest_page.py: backtest page (uses build_static.shell with T())
- build_static.py: static info pages (disclaimer, privacy, methodology, etc.)

This script:
1. Runs each build with SITE_LANG=en → outputs to public/ (overwrites zh)
2. After all EN versions generated, copies zh versions back to public/
3. Copies EN versions to public/en/ subpath
4. Restores zh versions in public/ as default

Charts (png) and ticker JSON stay at root — language-neutral data.
"""
from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/kenken/dev/dsa-hk")
PUB = ROOT / "public"
EN = PUB / "en"
REPO = ROOT

# Pages to build with SITE_LANG=en, output to public/en/<subpath>/
# Tuple: (zh_source_page, en_subpath, build_command)
# build_command runs from REPO with cwd=REPO and uses SITE_LANG=en
PAGES = [
    # Home page → /en/
    ("index.html", "index", ["python3", "scripts/build_home.py"]),
    # Hub + ticker detail (HK only for EN; US is skipped — not enough EN content)
    ("hk200/index.html", "hk200", ["python3", "scripts/build_dashboard.py", "HK"]),
    # Backtest page → /en/backtest/
    ("backtest.html", "backtest", ["python3", "scripts/build_backtest_page.py"]),
    # Static info pages — single call generates all (disclaimer, privacy, methodology, insights, intent pages)
    # The build_static script also writes more pages; we just copy the ones we need
]

# Static pages to extract from build_static.py output
STATIC_PAGES = [
    ("disclaimer.html", "disclaimer"),
    ("privacy.html", "privacy"),
    ("methodology.html", "methodology"),
    ("insights.html", "insights"),
]


def run(cmd: list[str], env_extra: dict) -> int:
    """Run a build command with SITE_LANG=en."""
    import os
    env = os.environ.copy()
    env["SITE_LANG"] = "en"
    env.update(env_extra)
    print(f"  $ {' '.join(cmd)}  (SITE_LANG=en)")
    r = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ✗ FAILED: {r.stderr[-500:]}")
    return r.returncode


def restore_zh(zh_files: list[Path]) -> None:
    """Restore zh versions from backup."""
    for src in zh_files:
        bak = src.with_suffix(src.suffix + ".zh")
        if bak.exists():
            shutil.copy(bak, src)
            bak.unlink()


def backup_zh(zh_files: list[Path]) -> None:
    """Backup zh versions before overwriting."""
    for src in zh_files:
        bak = src.with_suffix(src.suffix + ".zh")
        if src.exists() and not bak.exists():
            shutil.copy(src, bak)


def main():
    print(f"=== Full EN build via SITE_LANG=en ===\n")

    # 1) Backup zh versions
    zh_sources = []
    for src_path, subpath, _ in PAGES:
        full = PUB / src_path
        if full.exists():
            zh_sources.append(full)
    backup_zh(zh_sources)
    print(f"Backed up {len(zh_sources)} zh source files")

    # 2) Run all builds with SITE_LANG=en (overwrites public/* with EN)
    print("\n[1/3] Running build scripts with SITE_LANG=en...")
    # Run each unique build command once
    seen_cmds = set()
    for src_path, subpath, cmd in PAGES:
        if not (REPO / "scripts" / Path(cmd[1]).name).exists():
            print(f"  ! skip {src_path} (script not found)")
            continue
        cmd_key = tuple(cmd)
        if cmd_key in seen_cmds:
            continue
        seen_cmds.add(cmd_key)
        rc = run(cmd, {})
        if rc != 0:
            print(f"  ✗ build failed for {src_path}")
    # Also run build_static.py once to generate all static pages
    static_cmd = ("python3", "scripts/build_static.py")
    if static_cmd not in seen_cmds:
        if (REPO / "scripts" / "build_static.py").exists():
            rc = run(list(static_cmd), {})
            if rc != 0:
                print(f"  ✗ build_static.py failed")

    # 3) Copy EN versions to /en/ subpath
    print("\n[2/3] Copying EN versions to /en/ subpath...")
    EN.mkdir(parents=True, exist_ok=True)
    for src_path, subpath, _ in PAGES:
        src = PUB / src_path
        if not src.exists():
            print(f"  ! {src_path} not built, skip")
            continue
        target_dir = EN / subpath
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "index.html"
        shutil.copy(src, target)
        print(f"  {src_path} → {target.relative_to(PUB)} ({target.stat().st_size:,} B)")
    # Also copy static pages
    for src_path, subpath in STATIC_PAGES:
        src = PUB / src_path
        if not src.exists():
            print(f"  ! {src_path} not built, skip")
            continue
        target_dir = EN / subpath
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "index.html"
        # 2026-08-30: only copy if /en/ target doesn't exist yet (preserve
        # hand-edited EN content). After first run, the /en/ file is stable.
        if not target.exists():
            shutil.copy(src, target)
            print(f"  {src_path} → {target.relative_to(PUB)} ({target.stat().st_size:,} B) [new]")
        else:
            print(f"  {src_path} → {target.relative_to(PUB)} [skip, /en/ already hand-edited]")

    # 4) Restore zh versions in /public/
    print("\n[3/3] Restoring zh versions in /public/...")
    restore_zh(zh_sources)
    print(f"Restored {len(zh_sources)} files")

    print("\n✓ Full EN build complete. /en/ subpath now serves English.")


if __name__ == "__main__":
    main()
