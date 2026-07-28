from __future__ import annotations

import gzip
import json

import pytest

from papers_crawler.article import validate_article_document, verify_content_hash
from papers_crawler.corpus import CrawlConfig, sync_corpus
from papers_crawler.normalize import jats_to_article, metadata_to_article
from papers_crawler.providers import (
    PdfRejected,
    _request,
    fetch_crossref_tdm,
    fetch_reusable_full_text,
)

META = {
    "doi": "10.1000/TEST",
    "title": "Single-cell expression",
    "journal": "Cell",
    "publisher_family": "cell_press",
    "canonical_url": "https://doi.org/10.1000/test",
    "issns": ["0092-8674"],
    "published": "2024-01-01",
    "article_type": "journal-article",
    "language": "en",
}

JATS = b"""<article><front><article-meta>
<article-id pub-id-type="doi">10.1000/test</article-id>
<title-group><article-title>Single-cell expression</article-title></title-group>
</article-meta></front><body><sec><title>Data availability</title>
<p>Counts are available under GSE123456.</p></sec></body></article>"""


def test_jats_contract_and_hash():
    doc = jats_to_article(
        JATS,
        META,
        provider="fixture",
        retrieval_basis="test",
        license_url="https://creativecommons.org/licenses/by/4.0/",
    )
    validate_article_document(doc)
    assert verify_content_hash(doc)
    assert doc["content"]["sections"][0]["paragraphs"][0]["paragraph_id"] == "s1-p1"


def test_production_default_is_one_paper_per_minute(tmp_path):
    assert CrawlConfig(tmp_path).min_interval_seconds == 60.0


def test_metadata_has_explicit_terminal_state():
    doc = metadata_to_article(
        META, provider="crossref", status="license_unknown"
    )
    validate_article_document(doc)
    assert doc["access"]["reuse_allowed"] is False


def test_pdf_is_rejected_before_request():
    with pytest.raises(PdfRejected):
        _request(object(), "https://example.org/article.pdf")


def test_sync_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "papers_crawler.corpus.enrich_europe_pmc", lambda metadata: metadata
    )
    monkeypatch.setattr(
        "papers_crawler.corpus.fetch_reusable_full_text",
        lambda metadata: (
            None,
            None,
            None,
            [
                __import__(
                    "papers_crawler.providers", fromlist=["ProviderAttempt"]
                ).ProviderAttempt("fixture", "", "no_machine_endpoint")
            ],
        ),
    )
    items = [(META, "cursor-2")]
    first = sync_corpus(
        CrawlConfig(tmp_path, min_interval_seconds=0), discovered=items
    )
    second = sync_corpus(
        CrawlConfig(tmp_path, min_interval_seconds=0), discovered=items
    )
    assert first["metadata_only"] == second["metadata_only"] == 1
    article_files = list((tmp_path / "articles").rglob("*.json.gz"))
    assert len(article_files) == 1
    with gzip.open(article_files[0], "rt", encoding="utf-8") as handle:
        assert json.load(handle)["article_id"] == "doi:10.1000/test"
    import sqlite3

    connection = sqlite3.connect(tmp_path / "crawl-state.db")
    assert connection.execute(
        "SELECT status FROM processing_queue"
    ).fetchone()[0] == "pending"
    assert connection.execute("SELECT status FROM crawl_queue").fetchone()[0] == "done"
    connection.close()


def test_tdm_link_is_not_license_proof():
    body, attempt = fetch_crossref_tdm(
        {**META, "tdm_links": [{"URL": "https://example.org/article.xml"}]}
    )
    assert body is None
    assert attempt.status == "license_unknown"


def test_keyless_chain_still_reaches_terminal_state(monkeypatch):
    monkeypatch.delenv("ELSEVIER_API_KEY", raising=False)
    monkeypatch.delenv("SPRINGER_NATURE_API_KEY", raising=False)
    body, provider, basis, attempts = fetch_reusable_full_text(META)
    assert body is provider is basis is None
    assert attempts
    assert all(attempt.status in {"license_unknown", "no_machine_endpoint"} for attempt in attempts)
