"""Normalize JATS-like XML into ``bioparser.article.v1``."""

from __future__ import annotations

import hashlib
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

from bs4 import BeautifulSoup, Tag

from .article import SCHEMA_VERSION, article_id_for, content_sha256, normalize_doi

PARSER_VERSION = "papers-crawler/2.2.0"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _all(root: ET.Element, name: str) -> list[ET.Element]:
    return [node for node in root.iter() if _local(node.tag) == name]


def _paragraph(node: ET.Element, paragraph_id: str) -> dict[str, Any]:
    citations: list[str] = []
    for xref in node.iter():
        if _local(xref.tag) == "xref" and xref.attrib.get("rid"):
            citations.extend(xref.attrib["rid"].split())
    return {
        "paragraph_id": paragraph_id,
        "text": _text(node),
        "citations": list(dict.fromkeys(citations)),
    }


def _section_type(title: str) -> str:
    low = title.lower()
    for token, target in (
        ("data availability", "data_availability"),
        ("availability of data", "data_availability"),
        ("method", "methods"),
        ("material", "methods"),
        ("result", "results"),
        ("discussion", "discussion"),
        ("introduction", "introduction"),
        ("conclusion", "conclusion"),
        ("reference", "references"),
        ("acknowledg", "acknowledgments"),
    ):
        if token in low:
            return target
    return "other"


def spdx_for_license(url: str | None) -> str | None:
    low = (url or "").lower().rstrip("/")
    for token, spdx in (
        ("creativecommons.org/licenses/by/4.0", "CC-BY-4.0"),
        ("creativecommons.org/licenses/by-nc/4.0", "CC-BY-NC-4.0"),
        ("creativecommons.org/publicdomain/zero/1.0", "CC0-1.0"),
    ):
        if token in low:
            return spdx
    return None


def reuse_allowed(license_url: str | None, *, pmc_open: bool = False) -> bool:
    return pmc_open or "creativecommons.org/" in (license_url or "").lower()


