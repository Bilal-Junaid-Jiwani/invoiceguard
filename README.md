# InvoiceGuard — website + docs (static, published to GitHub Pages)

This branch is the published output. Source lives on `main` in `docs/` and `site/`.

## Structure

- `/` — documentation site home (title "invoiceguard docs"): 13 pages built from
  `docs/src/*.md` by `docs/build.py` (ledger-paper theme, client-side search,
  sitemap, llms.txt)
- `/site/` — marketing website (distinct design, real dashboard screenshots)
- `404.html` — themed not-found page
- `.nojekyll` — present (empty), so Pages serves files as-is

## Rebuilding

```bash
cd docs && python3 build.py   # regenerates *.html, sitemap.xml, llms.txt, search index
```

Then publish this branch's root from the rebuilt `docs/` output and `/site/`
from the `site/` source.
