"""PubMed crawling module using NCBI E-utilities API.

Retrieves article titles and metadata from PubMed for a given journal and year
range.  Both open-access (PMC) and subscription-only (fee-based) papers are
returned – the ``open_access`` flag indicates which type each article is.

No browser automation is required: all requests go to the NCBI REST API.
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import time
import traceback
import zipfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NCBI E-utilities base URL
# ---------------------------------------------------------------------------
_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# NCBI asks for a polite delay between requests when not using an API key.
# 3 requests/second without key, 10/second with key.
_REQUEST_DELAY = 0.5  # seconds


# ---------------------------------------------------------------------------
# Low-level helpers (synchronous, called via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _esearch(query: str, retmax: int = 10_000, api_key: Optional[str] = None) -> List[str]:
    """Run an esearch query and return a list of PMIDs."""
    params: Dict = {
        "db": "pubmed",
        "term": query,
        "retmax": retmax,
        "retmode": "json",
        "usehistory": "n",
    }
    if api_key:
        params["api_key"] = api_key

    resp = requests.get(f"{_EUTILS}/esearch.fcgi", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("esearchresult", {}).get("idlist", [])


def _esummary_batch(pmids: List[str], api_key: Optional[str] = None) -> List[Dict]:
    """Fetch article summaries for a list of PMIDs (max 500 at a time)."""
    if not pmids:
        return []

    params: Dict = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "json",
    }
    if api_key:
        params["api_key"] = api_key

    resp = requests.get(f"{_EUTILS}/esummary.fcgi", params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    results: List[Dict] = []
    result_dict = data.get("result", {})
    uid_list = result_dict.get("uids", [])

    for uid in uid_list:
        art = result_dict.get(uid, {})
        if not art or "error" in art:
            continue

        # Publication date
        pub_date_str = art.get("pubdate", art.get("epubdate", ""))
        year: Optional[int] = None
        try:
            year = int(pub_date_str[:4])
        except (ValueError, TypeError):
            pass

        # DOI – lives inside articleids list
        doi = ""
        for id_obj in art.get("articleids", []):
            if id_obj.get("idtype") == "doi":
                doi = id_obj.get("value", "")
                break

        # PMC ID – indicates availability on PubMed Central (open access)
        pmc_id = ""
        for id_obj in art.get("articleids", []):
            if id_obj.get("idtype") == "pmc":
                pmc_id = id_obj.get("value", "")
                break

        is_oa = bool(pmc_id)

        results.append(
            {
                "pmid": uid,
                "title": art.get("title", "").rstrip("."),
                "authors": ", ".join(
                    a.get("name", "") for a in art.get("authors", [])
                ),
                "journal": art.get("fulljournalname", art.get("source", "")),
                "pub_date": pub_date_str,
                "year": year,
                "doi": doi,
                "pmc_id": pmc_id,
                "open_access": is_oa,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                "pmc_url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/" if pmc_id else "",
            }
        )

    return results


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def search_pubmed_async(
    journal: str,
    year_from: int,
    year_to: int,
    keywords: str = "",
    limit: Optional[int] = None,
    chunk_size_months: int = 6,
    api_key: Optional[str] = None,
    progress_callback=None,
) -> Tuple[List[Dict], List[Dict]]:
    """Search PubMed for articles in a journal within a year range.

    Returns **all** matching articles with an ``open_access`` flag, regardless
    of whether the full text is freely available.

    Uses the NCBI E-utilities REST API – no browser or Playwright required.

    Args:
        journal:   Journal name as it appears in PubMed (e.g. ``"Nature Immunology"``
                   or ``"Cell"``).  Use ``[jour]`` field tag if ambiguous.
        year_from: Start publication year (inclusive).
        year_to:   End publication year (inclusive).
        keywords:  Additional keywords to narrow the search (optional).
        limit:     Maximum number of articles to retrieve.
        api_key:   NCBI API key – raises the rate limit from 3 to 10 req/s.
                   Register at https://www.ncbi.nlm.nih.gov/account/
        progress_callback: Called with ``(article_dict: Dict)`` after each
            article record is processed.

    Returns:
        Tuple[all_articles, oa_articles] where each element is a list of dicts::

            {
                "pmid":        str,
                "title":       str,
                "authors":     str,
                "journal":     str,
                "pub_date":    str,
                "year":        int | None,
                "doi":         str,
                "pmc_id":      str,
                "open_access": bool,
                "url":         str,   # PubMed URL
                "pmc_url":     str,   # PMC URL (empty for fee-only papers)
            }
    """
    # ── Build query & Step 1: esearch – get PMIDs ───────────────────────────
    import calendar
    from datetime import date, timedelta
    
    print(" Fetching PMIDs from PubMed in chunks...", flush=True)
    pmids = []
    
    start_date = date(year_from, 1, 1)
    end_date = date(year_to, 12, 31)
    current_start = start_date
    
    while current_start <= end_date:
        months_to_add = chunk_size_months - 1
        end_year = current_start.year + (current_start.month + months_to_add - 1) // 12
        end_month = (current_start.month + months_to_add - 1) % 12 + 1
        last_day = calendar.monthrange(end_year, end_month)[1]
        current_end = date(end_year, end_month, last_day)
        if current_end > end_date:
            current_end = end_date
            
        date_filter = f"{current_start.strftime('%Y/%m/%d')}:{current_end.strftime('%Y/%m/%d')}[ppdat]"
        
        query_parts = [f'"{journal}"[jour]', date_filter]
        if keywords.strip():
            query_parts.append(keywords.strip())
        query = " AND ".join(query_parts)
        
        print(f" Query: {query}", flush=True)
        chunk_pmids = await asyncio.to_thread(_esearch, query, 10000, api_key)
        pmids.extend(chunk_pmids)
        
        if limit and len(pmids) >= limit:
            pmids = pmids[:limit]
            break
            
        if current_end == end_date:
            break
        current_start = current_end + timedelta(days=1)
        
    print(f"Found {len(pmids)} PMIDs", flush=True)

    if not pmids:
        return [], []

    # ── Step 2: esummary – get metadata in batches of 200 ───────────────────
    all_articles: List[Dict] = []
    oa_articles: List[Dict] = []
    batch_size = 200

    for i in range(0, len(pmids), batch_size):
        batch = pmids[i : i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(pmids) + batch_size - 1) // batch_size
        print(f"Fetching metadata: batch {batch_num}/{total_batches} ({len(batch)} articles)...", flush=True)

        records = await asyncio.to_thread(_esummary_batch, batch, api_key)

        for rec in records:
            all_articles.append(rec)
            if rec["open_access"]:
                oa_articles.append(rec)
            if progress_callback:
                progress_callback(rec)

        # Polite delay between batches
        await asyncio.sleep(_REQUEST_DELAY)

    print(
        f"\nPubMed: {len(all_articles)} articles total "
        f"({len(oa_articles)} open-access, "
        f"{len(all_articles) - len(oa_articles)} fee-based)",
        flush=True,
    )
    return all_articles, oa_articles


async def crawl_pubmed_async(
    journal: str,
    year_from: int = 2024,
    year_to: int = 2024,
    keywords: str = "",
    out_folder: str = "papers_pubmed",
    limit: Optional[int] = None,
    chunk_size_months: int = 6,
    api_key: Optional[str] = None,
    save_csv: bool = True,
    progress_callback=None,
) -> Tuple[List[Dict], List[Dict]]:
    """Crawl PubMed for articles and save a CSV summary.

    Wraps :func:`search_pubmed_async` and persists results to disk as:
    * ``<out_folder>/pubmed_titles_<timestamp>.csv`` – all articles
    * ``<out_folder>/pubmed_oa_<timestamp>.csv``    – open-access only

    Args:
        journal:    Journal name as understood by PubMed (e.g. ``"Nature"``).
        year_from:  Start year.
        year_to:    End year.
        keywords:   Extra search terms.
        out_folder: Directory for output files.
        limit:      Max articles to fetch.
        api_key:    NCBI API key (optional but recommended for large crawls).
        save_csv:   Whether to write CSV files.
        progress_callback: Called with ``(article_dict)`` for each article.

    Returns:
        Tuple[all_articles, oa_articles] – same as :func:`search_pubmed_async`.
    """
    os.makedirs(out_folder, exist_ok=True)

    all_articles, oa_articles = await search_pubmed_async(
        journal=journal,
        year_from=year_from,
        year_to=year_to,
        keywords=keywords,
        limit=limit,
        chunk_size_months=chunk_size_months,
        api_key=api_key,
        progress_callback=progress_callback,
    )

    if save_csv and all_articles:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _write_csv(
            all_articles,
            os.path.join(out_folder, f"pubmed_titles_{timestamp}.csv"),
            label="all articles",
        )
        if oa_articles:
            _write_csv(
                oa_articles,
                os.path.join(out_folder, f"pubmed_oa_{timestamp}.csv"),
                label="open-access articles",
            )

    return all_articles, oa_articles


# ---------------------------------------------------------------------------
# Multi-journal helper
# ---------------------------------------------------------------------------

async def crawl_pubmed_journals_async(
    journals: List[str],
    year_from: int = 2024,
    year_to: int = 2024,
    keywords: str = "",
    out_folder: str = "papers_pubmed",
    limit_per_journal: Optional[int] = None,
    chunk_size_months: int = 6,
    api_key: Optional[str] = None,
    save_csv: bool = True,
    progress_callback=None,
) -> Tuple[List[Dict], List[Dict]]:
    """Crawl multiple PubMed journals and aggregate results.

    Iterates over ``journals`` and calls :func:`crawl_pubmed_async` for each,
    merging results into a single list.

    Args:
        journals: List of journal names (e.g. ``["Nature", "Cell", "Science"]``).
        year_from / year_to: Publication year range.
        keywords: Additional search terms applied to every journal.
        out_folder: Directory for output CSV files.
        limit_per_journal: Max articles per journal.
        api_key: NCBI API key.
        save_csv: Write per-journal and aggregate CSVs.
        progress_callback: Called with ``(article_dict)`` for each article.

    Returns:
        Tuple[all_articles, oa_articles] merged across all journals.
    """
    os.makedirs(out_folder, exist_ok=True)
    all_articles: List[Dict] = []
    oa_articles: List[Dict] = []

    for journal in journals:
        print(f"\n{'─'*60}", flush=True)
        jnl_folder = os.path.join(out_folder, _safe_name(journal))
        arts, oa = await crawl_pubmed_async(
            journal=journal,
            year_from=year_from,
            year_to=year_to,
            keywords=keywords,
            out_folder=jnl_folder,
            limit=limit_per_journal,
            chunk_size_months=chunk_size_months,
            api_key=api_key,
            save_csv=save_csv,
            progress_callback=progress_callback,
        )
        all_articles.extend(arts)
        oa_articles.extend(oa)

    # Aggregate CSV
    # if save_csv and all_articles:
    #     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    #     _write_csv(
    #         all_articles,
    #         os.path.join(out_folder, f"pubmed_all_journals_{timestamp}.csv"),
    #         label="all journals combined",
    #     )

    print(
        f"\n PubMed crawl complete: {len(all_articles)} articles across {len(journals)} journal(s) "
        f"({len(oa_articles)} OA)",
        flush=True,
    )
    return all_articles, oa_articles


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _write_csv(articles: List[Dict], path: str, label: str = "") -> None:
    """Write article list to a CSV file."""
    fieldnames = [
        "pmid", "title", "authors", "journal", "pub_date", "year",
        "doi", "open_access", "pmc_id", "url", "pmc_url",
    ]
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", restval="")
            writer.writeheader()
            writer.writerows(articles)
        size_kb = os.path.getsize(path) / 1024
        print(f"Saved {label} CSV ({len(articles)} rows, {size_kb:.1f} KB): {path}", flush=True)
    except Exception as e:
        logger.error(f" Failed to write CSV {path}: {e}")


def _safe_name(name: str) -> str:
    """Convert a string to a safe directory name."""
    import re
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name).strip("_")
