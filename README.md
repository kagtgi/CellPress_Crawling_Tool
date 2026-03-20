# papers-crawler

A research-paper crawler that can:

| Target | What you get |
|--------|-------------|
| **Cell.com (CellPress)** | Open-access **PDFs** and/or structured **full-text JSON** |
| **Nature.com** | Structured **full-text JSON** (open-access articles) |
| **Nature.com** | **All paper titles** for a year range — open-access *and* fee-based |
| **PubMed** | **All paper titles + metadata** for any journal/year — no browser needed |

---

## Features

- Discovers all journals from Cell.com and Nature.com automatically
- Year-range filtering for targeted crawling
- Multi-journal selection in a single call
- **Open-access full-text JSON** extraction (Cell.com + Nature.com)
- **All-title listing** including fee/subscription papers (Nature.com + PubMed)
- PubMed crawler using the NCBI E-utilities REST API — no browser required
- CSV + ZIP export summaries after every crawl
- Streamlit web UI for point-and-click operation
- Progress callbacks for custom UI / logging integration
- Automatic cookie-consent handling and Cloudflare-safe delays

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/kagtgi/CellPress_Crawling_Tool.git
cd CellPress_Crawling_Tool

# 2. (Recommended) create a virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install Firefox browser binaries used by Playwright (run once)
playwright install firefox
playwright install --only-shell
```

### Editable install (for development / Jupyter)

```bash
pip install -e .
```

---

## Quick start

### Streamlit web UI

```bash
streamlit run scripts/run_crawler_streamlit.py
```

1. Click **"Load journals from Cell.com"**
2. Select one or more journals
3. Set year range and output folder
4. Click **"Start Crawl"**

### Python / Jupyter

```python
import asyncio
from papers_crawler import (
    crawl_async,               # CellPress PDFs
    crawl_text_async,          # CellPress full-text JSON
    crawl_text_nature_async,   # Nature full-text JSON
    crawl_titles_nature_async, # ALL Nature titles (OA + fee)
    crawl_pubmed_async,        # PubMed title + metadata
)
```

---

## Command-Line Usage

The refactored codebase provides modular command-line scripts for each source to flexibly extract papers (PDFs and/or JSON metadata).

### 1. Cell.com Crawler

Crawl Cell.com papers to extract PDFs and structured full-text JSON.

```bash
python -m papers_crawler.cell.crawl_cell --journal-slugs cell immunity \
    --year-from 2024 --year-to 2025 \
    --pdf-output ./data/pdfs \
    --json-output ./data/json \
    --max-papers 20
```

**Parameters:**

- `--journal-slugs`: (Required) Space-separated journal identifiers. Slugs can be found by looking at the journal URL on Cell.com (e.g., for `https://www.cell.com/immunity/home`, the slug is `immunity`).
- `--year-from`, `--year-to`: Start and end year filter.
- `--pdf-output`, `--json-output`: Output directories.
- `--max-papers`: Maximum number of papers per journal.
- `--no-pdf`, `--no-json`: Add these flags to skip extracting PDF or JSON formats.

**Output:**

- Downloaded PDFs stored in `./data/pdfs/<slug>/`
- Structured JSON extracted stored in `./data/json/<slug>/Article_Title_YYYY.json`

### 2. Nature.com Crawler

Crawl Nature.com open-access papers for PDFs and structured full-text JSON.

```bash
# Example 1: By journal slugs
python -m papers_crawler.nature.crawl_nature --journal-slugs nature-medicine nature-immunology \
    --year-from 2024 --year-to 2025 \
    --pdf-output ./data/pdfs \
    --json-output ./data/json \
    --max-papers 50

# Example 2: By input file (CSV, Excel, or JSONL containing a "url" column/field)
python -m papers_crawler.nature.crawl_nature --use-input-file y \
    --input-file ./input.csv \
    --pdf-output ./data/pdfs \
    --json-output ./data/json
```

**Parameters:**

