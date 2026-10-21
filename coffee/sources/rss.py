"""Generic RSS/Atom source via feedparser."""
from __future__ import annotations

import feedparser

from ..models import Item
from .base import entry_datetime, extract_links, http_get, strip_html


class RSSSource:
    def __init__(self, key: str, name: str, url: str):
        self.key = key
        self.name = name
        self.url = url

    def fetch(self) -> list[Item]:
        resp = http_get(self.url)
        return self.parse(resp.content)

    def parse(self, content: bytes | str) -> list[Item]:
        feed = feedparser.parse(content)
        items: list[Item] = []
        for e in feed.entries:
            link = e.get("link")
            title = e.get("title")
            if not link or not title:
                continue
            text = None
            if e.get("content"):
                text = e["content"][0].get("value")
            text = text or e.get("summary") or e.get("description")
            author = e.get("author")
            items.append(
                Item(
                    source=self.key,
                    url=link,
                    title=title,
                    published_at=entry_datetime(e),
                    author=author,
                    raw_text=strip_html(text),
                    links=extract_links(text),
                )
            )
        return items


def github_releases_source(repo: str, name: str) -> RSSSource:
    key = f"gh:{repo.split('/')[1]}"
    return RSSSource(key, name, f"https://github.com/{repo}/releases.atom")
