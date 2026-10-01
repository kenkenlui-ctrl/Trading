"""patch_i18n_nav2.py — Add lang switch to en/ pages with simple <a> nav structure."""
import re
from pathlib import Path

EN_DIR = Path('/Users/kenken/dev/dsa-hk/public/en')

# en/* URL → corresponding zh URL
en_to_zh = {
    'backtest/index.html':     '/backtest.html',
    'disclaimer/index.html':   '/disclaimer',
    'privacy/index.html':      '/privacy',
}

# These pages use simple <nav class="nav"> with direct <a> children
# (no nav-links div). Insert a new <a class="lang-switch"> after the
# <nav> block's existing <a> tags.
for en_path, zh_url in en_to_zh.items():
    p = EN_DIR / en_path
    if not p.exists():
        print(f"  en/{en_path} — MISSING")
        continue
    s = p.read_text(encoding='utf-8')
    if 'class="lang-switch"' in s:
        print(f"  en/{en_path} — already patched")
        continue
    # Find <nav class="nav">...</nav> block
    m = re.search(r'(<nav class="nav"[^>]*>)(.*?)(</nav>)', s, re.S)
    if not m:
        print(f"  en/{en_path} — no <nav> found, skip")
        continue
    # Append lang switch after last </a> inside nav
    insertion = f'\n  <a class="lang-switch" href="{zh_url}" title="切換至繁體中文" aria-label="切換至繁體中文">中</a>'
    s = s[:m.end()-len(m.group(3))] + m.group(2) + insertion + m.group(3) + s[m.end():]
    p.write_text(s, encoding='utf-8')
    print(f"  en/{en_path} — added lang switch to {zh_url}")
