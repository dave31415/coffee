import json
from pathlib import Path

from coffee.sources.arxiv import ArxivSource, build_query
from coffee.sources.hf_papers import HFPapersSource
from coffee.sources.rss import RSSSource, github_releases_source

FIX = Path(__file__).parent / "fixtures"


def test_rss_parse():
    items = RSSSource("simonwillison", "Simon Willison", "x").parse((FIX / "simonwillison.atom").read_bytes())
    assert len(items) == 3
    i = items[0]
    assert i.source == "simonwillison"
    assert i.url.startswith("https://simonwillison.net/")
    assert i.title and i.raw_text and "<" not in i.raw_text
    assert i.published_at.tzinfo is not None


def test_github_source_key_and_url():
    s = github_releases_source("stanfordnlp/dspy", "DSPy")
    assert s.key == "gh:dspy"
    assert s.url == "https://github.com/stanfordnlp/dspy/releases.atom"


def test_arxiv_parse_and_query():
    assert build_query(["cs.AI"], ["tool use"]) == '(cat:cs.AI) AND (abs:"tool use")'
    items = ArxivSource().parse((FIX / "arxiv.atom").read_bytes())
    assert len(items) == 2
    assert all(i.url.startswith("https://arxiv.org/abs/") for i in items)
    assert all(i.author for i in items)


def test_hf_parse():
    data = json.loads((FIX / "hf_papers.json").read_text())
    items = HFPapersSource().parse(data)
    assert len(items) == 2
    assert items[0].url.startswith("https://huggingface.co/papers/")
    assert items[0].raw_text
