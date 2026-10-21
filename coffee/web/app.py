"""FastAPI viewer. Reads the database; never fetches sources on request."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import briefing, config, db, detail, refresh

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

RANGES = {"today": 1, "3d": 3, "7d": 7, "30d": 30, "all": None}
RANGE_LABELS = {"today": "Today", "3d": "3 days", "7d": "Week", "30d": "Month", "all": "All"}


def _since(range_key: str) -> datetime | None:
    days = RANGES.get(range_key, 3)
    if days is None:
        return None
    return datetime.now(UTC) - timedelta(days=days)


def _fmt_date(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value or ""
    delta = datetime.now(UTC) - dt
    if delta < timedelta(hours=1):
        return f"{max(1, int(delta.total_seconds() // 60))}m ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)}h ago"
    if delta < timedelta(days=7):
        return f"{delta.days}d ago"
    return dt.strftime("%b %d")


def _group(rows):
    """Group rows by category in fixed display order; unscored items go in their own group."""
    order = ["developments", "tools", "research", "production", "noise", None]
    groups: dict = {k: [] for k in order}
    for r in rows:
        groups.setdefault(r["category"], []).append(r)
    labels = dict(config.CATEGORY_LABELS)
    labels[None] = "Not yet scored"
    return [(labels.get(k, k), v) for k, v in groups.items() if v]


def render(name: str, ctx: dict):
    return templates.TemplateResponse(ctx["request"], name, ctx)


templates.env.filters["reldate"] = _fmt_date
templates.env.filters["md"] = detail.render_markdown
templates.env.globals["source_names"] = config.source_names()
templates.env.globals["category_labels"] = config.CATEGORY_LABELS
templates.env.globals["range_labels"] = RANGE_LABELS


def create_app(auto_refresh: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        with db.session() as conn:
            stale = refresh.is_stale(conn)
        if auto_refresh and stale:
            refresh.run_in_background()
        yield

    app = FastAPI(title="coffee", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def _list_context(request: Request):
        qp = request.query_params
        range_key = qp.get("range", "3d")
        if range_key not in RANGES:
            range_key = "3d"
        category = qp.get("category") or None
        source = qp.get("source") or None
        q = qp.get("q") or None
        show_hidden = qp.get("show_hidden") == "1"
        try:
            min_score = int(qp.get("min_score", "3"))
        except ValueError:
            min_score = 3
        with db.session() as conn:
            rows = db.query_items(
                conn,
                since=_since(range_key),
                category=category,
                source=source,
                min_score=min_score or None,
                q=q,
                show_hidden=show_hidden,
            )
        return {
            "request": request,
            "groups": _group(rows),
            "count": len(rows),
            "filters": {
                "range": range_key,
                "category": category or "",
                "source": source or "",
                "min_score": min_score,
                "q": q or "",
                "show_hidden": show_hidden,
            },
            "refreshing": refresh.is_running(),
        }

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        ctx = _list_context(request)
        if request.headers.get("HX-Request"):
            return render("_items.html", ctx)
        return render("index.html", ctx)

    @app.get("/weekly", response_class=HTMLResponse)
    def weekly(request: Request):
        since = datetime.now(UTC) - timedelta(days=7)
        with db.session() as conn:
            rows = db.query_items(conn, since=since, min_score=4, include_unscored=False, limit=1000)
        groups = [(label, items[:10]) for label, items in _group(rows) if label != "Noise"]
        ctx = {"request": request, "groups": groups, "count": sum(len(v) for _, v in groups),
               "filters": None, "refreshing": refresh.is_running()}
        return render("weekly.html", ctx)

    @app.post("/items/{item_id}/rate", response_class=HTMLResponse)
    def rate(request: Request, item_id: int, value: int = Form(...)):
        value = max(-1, min(1, value))
        with db.session() as conn:
            row = db.get_item(conn, item_id)
            if row is not None:
                # Clicking the active thumb clears it.
                db.set_rating(conn, item_id, 0 if row["rating"] == value else value)
            row = db.get_item(conn, item_id)
        return render("_item.html", {"request": request, "item": row})

    @app.post("/items/{item_id}/hide", response_class=HTMLResponse)
    def hide(request: Request, item_id: int):
        with db.session() as conn:
            row = db.get_item(conn, item_id)
            if row is not None:
                db.set_hidden(conn, item_id, not row["hidden"])
            row = db.get_item(conn, item_id)
        return render("_item.html", {"request": request, "item": row})

    def _detail_ctx(request: Request, item_id: int) -> dict:
        with db.session() as conn:
            row = db.get_item(conn, item_id)
        return {"request": request, "item": row, "has_key": detail.has_api_key()}

    @app.get("/items/{item_id}/more", response_class=HTMLResponse)
    def more(request: Request, item_id: int):
        ctx = _detail_ctx(request, item_id)
        ctx["error"] = None
        ctx["summary"] = None
        if ctx["item"] is None:
            return HTMLResponse("", status_code=404)
        if not ctx["has_key"]:
            ctx["error"] = "ANTHROPIC_API_KEY is not set, so a longer summary cannot be generated."
        else:
            try:
                with db.session() as conn:
                    ctx["summary"] = detail.long_summary(conn, ctx["item"])
            except Exception as exc:  # noqa: BLE001 - show the failure in the panel
                ctx["error"] = f"Could not generate a summary: {exc}"
        return render("_more.html", ctx)

    @app.get("/items/{item_id}/chat", response_class=HTMLResponse)
    def chat(request: Request, item_id: int):
        ctx = _detail_ctx(request, item_id)
        if ctx["item"] is None:
            return HTMLResponse("", status_code=404)
        with db.session() as conn:
            ctx["messages"] = db.chat_messages(conn, item_id)
        ctx["error"] = None
        return render("_chat.html", ctx)

    @app.post("/items/{item_id}/chat", response_class=HTMLResponse)
    def chat_send(request: Request, item_id: int, message: str = Form("")):
        ctx = _detail_ctx(request, item_id)
        if ctx["item"] is None:
            return HTMLResponse("", status_code=404)
        ctx["error"] = None
        message = message.strip()
        if message and not ctx["has_key"]:
            ctx["error"] = "ANTHROPIC_API_KEY is not set, so chat is unavailable."
        elif message:
            try:
                with db.session() as conn:
                    detail.chat_reply(conn, ctx["item"], message)
            except Exception as exc:  # noqa: BLE001
                ctx["error"] = f"Reply failed: {exc}"
        with db.session() as conn:
            ctx["messages"] = db.chat_messages(conn, item_id)
        return render("_chat.html", ctx)

    @app.post("/items/{item_id}/chat/clear", response_class=HTMLResponse)
    def chat_clear(request: Request, item_id: int):
        with db.session() as conn:
            db.clear_chat(conn, item_id)
        # Empty body collapses the detail panel; Discuss reopens it.
        return HTMLResponse("")

    @app.get("/briefing", response_class=HTMLResponse)
    def briefing_panel(request: Request, force: int = 0):
        ctx = {"request": request, "error": None, "has_key": detail.has_api_key(),
               "hours": config.BRIEFING_HOURS, "refreshing": refresh.is_running()}
        try:
            with db.session() as conn:
                if ctx["has_key"]:
                    data = briefing.get_briefing(conn, force=bool(force))
                else:
                    rows, threshold = briefing.select_items(conn)
                    data = {"items": {r["id"]: r for r in rows}, "threshold": threshold,
                            "count": len(rows), "briefing": None, "generated_at": None}
                    if rows:
                        ctx["error"] = "ANTHROPIC_API_KEY is not set, so the briefing cannot be generated."
        except Exception as exc:  # noqa: BLE001
            log.warning("briefing failed: %s", exc)
            data = {"items": {}, "threshold": config.BRIEFING_MIN_SCORE, "count": 0, "briefing": None, "generated_at": None}
            ctx["error"] = f"Could not generate the briefing: {exc}"
        ctx.update(data)
        return render("_briefing.html", ctx)

    @app.get("/status", response_class=HTMLResponse)
    def status(request: Request):
        with db.session() as conn:
            counts = db.source_counts(conn)
            last = refresh.last_refresh(conn)
        ctx = {
            "request": request,
            "counts": counts,
            "total": sum(r["n"] for r in counts),
            "unprocessed": sum(r["unprocessed"] or 0 for r in counts),
            "last_refresh": last.isoformat() if last else None,
            "refreshing": refresh.is_running(),
            "error": refresh.last_error(),
            "result": refresh.last_result(),
            "has_key": __import__("coffee.process", fromlist=["has_api_key"]).has_api_key(),
            "model": config.MODEL,
        }
        if request.headers.get("HX-Request"):
            return render("_status.html", ctx)
        return render("status.html", ctx)

    @app.post("/refresh")
    def start_refresh(request: Request):
        refresh.run_in_background()
        if request.headers.get("HX-Request"):
            return status(request)
        return RedirectResponse("/status", status_code=303)

    return app
