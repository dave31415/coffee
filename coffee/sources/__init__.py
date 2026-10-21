"""Source registry."""
from __future__ import annotations

from .. import config
from .arxiv import ArxivSource
from .base import Source
from .hf_papers import HFPapersSource
from .rss import RSSSource, github_releases_source


def all_sources() -> list[Source]:
    sources: list[Source] = [RSSSource(key, name, url) for key, name, url in config.RSS_FEEDS]
    sources += [github_releases_source(repo, name) for repo, name in config.GITHUB_REPOS]
    sources.append(ArxivSource())
    sources.append(HFPapersSource())
    return sources
