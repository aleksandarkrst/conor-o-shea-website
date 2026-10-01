#!/usr/bin/env python3
"""Regenerate the article list in articles.html from a Substack RSS feed,
and generate a full-text on-site page per article under articles/.

Run manually with:  SUBSTACK_FEED_URL=https://yourname.substack.com/feed python3 scripts/sync_substack_articles.py

Substack's edge blocks direct fetches from GitHub Actions' runner IPs
(confirmed: the feed loads fine in a browser, but 403s every direct
request from CI regardless of headers). To work around that, a direct
fetch is tried first and, on any failure, falls back to rss2json.com's
public RSS-to-JSON proxy, which fetches the feed from its own
infrastructure and hands back parsed JSON.

The RSS feed's <content:encoded> field already carries each post's full
HTML body, not just a summary - previously this was only used to compute
a read-time estimate and then discarded. Now it's also used to render a
real, full-text page for each article at articles/<slug>.html, so AI
crawlers and search engines can read the actual writing on this domain
instead of only ever seeing a title + "Read on Substack" link. Each page
is self-canonical (not pointing back to Substack) since the goal is for
this domain to get credit for the content, with a visible "originally
published on Substack" link for anyone who wants to read/comment there.

Only posts currently in the RSS feed window get a page generated on any
given run (Substack's feed is not a full historical archive) - pages for
older posts that scroll out of the feed are never deleted once created,
so their URLs keep working indefinitely.
"""

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

FEED_URL = os.environ.get("SUBSTACK_FEED_URL", "")
RSS2JSON_API_KEY = os.environ.get("RSS2JSON_API_KEY", "")
REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
ARTICLES_HTML = os.path.join(REPO_ROOT, "articles.html")
SITEMAP_XML = os.path.join(REPO_ROOT, "sitemap.xml")
ARTICLES_DIR = os.path.join(REPO_ROOT, "articles")
START_MARKER = "<!-- SUBSTACK_ARTICLES_START -->"
END_MARKER = "<!-- SUBSTACK_ARTICLES_END -->"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
WORDS_PER_MINUTE = 200
SITE_URL = "https://conorosheafitness.com"
DEFAULT_OG_IMAGE = f"{SITE_URL}/images/conor-stage-wide.jpg"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def normalize_feed_url(url):
    url = url.strip().rstrip("/")
    if not url:
        return url
    if url.endswith("/feed") or url.endswith(".xml") or "/feed?" in url:
        return url
    return f"{url}/feed"


def strip_tags(text):
    return re.sub(r"<[^>]+>", " ", text or "")


def read_minutes(content):
    words = strip_tags(content).split()
    return max(1, round(len(words) / WORDS_PER_MINUTE))


def slug_from_link(link):
    """Extract the Substack post slug from its URL path, e.g.
    https://conorosheafit.substack.com/p/some-post -> "some-post".
    Substack already guarantees these are unique (appending a random
    suffix on collision), so the slug is safe to use as a filename.
    """
    path = urllib.parse.urlparse(link.strip()).path
    return path.rstrip("/").split("/")[-1]


def excerpt(content_html, limit=160):
    text = strip_tags(content_html)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"


def first_image_url(content_html):
    m = re.search(r'<img[^>]+src="([^"]+)"', content_html)
    return m.group(1) if m else None


def sanitize_content(content_html):
    """Defense in depth: strip script/style tags before embedding
    Substack's content HTML, even though Substack's own editor output
    doesn't normally include them.
    """
    cleaned = re.sub(r"<script.*?</script>", "", content_html, flags=re.S | re.I)
    cleaned = re.sub(r"<style.*?</style>", "", cleaned, flags=re.S | re.I)
    return cleaned


