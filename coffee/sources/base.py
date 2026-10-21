"""Source protocol and shared fetch helpers."""
from __future__ import annotations

import calendar
import html
import re
from datetime import UTC, datetime
from typing import Protocol

import httpx

from .. import config
from ..models import Item

_TAG = re.compile(r"<[^>]+>")
_A = re.compile(r'<a\s[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_WS = re.compile(r"\s+")

USER_AGENT = "coffee/0.1 (personal news reader)"


class Source(Protocol):
    key: str
    name: str

    def fetch(self) -> list[Item]: ...


def http_get(url: str, **kwargs) -> httpx.Response:
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True, headers=headers) as client:
        resp = client.get(url, **kwargs)
        resp.raise_for_status()
        return resp


def strip_html(text: str | None) -> str | None:
    if not text:
        return None
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return _WS.sub(" ", text).strip() or None


def struct_time_to_dt(st) -> datetime | None:
    """feedparser gives UTC struct_time; convert to aware datetime."""
    if not st:
        return None
    return datetime.fromtimestamp(calendar.timegm(st), tz=UTC)


def entry_datetime(entry) -> datetime:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        dt = struct_time_to_dt(entry.get(attr))
        if dt:
            return dt
    return datetime.now(UTC)


def extract_links(text: str | None, limit: int = 60) -> list[tuple[str, str]]:
    """(anchor text, url) pairs from feed HTML, deduped by url."""
    if not text:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _A.finditer(text):
        url = html.unescape(m.group(1)).strip()
        if url in seen or url.startswith(("#", "mailto:", "javascript:")):
            continue
        seen.add(url)
        out.append((strip_html(m.group(2)) or "", url))
        if len(out) >= limit:
            break
    return out
