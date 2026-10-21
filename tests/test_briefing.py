from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from coffee import briefing, db
from coffee.models import Item


def _seed(conn, scores, hours_ago=1):
    for i, s in enumerate(scores):
        db.insert_item(conn, Item(source="s", url=f"https://x.com/{i}", title=f"Title {i}",
                                  published_at=datetime.now(UTC) - timedelta(hours=hours_ago), raw_text="t"))
        db.save_judgment(conn, i + 1, "tools", s, f"summary {i}")


class FakeMessages:
    def __init__(self):
        self.calls = 0

    def parse(self, **kw):
        self.calls += 1
        text = kw["messages"][0]["content"]
        ids = [int(s.split('"')[0]) for s in text.split('<item id="')[1:]]
        out = briefing.Briefing(lede="Big day.", bullets=[
            briefing.Bullet(text="First thing.", item_ids=[ids[0], 999]),
            briefing.Bullet(text="  ", item_ids=[]),
        ])
        return SimpleNamespace(stop_reason="end_turn", parsed_output=out)


def test_select_uses_threshold_then_falls_back():
    conn = db.connect(":memory:")
    _seed(conn, [5, 4, 4, 3, 2])
    rows, t = briefing.select_items(conn)
    assert t == 4 and [r["score"] for r in rows] == [5, 4, 4]
    conn = db.connect(":memory:")
    _seed(conn, [4, 3, 3, 2])
    rows, t = briefing.select_items(conn)
    assert t == 3 and len(rows) == 3


def test_old_items_excluded_unless_newly_fetched():
    conn = db.connect(":memory:")
    _seed(conn, [5, 5], hours_ago=48)
    rows, _ = briefing.select_items(conn)
    assert len(rows) == 2  # fetched just now, so still news to the reader
    conn.execute("UPDATE items SET fetched_at = '2020-01-01T00:00:00+00:00'")
    rows, _ = briefing.select_items(conn)
    assert rows == []


def test_generate_filters_and_caches_by_item_set():
    conn = db.connect(":memory:")
    _seed(conn, [5, 4])
    fake = FakeMessages()
    client = SimpleNamespace(messages=fake)
    out = briefing.get_briefing(conn, client=client)
    assert out["briefing"]["lede"] == "Big day."
    assert out["briefing"]["bullets"] == [{"text": "First thing.", "item_ids": [1]}]
    assert set(out["items"]) == {1, 2}
    briefing.get_briefing(conn, client=client)
    assert fake.calls == 1
    # A new qualifying item changes the set and regenerates.
    db.insert_item(conn, Item(source="s", url="https://x.com/new", title="New", published_at=datetime.now(UTC)))
    db.save_judgment(conn, 3, "research", 5, "s")
    briefing.get_briefing(conn, client=client)
    assert fake.calls == 2
    briefing.get_briefing(conn, client=client, force=True)
    assert fake.calls == 3


def test_briefing_route(monkeypatch, tmp_path):
    monkeypatch.setattr("coffee.config.DB_PATH", tmp_path / "t.db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr("coffee.briefing._client", lambda: SimpleNamespace(messages=FakeMessages()))
    with db.session() as conn:
        _seed(conn, [5, 4, 4])
    from coffee.web.app import create_app
    with TestClient(create_app(auto_refresh=False)) as c:
        r = c.get("/briefing")
        assert r.status_code == 200 and "Big day." in r.text and 'href="https://x.com/0"' in r.text
        assert "3 items scored 4+" in r.text
        assert 'hx-get="/briefing"' in c.get("/").text


def test_briefing_route_without_key(monkeypatch, tmp_path):
    monkeypatch.setattr("coffee.config.DB_PATH", tmp_path / "t.db")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with db.session() as conn:
        _seed(conn, [5])
    from coffee.web.app import create_app
    with TestClient(create_app(auto_refresh=False)) as c:
        r = c.get("/briefing")
        assert r.status_code == 200 and "ANTHROPIC_API_KEY" in r.text
