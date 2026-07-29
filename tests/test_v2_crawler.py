from __future__ import annotations

import gzip
import json

import pytest

from papers_crawler.article import validate_article_document, verify_content_hash
from papers_crawler.corpus import CrawlConfig, sync_corpus
from papers_crawler.normalize import (
    html_to_article,
    jats_to_article,
    metadata_to_article,
)
from papers_crawler.providers import (
    PdfRejected,
    _request,
    fetch_crossref_tdm,
    fetch_nature_html,
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

NATURE_HTML = b"""<!doctype html><html><body><article>
<h1 class="c-article-title">Single-cell expression</h1>
<section id="Abs1"><h2>Abstract</h2><p>We profiled human T cells.</p></section>
<section data-title="Methods"><h2>Methods</h2>
<p>Libraries used 10x Genomics Chromium.</p></section>
<section data-title="Data availability"><h2>Data availability</h2>
<p>Counts are available under GSE123456.</p></section>
<figure id="Fig1"><img src="/image.jpg"><figcaption>Expression overview.</figcaption>
</figure><script>window.secret = true</script>
</article></body></html>"""


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


def test_retrieval_timestamp_does_not_create_a_new_content_version():
    first = jats_to_article(
        JATS,
        META,
        provider="fixture",
        retrieval_basis="test",
        license_url="https://creativecommons.org/licenses/by/4.0/",
    )
    second = json.loads(json.dumps(first))
    second["provenance"]["retrieved_at"] = "2099-01-01T00:00:00Z"
    assert first["provenance"]["content_sha256"] == second["provenance"][
        "content_sha256"
    ]
    assert verify_content_hash(second)


def test_nature_html_contract_omits_media_and_preserves_prose():
    doc = html_to_article(
        NATURE_HTML,
        {**META, "publisher_family": "nature_portfolio"},
        provider="nature_html",
        retrieval_basis="CC-licensed Nature HTML",
        license_url="https://creativecommons.org/licenses/by/4.0/",
    )
    validate_article_document(doc)
    assert verify_content_hash(doc)
    assert doc["provenance"]["source_format"] == "text/html"
    assert doc["content"]["abstract"][0]["text"] == "We profiled human T cells."
    assert doc["content"]["data_availability"][0]["paragraph_id"] == "s2-p1"
    assert doc["content"]["figures"][0]["caption"] == "Expression overview."
    serialized = json.dumps(doc)
    assert "image.jpg" not in serialized
    assert "window.secret" not in serialized


class _NatureSession:
    def __init__(self):
        self.headers = None

    def get(self, url, params=None, timeout=None, headers=None):
        self.headers = headers
        response = _StubResponse("https://www.nature.com/articles/s41586-test")
        response.headers = {"content-type": "text/html; charset=utf-8"}
        response.content = NATURE_HTML
        return response


def test_nature_html_is_keyless_license_gated_and_uses_tdm_agent():
    session = _NatureSession()
    body, attempt = fetch_nature_html(
        {
            **META,
            "publisher_family": "nature_portfolio",
            "canonical_url": "https://doi.org/10.1038/s41586-test",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
        },
        session=session,
    )
    assert body == NATURE_HTML
    assert attempt.status == "reusable_full_text"
    assert "TextDataMining" in session.headers["User-Agent"]
    assert "text/html" in session.headers["Accept"]

    body, attempt = fetch_nature_html(
        {
            **META,
            "publisher_family": "nature_portfolio",
            "canonical_url": "https://www.nature.com/articles/s41586-test",
        },
        session=session,
    )
    assert body is None
    assert attempt.status == "license_unknown"


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
        CrawlConfig(tmp_path, min_interval_seconds=0.001), discovered=items
    )
    second = sync_corpus(
        CrawlConfig(tmp_path, min_interval_seconds=0.001), discovered=items
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


def test_keyless_chain_still_reaches_terminal_state():
    body, provider, basis, attempts = fetch_reusable_full_text(META)
    assert body is provider is basis is None
    assert attempts
    assert all(attempt.status in {"license_unknown", "no_machine_endpoint"} for attempt in attempts)


def test_keyed_publisher_providers_are_gone():
    """The keyed Elsevier/Springer fetchers were removed with the web->JSON
    switch. Asserting their *absence* is stronger than monkeypatching them out:
    a reader must not find a fetcher implying a capability production lacks.
    """
    import pathlib

    from papers_crawler import providers

    for name in ("fetch_elsevier_xml", "fetch_springer_jats"):
        assert not hasattr(providers, name), f"{name} should have been removed"
    source = pathlib.Path(providers.__file__).read_text(encoding="utf-8")
    for env in ("ELSEVIER_API_KEY", "SPRINGER_NATURE_API_KEY"):
        assert env not in source, f"{env} must not be read anywhere"


def test_default_paper_interval_is_sixty_seconds():
    """The 60s politeness gate is the documented default; guard it."""
    from papers_crawler.cli import _parser
    from papers_crawler.corpus import CrawlConfig

    args = _parser().parse_args(["sync", "--output", "out"])
    assert args.paper_interval == 60.0
    assert CrawlConfig(output_dir="out").min_interval_seconds == 60.0


def test_zero_or_negative_paper_interval_is_rejected(tmp_path):
    """`--paper-interval 0` must not silently disable throttling."""
    import pytest

    from papers_crawler.corpus import CrawlConfig

    for bad in (0, 0.0, -1):
        with pytest.raises(ValueError, match="greater than zero"):
            CrawlConfig(output_dir=tmp_path, min_interval_seconds=bad)


def test_max_articles_flag_is_plumbed_into_config(tmp_path):
    """corpus_job.sh pilot mode relies on --max-articles reaching CrawlConfig."""
    from papers_crawler.cli import _parser
    from papers_crawler.corpus import CrawlConfig

    args = _parser().parse_args(
        ["sync", "--output", str(tmp_path), "--max-articles", "20"]
    )
    assert args.max_articles == 20
    assert CrawlConfig(output_dir=tmp_path, max_articles=20).max_articles == 20


# Crossref rejects unknown query parameters with a 400 validation-failure rather
# than ignoring them, so a single stray param silently kills all discovery. The
# v2.0.0 crawler sent "cursor-max", which is not a Crossref parameter:
#   {"type":"unknown-parameter","value":"cursor-max",
#    "message":"Parameter cursor-max specified but there is no such parameter
#               available on any route"}
_CROSSREF_ALLOWED_PARAMS = {
    "filter", "cursor", "rows", "select", "sort", "order", "query", "offset",
    "sample", "facet", "mailto",
}


class _CapturingSession:
    """Session stub that records the params of the first request and stops."""

    def __init__(self):
        self.params = None

    def get(self, url, params=None, timeout=None, headers=None):
        self.params = params
        return _StubResponse(url)


class _StubResponse:
    status_code = 200

    def __init__(self, url):
        self.url = url
        self.headers = {"content-type": "application/json"}
        self.content = b"{}"

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"items": [], "next-cursor": None}}


