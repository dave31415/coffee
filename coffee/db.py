"""SQLite storage."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Iterator

from . import config
from .models import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  title_key TEXT NOT NULL,
  author TEXT,
  published_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  raw_text TEXT,
  category TEXT,
  score INTEGER,
  summary TEXT,
  processed_at TEXT,
  rating INTEGER,
  hidden INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_title_key ON items(title_key);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_item ON chat_messages(item_id);
CREATE TABLE IF NOT EXISTS briefings (
  key TEXT PRIMARY KEY,
  day TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

# Columns added after the first release; applied with ALTER TABLE when missing.
MIGRATIONS = {
    "links": "TEXT",
    "full_text": "TEXT",
    "full_fetched_at": "TEXT",
    "long_summary": "TEXT",
}


def _migrate(conn: sqlite3.Connection) -> None:
    have = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    for col, typ in MIGRATIONS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(path=None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


@contextmanager
def session(path=None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- meta -------------------------------------------------------------------

def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# --- items -------------------------------------------------------------------

def insert_item(conn: sqlite3.Connection, item: Item) -> bool:
    """Insert if new. Returns True when a row was added."""
    if conn.execute("SELECT 1 FROM items WHERE url = ?", (item.url,)).fetchone():
        return False
    cutoff = (datetime.now(UTC) - timedelta(days=config.DEDUPE_TITLE_DAYS)).isoformat()
    dup = conn.execute(
        "SELECT 1 FROM items WHERE title_key = ? AND published_at >= ?",
        (item.title_key, cutoff),
    ).fetchone()
    if dup and item.title_key:
        return False
    conn.execute(
        """INSERT INTO items(source, url, title, title_key, author, published_at, fetched_at, raw_text, links)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item.source,
            item.url,
            item.title,
            item.title_key,
            item.author,
            item.published_at.astimezone(UTC).replace(microsecond=0).isoformat(),
            now_iso(),
            (item.raw_text or "")[: config.RAW_TEXT_LIMIT] or None,
            json.dumps([list(l) for l in item.links]) if item.links else None,
        ),
    )
    return True


def unprocessed_items(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM items WHERE processed_at IS NULL ORDER BY published_at DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def save_judgment(conn: sqlite3.Connection, item_id: int, category: str, score: int, summary: str) -> None:
    conn.execute(
        "UPDATE items SET category = ?, score = ?, summary = ?, processed_at = ? WHERE id = ?",
        (category, score, summary, now_iso(), item_id),
    )


def set_rating(conn: sqlite3.Connection, item_id: int, value: int) -> None:
    conn.execute("UPDATE items SET rating = ? WHERE id = ?", (value, item_id))


def set_hidden(conn: sqlite3.Connection, item_id: int, hidden: bool) -> None:
    conn.execute("UPDATE items SET hidden = ? WHERE id = ?", (1 if hidden else 0, item_id))


def get_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def query_items(
    conn: sqlite3.Connection,
    *,
    since: datetime | None = None,
    category: str | None = None,
    source: str | None = None,
    min_score: int | None = None,
    q: str | None = None,
    show_hidden: bool = False,
    include_unscored: bool = True,
    limit: int = 500,
) -> list[sqlite3.Row]:
    where = []
    args: list = []
    if since:
        where.append("published_at >= ?")
        args.append(since.astimezone(UTC).isoformat())
    if category:
        where.append("category = ?")
        args.append(category)
    if source:
        where.append("source = ?")
        args.append(source)
    if min_score:
        if include_unscored:
            where.append("(score IS NULL OR score >= ?)")
        else:
            where.append("score >= ?")
        args.append(min_score)
    if q:
        where.append("title LIKE ?")
        args.append(f"%{q}%")
    if not show_hidden:
        where.append("hidden = 0")
    sql = "SELECT * FROM items"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(score, 0) DESC, published_at DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def source_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT source, COUNT(*) AS n, SUM(processed_at IS NULL) AS unprocessed, MAX(published_at) AS latest "
        "FROM items GROUP BY source ORDER BY source"
    ).fetchall()


# --- full text, long summary, chat ---------------------------------------------

def save_full(conn: sqlite3.Connection, item_id: int, text: str, links: list[list[str]]) -> None:
    conn.execute(
        "UPDATE items SET full_text = ?, links = ?, full_fetched_at = ? WHERE id = ?",
        (text, json.dumps(links), now_iso(), item_id),
    )


def save_long_summary(conn: sqlite3.Connection, item_id: int, payload: str) -> None:
    conn.execute("UPDATE items SET long_summary = ? WHERE id = ?", (payload, item_id))


def chat_messages(conn: sqlite3.Connection, item_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chat_messages WHERE item_id = ? ORDER BY id", (item_id,)
    ).fetchall()


def add_chat_message(conn: sqlite3.Connection, item_id: int, role: str, content: str) -> None:
    conn.execute(
        "INSERT INTO chat_messages(item_id, role, content, created_at) VALUES(?, ?, ?, ?)",
        (item_id, role, content, now_iso()),
    )


def clear_chat(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("DELETE FROM chat_messages WHERE item_id = ?", (item_id,))


# --- briefings -------------------------------------------------------------------

def get_briefing(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM briefings WHERE key = ?", (key,)).fetchone()


def save_briefing(conn: sqlite3.Connection, key: str, day: str, content: str) -> None:
    conn.execute(
        "INSERT INTO briefings(key, day, content, created_at) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET content = excluded.content, created_at = excluded.created_at",
        (key, day, content, now_iso()),
    )