def build_featured_card(item):
    title = html.escape(item["title"].strip())
    slug = slug_from_link(item["link"])
    date = item["pub_dt"].strftime("%d %b %Y")
    iso_date = item["pub_dt"].strftime("%Y-%m-%d")
    minutes = read_minutes(item["content"])
    return (
        '<section class="articles-featured-section">\n'
        '  <div class="wrap">\n'
        "    <article>\n"
        f'      <a class="article-featured" href="articles/{slug}.html">\n'
        '        <p class="article-featured-eyebrow">Latest post</p>\n'
        f'        <h2 class="article-featured-title">{title}</h2>\n'
        '        <div class="article-featured-meta">\n'
        f'          <time class="article-featured-date" datetime="{iso_date}">{date}</time>\n'
        '          <span class="article-featured-dot"></span>\n'
        f'          <span class="article-featured-read">{minutes} min read</span>\n'
        '          <span class="article-featured-link">Read article &rarr;</span>\n'
        "        </div>\n"
        "      </a>\n"
        "    </article>\n"
        "  </div>\n"
        "</section>"
    )


def build_more_row(item, index):
    title = html.escape(item["title"].strip())
    slug = slug_from_link(item["link"])
    date = item["pub_dt"].strftime("%d %b %Y")
    iso_date = item["pub_dt"].strftime("%Y-%m-%d")
    minutes = read_minutes(item["content"])
    return (
        "      <article>\n"
        f'        <a class="article-row" href="articles/{slug}.html">\n'
        f'          <span class="article-index">{index:02d}</span>\n'
        f'          <span class="article-title">{title}</span>\n'
        '          <span class="article-meta">\n'
        f'            <time class="article-date" datetime="{iso_date}">{date}</time>\n'
        f'            <span class="article-read">{minutes} min read</span>\n'
        "          </span>\n"
        "        </a>\n"
        "      </article>"
    )


def build_more_section(rest_items):
    if not rest_items:
        return ""
    rows = "\n".join(
        build_more_row(item, index) for index, item in enumerate(rest_items, start=2)
    )
    return (
        '\n<section class="articles-more-section">\n'
        '  <div class="wrap">\n'
        '    <p class="articles-more-label">More posts</p>\n'
        '    <div class="articles-list">\n'
        f"{rows}\n"
        "    </div>\n"
        "  </div>\n"
        "</section>"
    )


def build_empty_state():
    return (
        '<section class="articles-featured-section">\n'
        '  <div class="wrap">\n'
        '    <p class="empty-note">Nothing published yet. Check back soon.</p>\n'
        "  </div>\n"
        "</section>"
    )


def build_articles_jsonld(items):
    entries = []
    for item in items:
        slug = slug_from_link(item["link"])
        entries.append({
            "@type": "BlogPosting",
            "headline": item["title"].strip(),
            "url": f"{SITE_URL}/articles/{slug}.html",
            "datePublished": item["pub_dt"].strftime("%Y-%m-%d"),
            "timeRequired": f"PT{read_minutes(item['content'])}M",
            "author": {"@type": "Person", "name": "Conor O'Shea"},
            "publisher": {"@type": "Organization", "name": "Conor O'Shea Fitness"},
        })
    data = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "item": entry}
            for i, entry in enumerate(entries, start=1)
        ],
    }
    return (
        '\n<script type="application/ld+json">\n'
        + json.dumps(data, indent=2)
        + "\n</script>"
    )


def build_articles_block(items):
    if not items:
        return build_empty_state()
    featured, rest = items[0], items[1:]
    return (
        build_featured_card(featured)
        + build_more_section(rest)
        + build_articles_jsonld(items)
    )


# --- Full-text article page generation -------------------------------

ARTICLE_NAV = """<nav class="nav">
  <a href="../index.html" class="brand">Conor O'Shea <span>/ Low Back Rehab</span></a>
  <div class="nav-links">
    <a href="../index.html#story">Story</a>
    <a href="../index.html#method">Method</a>
    <a href="../testimonials.html">Results</a>
    <a href="../index.html#faq">FAQ</a>
    <a href="../articles.html">Articles</a>
    <a href="../about.html">About</a>
    <a href="../assessment-call.html">Assessment Call</a>
  </div>
  <a href="https://lowbackassessment.conorosheafitness.com/" class="btn btn-sm">Take the Assessment</a>
</nav>"""

