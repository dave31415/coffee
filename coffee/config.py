"""Static configuration: sources, paths, model."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.environ.get("COFFEE_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "coffee.db"
PROFILE_PATH = ROOT / "profile.md"

MODEL = os.environ.get("COFFEE_MODEL", "claude-haiku-5-5")
# Used for the More / Discuss features: few calls, quality matters more than cost.
DETAIL_MODEL = os.environ.get("COFFEE_DETAIL_MODEL", "claude-sonnet-5-5")
STALE_HOURS = float(os.environ.get("COFFEE_STALE_HOURS", "6"))
HTTP_TIMEOUT = 20.0
RAW_TEXT_LIMIT = 3000
FULL_TEXT_LIMIT = 60000
MAX_LINKS = 120
DEDUPE_TITLE_DAYS = 14
# Feeds that carry their whole archive would otherwise backfill years of items.
MAX_ITEM_AGE_DAYS = 14
LLM_BATCH_SIZE = 10

# Daily briefing in the sidebar. 36h so a morning read includes yesterday's arXiv batch,
# which lands once a day around 18:00 UTC.
BRIEFING_HOURS = 36
BRIEFING_MIN_SCORE = 4
BRIEFING_MAX_ITEMS = 40

# (source key, display name, feed url)
RSS_FEEDS: list[tuple[str, str, str]] = [
    # Practitioners and aggregators
    ("simonwillison", "Simon Willison", "https://simonwillison.net/atom/everything/"),
    ("latentspace", "Latent Space", "https://www.latent.space/feed"),
    (
        "hackernews",
        "Hacker News",
        "https://hnrss.org/newest?q=agent%20OR%20agentic%20OR%20LLM%20OR%20MCP%20OR%20OpenAI"
        "%20OR%20Anthropic%20OR%20Claude%20OR%20GPT%20OR%20Gemini&points=50",
    ),
    # Labs (Anthropic has no feed; its news arrives via the sources above)
    ("openai", "OpenAI", "https://openai.com/news/rss.xml"),
    ("deepmind", "Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("googleai", "Google AI", "https://blog.google/technology/ai/rss/"),
    ("qwen", "Qwen", "https://qwenlm.github.io/blog/index.xml"),
    ("hfblog", "HF Blog", "https://huggingface.co/blog/feed.xml"),
    # Newsletters
    ("importai", "Import AI", "https://importai.substack.com/feed"),
    ("interconnects", "Interconnects", "https://www.interconnects.ai/feed"),
    ("aheadofai", "Ahead of AI", "https://magazine.sebastianraschka.com/feed"),
    ("smolai", "AI News (smol.ai)", "https://news.smol.ai/rss.xml"),
    # Practitioner blogs
    ("eugeneyan", "Eugene Yan", "https://eugeneyan.com/rss/"),
    ("hamel", "Hamel Husain", "https://hamel.dev/index.xml"),
    ("lilianweng", "Lilian Weng", "https://lilianweng.github.io/index.xml"),
]

GITHUB_REPOS: list[tuple[str, str]] = [
    ("langchain-ai/langgraph", "LangGraph"),
    ("openai/openai-agents-python", "OpenAI Agents SDK"),
    ("anthropics/claude-code", "Claude Code"),
    ("stanfordnlp/dspy", "DSPy"),
    ("modelcontextprotocol/modelcontextprotocol", "MCP"),
    ("Arize-ai/phoenix", "Arize Phoenix"),
]

ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.MA"]
ARXIV_TERMS = [
    "LLM agent",
    "language agent",
    "tool use",
    "agentic",
    "multi-agent",
    "long-horizon",
    "context engineering",
]
ARXIV_MAX_RESULTS = 100

HF_DAILY_PAPERS_URL = "https://huggingface.co/api/daily_papers"

CATEGORIES = ["developments", "tools", "research", "production", "noise"]
CATEGORY_LABELS = {
    "developments": "Important developments",
    "tools": "New tools",
    "research": "Research",
    "production": "Production lessons",
    "noise": "Noise",
}


def source_names() -> dict[str, str]:
    """Map source key -> display name for every configured source."""
    names = {key: name for key, name, _ in RSS_FEEDS}
    for repo, name in GITHUB_REPOS:
        names[f"gh:{repo.split('/')[1]}"] = name
    names["arxiv"] = "arXiv"
    names["hfpapers"] = "HF Papers"
    return names
