"""License-aware full-text JSON crawler.

The v2 public surface is deliberately browser- and PDF-free. Legacy browser
modules remain importable by fully qualified name when their optional extra is
installed, but importing this package never imports Playwright or Streamlit.
"""

from .article import (
    SCHEMA_VERSION,
    article_id_for,
    content_sha256,
    load_article_schema,
    validate_article_document,
    verify_content_hash,
)
from .corpus import CrawlConfig, sync_corpus

__version__ = "2.0.0"

__all__ = [
    "SCHEMA_VERSION",
    "CrawlConfig",
    "article_id_for",
    "content_sha256",
    "load_article_schema",
    "sync_corpus",
    "validate_article_document",
    "verify_content_hash",
]
