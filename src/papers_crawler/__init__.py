"""papers_crawler package"""

# Regular sync API (for scripts and Streamlit)
from .crawler import crawl, discover_journals

# Async API (for Colab/Jupyter notebooks)
from .crawler_async import crawl_async, discover_journals_async

# Cell.com text extraction
from .crawl_text_async import crawl_text_async

# Nature.com text extraction
from .crawl_text_async_nature import (
    crawl_text_nature_async,
    discover_journals_nature_async,
    extract_fulltext_nature_as_json,
)

__all__ = [
    "crawl",
    "discover_journals",
    "crawl_async",
    "discover_journals_async",
    "crawl_colab",
    "discover_journals_colab",
    "crawl_text_async",
    "crawl_text_nature_async",
    "discover_journals_nature_async",
    "extract_fulltext_nature_as_json",
]
