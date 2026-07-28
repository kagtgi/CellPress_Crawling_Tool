"""Keyless scholarly discovery and reusable full-text retrieval providers."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

USER_AGENT = "BioParser/2.0 TextDataMining (mailto:corpus@tasih.ai)"
PDF_TYPES = {"application/pdf", "application/x-pdf"}

#: Transient HTTP statuses worth retrying. Crossref intermittently 500s on deep
#: cursor pagination, and a single blip previously aborted an entire multi-week
#: backfill because _request called raise_for_status() with no retry.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = max(1, int(os.environ.get("PAPERS_CRAWLER_HTTP_ATTEMPTS", "5")))
MAX_BACKOFF_SECONDS = 60.0


def _backoff_seconds(response: requests.Response | None, attempt: int) -> float:
    if response is not None:
        header = response.headers.get("retry-after")
        if header:
            try:
                return max(0.0, min(MAX_BACKOFF_SECONDS, float(header)))
            except ValueError:
                pass
    return min(MAX_BACKOFF_SECONDS, 2.0**attempt)
REUSABLE_LICENSE_TOKENS = (
    "creativecommons.org/licenses/",
    "creativecommons.org/publicdomain/",
)


class PdfRejected(RuntimeError):
    """Raised before a PDF response can be accepted or persisted."""


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    endpoint: str
    status: str
    http_status: int | None = None
    reason: str | None = None


def _request(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 45,
) -> requests.Response:
    if url.lower().split("?", 1)[0].endswith(".pdf"):
        raise PdfRejected(f"PDF URL rejected: {url}")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/xml, application/json;q=0.9, text/xml;q=0.8",
    }
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = session.get(
                url, params=params, timeout=timeout, headers=headers
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS - 1:
                raise
            time.sleep(_backoff_seconds(None, attempt))
            continue
        # PDF rejection stays terminal - never retried, never persisted.
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type in PDF_TYPES or response.content.startswith(b"%PDF-"):
            raise PdfRejected(f"PDF response rejected: {response.url}")
        status = getattr(response, "status_code", 200)
        if status in RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
            delay = _backoff_seconds(response, attempt)
            response.close()
            time.sleep(delay)
            continue
        response.raise_for_status()
        return response
    raise last_error or requests.RequestException(f"request failed: {url}")


def has_reusable_license(metadata: dict[str, Any]) -> bool:
    value = str(metadata.get("license_url") or "").lower()
    return any(token in value for token in REUSABLE_LICENSE_TOKENS)


#: Title prefixes that mark a non-research record. Crossref types these all as
#: "journal-article", so `type` cannot filter them.
_NON_RESEARCH_PREFIXES = (
    "correction:", "author correction:", "publisher correction:",
    "retraction:", "retraction note", "erratum", "editorial expression of concern",
    "addendum:", "comment on", "reply to", "corrigendum",
)


def load_journals(*, include_multidisciplinary: bool = False) -> list[dict[str, Any]]:
    """Return the journal registry, life-science scope only by default.

    Nature / Nature Communications / Scientific Reports publish across all of
    science, and Crossref exposes no usable ``subject`` for them (verified
    empty for every Nature-portfolio record), so a biology-only corpus cannot be
    obtained from them by metadata filtering — an ISSN filter alone admits civil
    engineering, mathematics and climate papers. They stay in the registry
    tagged ``multidisciplinary`` and are excluded unless explicitly requested.
    """
    from importlib.resources import files

    path = files("papers_crawler").joinpath("data/life_science_journals.json")
    journals = json.loads(path.read_text(encoding="utf-8"))
    if include_multidisciplinary:
        return journals
    return [j for j in journals if j.get("scope", "life_science") == "life_science"]


def is_research_article(metadata: dict[str, Any]) -> bool:
    """False for corrections, retractions, errata and commentary."""
    title = str(metadata.get("title") or "").strip().lower()
    return bool(title) and not title.startswith(_NON_RESEARCH_PREFIXES)


def discover_crossref(
    *,
    start_year: int,
    end_year: int,
    rows: int = 100,
    cursor: str = "*",
    session: requests.Session | None = None,
    include_multidisciplinary: bool = False,
) -> Iterator[tuple[dict[str, Any], str | None]]:
    """Yield registry-filtered Crossref works and the cursor for resumption.

    Discovery is chunked **one year at a time**. Crossref serves the first page
    of a 2010-2026 × 50-ISSN query fine (212k results) but 500s once the cursor
    walks deep into it, which killed the whole backfill. Per-year result sets are
    ~10k and page reliably.

    The yielded cursor is ``"<year>|<crossref-cursor>"`` so a resumed run
    continues in the right year; a bare cursor is treated as ``start_year``.
    """
    client = session or requests.Session()
    journals = load_journals(include_multidisciplinary=include_multidisciplinary)
    by_issn = {
        issn.upper(): journal
        for journal in journals
        for issn in journal.get("issns", [])
    }
    wanted = set(by_issn)
    resume_year, tagged, resume_cursor = (cursor or "*").partition("|")
    if tagged and resume_year.isdigit() and resume_cursor:
        first_year, current = int(resume_year), resume_cursor
    else:
        # An untagged cursor predates year-chunking: it encodes the shard state
        # of the old multi-year query, and Crossref 500s when it is replayed
        # against a single-year filter. Discard it and restart the year cleanly
        # rather than resuming into a guaranteed failure.
        first_year, current = start_year, "*"
    for year in range(max(first_year, start_year), end_year + 1):
        yield from _discover_crossref_year(
            client, by_issn, wanted, year=year, rows=rows, cursor=current
        )
        current = "*"  # each year starts a fresh cursor


def _discover_crossref_year(
    client: requests.Session,
    by_issn: dict[str, dict[str, Any]],
    wanted: set[str],
    *,
    year: int,
    rows: int,
    cursor: str,
) -> Iterator[tuple[dict[str, Any], str | None]]:
    start_year = end_year = year
    current = cursor
    while current:
        response = _request(
            client,
            "https://api.crossref.org/works",
            params={
                "filter": (
                    f"from-pub-date:{start_year}-01-01,"
                    f"until-pub-date:{end_year}-12-31,"
                    + ",".join(f"issn:{x}" for x in sorted(wanted))
                ),
                "cursor": current,
                # No "cursor-max": Crossref has no such parameter and hard-fails
                # the whole request with 400 validation-failure
                # ("Parameter cursor-max specified but there is no such
                # parameter available on any route") rather than ignoring it.
                # `rows` alone bounds the page size for cursor paging.
                "rows": rows,
                "select": (
                    "DOI,title,author,container-title,published,URL,ISSN,type,"
                    "license,link,publisher"
                ),
            },
        )
        message = response.json()["message"]
        raw_cursor = message.get("next-cursor")
        # Tag the cursor with its year so a resumed run continues in that year.
        next_cursor = f"{year}|{raw_cursor}" if raw_cursor else None
        items = message.get("items", [])
        for item in items:
            issns = [str(x).upper() for x in item.get("ISSN", [])]
            match = next((by_issn[x] for x in issns if x in by_issn), None)
            if not match:
                continue
            date_parts = (
                item.get("published", {}).get("date-parts") or [[None]]
            )[0]
            published = "-".join(f"{int(x):02d}" for x in date_parts if x is not None)
            licenses = item.get("license") or []
            title = (item.get("title") or [""])[0]
            # Corrections/retractions/errata are typed "journal-article" by
            # Crossref, so skip them here or they pollute the corpus.
            if not is_research_article({"title": title}):
                continue
            yield (
                {
                    "doi": item.get("DOI"),
                    "title": title,
                    "authors": [
                        {
                            "given": author.get("given"),
                            "family": author.get("family"),
                            "literal": " ".join(
                                x
                                for x in (author.get("given"), author.get("family"))
                                if x
                            ),
                        }
                        for author in item.get("author", [])
                    ],
                    "journal": (item.get("container-title") or [match["name"]])[0],
                    "publisher_family": match["publisher_family"],
                    "publisher_id": None,
                    "canonical_url": item.get("URL"),
                    "issns": issns,
                    "published": published or None,
                    "article_type": item.get("type"),
                    "language": item.get("language") or "en",
                    "license_url": licenses[0].get("URL") if licenses else None,
                    "tdm_links": item.get("link") or [],
                },
                next_cursor,
            )
        # Advance on the RAW cursor: `next_cursor` is year-tagged for callers
        # and would be rejected by Crossref if sent back as a cursor.
        if not items or not raw_cursor or raw_cursor == current:
            break
        current = raw_cursor


def discover_europe_pmc(
    *,
    start_year: int,
    end_year: int,
    page_size: int = 1000,
    cursor: str = "*",
    session: requests.Session | None = None,
) -> Iterator[tuple[dict[str, Any], str | None]]:
    """Yield PubMed/Europe PMC metadata for the checked-in ISSN registry."""
    client = session or requests.Session()
    journals = load_journals()
    by_issn = {
        issn.upper(): journal
        for journal in journals
        for issn in journal.get("issns", [])
    }
    issn_query = " OR ".join(f'ISSN:"{issn}"' for issn in sorted(by_issn))
    query = (
        f"FIRST_PDATE:[{start_year}-01-01 TO {end_year}-12-31] "
        f"AND ({issn_query})"
    )
    current = cursor
    while current:
        response = _request(
            client,
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": min(page_size, 1000),
                "cursorMark": current,
            },
        )
        payload = response.json()
        next_cursor = payload.get("nextCursorMark")
        results = payload.get("resultList", {}).get("result", [])
        for item in results:
            issn = str(item.get("journalInfo", {}).get("journal", {}).get("issn") or "").upper()
            journal = by_issn.get(issn)
            if not journal:
                continue
            yield (
                {
                    "doi": item.get("doi"),
                    "pmcid": item.get("pmcid"),
                    "pmid": item.get("pmid"),
                    "publisher_id": None,
                    "canonical_url": (
                        f"https://europepmc.org/article/MED/{item.get('pmid')}"
                        if item.get("pmid")
                        else None
                    ),
                    "title": item.get("title") or "",
                    "authors": [
                        {"given": None, "family": None, "literal": name}
                        for name in item.get("authorString", "").split(", ")
                        if name
                    ],
                    "journal": item.get("journalTitle") or journal["name"],
                    "publisher_family": journal["publisher_family"],
                    "issns": [issn] if issn else [],
                    "published": item.get("firstPublicationDate"),
                    "article_type": item.get("pubType"),
                    "language": "en",
                    "license_url": None,
                    "tdm_links": [],
                    "is_open_access": str(item.get("isOpenAccess", "")).upper() == "Y",
                },
                next_cursor,
            )
        if not results or not next_cursor or next_cursor == current:
            break
        current = next_cursor


def enrich_europe_pmc(
    metadata: dict[str, Any],
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Resolve DOI/PMID to Europe PMC identifiers and OA flags."""
    client = session or requests.Session()
    identifier = metadata.get("doi") or metadata.get("pmid")
    if not identifier:
        return metadata
    query_field = "DOI" if metadata.get("doi") else "EXT_ID"
    response = _request(
        client,
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        params={
            "query": f'{query_field}:"{identifier}"',
            "format": "json",
            "pageSize": 1,
        },
    )
    results = response.json().get("resultList", {}).get("result", [])
    if not results:
        return metadata
    result = results[0]
    enriched = dict(metadata)
    enriched["pmcid"] = result.get("pmcid") or metadata.get("pmcid")
    enriched["pmid"] = result.get("pmid") or metadata.get("pmid")
    enriched["is_open_access"] = str(result.get("isOpenAccess", "")).upper() == "Y"
    return enriched