def jats_to_article(
    xml_bytes: bytes,
    metadata: dict[str, Any],
    *,
    provider: str,
    retrieval_basis: str,
    license_url: str | None,
) -> dict[str, Any]:
    root = ET.fromstring(xml_bytes)
    doi = normalize_doi(metadata.get("doi"))
    pmcid = metadata.get("pmcid")
    pmid = metadata.get("pmid")
    for node in _all(root, "article-id"):
        kind = node.attrib.get("pub-id-type")
        if kind == "doi" and not doi:
            doi = normalize_doi(_text(node))
        elif kind in {"pmc", "pmcid"} and not pmcid:
            pmcid = _text(node)
        elif kind == "pmid" and not pmid:
            pmid = _text(node)

    titles = _all(root, "article-title")
    journals = _all(root, "journal-title")
    title = metadata.get("title") or (_text(titles[0]) if titles else "")
    journal = metadata.get("journal") or (
        _text(journals[0]) if journals else None
    )
    authors = []
    for contrib in _all(root, "contrib"):
        if contrib.attrib.get("contrib-type", "author") != "author":
            continue
        names = [n for n in contrib if _local(n.tag) == "name"]
        surname = next(
            (_text(n) for name in names for n in name if _local(n.tag) == "surname"),
            "",
        )
        given = next(
            (
                _text(n)
                for name in names
                for n in name
                if _local(n.tag) == "given-names"
            ),
            "",
        )
        literal = " ".join(x for x in (given, surname) if x) or _text(contrib)
        if literal:
            authors.append(
                {"given": given or None, "family": surname or None, "literal": literal}
            )

    abstract: list[dict[str, Any]] = []
    abstracts = _all(root, "abstract")
    if abstracts:
        for idx, para in enumerate(_all(abstracts[0], "p"), 1):
            item = _paragraph(para, f"abs-p{idx}")
            if item["text"]:
                abstract.append(item)

    sections: list[dict[str, Any]] = []
    data_availability: list[dict[str, Any]] = []
    bodies = _all(root, "body")
    if bodies:
        # Only top-level sections. Nested section text remains in its parent,
        # preserving source order without duplicating each nested paragraph.
        top_sections = [n for n in list(bodies[0]) if _local(n.tag) == "sec"]
        for sidx, sec in enumerate(top_sections, 1):
            title_node = next((n for n in sec if _local(n.tag) == "title"), None)
            sec_title = _text(title_node) or f"Section {sidx}"
            sec_type = _section_type(sec_title)
            paragraphs = []
            for pidx, para in enumerate(_all(sec, "p"), 1):
                item = _paragraph(para, f"s{sidx}-p{pidx}")
                if item["text"]:
                    paragraphs.append(item)
            sections.append(
                {
                    "section_id": f"s{sidx}",
                    "section_type": sec_type,
                    "title": sec_title,
                    "paragraphs": paragraphs,
                }
            )
            if sec_type == "data_availability":
                data_availability.extend(paragraphs)

    references = [
        {
            "reference_id": ref.attrib.get("id") or f"ref{idx}",
            "text": _text(ref),
        }
        for idx, ref in enumerate(_all(root, "ref"), 1)
        if _text(ref)
    ]
    figures = []
    for idx, fig in enumerate(_all(root, "fig"), 1):
        caption = next((n for n in fig.iter() if _local(n.tag) == "caption"), None)
        figures.append(
            {
                "figure_id": fig.attrib.get("id") or f"fig{idx}",
                "caption": _text(caption),
            }
        )
    tables = []
    for idx, table in enumerate(_all(root, "table-wrap"), 1):
        caption = next(
            (n for n in table.iter() if _local(n.tag) == "caption"), None
        )
        tables.append(
            {
                "table_id": table.attrib.get("id") or f"table{idx}",
                "caption": _text(caption),
                "text": _text(table),
            }
        )

    document = {
        "schema_version": SCHEMA_VERSION,
        "article_id": article_id_for(
            doi=doi,
            pmcid=pmcid,
            publisher_id=metadata.get("publisher_id"),
            canonical_url=metadata.get("canonical_url"),
        ),
        "identifiers": {
            "doi": doi,
            "pmcid": pmcid,
            "pmid": pmid,
            "publisher_id": metadata.get("publisher_id"),
            "canonical_url": metadata.get("canonical_url"),
            "issns": sorted(set(metadata.get("issns") or [])),
        },
        "bibliography": {
            "title": title,
            "authors": authors or metadata.get("authors") or [],
            "journal": journal,
            "publisher_family": metadata.get("publisher_family", "unknown"),
            "published": metadata.get("published"),
            "article_type": metadata.get("article_type"),
            "language": metadata.get("language") or "en",
        },
        "access": {
            "status": "reusable_full_text",
            "open_access": True,
            "license_url": license_url,
            "license_spdx": spdx_for_license(license_url),
            "reuse_allowed": True,
            "retrieval_basis": retrieval_basis,
        },
        "content": {
            "abstract": abstract,
            "sections": sections,
            "figures": figures,
            "tables": tables,
            "references": references,
            "supplements": [],
            "data_availability": data_availability,
        },
        "provenance": {
            "provider": provider,
            "source_format": "application/xml+jats",
            "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "parser_version": PARSER_VERSION,
            "source_sha256": hashlib.sha256(xml_bytes).hexdigest(),
            "content_sha256": "0" * 64,
            "warnings": [],
            "completeness": {
                "section_count": len(sections),
                "paragraph_count": len(abstract)
                + sum(len(section["paragraphs"]) for section in sections),
                "reference_count": len(references),
                "has_data_availability": bool(data_availability),
            },
        },
    }
    document["provenance"]["content_sha256"] = content_sha256(document)
    return document


