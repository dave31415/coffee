"""arXiv API search, returned as Atom and parsed with feedparser."""
from __future__ import annotations

import time

import feedparser

from .. import config
from ..models import Item
from .base import entry_datetime, http_get, strip_html

API_URL = "https://export.arxiv.org/api/query"
COURTESY_DELAY = 3.0


def build_query(categories=None, terms=None) -> str:
    categories = categories or config.ARXIV_CATEGORIES
    terms = terms or config.ARXIV_TERMS
    cats = " OR ".join(f"cat:{c}" for c in categories)
    abs_terms = " OR ".join(f'abs:"{t}"' for t in terms)
    return f"({cats}) AND ({abs_terms})"


class ArxivSource:
    key = "arxiv"
    name = "arXiv"

    def __init__(self, max_results: int | None = None):
        self.max_results = max_results or config.ARXIV_MAX_RESULTS

    def fetch(self) -> list[Item]:
        params = {
            "search_query": build_query(),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": self.max_results,
        }
        time.sleep(COURTESY_DELAY)
        resp = http_get(API_URL, params=params)
        return self.parse(resp.content)

    def parse(self, content: bytes | str) -> list[Item]:
        feed = feedparser.parse(content)
        items: list[Item] = []
        for e in feed.entries:
            link = e.get("link") or e.get("id")
            if not link:
                continue
            # Prefer the abstract page over the versioned id.
            link = link.replace("http://", "https://")
            authors = ", ".join(a.get("name", "") for a in e.get("authors", []) if a.get("name"))
            items.append(
                Item(
                    source=self.key,
                    url=link,
                    title=e.get("title", "").replace("\n", " "),
                    published_at=entry_datetime(e),
                    author=authors or None,
                    raw_text=strip_html(e.get("summary")),
                )
            )
        return items
