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
import xml.etree.ElementTree as ET
import re
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


def fetch_abstracts_batch(pmids: List[str], api_key: Optional[str] = None) -> Dict[str, Dict]:
    """
    Fetches abstracts and MeSH categories for a list of PMIDs in a SINGLE network request.
    Returns a dictionary mapping PMID -> {"abstract": String, "categories": List[str]}.
    """
    if not pmids:
        return {}

    url = f"{_EUTILS}/efetch.fcgi"
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml"
    }
    if api_key:
        params["api_key"] = api_key

    try:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        xml_data = resp.text

        # print(f"\n XML response: {xml_data}")

        # This safely converts them to ^{content} and _{content} and prevents 
        # ElementTree from truncating mixed-content sentences.
        xml_data = re.sub(r'<sup\b[^>]*>(.*?)</sup>', r'^{\1}', xml_data, flags=re.IGNORECASE | re.DOTALL)
        xml_data = re.sub(r'<sub\b[^>]*>(.*?)</sub>', r'_{\1}', xml_data, flags=re.IGNORECASE | re.DOTALL)
        
        # Strip out any other rogue HTML formatting tags sometimes found in PubMed abstracts (like <i>, <b>)
        xml_data = re.sub(r'<i\b[^>]*>(.*?)</i>', r'\1', xml_data, flags=re.IGNORECASE | re.DOTALL)
        xml_data = re.sub(r'<b\b[^>]*>(.*?)</b>', r'\1', xml_data, flags=re.IGNORECASE | re.DOTALL)
        xml_data = xml_data.replace('\u2009', ' ')

        root = ET.fromstring(xml_data)
        abstracts_dict = {}

        # Iterate through every article returned in the XML
        for article in root.findall('.//PubmedArticle'):
            # Safely grab the PMID
            pmid_node = article.find('.//PMID')
            if pmid_node is None or not pmid_node.text:
                continue
            current_pmid = pmid_node.text.strip()

            # print(f"        + Extract the abstract of {current_pmid}", flush=True)

            # Find the abstract sections
            abstract_sections = []
            for abstract_text in article.findall('.//AbstractText'):
                label = abstract_text.attrib.get('Label')
                
                # .itertext() safely grabs all text inside the node
                text_content = "".join(abstract_text.itertext()).strip()
                
                if not text_content:
                    continue
                
                if label:
                    abstract_sections.append(f"{label}: {text_content}")
                else:
                    abstract_sections.append(text_content)

            # Extract MeSH categories
            mesh_headings = []
            mesh_list = article.find('.//MeshHeadingList')
            if mesh_list is not None:
                default_headings = []
                for mesh in mesh_list.findall('.//MeshHeading'):
                    desc = mesh.find('.//DescriptorName')
                    if desc is not None and desc.text:
                        major_topic = desc.attrib.get('MajorTopicYN', 'N')
                        text = desc.text.strip()
                        default_headings.append(text)
                        if major_topic == 'Y':
                            mesh_headings.append(text)
                
                if not mesh_headings:
                    mesh_headings = default_headings[:3]

            # Join all sections with a newline and save to our dictionary
            abstracts_dict[current_pmid] = {
                "abstract": "\n".join(abstract_sections),
                "categories": mesh_headings
            }

        return abstracts_dict

    except Exception as e:
        logger.error(f"Failed to batch fetch abstracts: {e}")
        return {}

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

    print(f"    - Fetching abstracts for {len(pmids)} PMIDs...", flush=True)
    abstracts_map = fetch_abstracts_batch(pmids, api_key)

    results: List[Dict] = []
    result_dict = data.get("result", {})
    uid_list = result_dict.get("uids", [])

    for uid in uid_list:
        art = result_dict.get(uid, {})
        if not art or "error" in art:
            continue

        # Publication date
        pub_date_str = art.get("pubdate", None)
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

        # Checking if the paper is open-access using Open Access App Service API
        is_oa = None
        is_pa = bool(pmc_id)

        if not pmc_id:
            is_oa = False
        else:
            oa_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmc_id}"
            oa_resp = requests.get(oa_url, timeout=30)
            if oa_resp.status_code != 200:
                logger.error(f"Failed to query OA API. HTTP status: {oa_resp.status}")
                raise  Exception(f"Failed to query OA API. HTTP status: {oa_resp.status}")
                
            xml_data = oa_resp.text

            # print(f"\nContent: \n{xml_data}")
            
            try:
                root = ET.fromstring(xml_data)
            except ET.ParseError:
                logger.error(f"Failed to parse XML from OA API for {pmc_id}")
                raise  Exception(f"Failed to query OA API. HTTP status: {oa_resp.status}")
            
            error_node = root.find('.//error')
            if error_node is not None:
                err_code = error_node.attrib.get('code', 'Unknown')
                err_msg = error_node.text or 'No error message provided'
                # logger.error(f"OA API returned error for {pmc_id}: [{err_code}] {err_msg}")

                is_oa = False
            else:
                is_oa = True

        abstract_info = abstracts_map.get(uid, {})
        article_abstract = abstract_info.get("abstract", "")
        categories = abstract_info.get("categories", [])

        results.append(
            {
                "pmid": uid,
                "title": art.get("title", "").rstrip("."),
                "authors": ", ".join(
                    a.get("name", "") for a in art.get("authors", [])
                ),
                "abstract": article_abstract,
                "categories": categories,
                "journal": art.get("fulljournalname", art.get("source", "")),
                "pub_date": pub_date_str,
                "year": year,
                "doi": doi,
                "pmc_id": pmc_id,
                "open_access": is_oa,
                "public_access": is_pa,
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
    time_tracker=None,
):
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
                "abstract":    str,
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
        return

    # ── Step 2: esummary – get metadata in batches of 200 ───────────────────
    batch_size = 200
    total_yielded = 0
    oa_yielded = 0
    pa_yielded = 0

    for i in range(0, len(pmids), batch_size):
        batch = pmids[i : i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(pmids) + batch_size - 1) // batch_size
        print(f"Fetching metadata: batch {batch_num}/{total_batches} ({len(batch)} articles)...", flush=True)

        batch_start_time = datetime.now()
        records = await asyncio.to_thread(_esummary_batch, batch, api_key)
        batch_end_time = datetime.now()
        
        if time_tracker:
            duration = (batch_end_time - batch_start_time).total_seconds()
            time_tracker.record_metadata(batch_num, len(batch), batch_start_time, batch_end_time, duration)

        batch_all = []
        batch_oa = []
        batch_pa = []
        
        for rec in records:
            batch_all.append(rec)
            if rec["open_access"]:
                batch_oa.append(rec)
            if rec["public_access"]:
                batch_pa.append(rec)
            if progress_callback:
                progress_callback(rec)

        total_yielded += len(batch_all)
        oa_yielded += len(batch_oa)
        pa_yielded += len(batch_pa)

        yield batch_all, batch_oa, batch_pa

        # Polite delay between batches
        await asyncio.sleep(_REQUEST_DELAY)

    print(
        f"\nPubMed: {total_yielded} articles total "
        f"({oa_yielded} open-access, "
        f"{pa_yielded} public-access, "
        f"{total_yielded - pa_yielded} closed-access)",
        flush=True,
    )


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
    time_tracker=None,
):
    """Crawl PubMed for articles and save a CSV summary progressively.
    
    Wraps :func:`search_pubmed_async` and persists batches directly to disk:
    * ``<out_folder>/pubmed_titles_<timestamp>.csv`` – all articles
    * ``<out_folder>/pubmed_oa_<timestamp>.csv``    – open-access only
    """
    os.makedirs(out_folder, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    titles_path = os.path.join(out_folder, f"pubmed_titles_{timestamp}.csv")
    oa_path = os.path.join(out_folder, f"pubmed_oa_{timestamp}.csv")
    pa_path = os.path.join(out_folder, f"pubmed_pa_{timestamp}.csv")

    async for batch_all, batch_oa, batch_pa in search_pubmed_async(
        journal=journal,
        year_from=year_from,
        year_to=year_to,
        keywords=keywords,
        limit=limit,
        chunk_size_months=chunk_size_months,
        api_key=api_key,
        progress_callback=progress_callback,
        time_tracker=time_tracker,
    ):
        if save_csv:
            if batch_all:
                _write_csv(batch_all, titles_path, label="all articles", append=True)
            if batch_oa:
                _write_csv(batch_oa, oa_path, label="open-access articles", append=True)
            if batch_pa:
                _write_csv(batch_pa, pa_path, label="public-access articles", append=True)
                
        yield batch_all, batch_oa, batch_pa


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
    time_tracker=None,
):
    """Crawl multiple PubMed journals incrementally to preserve memory."""
    os.makedirs(out_folder, exist_ok=True)
    total_arts = 0
    total_oa = 0

    for journal in journals:
        print(f"\n{'-'*60}", flush=True)
        jnl_folder = os.path.join(out_folder, _safe_name(journal))
        
        async for batch_all, batch_oa, batch_pa in crawl_pubmed_async(
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
            time_tracker=time_tracker,
        ):
            total_arts += len(batch_all)
            total_oa += len(batch_oa)
            yield batch_all, batch_oa, batch_pa

    print(
        f"\nPubMed crawl complete: {total_arts} articles across {len(journals)} journal(s) "
        f"({total_oa} OA)",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _write_csv(articles: List[Dict], path: str, label: str = "", append: bool = False) -> None:
    """Write article list to a CSV file incrementally."""
    fieldnames = [
        "pmid", "title", "authors", "abstract", "categories", "journal", "pub_date", "year",
        "doi", "open_access", "public_access", "pmc_id", "url", "pmc_url",
    ]
    try:
        mode = "a" if append else "w"
        file_exists = os.path.exists(path) and os.path.getsize(path) > 0

        with open(path, mode, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", restval="")
            if not (append and file_exists):
                writer.writeheader()
            
            rows_to_write = []
            for art in articles:
                row = dict(art)
                if isinstance(row.get('categories'), list):
                    row['categories'] = ", ".join(row['categories'])
                rows_to_write.append(row)
                
            writer.writerows(rows_to_write)
        size_kb = os.path.getsize(path) / 1024
        print(f"Saved {label} CSV ({len(articles)} rows, {size_kb:.1f} KB): {path}", flush=True)
    except Exception as e:
        logger.error(f" Failed to write CSV {path}: {e}")


def _safe_name(name: str) -> str:
    """Convert a string to a safe directory name."""
    import re
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name).strip("_")
