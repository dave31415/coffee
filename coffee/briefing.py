"""Daily briefing: one short summary of today's high-scoring items, cached per item set."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from . import config, db

log = logging.getLogger(__name__)


class Bullet(BaseModel):
    text: str = Field(description="One sentence, at most 15 words, the hook for one story")
    item_ids: list[int] = Field(description="Ids of the items this bullet draws on")


class Briefing(BaseModel):
    lede: str = Field(description="One sentence, at most 12 words: the day's headline")
    bullets: list[Bullet] = Field(description="Three to five bullets, most important first")


PROMPT_VERSION = 2  # bump when the rubric changes so cached briefings regenerate

RUBRIC = """You are a newscaster opening the broadcast: tease the top stories in a few seconds.
From the items below, pick the three to five that matter most to someone building agentic AI systems.
Write one short hook per story: one sentence, at most 15 words, concrete, no explanation.
Think "Anthropic replaces Git after a 12x surge in agent commits", not a summary of the paper.
Lead with the single biggest story as a one-line lede of at most 12 words.
Skip routine releases and anything that only works as a long bullet. Each bullet cites its item ids."""


def _client():
    import anthropic

    return anthropic.Anthropic()


def _recent(conn: sqlite3.Connection, since: datetime, min_score: int) -> list[sqlite3.Row]:
    """Scored, visible items that were published or first seen since `since`."""
    iso = since.astimezone(UTC).isoformat()
    return conn.execute(
        "SELECT * FROM items WHERE hidden = 0 AND score >= ? AND (published_at >= ? OR fetched_at >= ?) "
        "ORDER BY score DESC, published_at DESC LIMIT ?",
        (min_score, iso, iso, config.BRIEFING_MAX_ITEMS),
    ).fetchall()


def select_items(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], int]:
    """Items for the briefing and the score threshold actually used."""
    since = datetime.now(UTC) - timedelta(hours=config.BRIEFING_HOURS)
    threshold = config.BRIEFING_MIN_SCORE
    rows = _recent(conn, since, threshold)
    if len(rows) < 3 and threshold > 3:
        threshold = 3
        rows = _recent(conn, since, threshold)
    return rows, threshold


def cache_key(rows) -> str:
    ids = ",".join(str(r["id"]) for r in sorted(rows, key=lambda r: r["id"]))
    return hashlib.sha1(f"v{PROMPT_VERSION}:{ids}".encode()).hexdigest()[:16]


def _format(rows) -> str:
    parts = []
    for r in rows:
        parts.append(
            f"<item id=\"{r['id']}\" score=\"{r['score']}\" category=\"{r['category']}\" source=\"{r['source']}\">\n"
            f"title: {r['title']}\nsummary: {r['summary']}\n</item>"
        )
    return "\n\n".join(parts)


def generate(rows, client=None) -> dict:
    client = client or _client()
    response = client.messages.parse(
        model=config.DETAIL_MODEL,
        max_tokens=1200,
        system=RUBRIC,
        messages=[{"role": "user", "content": _format(rows)}],
        output_format=Briefing,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError("no briefing returned")
    parsed = response.parsed_output
    known = {r["id"] for r in rows}
    bullets = [
        {"text": b.text.strip(), "item_ids": [i for i in b.item_ids if i in known]}
        for b in parsed.bullets
        if b.text.strip()
    ][:5]
    return {"lede": parsed.lede.strip(), "bullets": bullets}


def get_briefing(conn: sqlite3.Connection, client=None, force: bool = False) -> dict:
    """Return the cached briefing for today's item set, generating it if needed.

    Result: {"items": {id: row}, "threshold": int, "count": int, "briefing": dict|None, "generated_at": str|None}
    """
    rows, threshold = select_items(conn)
    result = {"items": {r["id"]: r for r in rows}, "threshold": threshold, "count": len(rows),
              "briefing": None, "generated_at": None}
    if not rows:
        return result
    key = cache_key(rows)
    if not force:
        cached = db.get_briefing(conn, key)
        if cached:
            result["briefing"] = json.loads(cached["content"])
            result["generated_at"] = cached["created_at"]
            return result
    briefing = generate(rows, client)
    db.save_briefing(conn, key, datetime.now().date().isoformat(), json.dumps(briefing))
    conn.commit()
    result["briefing"] = briefing
    result["generated_at"] = db.now_iso()
    return result
