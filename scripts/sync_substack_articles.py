#!/usr/bin/env python3
"""Regenerate the article list in articles.html from a Substack RSS feed.

Run manually with:  SUBSTACK_FEED_URL=https://yourname.substack.com/feed python3 scripts/sync_substack_articles.py
"""

import html
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

FEED_URL = os.environ.get("SUBSTACK_FEED_URL", "")
ARTICLES_HTML = os.path.join(os.path.dirname(__file__), "..", "articles.html")
START_MARKER = "<!-- SUBSTACK_ARTICLES_START -->"
END_MARKER = "<!-- SUBSTACK_ARTICLES_END -->"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
WORDS_PER_MINUTE = 200
# Substack serves RSS at /feed and 403s bot-like requests to the plain
# publication homepage, so normalize a pasted-in homepage URL here rather
# than fail confusingly at fetch time.
USER_AGENT = "Mozilla/5.0 (compatible; conorosheafitness-articles-sync/1.0; +https://conorosheafitness.com)"


def normalize_feed_url(url):
    url = url.strip().rstrip("/")
    if not url:
        return url
    if url.endswith("/feed") or url.endswith(".xml") or "/feed?" in url:
        return url
    return f"{url}/feed"


def fetch_feed(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def strip_tags(text):
    return re.sub(r"<[^>]+>", " ", text or "")


def read_minutes(content_encoded):
    words = strip_tags(content_encoded).split()
    return max(1, round(len(words) / WORDS_PER_MINUTE))


def format_date(pub_date):
    dt = parsedate_to_datetime(pub_date)
    return dt.strftime("%d %b %Y")


def build_row(title, link, pub_date, content_encoded):
    date = format_date(pub_date)
    minutes = read_minutes(content_encoded)
    title = html.escape(title.strip())
    link = html.escape(link.strip(), quote=True)
    return (
        f'      <a class="article-row" href="{link}" target="_blank" rel="noopener">\n'
        f'        <span class="article-date">{date}</span>\n'
        f'        <span class="article-title">{title}</span>\n'
        f'        <span class="article-read">{minutes} min read</span>\n'
        f"      </a>"
    )


def build_articles_block(items):
    if not items:
        return '      <p class="empty-note">Nothing published yet. Check back soon.</p>'
    return "\n".join(build_row(**item) for item in items)


def parse_items(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        content_encoded = item.findtext(f"{CONTENT_NS}encoded") or ""
        if not (title and link and pub_date):
            continue
        items.append({
            "title": title,
            "link": link,
            "pub_date": pub_date,
            "content_encoded": content_encoded,
        })
    return items


def main():
    if not FEED_URL:
        print("SUBSTACK_FEED_URL is not set, skipping sync.", file=sys.stderr)
        return 0

    feed_url = normalize_feed_url(FEED_URL)
    try:
        xml_bytes = fetch_feed(feed_url)
    except urllib.error.HTTPError as e:
        print(f"Failed to fetch {feed_url}: HTTP {e.code} {e.reason}", file=sys.stderr)
        return 1
    items = parse_items(xml_bytes)
    # Substack feeds are newest-first already; keep it that way explicitly.
    items.sort(key=lambda i: parsedate_to_datetime(i["pub_date"]), reverse=True)
    block = build_articles_block(items)

    with open(ARTICLES_HTML, "r", encoding="utf-8") as f:
        page = f.read()

    if START_MARKER not in page or END_MARKER not in page:
        print(f"Could not find {START_MARKER} / {END_MARKER} in articles.html", file=sys.stderr)
        return 1

    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL
    )
    new_page = pattern.sub(f"{START_MARKER}\n{block}\n      {END_MARKER}", page)

    if new_page != page:
        with open(ARTICLES_HTML, "w", encoding="utf-8") as f:
            f.write(new_page)
        print(f"Updated articles.html with {len(items)} article(s).")
    else:
        print("articles.html already up to date.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