- `--journal-slugs`: (Required unless using input file) Space-separated journal identifiers. Find a slug in the journal's Nature.com URL (e.g., `https://www.nature.com/nm/` maps to `nature-medicine`). Alternatively, use the `discover_journals_nature_async()` API.
- `--use-input-file`: Set to `y` to crawl from a list of URLs instead of journals parameters.
- `--input-file`: Path to the input CSV/Excel/JSONL file containing a `url` column (used when `--use-input-file y`).
- `--year-from`, `--year-to`: Start and end year filter for crawling.
- `--pdf-output`, `--json-output`: Output directories for downloaded content.
- `--max-papers`: Maximum number of papers per journal.

**Output:**

- Downloaded open-access PDFs in `./data/pdfs/<slug>/` or `./data/pdfs/` (if using input file).
- Extracted JSON text in `./data/json/<slug>/Article_Title_YYYY.json` or `./data/json/<Article_ID>.json` (if using input file).

### 3. PubMed Crawler

Crawl titles and metadata using the NCBI E-utilities REST API (no browser required).

```bash
python -m papers_crawler.pubmed.crawl_pubmed --journals "Nature Immunology" "Cell" \
    --year-from 2024 --year-to 2025 \
    --out-folder ./data/pubmed \
    --max-papers 1000 \
    --chunk-size 6 \
    --keywords "cancer" \
    --api-key "YOUR_NCBI_API_KEY"
```

**Parameters:**

- `--journals`: (Required) Space-separated exact journal names as they appear in PubMed (e.g., `"Nature Immunology"`, `"Cell"`). Enclose names with spaces in quotes.
- `--year-from`, `--year-to`: Publication year range.
- `--out-folder`: Directory where the resulting CSV files and summaries will be saved.
- `--max-papers`: Max number of records to fetch per journal. If omitted, all matching papers in the time range are fetched.
- `--chunk-size`: Chunk size in months for splitting time ranges to bypass PubMed's query limits (default: 6).
- `--keywords`: Optional extra search terms for more specific scraping.
- `--api-key`: Optional NCBI API key for higher rate limits (10 req/s vs 3 req/s).

**Output:**

- CSV summaries for the fetched metadata saved in `./data/pubmed/` (e.g., `pubmed_all_journals_YYYYMMDD_HHMMSS.csv`).

---

## API reference

### 1 — CellPress: discover journals

```python
from papers_crawler import discover_journals, discover_journals_async

# Sync (scripts, Streamlit)
journals = discover_journals(force_refresh=False)
# → List[Tuple[str, str]]  — (slug, display_name)

# Async (Jupyter / Colab)
journals = await discover_journals_async(force_refresh=False)
```

### 2 — CellPress: download PDFs

```python
from papers_crawler import crawl, crawl_async

# Sync
file_paths, titles = crawl(
    keywords      = "",            # free-text filter; "" = no filter
    year_from     = 2023,
    year_to       = 2024,
    out_folder    = "papers",      # PDFs saved here, sub-folder per journal
    headless      = True,
    limit         = 10,            # max per journal; None = unlimited
    journal_slugs = ["immunity", "cell"],
)

# Async (Jupyter / Colab) — same params + crawl_archives
file_paths, titles = await crawl_async(
    year_from      = 2023,
    year_to        = 2024,
    out_folder     = "papers",
    limit          = 10,
    journal_slugs  = ["immunity"],
    crawl_archives = True,  # also scrape /issue archive pages
)
```

### 3 — CellPress: extract full-text JSON

```python
from papers_crawler import crawl_text_async

json_paths, titles = await crawl_text_async(
    year_from     = 2024,
    year_to       = 2024,
    out_folder    = "papers_text",
    limit         = 5,
    journal_slugs = ["immunity"],
)
```

Each JSON file contains:

```json
{
  "url": "https://www.cell.com/...",
  "extracted_at": "2024-01-15T10:30:00",
  "title": "Article title",
  "authors": "Author A, Author B",
  "publication_date": "2024-01-10",
  "doi": "10.1016/...",
  "Abstract": "...",
  "Introduction": "...",
  "Results": "...",
  "Discussion": "...",
  "Figures": "...",
  "References": "..."
}
```