def fetch_europe_pmc_jats(
    metadata: dict[str, Any],
    *,
    session: requests.Session | None = None,
    throttle_seconds: float = 1 / 3,
) -> tuple[bytes | None, ProviderAttempt]:
    """Fetch keyless PMC full-text XML, serialized at at most three requests/s."""
    pmcid = metadata.get("pmcid")
    if not pmcid:
        return None, ProviderAttempt(
            "europe_pmc", "", "no_machine_endpoint", reason="no PMCID"
        )
    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/"
        f"{quote(str(pmcid))}/fullTextXML"
    )
    time.sleep(max(0, throttle_seconds))
    try:
        response = _request(session or requests.Session(), url)
    except PdfRejected as exc:
        return None, ProviderAttempt("europe_pmc", url, "closed", reason=str(exc))
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        status = "no_machine_endpoint" if code in {404, 410} else "retryable_failure"
        return None, ProviderAttempt(
            "europe_pmc", url, status, http_status=code, reason=str(exc)
        )
    except requests.RequestException as exc:
        return None, ProviderAttempt(
            "europe_pmc", url, "retryable_failure", reason=str(exc)
        )
    return response.content, ProviderAttempt(
        "europe_pmc", url, "reusable_full_text", http_status=response.status_code
    )


def fetch_crossref_tdm(
    metadata: dict[str, Any],
    *,
    session: requests.Session | None = None,
) -> tuple[bytes | None, ProviderAttempt]:
    """Try licensed XML TDM links; links never establish the license themselves."""
    if not has_reusable_license(metadata):
        return None, ProviderAttempt(
            "crossref_tdm", "", "license_unknown", reason="no reusable license"
        )
    links = metadata.get("tdm_links") or []
    candidates = [
        link
        for link in links
        if "pdf" not in str(link.get("content-type") or "").lower()
        and not str(link.get("URL") or "").lower().split("?", 1)[0].endswith(".pdf")
    ]
    if not candidates:
        return None, ProviderAttempt(
            "crossref_tdm", "", "no_machine_endpoint", reason="no non-PDF TDM link"
        )
    link = candidates[0]
    url = str(link.get("URL") or "")
    try:
        response = _request(session or requests.Session(), url)
    except PdfRejected as exc:
        return None, ProviderAttempt("crossref_tdm", url, "closed", reason=str(exc))
    except requests.RequestException as exc:
        code = exc.response.status_code if isinstance(exc, requests.HTTPError) and exc.response is not None else None
        return None, ProviderAttempt(
            "crossref_tdm",
            url,
            "retryable_failure",
            http_status=code,
            reason=str(exc),
        )
    return response.content, ProviderAttempt(
        "crossref_tdm", url, "reusable_full_text", response.status_code
    )


