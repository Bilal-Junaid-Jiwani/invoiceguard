# InvoiceGuard — website + docs (static)

GitHub Pages-ready static site. Pure HTML/CSS + minimal vanilla JS. No frameworks, no build step, no external requests.

## Pages

- `index.html` — landing: pain stats → 3 steps → screenshot placeholders → wedge → quickstart → ROI/pricing → managed-hosting note → CTA
- `docs.html` — installation, Stripe setup, SMTP setup, contract & dunning config, CLI reference, FAQ, limits of v1, roadmap
- `changelog.html` — v0.1.0 entry (2026-09-30)
- `404.html` — GitHub Pages not-found page
- `.nojekyll` — present (empty), so Pages serves files as-is

## Assets

- `assets/css/style.css` — dark dev-tool theme, responsive (breakpoints 960 / 860 / 560)
- `assets/js/main.js` — mobile nav toggle, copy buttons, footer year, docs scrollspy
- `assets/img/logo.svg` — shield+check mark

## Placeholder vs final

- **FINAL:** all copy (written strictly from the product-facts brief — stats cited with source names: Kaplan Group April 2026; Freelancers Union 2022), quickstart commands, layout, CSS, JS, logo.
- **FINAL (QA swap, 2026-09-30):** the two screenshot frames on `index.html` ("See it in action") now hold real 16:10 captures of the integrated dashboard (`assets/img/dashboard-list.png`, `assets/img/dashboard-detail.png`), seeded demo data, zero layout shift.
- **CONFIRMED:** GitHub links point to the live repo `https://github.com/Bilal-Junaid-Jiwani/invoiceguard` (public). Site deployed at `https://bilal-junaid-jiwani.github.io/invoiceguard/`. All other links are relative.

## Honesty checklist (verified while writing)

- No testimonials, no client logos, no quotes — none on any page.
- No invented metrics: no stars, forks, user counts, or download numbers anywhere.
- Stats carry their source names inline (Kaplan Group report April 2026; Freelancers Union 2022 survey, NY with writer & creative guilds).
- Managed hosting is "coming soon" with explicit "no waitlist, no launch date, no prices yet" wording; the only price mentioned is the planned flat $15–29/mo, framed as planned.
- v1 limits stated plainly: acknowledgment checkbox (not e-signature), no built-in mail service, single-machine SQLite, email-only dunning.
- "Not legal advice" callout on the contract section.

## Deploy

Push the contents of this directory to the repo root (or `docs/`), enable GitHub Pages. Relative links throughout; nothing assumes a subpath.
