"""Normalized item produced by every source."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "ref", "source", "fbclid", "gclid", "mc_cid", "mc_eid")
_TITLE_STRIP = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalize_url(url: str) -> str:
    """Canonical form used for dedupe: https, no tracking params, no fragment, no trailing slash."""
    url = url.strip()
    parts = urlsplit(url)
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def title_key(title: str) -> str:
    """Lowercase, punctuation-free title for cross-source dedupe."""
    t = _TITLE_STRIP.sub(" ", title.lower())
    return _SPACES.sub(" ", t).strip()


@dataclass
class Item:
    source: str
    url: str
    title: str
    published_at: datetime
    author: str | None = None
    raw_text: str | None = None
    links: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.url = normalize_url(self.url)
        self.title = _SPACES.sub(" ", self.title).strip()

    @property
    def title_key(self) -> str:
        return title_key(self.title)
