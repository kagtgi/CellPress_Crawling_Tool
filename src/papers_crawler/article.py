"""Canonical article identity, validation, and immutable serialization."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "bioparser.article.v1"


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value.strip(), flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I).strip().lower()
    return doi or None


def article_id_for(
    *,
    doi: str | None = None,
    pmcid: str | None = None,
    publisher_id: str | None = None,
    canonical_url: str | None = None,
) -> str:
    doi = normalize_doi(doi)
    if doi:
        return f"doi:{doi}"
    if pmcid:
        value = pmcid.strip().upper()
        return f"pmcid:{value if value.startswith('PMC') else 'PMC' + value}"
    if publisher_id:
        return f"publisher:{publisher_id.strip()}"
    if canonical_url:
        digest = hashlib.sha256(canonical_url.strip().encode()).hexdigest()[:24]
        return f"urlsha256:{digest}"
    raise ValueError("article identity requires DOI, PMCID, publisher id, or URL")


def load_article_schema() -> dict[str, Any]:
    path = files("papers_crawler").joinpath("schemas/article-document-v1.json")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_article_document(document: dict[str, Any]) -> None:
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("jsonschema is required to validate article JSON") from exc
    jsonschema.Draft202012Validator(load_article_schema()).validate(document)


def canonical_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def content_sha256(document: dict[str, Any]) -> str:
    """Hash normalized article content, excluding retrieval-time bookkeeping."""
    normalized = deepcopy(document)
    provenance = normalized.setdefault("provenance", {})
    provenance["content_sha256"] = "0" * 64
    provenance.pop("retrieved_at", None)
    return hashlib.sha256(canonical_bytes(normalized)).hexdigest()


def verify_content_hash(document: dict[str, Any]) -> bool:
    return document.get("provenance", {}).get("content_sha256") == content_sha256(
        document
    )


def write_immutable(document: dict[str, Any], root: str | Path) -> Path:
    validate_article_document(document)
    if not verify_content_hash(document):
        raise ValueError("article content_sha256 does not match canonical content")
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", document["article_id"])
    digest = document["provenance"]["content_sha256"]
    dest = Path(root) / "articles" / safe_id / f"{digest}.json.gz"
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(dest.suffix + ".tmp")
    with gzip.open(temp, "wt", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, sort_keys=True)
    temp.replace(dest)
    return dest
