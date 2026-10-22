import socket

from coffee import cli


def test_claim_port_when_free():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert cli.claim_port("127.0.0.1", port) is True


def test_is_coffee_matches_only_our_server():
    assert cli._is_coffee("/x/.venv/bin/python /x/.venv/bin/coffee serve --port 8765 --open")
    assert not cli._is_coffee("/usr/bin/python3 -m http.server 8765")
    assert not cli._is_coffee("coffee collect")