def _html_text(node: Tag | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def html_to_article(
    html_bytes: bytes,
    metadata: dict[str, Any],
    *,
    provider: str,
    retrieval_basis: str,
    license_url: str | None,
) -> dict[str, Any]:
    """Normalize a license-gated publisher HTML article without media.

    The parser is intentionally conservative: it reads only the article
    element, removes executable/media/navigation elements, preserves ordered
    prose and text captions, and never follows links embedded in the page.
    """
    soup = BeautifulSoup(html_bytes, "html.parser")
    article = soup.find("article") or soup.find("main")
    if not isinstance(article, Tag):
        raise ValueError("publisher HTML contains no article or main element")
    for node in article.find_all(
        [
            "script",
            "style",
            "noscript",
            "nav",
            "aside",
            "form",
            "button",
            "img",
            "picture",
            "video",
            "audio",
            "svg",
            "canvas",
            "iframe",
        ]
    ):
        node.decompose()

    title_node = article.select_one("h1.c-article-title") or article.find("h1")
    title = metadata.get("title") or _html_text(title_node)

    abstract: list[dict[str, Any]] = []
    abstract_node = (
        article.select_one("section#Abs1")
        or article.select_one('[data-title="Abstract"]')
        or article.select_one("section.c-article-section__abstract")
    )
    if isinstance(abstract_node, Tag):
        for idx, para in enumerate(abstract_node.find_all("p"), 1):
            text = _html_text(para)
            if text:
                abstract.append(
                    {
                        "paragraph_id": f"abs-p{idx}",
                        "text": text,
                        "citations": [],
                    }
                )

    sections: list[dict[str, Any]] = []
    data_availability: list[dict[str, Any]] = []
    for node in article.find_all("section"):
        if not isinstance(node, Tag) or node is abstract_node:
            continue
        heading = node.find(["h2", "h3"])
        sec_title = _html_text(heading) or str(node.get("data-title") or "").strip()
        if not sec_title:
            continue
        sec_type = _section_type(sec_title)
        if sec_type == "references":
            continue
        paragraphs: list[dict[str, Any]] = []
        for para in node.find_all("p"):
            if para.find_parent("section") is not node:
                continue
            if para.find_parent(["figcaption", "table"]):
                continue
            text = _html_text(para)
            if text:
                paragraphs.append(
                    {
                        "paragraph_id": "",
                        "text": text,
                        "citations": [],
                    }
                )
        if not paragraphs:
            continue
        section_id = f"s{len(sections) + 1}"
        for pidx, paragraph in enumerate(paragraphs, 1):
            paragraph["paragraph_id"] = f"{section_id}-p{pidx}"
        section = {
            "section_id": section_id,
            "section_type": sec_type,
            "title": sec_title,
            "paragraphs": paragraphs,
        }
        sections.append(section)
        if sec_type == "data_availability":
            data_availability.extend(paragraphs)

    figures = []
    for idx, figure in enumerate(article.find_all("figure"), 1):
        caption = figure.find("figcaption")
        figures.append(
            {
                "figure_id": str(figure.get("id") or f"fig{idx}"),
                "caption": _html_text(caption),
            }
        )
    tables = []
    for idx, table in enumerate(article.find_all("table"), 1):
        caption = table.find("caption")
        tables.append(
            {
                "table_id": str(table.get("id") or f"table{idx}"),
                "caption": _html_text(caption),
                "text": _html_text(table),
            }
        )
    reference_items = article.select(
        "ol.c-article-references li, .c-article-references li, "
        "section[data-title='References'] li"
    )
    references = [
        {
            "reference_id": str(item.get("id") or f"ref{idx}"),
            "text": _html_text(item),
        }
        for idx, item in enumerate(reference_items, 1)
        if _html_text(item)
    ]
    if not sections and not abstract:
        raise ValueError("publisher HTML contains no reusable article prose")

    document = {
        "schema_version": SCHEMA_VERSION,
        "article_id": article_id_for(
            doi=metadata.get("doi"),
            pmcid=metadata.get("pmcid"),
            publisher_id=metadata.get("publisher_id"),
            canonical_url=metadata.get("canonical_url"),
        ),
        "identifiers": {
            "doi": normalize_doi(metadata.get("doi")),
            "pmcid": metadata.get("pmcid"),
            "pmid": metadata.get("pmid"),
            "publisher_id": metadata.get("publisher_id"),
            "canonical_url": metadata.get("canonical_url"),
            "issns": sorted(set(metadata.get("issns") or [])),
        },
        "bibliography": {
            "title": title,
            "authors": metadata.get("authors") or [],
            "journal": metadata.get("journal"),
            "publisher_family": metadata.get("publisher_family", "unknown"),
            "published": metadata.get("published"),
            "article_type": metadata.get("article_type"),
            "language": metadata.get("language") or "en",
        },
        "access": {
            "status": "reusable_full_text",
            "open_access": True,
            "license_url": license_url,
            "license_spdx": spdx_for_license(license_url),
            "reuse_allowed": True,
            "retrieval_basis": retrieval_basis,
        },
        "content": {
            "abstract": abstract,
            "sections": sections,
            "figures": figures,
            "tables": tables,
            "references": references,
            "supplements": [],
            "data_availability": data_availability,
        },
        "provenance": {
            "provider": provider,
            "source_format": "text/html",
            "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "parser_version": PARSER_VERSION,
            "source_sha256": hashlib.sha256(html_bytes).hexdigest(),
            "content_sha256": "0" * 64,
            "warnings": ["publisher media omitted"],
            "completeness": {
                "section_count": len(sections),
                "paragraph_count": len(abstract)
                + sum(len(section["paragraphs"]) for section in sections),
                "reference_count": len(references),
                "has_data_availability": bool(data_availability),
            },
        },
    }
    document["provenance"]["content_sha256"] = content_sha256(document)
    return document


def metadata_to_article(
    metadata: dict[str, Any],
    *,
    provider: str,
    status: str,
    retrieval_basis: str | None = None,
    warning: str | None = None,
) -> dict[str, Any]:
    """Build a schema-valid metadata-only article with an explicit access state."""
    document = {
        "schema_version": SCHEMA_VERSION,
        "article_id": article_id_for(
            doi=metadata.get("doi"),
            pmcid=metadata.get("pmcid"),
            publisher_id=metadata.get("publisher_id"),
            canonical_url=metadata.get("canonical_url"),
        ),
        "identifiers": {
            "doi": normalize_doi(metadata.get("doi")),
            "pmcid": metadata.get("pmcid"),
            "pmid": metadata.get("pmid"),
            "publisher_id": metadata.get("publisher_id"),
            "canonical_url": metadata.get("canonical_url"),
            "issns": sorted(set(metadata.get("issns") or [])),
        },
        "bibliography": {
            "title": metadata.get("title") or "",
            "authors": metadata.get("authors") or [],
            "journal": metadata.get("journal"),
            "publisher_family": metadata.get("publisher_family", "unknown"),
            "published": metadata.get("published"),
            "article_type": metadata.get("article_type"),
            "language": metadata.get("language") or "en",
        },
        "access": {
            "status": status,
            "open_access": False,
            "license_url": metadata.get("license_url"),
            "license_spdx": spdx_for_license(metadata.get("license_url")),
            "reuse_allowed": False,
            "retrieval_basis": retrieval_basis,
        },
        "content": {
            "abstract": [],
            "sections": [],
            "figures": [],
            "tables": [],
            "references": [],
            "supplements": [],
            "data_availability": [],
        },
        "provenance": {
            "provider": provider,
            "source_format": "metadata",
            "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "parser_version": PARSER_VERSION,
            "source_sha256": hashlib.sha256(
                repr(sorted(metadata.items())).encode()
            ).hexdigest(),
            "content_sha256": "0" * 64,
            "warnings": [warning] if warning else [],
            "completeness": {
                "section_count": 0,
                "paragraph_count": 0,
                "reference_count": 0,
                "has_data_availability": False,
            },
        },
    }
    document["provenance"]["content_sha256"] = content_sha256(document)
    return document
