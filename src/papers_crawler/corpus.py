"""Resumable crawler orchestration and append-only manifests."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .article import article_id_for, validate_article_document, write_immutable
from .normalize import html_to_article, jats_to_article, metadata_to_article
from .providers import (
    ProviderAttempt,
    discover_crossref,
    discover_europe_pmc,
    enrich_europe_pmc,
    fetch_reusable_full_text,
)


@dataclass(frozen=True)
class CrawlConfig:
    output_dir: Path
    start_year: int = 2010
    end_year: int = time.gmtime().tm_year
    rows: int = 100
    max_articles: int | None = None
    min_interval_seconds: float = 60.0
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        if self.start_year < 1900 or self.end_year < self.start_year:
            raise ValueError("invalid year range")
        # A zero interval silently disables the politeness gate that the README
        # documents as non-negotiable. Reject it explicitly rather than let
        # `--paper-interval 0` hammer publishers.
        if self.min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds must be greater than zero")


class CrawlState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS crawl_state (
              provider TEXT PRIMARY KEY,
              cursor TEXT,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS articles (
              article_id TEXT PRIMARY KEY,
              content_sha256 TEXT NOT NULL,
              json_path TEXT NOT NULL,
              access_status TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              article_id TEXT NOT NULL,
              provider TEXT NOT NULL,
              endpoint TEXT NOT NULL,
              status TEXT NOT NULL,
              http_status INTEGER,
              reason TEXT,
              attempted_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS crawl_runs (
              run_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS crawl_queue (
              article_id TEXT PRIMARY KEY,
              metadata_json TEXT NOT NULL,
              status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS processing_queue (
              article_id TEXT NOT NULL,
              content_sha256 TEXT NOT NULL,
              json_path TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(article_id, content_sha256)
            );
            """
        )

    def cursor(self, provider: str) -> str:
        row = self.connection.execute(
            "SELECT cursor FROM crawl_state WHERE provider = ?", (provider,)
        ).fetchone()
        return row["cursor"] if row and row["cursor"] else "*"

    def set_cursor(self, provider: str, cursor: str | None) -> None:
        self.connection.execute(
            """
            INSERT INTO crawl_state(provider, cursor, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(provider) DO UPDATE SET
              cursor=excluded.cursor, updated_at=excluded.updated_at
            """,
            (provider, cursor),
        )
        self.connection.commit()

    def record_attempt(self, article_id: str, attempt: ProviderAttempt) -> None:
        self.connection.execute(
            """
            INSERT INTO attempts(
              article_id, provider, endpoint, status, http_status, reason, attempted_at
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                article_id,
                attempt.provider,
                attempt.endpoint,
                attempt.status,
                attempt.http_status,
                attempt.reason,
            ),
        )
        self.connection.commit()

    def record_article(self, document: dict[str, Any], path: Path) -> None:
        self.connection.execute(
            """
            INSERT INTO articles(
              article_id, content_sha256, json_path, access_status, updated_at
            ) VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(article_id) DO UPDATE SET
              content_sha256=excluded.content_sha256,
              json_path=excluded.json_path,
              access_status=excluded.access_status,
              updated_at=excluded.updated_at
            """,
            (
                document["article_id"],
                document["provenance"]["content_sha256"],
                str(path),
                document["access"]["status"],
            ),
        )
        self.connection.execute(
            """
            INSERT INTO processing_queue(
              article_id, content_sha256, json_path, status, updated_at
            ) VALUES (?, ?, ?, 'pending', datetime('now'))
            ON CONFLICT(article_id, content_sha256) DO UPDATE SET
              json_path=excluded.json_path,
              status=CASE
                WHEN processing_queue.status='done' THEN 'done'
                ELSE 'pending'
              END,
              updated_at=excluded.updated_at
            """,
            (
                document["article_id"],
                document["provenance"]["content_sha256"],
                str(path),
            ),
        )
        self.connection.commit()

    def start_run(self, run_id: str) -> None:
        self.connection.execute(
            """
            INSERT INTO crawl_runs(run_id, status, started_at)
            VALUES (?, 'running', datetime('now'))
            """,
            (run_id,),
        )
        self.connection.commit()

    def finish_run(self, run_id: str, status: str) -> None:
        self.connection.execute(
            """
            UPDATE crawl_runs SET status=?, completed_at=datetime('now')
            WHERE run_id=?
            """,
            (status, run_id),
        )
        self.connection.commit()

    def enqueue_crawl(self, article_id: str, metadata: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO crawl_queue(
              article_id, metadata_json, status, updated_at
            ) VALUES (?, ?, 'pending', datetime('now'))
            ON CONFLICT(article_id) DO UPDATE SET
              metadata_json=excluded.metadata_json,
              status=CASE WHEN crawl_queue.status='done' THEN 'done' ELSE 'pending' END,
              updated_at=excluded.updated_at
            """,
            (article_id, json.dumps(metadata, ensure_ascii=False, sort_keys=True)),
        )
        self.connection.commit()

    def mark_crawl(
        self, article_id: str, status: str, error: str | None = None
    ) -> None:
        self.connection.execute(
            """
            UPDATE crawl_queue SET status=?,
              attempts=attempts + CASE WHEN ?='fetching' THEN 1 ELSE 0 END,
              last_error=?, updated_at=datetime('now') WHERE article_id=?
            """,
            (status, status, error, article_id),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


def _manifest_append(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def sync_corpus(
    config: CrawlConfig,
    *,
    discovered: Iterable[tuple[dict[str, Any], str | None]] | None = None,
) -> dict[str, Any]:
    """Discover, fetch, validate, and persist an idempotent corpus batch."""
    root = config.output_dir
    root.mkdir(parents=True, exist_ok=True)
    state = CrawlState(root / "crawl-state.db")
    run_id = config.run_id or (
        time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        + "-"
        + uuid.uuid4().hex[:8]
    )
    manifest = root / "runs" / run_id / "manifest.jsonl"
    counts = {"discovered": 0, "full_text": 0, "metadata_only": 0, "failed": 0}
    state.start_run(run_id)
    last_paper_started: float | None = None
    try:
        if discovered is None:
            sources = (
                (
                    "crossref",
                    discover_crossref(
                        start_year=config.start_year,
                        end_year=config.end_year,
                        rows=config.rows,
                        cursor=state.cursor("crossref"),
                    ),
                ),
                (
                    "europe_pmc_discovery",
                    discover_europe_pmc(
                        start_year=config.start_year,
                        end_year=config.end_year,
                        page_size=max(config.rows, 100),
                        cursor=state.cursor("europe_pmc_discovery"),
                    ),
                ),
            )
        else:
            sources = (("fixture", discovered),)
        seen: set[str] = set()
        for discovery_provider, source in sources:
          for metadata, next_cursor in source:
            if config.max_articles is not None and counts["discovered"] >= config.max_articles:
                break
            article_id = article_id_for(
                doi=metadata.get("doi"),
                pmcid=metadata.get("pmcid"),
                publisher_id=metadata.get("publisher_id"),
                canonical_url=metadata.get("canonical_url"),
            )
            if article_id in seen:
                state.set_cursor(discovery_provider, next_cursor)
                continue
            seen.add(article_id)
            counts["discovered"] += 1
            state.enqueue_crawl(article_id, metadata)
            try:
                if last_paper_started is not None:
                    remaining = (
                        config.min_interval_seconds
                        - (time.monotonic() - last_paper_started)
                    )
                    if remaining > 0:
                        time.sleep(remaining)
                last_paper_started = time.monotonic()
                state.mark_crawl(article_id, "fetching")
                enriched = enrich_europe_pmc(metadata)
                xml_bytes, provider, basis, attempts = fetch_reusable_full_text(
                    enriched
                )
                for attempt in attempts:
                    state.record_attempt(article_id, attempt)
                if xml_bytes and provider and basis:
                    normalizer = (
                        html_to_article if provider == "nature_html" else jats_to_article
                    )
                    document = normalizer(
                        xml_bytes,
                        enriched,
                        provider=provider,
                        retrieval_basis=basis,
                        license_url=enriched.get("license_url"),
                    )
                    counts["full_text"] += 1
                else:
                    attempt = attempts[-1] if attempts else ProviderAttempt(
                        "providers", "", "no_machine_endpoint"
                    )
                    document = metadata_to_article(
                        enriched,
                        provider="crossref",
                        status=attempt.status,
                        retrieval_basis="Crossref metadata and Europe PMC lookup",
                        warning=attempt.reason,
                    )
                    counts["metadata_only"] += 1
                validate_article_document(document)
                dest = write_immutable(document, root)
                state.record_article(document, dest)
                state.mark_crawl(article_id, "done")
                _manifest_append(
                    manifest,
                    {
                        "article_id": document["article_id"],
                        "content_sha256": document["provenance"]["content_sha256"],
                        "json_uri": str(dest),
                        "access_status": document["access"]["status"],
                    },
                )
                state.set_cursor(discovery_provider, next_cursor)
            except Exception as exc:
                counts["failed"] += 1
                state.mark_crawl(article_id, "retryable_failure", type(exc).__name__)
                state.record_attempt(
                    article_id,
                    ProviderAttempt(
                        "sync", "", "retryable_failure", reason=type(exc).__name__
                    ),
                )
          if config.max_articles is not None and counts["discovered"] >= config.max_articles:
              break
        summary = {"run_id": run_id, "manifest": str(manifest), **counts}
        (manifest.parent / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
        )
        state.finish_run(run_id, "complete" if counts["failed"] == 0 else "partial")
        return summary
    except Exception:
        state.finish_run(run_id, "failed")
        raise
    finally:
        state.close()
