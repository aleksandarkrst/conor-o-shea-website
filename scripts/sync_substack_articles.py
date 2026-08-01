#!/usr/bin/env python3
"""Regenerate the article list in articles.html from a Substack RSS feed.

Run manually with:  SUBSTACK_FEED_URL=https://yourname.substack.com/feed python3 scripts/sync_substack_articles.py

Substack's edge blocks direct fetches from GitHub Actions' runner IPs
(confirmed: the feed loads fine in a browser, but 403s every direct
request from CI regardless of headers). To work around that, a direct
fetch is tried first and, on any failure, falls back to rss2json.com's
public RSS-to-JSON proxy, which fetches the feed from its own
infrastructure and hands back parsed JSON.
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
ARTICLES_HTML = os.path.join(os.path.dirname(__file__), "..", "articles.html")
START_MARKER = "<!-- SUBSTACK_ARTICLES_START -->"
END_MARKER = "<!-- SUBSTACK_ARTICLES_END -->"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
WORDS_PER_MINUTE = 200
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


def build_featured_card(item):
    title = html.escape(item["title"].strip())
    link = html.escape(item["link"].strip(), quote=True)
    date = item["pub_dt"].strftime("%d %b %Y")
    iso_date = item["pub_dt"].strftime("%Y-%m-%d")
    minutes = read_minutes(item["content"])
    return (
        '<section class="articles-featured-section">\n'
        '  <div class="wrap">\n'
        "    <article>\n"
        f'      <a class="article-featured" href="{link}" target="_blank" rel="noopener">\n'
        '        <p class="article-featured-eyebrow">Latest post</p>\n'
        f'        <h2 class="article-featured-title">{title}</h2>\n'
        '        <div class="article-featured-meta">\n'
        f'          <time class="article-featured-date" datetime="{iso_date}">{date}</time>\n'
        '          <span class="article-featured-dot"></span>\n'
        f'          <span class="article-featured-read">{minutes} min read</span>\n'
        '          <span class="article-featured-link">Read on Substack &rarr;</span>\n'
        "        </div>\n"
        "      </a>\n"
        "    </article>\n"
        "  </div>\n"
        "</section>"
    )


def build_more_row(item, index):
    title = html.escape(item["title"].strip())
    link = html.escape(item["link"].strip(), quote=True)
    date = item["pub_dt"].strftime("%d %b %Y")
    iso_date = item["pub_dt"].strftime("%Y-%m-%d")
    minutes = read_minutes(item["content"])
    return (
        "      <article>\n"
        f'        <a class="article-row" href="{link}" target="_blank" rel="noopener">\n'
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
        entries.append({
            "@type": "BlogPosting",
            "headline": item["title"].strip(),
            "url": item["link"].strip(),
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
        with open(ARTICLES_HTML, "w", encoding="utf-8") as f:
            f.write(new_page)
        print(f"Updated articles.html with {len(items)} article(s).")
    else:
        print("articles.html already up to date.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