ARTICLE_FOOTER = """<footer class="footer">
  <span>&copy; 2026 Conor O'Shea Fitness Ltd.</span>
  <div class="footer-links">
    <a href="mailto:help@conorosheafitness.com">Email</a>
    <a href="https://www.instagram.com/conorosheafit/" target="_blank" rel="noopener">Instagram</a>
    <a href="https://www.youtube.com/@conorosheafit" target="_blank" rel="noopener">YouTube</a>
    <a href="https://ie.linkedin.com/in/conorosheafit" target="_blank" rel="noopener">LinkedIn</a>
    <a href="https://open.spotify.com/show/2gnwofsr3eItEj1TJXbmIi" target="_blank" rel="noopener">Spotify</a>
  </div>
</footer>"""


def build_article_page(item):
    slug = slug_from_link(item["link"])
    title = item["title"].strip()
    title_html = html.escape(title)
    date_str = item["pub_dt"].strftime("%d %b %Y")
    iso_date = item["pub_dt"].strftime("%Y-%m-%d")
    minutes = read_minutes(item["content"])
    content_html = sanitize_content(item["content"])
    description = html.escape(excerpt(item["content"]))
    image = first_image_url(item["content"]) or DEFAULT_OG_IMAGE
    canonical = f"{SITE_URL}/articles/{slug}.html"
    substack_url = html.escape(item["link"].strip(), quote=True)
    page_title_html = f"{title_html} | Conor O'Shea Fitness"

    breadcrumb_jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{SITE_URL}/"},
            {"@type": "ListItem", "position": 2, "name": "Articles", "item": f"{SITE_URL}/articles.html"},
            {"@type": "ListItem", "position": 3, "name": title, "item": canonical},
        ],
    }, indent=2)

    blogposting_jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": title,
        "url": canonical,
        "mainEntityOfPage": canonical,
        "image": image,
        "datePublished": iso_date,
        "dateModified": iso_date,
        "author": {"@type": "Person", "name": "Conor O'Shea"},
        "publisher": {"@type": "Organization", "name": "Conor O'Shea Fitness"},
    }, indent=2)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title_html}</title>
<meta name="description" content="{description}">
<meta name="author" content="Conor O'Shea">

<link rel="canonical" href="{canonical}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Conor O'Shea Fitness">
<meta property="og:title" content="{page_title_html}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{image}">
<meta property="article:published_time" content="{iso_date}">
<meta property="article:author" content="Conor O'Shea">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{page_title_html}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{image}">

<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Barlow:wght@400;500;600;700&family=Barlow+Condensed:wght@500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../styles.css">

<script type="application/ld+json">
{breadcrumb_jsonld}
</script>

<!--
  This page is auto-generated from the Substack RSS feed by
  scripts/sync_substack_articles.py (run on a schedule via
  .github/workflows/sync-substack-articles.yml). Do not edit this file
  by hand; it will be overwritten on the next sync. dateModified is
  kept in sync with the publish date here, same as other pages.
-->
<script type="application/ld+json">
{blogposting_jsonld}
</script>

<!-- Start of HubSpot Embed Code -->
<script type="text/javascript" id="hs-script-loader" async defer src="//js-eu1.hs-scripts.com/148058133.js"></script>
<!-- End of HubSpot Embed Code -->
</head>
<body>

{ARTICLE_NAV}

<main>

<!-- PAGE HEAD -->
<section class="page-head">
  <div class="wrap article-body">
    <p class="eyebrow mb">Article</p>
    <h1 class="display" style="font-size:38px;">{title_html}</h1>
    <p class="article-meta-line">{date_str} &middot; {minutes} min read &middot; Written by Conor O'Shea &middot; Conor O'Shea Fitness</p>
  </div>
</section>

<section class="pad" style="padding-top:0;">
  <div class="wrap article-body">
    <div class="prose">
{content_html}
    </div>
  </div>
</section>