def fetch_elsevier_xml(
    metadata: dict[str, Any],
    *,
    session: requests.Session | None = None,
) -> tuple[bytes | None, ProviderAttempt]:
    key = os.environ.get("ELSEVIER_API_KEY")
    doi = metadata.get("doi")
    if not key:
        return None, ProviderAttempt(
            "elsevier", "", "no_machine_endpoint", reason="optional key unavailable"
        )
    if not doi or not has_reusable_license(metadata):
        return None, ProviderAttempt(
            "elsevier", "", "license_unknown", reason="DOI/license gate failed"
        )
    url = f"https://api.elsevier.com/content/article/doi/{quote(str(doi))}"
    client = session or requests.Session()
    try:
        response = client.get(
            url,
            timeout=45,
            headers={
                "X-ELS-APIKey": key,
                "User-Agent": USER_AGENT,
                "Accept": "application/xml",
            },
        )
        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type or response.content.startswith(b"%PDF-"):
            raise PdfRejected(f"PDF response rejected: {url}")
        response.raise_for_status()
    except PdfRejected as exc:
        return None, ProviderAttempt("elsevier", url, "closed", reason=str(exc))
    except requests.RequestException as exc:
        code = exc.response.status_code if isinstance(exc, requests.HTTPError) and exc.response is not None else None
        return None, ProviderAttempt(
            "elsevier", url, "retryable_failure", code, str(exc)
        )
    return response.content, ProviderAttempt(
        "elsevier", url, "reusable_full_text", response.status_code
    )


