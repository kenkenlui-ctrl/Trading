"""patch_i18n_nav3.py — backtest variant: single-line <nav class="leeks-site-nav">."""
import re
from pathlib import Path

p = Path('/Users/kenken/dev/dsa-hk/public/en/backtest/index.html')
s = p.read_text(encoding='utf-8')
if 'class="lang-switch"' in s:
    print('already patched')
else:
    # The leeks-site-nav has 5 elements. Insert lang switch after last </a>.
    # Pattern: ends with `<a href="/methodology.html">Methodology</a></nav>`
    new = s.replace(
        '<a href="/methodology.html">Methodology</a></nav>',
        '<a href="/methodology.html">Methodology</a>'
        '<a class="lang-switch" href="/backtest.html" title="切換至繁體中文" aria-label="切換至繁體中文">中</a>'
        '</nav>',
        1,
    )
    if new != s:
        p.write_text(new, encoding='utf-8')
        print('patched')
    else:
        print('pattern not found, no change')
