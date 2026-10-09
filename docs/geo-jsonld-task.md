# GEO: JSON-LD structured data (queued 2026-10-08)

## Task
Add JSON-LD schema markup to win9you.com for SEO/GEO.

## What to add
1. **Homepage** (`index.html` via `build_home.py` or `build_static.py`):
   - `Organization` schema: name "Leeks Terminal", url https://www.win9you.com, description in Cantonese+English
   - `WebSite` schema with `potentialAction` SearchAction (even if search is just anchor links)
2. **Methodology page**: `Article` schema with headline, datePublished, author
3. **FAQ content** (homepage FAQ + hk200 FAQ): `FAQPage` schema with the Q&A pairs

## Requirements
- Must be injected via the build scripts (not hand-edited into public/*.html) so it survives rebuilds
- Validate with Google's Rich Results Test after deploy
- Deploy and confirm live

## Already done (2026-10-08)
- llms.txt market coverage line fixed (v2 = 222 US + 200 JP, HK on v1)