def test_crossref_sends_only_known_parameters():
    from papers_crawler.providers import discover_crossref

    session = _CapturingSession()
    list(discover_crossref(start_year=2024, end_year=2024, session=session))

    assert session.params is not None, "no Crossref request was made"
    unknown = set(session.params) - _CROSSREF_ALLOWED_PARAMS
    assert not unknown, f"Crossref would 400 on unknown parameter(s): {unknown}"
    assert "cursor-max" not in session.params
    # cursor paging still bounded by rows
    assert session.params["cursor"] == "*"
    assert int(session.params["rows"]) > 0
    assert "issn:" in session.params["filter"]


def test_registry_excludes_multidisciplinary_by_default():
    """Nature/Nat Comms/Sci Reports publish all of science; Crossref exposes no
    usable `subject`, so an ISSN filter alone admits engineering and maths."""
    from papers_crawler.providers import load_journals

    names = {j["name"] for j in load_journals()}
    for banned in ("Nature", "Nature Communications", "Scientific Reports"):
        assert banned not in names, f"{banned} is multidisciplinary"
    # dedicated life-science journals are still present
    for kept in ("Cell", "Neuron", "Immunity", "Nature Genetics", "eLife"):
        assert kept in names
    assert all(j.get("scope") == "life_science" for j in load_journals())


def test_multidisciplinary_available_on_request():
    from papers_crawler.providers import load_journals

    names = {j["name"] for j in load_journals(include_multidisciplinary=True)}
    assert {"Nature", "Nature Communications", "Scientific Reports"} <= names


def test_every_registry_entry_is_well_formed():
    from papers_crawler.providers import load_journals

    for j in load_journals(include_multidisciplinary=True):
        assert j["name"] and j["publisher_family"]
        assert j["issns"], j["name"]
        assert j.get("scope") in {"life_science", "multidisciplinary"}
        for issn in j["issns"]:
            assert len(issn) == 9 and issn[4] == "-", (j["name"], issn)


def test_no_duplicate_issns_across_registry():
    from papers_crawler.providers import load_journals

    seen = {}
    for j in load_journals(include_multidisciplinary=True):
        for issn in j["issns"]:
            assert issn not in seen, f"{issn} in both {seen.get(issn)} and {j['name']}"
            seen[issn] = j["name"]