<p class="article-substack-note">Originally published on <a href="{substack_url}" target="_blank" rel="noopener">Substack</a>.</p>

<div class="booking-back">
  <a href="../articles.html" class="cta-secondary">&larr; Back to all articles</a>
</div>

<!-- SUBSCRIBE STRIP -->
<section class="subscribe-section">
  <div class="wrap">
    <div class="subscribe-strip">
      <div>
        <h2>Get new posts by email.</h2>
        <p>Published on Substack. No spam, unsubscribe any time.</p>
      </div>
      <a href="https://conorosheafit.substack.com/" target="_blank" rel="noopener" class="btn">Subscribe on Substack &rarr;</a>
    </div>
  </div>
</section>

</main>

{ARTICLE_FOOTER}

</body>
</html>
"""


def write_article_pages(items):
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    written = []
    for item in items:
        slug = slug_from_link(item["link"])
        if not slug:
            continue
        path = os.path.join(ARTICLES_DIR, f"{slug}.html")
        new_content = build_article_page(item)
        old_content = None
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                old_content = f.read()
        if new_content != old_content:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
            written.append(slug)
    return written


def add_sitemap_entries(sitemap_xml, items):
    """Append a <url> entry for each article not already present in
    sitemap.xml. Only adds missing entries - never rewrites lastmod for
    ones already there, since a published post's canonical date doesn't
    change (write_article_pages regenerates the page itself if content
    was edited on Substack, independent of the sitemap).
    """
    added = []
    new_entries = []
    for item in items:
        slug = slug_from_link(item["link"])
        if not slug:
            continue
        loc = f"{SITE_URL}/articles/{slug}.html"
        if f"<loc>{loc}</loc>" in sitemap_xml:
            continue
        iso_date = item["pub_dt"].strftime("%Y-%m-%d")
        new_entries.append(
            "  <url>\n"
            f"    <loc>{loc}</loc>\n"
            f"    <lastmod>{iso_date}</lastmod>\n"
            "    <changefreq>yearly</changefreq>\n"
            "    <priority>0.6</priority>\n"
            "  </url>\n"
        )
        added.append(slug)
    if not new_entries:
        return sitemap_xml, added
    insertion = "".join(new_entries)
    new_xml = sitemap_xml.replace("</urlset>", insertion + "</urlset>")
    return new_xml, added


def fetch_direct(url):
    req = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_rss_items(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        content = item.findtext(f"{CONTENT_NS}encoded") or ""
        if not (title and link and pub_date):
            continue
        items.append({
            "title": title,
            "link": link,
            "pub_dt": parsedate_to_datetime(pub_date),
            "content": content,
        })
    return items


def fetch_via_rss2json(feed_url, attempts=3, backoff_seconds=3):
    params = {"rss_url": feed_url}
    if RSS2JSON_API_KEY:
        params["api_key"] = RSS2JSON_API_KEY
    url = "https://api.rss2json.com/v1/api.json?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": REQUEST_HEADERS["User-Agent"]})

    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            last_error = e
            print(f"rss2json attempt {attempt}/{attempts} failed: {e}", file=sys.stderr)
            if attempt < attempts:
                time.sleep(backoff_seconds * attempt)
    else:
        raise last_error

    if data.get("status") != "ok":
        raise RuntimeError(f"rss2json returned an error: {data.get('message', data)}")

    items = []
    for item in data.get("items", []):
        title = (item.get("title") or "").strip()
        link = (item.get("link") or "").strip()
        pub_date_raw = (item.get("pubDate") or "").strip()
        content = item.get("content") or item.get("description") or ""
        if not (title and link and pub_date_raw):
            continue
        try:
            pub_dt = datetime.strptime(pub_date_raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pub_dt = parsedate_to_datetime(pub_date_raw)
        items.append({"title": title, "link": link, "pub_dt": pub_dt, "content": content})
    return items


def fetch_items(feed_url):
    try:
        xml_bytes = fetch_direct(feed_url)
        return parse_rss_items(xml_bytes)
    except urllib.error.HTTPError as e:
        print(
            f"Direct fetch of {feed_url} failed (HTTP {e.code}); "
            "falling back to the rss2json.com proxy.",
            file=sys.stderr,
        )
        return fetch_via_rss2json(feed_url)


def bump_date_modified(page, today):
    """Update articles.html's own WebPage dateModified to today.

    There is exactly one "dateModified" key in articles.html (in the
    WebPage JSON-LD block) - the per-article BlogPosting entries only
    carry datePublished, so this substitution is unambiguous.
    """
    pattern = re.compile(r'("dateModified":\s*")\d{4}-\d{2}-\d{2}(")')
    return pattern.sub(rf"\g<1>{today}\g<2>", page, count=1)


def bump_sitemap_lastmod(sitemap_xml, today):
    """Update sitemap.xml's <lastmod> for the articles.html <url> entry."""
    pattern = re.compile(
        r"(<loc>https://conorosheafitness\.com/articles\.html</loc>\s*<lastmod>)"
        r"\d{4}-\d{2}-\d{2}(</lastmod>)"
    )
    return pattern.sub(rf"\g<1>{today}\g<2>", sitemap_xml, count=1)


