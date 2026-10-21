"""Fetch every source and store new items."""
from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from . import config, db
from .sources import all_sources

log = logging.getLogger(__name__)


def collect(conn: sqlite3.Connection, sources=None) -> dict[str, int]:
    """Returns {source_key: new_item_count}. A failing source logs and is skipped."""
    results: dict[str, int] = {}
    for src in sources or all_sources():
        try:
            items = src.fetch()
        except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the run
            log.warning("%s: fetch failed: %s", src.key, exc)
            results[src.key] = -1
            continue
        cutoff = datetime.now(UTC) - timedelta(days=config.MAX_ITEM_AGE_DAYS)
        fresh = [item for item in items if item.published_at >= cutoff]
        added = sum(1 for item in fresh if db.insert_item(conn, item))
        conn.commit()
        log.info("%s: %d fetched, %d recent, %d new", src.key, len(items), len(fresh), added)
        results[src.key] = added
    return results