import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize(
    "title, ok",
    [
        ("A single-cell atlas of human liver", True),
        ("Correction: Genome-wide identification of X", False),
        ("Author Correction: Autoimmune response to C9orf72", False),
        ("Publisher Correction: something", False),
        ("Retraction Note: a paper", False),
        ("Erratum: another paper", False),
        ("Corrigendum to a paper", False),
        ("Comment on the recent findings", False),
        ("Reply to Smith et al.", False),
        ("", False),
    ],
)
def test_non_research_records_are_filtered(title, ok):
    from papers_crawler.providers import is_research_article

    assert is_research_article({"title": title}) is ok


# A transient Crossref 500 during deep cursor pagination previously aborted the
# whole backfill: _request called raise_for_status() with no retry.
class _FlakySession:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls += 1
        status = self.statuses.pop(0) if self.statuses else 200
        return _FlakyResponse(status, url)


class _FlakyResponse:
    def __init__(self, status_code, url, retry_after=None):
        self.status_code = status_code
        self.url = url
        self.headers = {"content-type": "application/json"}
        if retry_after:
            self.headers["retry-after"] = retry_after
        self.content = b"{}"

    def close(self):
        return None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise __import__("requests").HTTPError(f"{self.status_code}")


def test_request_retries_transient_server_errors(monkeypatch):
    from papers_crawler import providers

    slept = []
    monkeypatch.setattr(providers.time, "sleep", lambda s: slept.append(s))
    session = _FlakySession([500, 503, 429])
    response = providers._request(session, "https://api.crossref.org/works")
    assert response.status_code == 200
    assert session.calls == 4          # three transient failures, then success
    assert len(slept) == 3


def test_request_honours_retry_after(monkeypatch):
    from papers_crawler import providers

    slept = []
    monkeypatch.setattr(providers.time, "sleep", lambda s: slept.append(s))

    class _Once:
        def __init__(self):
            self.calls = 0

        def get(self, url, params=None, timeout=None, headers=None):
            self.calls += 1
            if self.calls == 1:
                return _FlakyResponse(503, url, retry_after="9")
            return _FlakyResponse(200, url)

    providers._request(_Once(), "https://api.crossref.org/works")
    assert slept == [9.0]


def test_request_still_raises_after_exhausting_attempts(monkeypatch):
    import requests as _requests

    from papers_crawler import providers

    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    session = _FlakySession([500] * 20)
    with _pytest.raises(_requests.HTTPError):
        providers._request(session, "https://api.crossref.org/works")


def test_pdf_rejection_is_never_retried(monkeypatch):
    from papers_crawler import providers
    from papers_crawler.providers import PdfRejected

    monkeypatch.setattr(providers.time, "sleep", lambda s: None)

    class _Pdf:
        calls = 0

        def get(self, url, params=None, timeout=None, headers=None):
            _Pdf.calls += 1
            r = _FlakyResponse(200, url)
            r.headers["content-type"] = "application/pdf"
            return r

    session = _Pdf()
    with _pytest.raises(PdfRejected):
        providers._request(session, "https://example.org/x")
    assert _Pdf.calls == 1, "PDF rejection must be terminal, not retried"


# Crossref serves the first page of a 2010-2026 x 50-ISSN query (212k results)
# but 500s once the cursor walks deep into it, which aborted the backfill. Chunk
# discovery per year so each cursor walk stays shallow.
class _YearRecordingSession:
    """Records the pub-date filter of every request; 1 item then end per year."""

    def __init__(self):
        self.filters = []
        self.cursors = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.filters.append(params["filter"])
        self.cursors.append(params["cursor"])
        return _YearResponse(url)


class _YearResponse:
    status_code = 200

    def __init__(self, url):
        self.url = url
        self.headers = {"content-type": "application/json"}
        self.content = b"{}"

    def close(self):
        return None

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "message": {
                "items": [{"DOI": "10.1/x", "title": ["T"], "ISSN": ["0092-8674"],
                           "container-title": ["Cell"], "type": "journal-article"}],
                "next-cursor": None,
            }
        }


def test_discovery_is_chunked_one_year_at_a_time():
    from papers_crawler.providers import discover_crossref

    s = _YearRecordingSession()
    list(discover_crossref(start_year=2010, end_year=2014, session=s,
                           newest_first=False))

    assert len(s.filters) == 5, "expected one request per year"
    for offset, flt in enumerate(s.filters):
        year = 2010 + offset
        assert f"from-pub-date:{year}-01-01" in flt
        assert f"until-pub-date:{year}-12-31" in flt
    # never a single multi-year span (that is what Crossref 500s on)
    assert not any("from-pub-date:2010-01-01" in f and "until-pub-date:2014-12-31" in f
                   for f in s.filters)
    # every year starts a fresh cursor
    assert s.cursors == ["*"] * 5


