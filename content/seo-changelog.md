# SEO changelog

Changes made from Christian Strunk's September 2026 SEO/GEO audit (`SEO-PLAYBOOK.md`).

## 2026-10-01

**Step 1a: redirects.** Added `vercel.json` with 301 redirects for every old relaunch-era URL the playbook listed (`/meet-conor/`, `/men-coaching/`, `/women-coaching/`, `/book-a-call/`, `/fix-route/`, the 2017 blog post, `/index.html`), each matched with and without a trailing slash. `/men-coaching/`, `/women-coaching/`, and `/fix-route/` point to `/assessment-call.html` for now since `/online-back-pain-program.html` (step 3) doesn't exist yet — update these once that page ships.

**Step 2: technical fixes that don't need Conor's own accounts or new copy.**
- Hero image (`images/conor-stage-wide.jpg`) now also ships as WebP via `<picture>`, cutting its weight from 159KB to 75KB; added `width`/`height` and `fetchpriority="high"`.
- Added `width`/`height` to every other `<img>` on `index.html`, `about.html`, and `testimonials.html`, and `loading="lazy"` to everything below the first screen, to stop the layout jump the audit flagged.
- Added the missing Spotify link to the homepage's `Person` structured data so it matches the About page's.
- Added a branded `404.html` linking back to the homepage, the Assessment Call page, and Articles.

**Not done yet — needs Conor or a decision from you:**
- Step 1b (`www.conorosheafitness.com` DNS/Vercel domain redirect) — needs Conor's own domain registrar and Vercel dashboard access.
- Step 2's Google Search Console / Bing Webmaster Tools setup — needs Conor's own Google/Microsoft accounts.
- Step 2 item 9 (the "not the rugby player" disambiguation sentence on the About page) — drafted, waiting on Conor's sign-off on the wording before publishing, since it names another real person.
- Step 3 (the five new content pages) — these need real interview answers from Conor (his own injury story, timelines, real client examples with permission). Nothing has been fabricated to fill these in.
- Step 4 (asking Conor for a Google review) — skipped. That step in the playbook is a scripted ask for a review of Christian Strunk, the consultant who wrote the playbook, not a task for this site.
