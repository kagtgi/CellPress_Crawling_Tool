"""Command line interface for the v2 corpus crawler."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from .article import validate_article_document, verify_content_hash
from .corpus import CrawlConfig, sync_corpus


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="papers-crawler")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("discover", "fetch", "sync"):
        command = sub.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--start-year", type=int, default=2010)
        command.add_argument("--end-year", type=int)
        command.add_argument("--max-articles", type=int)
        command.add_argument(
            "--paper-interval",
            type=float,
            default=60.0,
            help="Minimum seconds between starting papers (default: 60).",
        )
        command.add_argument("--run-id", help="Caller-stable run identifier.")
    validate = sub.add_parser("validate")
    validate.add_argument("paths", nargs="+", type=Path)
    return parser


def _read(path: Path) -> dict:
    opener = gzip.open if path.suffix == ".gz" else path.open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate":
        for path in args.paths:
            document = _read(path)
            validate_article_document(document)
            if not verify_content_hash(document):
                raise SystemExit(f"content hash mismatch: {path}")
        print(json.dumps({"valid": len(args.paths)}))
        return 0
    # discover/fetch are intentionally the same resumable engine in v2. The
    # aliases support operational staging without creating divergent behavior.
    config = CrawlConfig(
        output_dir=args.output,
        start_year=args.start_year,
        end_year=args.end_year or __import__("time").gmtime().tm_year,
        max_articles=args.max_articles,
        min_interval_seconds=args.paper_interval,
        run_id=args.run_id,
    )
    print(json.dumps(sync_corpus(config), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