def test_yielded_cursor_is_year_tagged_and_resumable():
    from papers_crawler.providers import discover_crossref

    class _Paging(_YearRecordingSession):
        def get(self, url, params=None, timeout=None, headers=None):
            self.filters.append(params["filter"])
            self.cursors.append(params["cursor"])
            r = _YearResponse(url)
            first = len(self.filters) == 1
            r.json = lambda: {"message": {
                "items": [{"DOI": "10.1/x", "title": ["T"], "ISSN": ["0092-8674"],
                           "container-title": ["Cell"], "type": "journal-article"}],
                "next-cursor": "RAWCUR" if first else None}}
            return r

    s = _Paging()
    out = list(discover_crossref(start_year=2020, end_year=2020, session=s))
    assert out[0][1] == "2020|RAWCUR", out[0][1]
    # the RAW cursor (not the tagged one) is what goes back to Crossref
    assert s.cursors == ["*", "RAWCUR"]

    # resuming from a tagged cursor starts in that year with the raw cursor
    s2 = _YearRecordingSession()
    list(discover_crossref(start_year=2010, end_year=2013,
                           cursor="2012|DEEPCUR", session=s2, newest_first=False))
    assert "from-pub-date:2012-01-01" in s2.filters[0]
    assert s2.cursors[0] == "DEEPCUR"
    assert len(s2.filters) == 2  # 2012 then 2013


def test_legacy_untagged_cursor_is_discarded_not_replayed():
    """A pre-chunking cursor encodes the OLD multi-year query's shard state.

    Replaying it against a single-year filter makes Crossref 500 - which is
    exactly how the backfill died after year-chunking was introduced.
    """
    from papers_crawler.providers import discover_crossref

    s = _YearRecordingSession()
    list(discover_crossref(start_year=2010, end_year=2011,
                           cursor="DnF1ZXJ5VGhlbkZldGNoJAAAAAATRnYc", session=s,
                           newest_first=False))
    assert s.cursors == ["*", "*"], "stale cursor must be dropped, not reused"
    assert "from-pub-date:2010-01-01" in s.filters[0]


def test_malformed_tagged_cursor_falls_back_safely():
    from papers_crawler.providers import discover_crossref

    for bad in ("notayear|CUR", "2012|", "|CUR", "|"):
        s = _YearRecordingSession()
        list(discover_crossref(start_year=2019, end_year=2019, cursor=bad,
                               session=s, newest_first=False))
        assert s.cursors == ["*"], bad
        assert "from-pub-date:2019-01-01" in s.filters[0]


def test_discovery_walks_newest_year_first_by_default():
    """At 1 paper/minute the budget is scarce; pre-2015 papers are mostly
    paywalled and predate machine-readable data-availability statements, so a
    2010-first backfill spends weeks before reaching productive years."""
    from papers_crawler.providers import discover_crossref

    s = _YearRecordingSession()
    list(discover_crossref(start_year=2010, end_year=2014, session=s))

    years = [int(f.split("from-pub-date:")[1][:4]) for f in s.filters]
    assert years == [2014, 2013, 2012, 2011, 2010], years
    assert s.cursors == ["*"] * 5


def test_newest_first_resume_continues_downward():
    from papers_crawler.providers import discover_crossref

    s = _YearRecordingSession()
    list(discover_crossref(start_year=2010, end_year=2020,
                           cursor="2015|DEEPCUR", session=s))

    years = [int(f.split("from-pub-date:")[1][:4]) for f in s.filters]
    assert years[0] == 2015, "must resume in the tagged year"
    assert years == sorted(years, reverse=True), "must keep walking downward"
    assert 2016 not in years, "already-crawled newer years must not repeat"
    assert s.cursors[0] == "DEEPCUR"
    assert years[-1] == 2010


def test_one_failing_year_does_not_abort_the_backfill(capsys):
    """A 17-year backfill must survive a transient Crossref outage in one year.

    Observed in production: 2026 500'd and exhausted the retry budget, killing
    the entire run - yet the same query returned HTTP 200 (10,987 results) five
    times in a row minutes later.
    """
    import requests

    from papers_crawler.providers import discover_crossref

    class _OneBadYear:
        def __init__(self):
            self.years = []

        def get(self, url, params=None, timeout=None, headers=None):
            year = int(params["filter"].split("from-pub-date:")[1][:4])
            self.years.append(year)
            if year == 2025:
                raise requests.HTTPError("500 Server Error")
            return _YearResponse(url)

    session = _OneBadYear()
    out = list(discover_crossref(start_year=2023, end_year=2026, session=session))

    # every year attempted, including the ones after the failure
    assert sorted(set(session.years)) == [2023, 2024, 2025, 2026]
    # the good years still produced results
    assert len(out) == 3, "years after the failure must still be crawled"
    # and the skip is reported, not silent
    err = capsys.readouterr().err
    assert "failed for 2025" in err
    assert "years skipped after retries" in err and "2025" in err