### 4 — Nature.com: discover journals

```python
from papers_crawler import discover_journals_nature_async

journals = await discover_journals_nature_async(force_refresh=False)
# → List[Tuple[str, str]]  — (slug, display_name)
```

### 5 — Nature.com: extract full-text JSON (open-access only)

```python
from papers_crawler import crawl_text_nature_async

json_paths, titles = await crawl_text_nature_async(
    year_from     = 2024,
    year_to       = 2024,
    out_folder    = "papers_nature",
    limit         = 5,
    journal_slugs = ["nature-medicine", "nature-immunology"],
)
```

### 6 — Nature.com: crawl ALL titles (open-access + fee-based)

Returns every article title listed on the journal's research-articles page, with an `open_access` flag — no full-text is downloaded.

```python
from papers_crawler import crawl_titles_nature_async

all_articles, oa_articles = await crawl_titles_nature_async(
    year_from     = 2024,
    year_to       = 2024,
    journal_slugs = ["nature", "nature-medicine"],
    limit         = 200,   # total cap across all journals
)

# Each record is a dict:
# {
#   "title":       str,
#   "url":         str,
#   "date":        str,   # "YYYY-MM-DD"
#   "year":        int,
#   "journal":     str,   # slug
#   "open_access": bool,
# }

for article in all_articles:
    status = "OA" if article["open_access"] else "fee"
    print(f"[{status}] {article['title']}")
```

### 7 — PubMed: search titles and metadata

Uses the **NCBI E-utilities REST API** — no browser or Playwright needed.

```python
from papers_crawler import search_pubmed_async, crawl_pubmed_async, crawl_pubmed_journals_async

# ── Single search ──────────────────────────────────────────────────────────
all_articles, oa_articles = await search_pubmed_async(
    journal   = "Nature Immunology",  # as it appears in PubMed
    year_from = 2023,
    year_to   = 2024,
    keywords  = "",                   # optional extra search terms
    limit     = 500,
    api_key   = None,                 # optional NCBI API key (10 req/s vs 3 req/s)
)

# ── Single journal + save CSV ──────────────────────────────────────────────
all_articles, oa_articles = await crawl_pubmed_async(
    journal    = "Cell",
    year_from  = 2023,
    year_to    = 2024,
    out_folder = "papers_pubmed",     # CSV saved here
    save_csv   = True,
)

# ── Multiple journals ──────────────────────────────────────────────────────
all_articles, oa_articles = await crawl_pubmed_journals_async(
    journals   = ["Nature", "Cell", "Science"],
    year_from  = 2024,
    year_to    = 2024,
    out_folder = "papers_pubmed",
)
```

Each article record contains:

```python
{
    "pmid":        "38123456",
    "title":       "Article title",
    "authors":     "Smith J, Doe A",
    "journal":     "Nature Immunology",
    "pub_date":    "2024 Jan",
    "year":        2024,
    "doi":         "10.1038/...",
    "pmc_id":      "PMC12345678",    # empty string if not on PMC
    "open_access": True,             # True if pmc_id is present
    "url":         "https://pubmed.ncbi.nlm.nih.gov/38123456/",
    "pmc_url":     "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12345678/",
}
```

> **Open-access detection in PubMed:** articles deposited in PubMed Central (PMC) are marked `open_access=True`. This covers most mandated open-access papers but may miss some hybrid OA articles not deposited in PMC.

---

## Common journal slugs

### CellPress

| Slug | Journal |
|------|---------|
| `cell` | Cell |
| `immunity` | Immunity |
| `neuron` | Neuron |
| `cancer-cell` | Cancer Cell |
| `cell-metabolism` | Cell Metabolism |
| `cell-reports` | Cell Reports |
| `current-biology` | Current Biology |
| `developmental-cell` | Developmental Cell |
| `joule` | Joule |
| `matter` | Matter |

### Nature

