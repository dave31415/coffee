"""Command line: coffee collect | process | refresh | serve."""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser

from . import db
from .collect import collect
from .process import process
from .refresh import run as refresh_run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="coffee", description="Agentic-AI news viewer")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect", help="fetch sources and store new items")
    p = sub.add_parser("process", help="score unprocessed items with Claude")
    p.add_argument("--limit", type=int)
    sub.add_parser("refresh", help="collect then process")
    s = sub.add_parser("serve", help="run the web viewer")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--open", action="store_true", help="open the browser once the server is up")
    s.add_argument("--no-refresh", action="store_true", help="do not auto-refresh on startup")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.cmd == "collect":
        with db.session() as conn:
            counts = collect(conn)
        print(f"new items: {sum(v for v in counts.values() if v > 0)}")
        return 0
    if args.cmd == "process":
        with db.session() as conn:
            print(f"judged: {process(conn, limit=args.limit)}")
        return 0
    if args.cmd == "refresh":
        counts = refresh_run()
        print(f"new items: {sum(v for v in counts.values() if v > 0) if counts else 0}")
        return 0
    if args.cmd == "serve":
        return serve(args.host, args.port, args.open, not args.no_refresh)
    return 1


def serve(host: str, port: int, open_browser: bool, auto_refresh: bool) -> int:
    import uvicorn

    from .web.app import create_app

    app = create_app(auto_refresh=auto_refresh)
    url = f"http://{host}:{port}"
    print(f"coffee is brewing at {url}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
