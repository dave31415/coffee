"""collect + process, with a lock so only one refresh runs at a time."""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from . import config, db
from .collect import collect
from .process import process

log = logging.getLogger(__name__)

_lock = threading.Lock()
_state = {"running": False, "last_error": None, "last_result": None}


def is_running() -> bool:
    return _state["running"]


def last_error() -> str | None:
    return _state["last_error"]


def last_result():
    return _state["last_result"]


def last_refresh(conn) -> datetime | None:
    value = db.get_meta(conn, "last_refresh")
    return datetime.fromisoformat(value) if value else None


def is_stale(conn) -> bool:
    last = last_refresh(conn)
    return last is None or datetime.now(UTC) - last > timedelta(hours=config.STALE_HOURS)


def run() -> dict | None:
    """Run a full refresh. Returns collect counts, or None if one was already running."""
    if not _lock.acquire(blocking=False):
        return None
    _state["running"] = True
    _state["last_error"] = None
    try:
        with db.session() as conn:
            counts = collect(conn)
            judged = process(conn)
            db.set_meta(conn, "last_refresh", db.now_iso())
        _state["last_result"] = {"collected": counts, "judged": judged}
        return counts
    except Exception as exc:  # noqa: BLE001
        log.exception("refresh failed")
        _state["last_error"] = str(exc)
        return None
    finally:
        _state["running"] = False
        _lock.release()


def run_in_background() -> bool:
    """Start a refresh thread unless one is already running."""
    if is_running():
        return False
    threading.Thread(target=run, name="coffee-refresh", daemon=True).start()
    return True
