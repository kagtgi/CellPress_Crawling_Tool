"""papers_crawler — crawl papers from Cell.com, Nature.com, and PubMed."""

__version__ = "1.0.0"

# Regular sync API (for scripts and Streamlit)
from .cell.crawl_cell_pdf_sync import crawl, discover_journals

# Async API (for Colab/Jupyter notebooks)
from .cell.crawl_cell_pdf_async import crawl_async, discover_journals_async

# Cell.com text extraction
from .cell.crawl_cell_text_async import crawl_text_async

# Nature.com text extraction
from .nature.crawl_nature_async import (
    crawl_text_nature_async,
    crawl_titles_nature_async,
    discover_journals_nature_async,
    extract_fulltext_nature_as_json,
)

# PubMed crawling (NCBI E-utilities, no browser required)
from .pubmed.crawl_pubmed_async import (
    search_pubmed_async,
    crawl_pubmed_async,
    crawl_pubmed_journals_async,
)

__all__ = [
    # Cell.com – PDF download
    "crawl",
    "discover_journals",
    "crawl_async",
    "discover_journals_async",
    # Cell.com – full-text JSON
    "crawl_text_async",
    # Nature.com – full-text JSON and PDF
    "crawl_text_nature_async",
    "crawl_titles_nature_async",
    "discover_journals_nature_async",
    "extract_fulltext_nature_as_json",
    # PubMed – title & metadata crawling
    "search_pubmed_async",
    "crawl_pubmed_async",
    "crawl_pubmed_journals_async",
]