| Slug | Journal |
|------|---------|
| `nature` | Nature |
| `nature-medicine` | Nature Medicine |
| `nature-cancer` | Nature Cancer |
| `nature-biotechnology` | Nature Biotechnology |
| `nature-genetics` | Nature Genetics |
| `nature-immunology` | Nature Immunology |
| `nature-neuroscience` | Nature Neuroscience |
| `nature-cell-biology` | Nature Cell Biology |
| `nature-methods` | Nature Methods |
| `nature-communications` | Nature Communications |

### PubMed journal names (examples)

Use the journal name **as it appears in PubMed** (full name or MeSH abbreviation):

| PubMed name | Journal |
|-------------|---------|
| `"Nature"` | Nature |
| `"Nature medicine"` | Nature Medicine |
| `"Cell"` | Cell |
| `"Science (New York, N.Y.)"` | Science |
| `"Nature immunology"` | Nature Immunology |
| `"The New England journal of medicine"` | NEJM |

> Tip: run `search_pubmed_async(journal="Nature Immunology", year_from=2024, year_to=2024, limit=1)` to verify the journal name resolves correctly before a large crawl.

---

## Progress callbacks

All browser-based crawl functions accept two optional callbacks:

```python
def on_file_saved(filename: str, filepath: str):
    """Called once per article after the file is saved."""
    print(f"Saved: {filename}")

def on_progress(current, total, status, file_size, speed_kbps, stage):
    """Called repeatedly with overall progress."""
    pct = 100 * current / max(total, 1)
    print(f"[{pct:.0f}%] {stage}: {status}")

json_paths, titles = await crawl_text_async(
    journal_slugs           = ["immunity"],
    year_from               = 2024,
    year_to                 = 2024,
    progress_callback       = on_file_saved,
    total_progress_callback = on_progress,
)
```

`crawl_pubmed_async` / `search_pubmed_async` accept a simpler `progress_callback(article_dict)` called per article.

---

## Output files

Every crawl produces a timestamped CSV summary and a ZIP archive in `out_folder`:

```
papers_nature/
├── nature-medicine/
│   ├── Article_Title_2024.json
│   └── ...
├── extraction_summary_20240115_103045.csv
└── all_nature_journals_json_20240115_103045.zip

papers_pubmed/
├── Cell/
│   ├── pubmed_titles_20240115_103045.csv
│   └── pubmed_oa_20240115_103045.csv
└── pubmed_all_journals_20240115_103045.csv
```

CSV columns (full-text crawlers): `Number, Journal, Article Name, Publish Date, File Path, File Size (KB)`

CSV columns (PubMed): `pmid, title, authors, journal, pub_date, year, doi, open_access, pmc_id, url, pmc_url`

---

## Troubleshooting

### Jupyter / Google Colab: `RuntimeError: This event loop is already running`

Use `await` directly — never `asyncio.run()` inside Jupyter:

```python
# Wrong:
asyncio.run(crawl_text_async(...))

# Correct:
result = await crawl_text_async(...)
```

### Cloudflare challenge / CAPTCHA

Set `headless=False` to open a visible browser, solve the CAPTCHA once, then the crawl resumes automatically.

### "No articles found" on Cell.com

- Confirm the slug is correct: `await discover_journals_async()`
- Cell.com's `/newarticles` page may be empty for older years — try `crawl_archives=True` in `crawl_async`.

### PubMed returns fewer results than expected

- Verify the journal name with a small test call first.
- Use an NCBI API key (`api_key=`) for higher rate limits (10 req/s vs 3 req/s). Register at https://www.ncbi.nlm.nih.gov/account/
- The default `limit=10_000` should cover most journals per year.

### `playwright._impl._errors.Error: Executable doesn't exist`

Re-install the browser: `playwright install firefox`

---

## Notes

- This tool only **downloads open-access content** from Cell.com and Nature.com to respect copyright.
- Title listing (`crawl_titles_nature_async`, `crawl_pubmed_async`) collects only metadata — no full-text is accessed for fee-based articles.
- Polite delays (≥1 second between requests) are built in for browser-based crawlers.
- NCBI E-utilities requests respect the 3 req/s limit (or 10 req/s with an API key).
- Journal lists are cached in `.cache/papers_crawler/` — delete the cache or pass `force_refresh=True` to update.
