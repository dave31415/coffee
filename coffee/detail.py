"""Long summaries and per-item chat, both grounded in the item's full text."""
from __future__ import annotations

import html
import json
import logging
import os
import sqlite3

import markdown
from pydantic import BaseModel, Field

from . import config, db
from .fulltext import ensure_full

log = logging.getLogger(__name__)


class Link(BaseModel):
    title: str = Field(description="Short label for the link")
    url: str = Field(description="Exact URL copied from the provided link list or the item URL")
    why: str = Field(description="A few words on what it is")


class LongSummary(BaseModel):
    paragraphs: list[str] = Field(description="Two to four short plain-language paragraphs")
    links: list[Link] = Field(description="Primary-source links worth following, best first, at most 6")


SUMMARY_RUBRIC = """Explain one item from an AI news feed to a smart, curious reader who is not an AI expert.
Two to four short paragraphs: what this is, what it actually does or found, and why it matters or does not.
Write plainly. Avoid jargon; when a technical term is unavoidable, say what it means in a few words the
first time you use it. Use a concrete everyday comparison when it genuinely helps. Keep the facts specific
(names, numbers, what changed) but never pile up acronyms, model names or benchmark scores without
saying what they mean. No filler, no "this article discusses".
Then pick the primary-source links worth following: the paper, repo, docs, original announcement, or
the thing being discussed. Copy URLs exactly from the link list given; never invent or alter a URL.
Skip navigation, social, subscription and author-profile links."""

CHAT_RUBRIC = """You are discussing one item from an AI news feed with a reader who builds agentic AI systems.
Ground your answers in the item text below. Quote or point to specifics. If the item does not contain
the answer, say so plainly and then give your best general knowledge, clearly marked as such.
Be concise and direct. Use markdown sparingly: short paragraphs, lists only when they help."""


def _client():
    import anthropic

    return anthropic.Anthropic()


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def render_markdown(text: str) -> str:
    """Escape any raw HTML, then render markdown."""
    return markdown.markdown(html.escape(text, quote=False), extensions=["fenced_code", "sane_lists"])


def item_context(row: sqlite3.Row) -> str:
    links = json.loads(row["links"] or "[]")
    link_lines = "\n".join(f"- {a or '(no text)'}: {u}" for a, u in links) or "(none)"
    return (
        f"<item>\ntitle: {row['title']}\nsource: {row['source']}\nurl: {row['url']}\n"
        f"author: {row['author'] or ''}\npublished: {row['published_at']}\n\n"
        f"<text>\n{row['full_text'] or row['raw_text'] or ''}\n</text>\n\n"
        f"<links>\n{link_lines}\n</links>\n</item>"
    )


def _system(rubric: str, row: sqlite3.Row) -> list[dict]:
    return [
        {"type": "text", "text": rubric},
        {"type": "text", "text": item_context(row), "cache_control": {"type": "ephemeral"}},
    ]


def _allowed_urls(row: sqlite3.Row) -> set[str]:
    urls = {row["url"]}
    for _, u in json.loads(row["links"] or "[]"):
        urls.add(u)
    return urls


def long_summary(conn: sqlite3.Connection, row: sqlite3.Row, client=None) -> dict:
    """Return {"paragraphs": [...], "links": [...]}, generating and caching on first call."""
    if row["long_summary"]:
        return json.loads(row["long_summary"])
    row = ensure_full(conn, row)
    client = client or _client()
    response = client.messages.parse(
        model=config.DETAIL_MODEL,
        max_tokens=3000,
        system=_system(SUMMARY_RUBRIC, row),
        messages=[{"role": "user", "content": "Write the longer summary and pick the links."}],
        output_format=LongSummary,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError("no summary returned")
    parsed = response.parsed_output
    allowed = _allowed_urls(row)
    links = [l.model_dump() for l in parsed.links if l.url in allowed][:6]
    result = {"paragraphs": [p.strip() for p in parsed.paragraphs if p.strip()], "links": links}
    db.save_long_summary(conn, row["id"], json.dumps(result))
    conn.commit()
    return result


def chat_reply(conn: sqlite3.Connection, row: sqlite3.Row, user_message: str, client=None) -> str:
    """Append the user message, get the assistant reply, store both. Returns the reply text."""
    row = ensure_full(conn, row)
    db.add_chat_message(conn, row["id"], "user", user_message)
    conn.commit()
    history = [{"role": m["role"], "content": m["content"]} for m in db.chat_messages(conn, row["id"])]
    client = client or _client()
    response = client.messages.create(
        model=config.DETAIL_MODEL,
        max_tokens=2000,
        system=_system(CHAT_RUBRIC, row),
        messages=history,
        output_config={"effort": "low"},
    )
    if response.stop_reason == "refusal":
        reply = "The model declined to answer that."
    else:
        reply = "".join(b.text for b in response.content if b.type == "text").strip() or "(empty reply)"
    db.add_chat_message(conn, row["id"], "assistant", reply)
    conn.commit()
    return reply
