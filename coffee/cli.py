"""Command line: coffee collect | process | refresh | serve."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
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


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _listeners(port: int) -> list[tuple[int, str]]:
    """Return (pid, command) for processes listening on the port, via lsof."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for tok in out.split():
        try:
            pid = int(tok)
        except ValueError:
            continue
        cmd = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
        found.append((pid, cmd))
    return found


def _is_coffee(cmd: str) -> bool:
    return "coffee" in cmd and ("serve" in cmd or "uvicorn" in cmd)


def claim_port(host: str, port: int) -> bool:
    """Make the port available, killing an earlier coffee server if one holds it.

    Returns False if the port is held by something that is not coffee.
    """
    if _port_free(host, port):
        return True
    listeners = _listeners(port)
    others = [(pid, cmd) for pid, cmd in listeners if not _is_coffee(cmd)]
    if others or not listeners:
        who = "; ".join(f"pid {pid}: {cmd}" for pid, cmd in others) or "unknown process"
        print(f"port {port} is in use by {who}", file=sys.stderr)
        print(f"stop it or run with a different port: PORT=8766 coffee", file=sys.stderr)
        return False
    for pid, _ in listeners:
        print(f"stopping earlier coffee server (pid {pid})", flush=True)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + 5
    while time.time() < deadline:
        if _port_free(host, port):
            return True
        time.sleep(0.2)
    for pid, _ in listeners:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.time() + 3
    while time.time() < deadline:
        if _port_free(host, port):
            return True
        time.sleep(0.2)
    print(f"could not free port {port}", file=sys.stderr)
    return False


def serve(host: str, port: int, open_browser: bool, auto_refresh: bool) -> int:
    import uvicorn

    from .web.app import create_app

    if not claim_port(host, port):
        return 1
    app = create_app(auto_refresh=auto_refresh)
    url = f"http://{host}:{port}"
    print(f"coffee is brewing at {url}", flush=True)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
