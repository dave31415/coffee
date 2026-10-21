from datetime import UTC, datetime

from coffee import db
from coffee.models import Item, normalize_url, title_key


def test_normalize_url_strips_tracking_and_trailing_slash():
    assert normalize_url("http://www.Example.com/a/b/?utm_source=x&id=2#frag") == "https://example.com/a/b?id=2"
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_title_key():
    assert title_key("  Hello, World!  (v2) ") == "hello world v2"


def _item(url, title="Same Title", source="a"):
    return Item(source=source, url=url, title=title, published_at=datetime.now(UTC), raw_text="x")


def test_insert_dedupes_by_url_and_recent_title():
    conn = db.connect(":memory:")
    assert db.insert_item(conn, _item("https://a.com/post"))
    assert not db.insert_item(conn, _item("http://www.a.com/post/?utm_campaign=z"))
    assert not db.insert_item(conn, _item("https://news.ycombinator.com/item?id=1", source="hn"))
    assert db.insert_item(conn, _item("https://b.com/other", title="Different"))
    assert conn.execute("select count(*) from items").fetchone()[0] == 2


def test_query_items_filters():
    conn = db.connect(":memory:")
    db.insert_item(conn, _item("https://a.com/1", title="One"))
    db.insert_item(conn, _item("https://a.com/2", title="Two"))
    rows = db.query_items(conn)
    db.save_judgment(conn, rows[0]["id"], "tools", 5, "s")
    db.save_judgment(conn, rows[1]["id"], "noise", 1, "s")
    assert [r["score"] for r in db.query_items(conn, min_score=3, include_unscored=False)] == [5]
    assert len(db.query_items(conn, category="noise")) == 1
    db.set_hidden(conn, rows[1]["id"], True)
    assert len(db.query_items(conn)) == 1
    assert len(db.query_items(conn, show_hidden=True)) == 2


def test_collect_skips_items_older_than_cutoff():
    from datetime import timedelta

    from coffee.collect import collect

    class Src:
        key = "t"
        name = "t"

        def fetch(self):
            now = datetime.now(UTC)
            return [_item("https://t.com/new", title="New"),
                    Item(source="t", url="https://t.com/old", title="Old", published_at=now - timedelta(days=400))]

    conn = db.connect(":memory:")
    assert collect(conn, [Src()]) == {"t": 1}
    assert conn.execute("select title from items").fetchone()[0] == "New"
