from datetime import UTC, datetime
from types import SimpleNamespace

from coffee import db
from coffee.models import Item
from coffee.process import Judgment, Judgments, process


class FakeMessages:
    def __init__(self, fail_on_call=None):
        self.calls = 0
        self.fail_on_call = fail_on_call

    def parse(self, **kwargs):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("boom")
        assert kwargs["output_format"] is Judgments
        text = kwargs["messages"][0]["content"]
        ids = [int(s.split('"')[0]) for s in text.split('<item id="')[1:]]
        js = [Judgment(id=i, category="tools", score=4, summary=f"sum {i}") for i in ids]
        return SimpleNamespace(stop_reason="end_turn", parsed_output=Judgments(judgments=js))


def _seed(conn, n):
    for i in range(n):
        db.insert_item(conn, Item(source="s", url=f"https://x.com/{i}", title=f"T{i}", published_at=datetime.now(UTC), raw_text="body"))


def test_process_batches_and_writes_back():
    conn = db.connect(":memory:")
    _seed(conn, 7)
    fake = FakeMessages()
    client = SimpleNamespace(messages=fake)
    assert process(conn, client=client, batch_size=3) == 7
    assert fake.calls == 3
    assert conn.execute("select count(*) from items where processed_at is null").fetchone()[0] == 0
    row = conn.execute("select category, score, summary from items limit 1").fetchone()
    assert (row["category"], row["score"]) == ("tools", 4)


def test_failed_batch_is_left_unprocessed():
    conn = db.connect(":memory:")
    _seed(conn, 6)
    client = SimpleNamespace(messages=FakeMessages(fail_on_call=1))
    assert process(conn, client=client, batch_size=3) == 3
    assert conn.execute("select count(*) from items where processed_at is null").fetchone()[0] == 3


def test_process_without_key_is_noop(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    conn = db.connect(":memory:")
    _seed(conn, 2)
    assert process(conn) == 0
