"""Fetch an item's own page and extract readable text plus links."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from . import config, db
from .sources.base import http_get

log = logging.getLogger(__name__)

_SKIP = {"script", "style", "noscript", "svg", "template", "math", "head"}
_BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre", "blockquote"}
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")
_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


class _Extractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip = 0
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                self._href = urljoin(self.base, href).split("#")[0]
                self._anchor = []

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag == "a" and self._href:
            text = _WS.sub(" ", "".join(self._anchor)).strip()
            self.links.append((text, self._href))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._anchor.append(data)


def extract(html: str, base_url: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (text, links) where links are (anchor text, absolute url), deduped by url."""
    p = _Extractor(base_url)
    try:
        p.feed(html)
        p.close()
    except Exception as exc:  # noqa: BLE001 - malformed HTML should degrade, not fail
        log.debug("html parse error: %s", exc)
    text = "".join(p.parts)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _NL.sub("\n\n", text).strip()
    seen: set[str] = set()
    links = []
    for anchor, url in p.links:
        if url in seen or url.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(url)
        links.append((anchor, url))
    return text, links


def candidate_urls(row: sqlite3.Row) -> list[str]:
    """Pages to try, best first. Papers get the HTML full text when it exists."""
    url = row["url"]
    if row["source"] in ("arxiv", "hfpapers"):
        m = _ARXIV_ID.search(url)
        if m:
            aid = m.group(1)
            return [f"https://arxiv.org/html/{aid}", f"https://arxiv.org/abs/{aid}", url]
    return [url]


def fetch(row: sqlite3.Row) -> tuple[str, list[tuple[str, str]]]:
    last_exc: Exception | None = None
    for url in candidate_urls(row):
        try:
            resp = http_get(url)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "xml" not in ctype:
            continue
        text, links = extract(resp.text, str(resp.url))
        if len(text) > 200:
            return text, links
    if last_exc:
        log.info("full text fetch failed for %s: %s", row["url"], last_exc)
    return "", []


def ensure_full(conn: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
    """Fetch and cache the full page on first use. Falls back to feed text."""
    if row["full_fetched_at"]:
        return row
    text, links = fetch(row)
    feed_links = json.loads(row["links"] or "[]")
    merged: list[list[str]] = []
    seen: set[str] = set()
    for anchor, url in [tuple(x) for x in feed_links] + links:
        if url not in seen:
            seen.add(url)
            merged.append([anchor, url])
    if not text:
        text = row["raw_text"] or ""
    db.save_full(conn, row["id"], text[: config.FULL_TEXT_LIMIT], merged[: config.MAX_LINKS])
    conn.commit()
    return db.get_item(conn, row["id"])
