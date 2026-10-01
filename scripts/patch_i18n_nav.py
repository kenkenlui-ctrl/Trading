"""patch_i18n_nav.py — Add EN↔ZH lang switch to /en/ pages (and rebuild /zh/ home with switch)."""
from pathlib import Path

# Build i18n nav patches. For each en/ file, add a lang switch that
# points back to the zh base URL (since user is currently on en/).
# For each zh home, build_home.py already added switch (handled separately).

EN_DIR = Path('/Users/kenken/dev/dsa-hk/public/en')

# en/* URL → corresponding zh URL
en_to_zh = {
    'index/index.html':        '/',
    'methodology/index.html':  '/methodology.html',
    'backtest/index.html':     '/backtest.html',
    'insights/index.html':     '/insights',
    'disclaimer/index.html':   '/disclaimer',
    'privacy/index.html':      '/privacy',
}

for en_path, zh_url in en_to_zh.items():
    p = EN_DIR / en_path
    if not p.exists():
        print(f"  en/{en_path} — MISSING, skip")
        continue
    s = p.read_text(encoding='utf-8')
    # Skip if already patched
    if 'class="lang-switch"' in s:
        print(f"  en/{en_path} — already patched")
        continue
    # Find <div class="nav-links"> ... </div> close, then add lang switch
    # Detect both build_home.py pattern (with home / methodology active markers)
    # and build_dashboard.py pattern. Insert AFTER </div> of nav-links.
    import re
    m = re.search(r'(<div class="nav-links">.*?</div>)', s, re.S)
    if not m:
        print(f"  en/{en_path} — no nav-links div found, skip")
        continue
    insertion = (
        f'\n    <a class="lang-switch" href="{zh_url}" title="切換至繁體中文" '
        f'aria-label="切換至繁體中文">中</a>'
    )
    s = s[:m.end()] + insertion + s[m.end():]
    p.write_text(s, encoding='utf-8')
    print(f"  en/{en_path} — added lang switch to {zh_url}")
