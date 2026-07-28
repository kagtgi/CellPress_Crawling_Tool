# papers-crawler v2

License-aware, resumable metadata and full-text JSON crawling for the checked-in
Cell Press and Nature Portfolio life-science journal registry.

Production v2:

- Discovers metadata through Crossref and Europe PMC/PubMed.
- Retrieves reusable full text as keyless PMC OA XML first.
- Tries licensed Crossref machine-readable links.
- Crawls CC-licensed Nature article HTML with a `TextDataMining` user agent and
  normalizes the page text to structural JSON without media.
- Does not screen-scrape Cell Press/ScienceDirect pages; keyless Cell Press
  full text comes from PMC/Europe PMC or licensed Crossref endpoints.
- Rejects PDF URLs, PDF media types, and PDF magic bytes.
- Validates immutable `bioparser.article.v1` JSON.
- Writes crawl-state SQLite, append-only manifests, provider attempts, and
  explicit terminal/retryable access states.
- Starts at most one paper every 60 seconds by default and publishes each
  validated article to a durable processing queue immediately.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
papers-crawler sync --output ./corpus --start-year 2010 --end-year 2026
papers-crawler validate ./corpus/articles/<id>/<hash>.json.gz
```

Use `--paper-interval 60` explicitly when an operations wrapper supplies the
rate. Setting it to zero is intended only for offline fixtures.

No publisher API key is required or used by the production provider chain.

The historical browser/UI modules remain under their fully qualified module
paths for one compatibility release and require explicit legacy extras. They
are not imported by `papers_crawler`, installed on the corpus VM, or used by
the v2 CLI.