def fetch_springer_jats(
    metadata: dict[str, Any],
    *,
    session: requests.Session | None = None,
) -> tuple[bytes | None, ProviderAttempt]:
    key = os.environ.get("SPRINGER_NATURE_API_KEY")
    doi = metadata.get("doi")
    if not key:
        return None, ProviderAttempt(
            "springer_nature", "", "no_machine_endpoint", reason="optional key unavailable"
        )
    if not doi or not has_reusable_license(metadata):
        return None, ProviderAttempt(
            "springer_nature", "", "license_unknown", reason="DOI/license gate failed"
        )
    url = "https://api.springernature.com/openaccess/jats"
    try:
        response = _request(
            session or requests.Session(),
            url,
            params={"q": f"doi:{doi}", "api_key": key},
        )
    except requests.RequestException as exc:
        code = exc.response.status_code if isinstance(exc, requests.HTTPError) and exc.response is not None else None
        return None, ProviderAttempt(
            "springer_nature", url, "retryable_failure", code, str(exc)
        )
    return response.content, ProviderAttempt(
        "springer_nature", url, "reusable_full_text", response.status_code
    )


def fetch_reusable_full_text(
    metadata: dict[str, Any],
    *,
    session: requests.Session | None = None,
) -> tuple[bytes | None, str | None, str | None, list[ProviderAttempt]]:
    """Run the licensed XML provider order without requiring optional keys."""
    attempts: list[ProviderAttempt] = []
    client = session or requests.Session()
    if metadata.get("is_open_access") and metadata.get("pmcid"):
        body, attempt = fetch_europe_pmc_jats(metadata, session=client)
        attempts.append(attempt)
        if body:
            return body, "europe_pmc", "PMC OA full-text XML", attempts
    for provider, basis, fetcher in (
        ("crossref_tdm", "Crossref licensed TDM XML", fetch_crossref_tdm),
        ("elsevier", "Elsevier Article Retrieval XML", fetch_elsevier_xml),
        ("springer_nature", "Springer Nature OA JATS", fetch_springer_jats),
    ):
        body, attempt = fetcher(metadata, session=client)
        attempts.append(attempt)
        if body:
            return body, provider, basis, attempts
    return None, None, None, attempts
