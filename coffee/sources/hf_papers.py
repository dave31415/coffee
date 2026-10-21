"""Hugging Face daily papers (JSON API)."""
from __future__ import annotations

from datetime import UTC, datetime

from .. import config
from ..models import Item
from .base import http_get


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)


class HFPapersSource:
    key = "hfpapers"
    name = "HF Papers"

    def fetch(self) -> list[Item]:
        resp = http_get(config.HF_DAILY_PAPERS_URL)
        return self.parse(resp.json())

    def parse(self, data: list[dict]) -> list[Item]:
        items: list[Item] = []
        for entry in data:
            paper = entry.get("paper") or {}
            pid = paper.get("id")
            title = paper.get("title") or entry.get("title")
            if not pid or not title:
                continue
            authors = ", ".join(a.get("name", "") for a in paper.get("authors", []) if a.get("name"))
            items.append(
                Item(
                    source=self.key,
                    url=f"https://huggingface.co/papers/{pid}",
                    title=title,
                    published_at=_parse_dt(entry.get("publishedAt") or paper.get("publishedAt")),
                    author=authors or None,
                    raw_text=paper.get("summary"),
                )
            )
        return items