def main():
    if not FEED_URL:
        print("SUBSTACK_FEED_URL is not set, skipping sync.", file=sys.stderr)
        return 0

    feed_url = normalize_feed_url(FEED_URL)
    try:
        items = fetch_items(feed_url)
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as e:
        print(f"Failed to fetch feed: {e}", file=sys.stderr)
        return 1

    items.sort(key=lambda i: i["pub_dt"], reverse=True)
    print(f"Feed returned {len(items)} item(s):", file=sys.stderr)
    for item in items:
        print(f"  - {item['pub_dt'].date()}  {item['title']}", file=sys.stderr)

    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Full-text page per article (idempotent: only (re)writes a file
    # when its content actually changed; never deletes older pages that
    # have since scrolled out of the RSS feed window).
    written_pages = write_article_pages(items)
    if written_pages:
        print(f"Wrote/updated {len(written_pages)} article page(s): {', '.join(written_pages)}")
    else:
        print("All article pages already up to date.")

    # sitemap.xml: add any article URLs that aren't listed yet.
    try:
        with open(SITEMAP_XML, "r", encoding="utf-8") as f:
            sitemap = f.read()
    except FileNotFoundError:
        sitemap = None

    if sitemap is not None:
        new_sitemap, added_slugs = add_sitemap_entries(sitemap, items)
        if added_slugs:
            sitemap = new_sitemap
            with open(SITEMAP_XML, "w", encoding="utf-8") as f:
                f.write(sitemap)
            print(f"Added {len(added_slugs)} new sitemap.xml entr(ies): {', '.join(added_slugs)}")
    else:
        print("sitemap.xml not found, skipping article sitemap entries.", file=sys.stderr)

    # articles.html: regenerate the featured/more-posts listing block.
    block = build_articles_block(items)

    with open(ARTICLES_HTML, "r", encoding="utf-8") as f:
        page = f.read()

    if START_MARKER not in page or END_MARKER not in page:
        print(f"Could not find {START_MARKER} / {END_MARKER} in articles.html", file=sys.stderr)
        return 1

    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL
    )
    new_page = pattern.sub(f"{START_MARKER}\n{block}\n{END_MARKER}", page)

    if new_page != page:
        new_page = bump_date_modified(new_page, today)

        with open(ARTICLES_HTML, "w", encoding="utf-8") as f:
            f.write(new_page)
        print(f"Updated articles.html with {len(items)} article(s), dateModified -> {today}.")

        if sitemap is not None:
            new_sitemap = bump_sitemap_lastmod(sitemap, today)
            if new_sitemap != sitemap:
                with open(SITEMAP_XML, "w", encoding="utf-8") as f:
                    f.write(new_sitemap)
                print(f"Updated sitemap.xml lastmod for articles.html -> {today}.")
    else:
        print("articles.html listing already up to date.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
