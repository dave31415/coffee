import json
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from coffee import db, detail
from coffee.fulltext import extract
from coffee.models import Item
from coffee.sources.base import extract_links

HTML = """<html><head><title>T</title><style>p{}</style></head><body>
<nav><a href="/">Home</a></nav>
<script>var x=1;</script>
<h1>Agent Paper</h1><p>First para with a <a href="https://github.com/x/repo">repo link</a>.</p>
<p>Second &amp; para.</p><a href="#top">top</a><a href="https://github.com/x/repo">dup</a>
</body></html>"""


def test_extract_text_and_links():
    text, links = extract(HTML, "https://example.com/post")
    assert "var x" not in text and "p{}" not in text
    assert "Agent Paper" in text and "Second & para." in text
    assert links == [("Home", "https://example.com/"), ("repo link", "https://github.com/x/repo")]


def test_extract_links_from_feed_html():
    links = extract_links('<p>See <a href="https://a.com/p?x=1&amp;y=2">the <b>paper</b></a> and <a href="#f">note</a></p>')
    assert links == [("the paper", "https://a.com/p?x=1&y=2")]


def _seed(conn):
    db.insert_item(conn, Item(source="simonwillison", url="https://x.com/post", title="Post",
                              published_at=datetime.now(UTC), raw_text="body text " * 50,
                              links=[("paper", "https://arxiv.org/abs/1234.5678")]))
    return db.get_item(conn, 1)


class FakeMessages:
    def __init__(self):
        self.parse_calls = 0
        self.create_calls = 0
        self.last_messages = None

    def parse(self, **kw):
        self.parse_calls += 1
        out = detail.LongSummary(
            paragraphs=["Para one.", " ", "Para two."],
            links=[detail.Link(title="Paper", url="https://arxiv.org/abs/1234.5678", why="the paper"),
                   detail.Link(title="Fake", url="https://evil.example/made-up", why="hallucinated")],
        )
        return SimpleNamespace(stop_reason="end_turn", parsed_output=out)

    def create(self, **kw):
        self.create_calls += 1
        self.last_messages = kw["messages"]
        n = len(kw["messages"])
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=f"reply **{n}**")])


def test_long_summary_filters_links_and_caches(monkeypatch):
    monkeypatch.setattr("coffee.fulltext.fetch", lambda row: ("", []))  # offline: fall back to feed text
    conn = db.connect(":memory:")
    row = _seed(conn)
    fake = FakeMessages()
    client = SimpleNamespace(messages=fake)
    out = detail.long_summary(conn, row, client=client)
    assert out["paragraphs"] == ["Para one.", "Para two."]
    assert [l["url"] for l in out["links"]] == ["https://arxiv.org/abs/1234.5678"]
    row = db.get_item(conn, 1)
    assert row["full_fetched_at"] and row["full_text"].startswith("body text")
    assert detail.long_summary(conn, row, client=client) == out
    assert fake.parse_calls == 1


def test_chat_roundtrip_persists_history(monkeypatch):
    monkeypatch.setattr("coffee.fulltext.fetch", lambda row: ("full page text", [("r", "https://r.com")]))
    conn = db.connect(":memory:")
    row = _seed(conn)
    fake = FakeMessages()
    client = SimpleNamespace(messages=fake)
    assert detail.chat_reply(conn, row, "hi", client=client) == "reply **1**"
    assert detail.chat_reply(conn, row, "more", client=client) == "reply **3**"
    assert [m["role"] for m in db.chat_messages(conn, 1)] == ["user", "assistant", "user", "assistant"]
    assert fake.last_messages[0] == {"role": "user", "content": "hi"}
    links = json.loads(db.get_item(conn, 1)["links"])
    assert ["paper", "https://arxiv.org/abs/1234.5678"] in links and ["r", "https://r.com"] in links


def test_chat_routes(monkeypatch, tmp_path):
    monkeypatch.setattr("coffee.config.DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr("coffee.fulltext.fetch", lambda row: ("", []))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    fake = FakeMessages()
    monkeypatch.setattr("coffee.detail._client", lambda: SimpleNamespace(messages=fake))
    with db.session() as conn:
        _seed(conn)
    from coffee.web.app import create_app
    with TestClient(create_app(auto_refresh=False)) as c:
        r = c.get("/items/1/chat")
        assert r.status_code == 200 and "Ask anything" in r.text
        r = c.post("/items/1/chat", data={"message": "what is it?"})
        assert "what is it?" in r.text and "<strong>1</strong>" in r.text
        r = c.get("/items/1/explain")
        assert "Para one." in r.text and "evil.example" not in r.text
        r = c.post("/items/1/chat/clear")
        assert r.status_code == 200 and r.text == ""
        assert "Ask anything" in c.get("/items/1/chat").text
        assert c.get("/items/999/explain").status_code == 404
