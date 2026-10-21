"""Score, categorize and summarize unprocessed items with Claude."""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Literal

from pydantic import BaseModel, Field

from . import config, db

log = logging.getLogger(__name__)

Category = Literal["developments", "tools", "research", "production", "noise"]


class Judgment(BaseModel):
    id: int = Field(description="The item id exactly as given")
    category: Category
    score: int = Field(ge=1, le=5, description="Relevance to the reader, 1 (skip) to 5 (must read)")
    summary: str = Field(description="One or two plain sentences on what it is and why it matters")


class Judgments(BaseModel):
    judgments: list[Judgment]


RUBRIC = """You triage a daily feed of AI news, papers and releases for one reader.
For each item, assign a category, a relevance score from 1 to 5, and a short summary.

Categories:
- developments: new capabilities, architectural ideas, significant model or platform changes
- tools: frameworks, libraries, services, releases worth investigating
- research: papers with a genuine contribution
- production: evaluations, reliability, security, cost, scaling, lessons from real systems
- noise: marketing, funding, hype, off-topic, or too thin to matter

Scoring: 5 = must read today; 4 = clearly relevant; 3 = worth a glance; 2 = marginal; 1 = skip.
Judge technical substance over popularity. Routine patch releases with no notable changes are 1 or 2.
Summaries are factual, specific, and never start with "This article" or "This paper".

The reader's interests follow. Return one judgment per item id."""


def _client():
    import anthropic

    return anthropic.Anthropic()


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _profile() -> str:
    try:
        return config.PROFILE_PATH.read_text().strip()
    except FileNotFoundError:
        return "(no profile.md found)"


def _format_items(rows) -> str:
    chunks = []
    for r in rows:
        body = (r["raw_text"] or "").strip()
        if len(body) > 1500:
            body = body[:1500] + "..."
        chunks.append(
            f"<item id=\"{r['id']}\">\nsource: {r['source']}\ntitle: {r['title']}\n"
            f"url: {r['url']}\ntext: {body}\n</item>"
        )
    return "\n\n".join(chunks)


def judge_batch(client, rows, model: str | None = None) -> list[Judgment]:
    """One API call for a batch of rows. Raises on API or parse failure."""
    system = [
        {"type": "text", "text": RUBRIC, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": _profile()},
    ]
    response = client.messages.parse(
        model=model or config.MODEL,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": _format_items(rows)}],
        output_format=Judgments,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model refused the batch")
    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError("no parsed output")
    return parsed.judgments


def process(conn: sqlite3.Connection, client=None, limit: int | None = None, batch_size: int | None = None) -> int:
    """Process unprocessed items. Returns count judged. Without an API key, does nothing."""
    if client is None:
        if not has_api_key():
            log.warning("ANTHROPIC_API_KEY not set; skipping LLM processing")
            return 0
        client = _client()
    batch_size = batch_size or config.LLM_BATCH_SIZE
    rows = db.unprocessed_items(conn, limit)
    done = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        ids = {r["id"] for r in batch}
        try:
            judgments = judge_batch(client, batch)
        except Exception as exc:  # noqa: BLE001 - leave the batch unprocessed and move on
            log.warning("batch starting at item %s failed: %s", batch[0]["id"], exc)
            continue
        for j in judgments:
            if j.id not in ids:
                log.warning("model returned unknown id %s", j.id)
                continue
            db.save_judgment(conn, j.id, j.category, j.score, j.summary.strip())
            done += 1
        conn.commit()
        log.info("judged %d/%d", min(i + batch_size, len(rows)), len(rows))
    return done
