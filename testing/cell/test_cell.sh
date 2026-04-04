#!/bin/bash

# ==========================================
# Testing Script: Cell.com Crawler Module
# ==========================================
# This script tests the `papers_crawler.cell` module by extracting
# full-text JSON and PDFs from the Cell.com website.
#
# Parameters used in this test:
#   --journal-slugs   : "cell" (Targets the journal "Cell")
#   --year-from       : 2024 (Limits to papers from 2024)
#   --year-to         : 2024 (Limits to papers to 2024)
#   --pdf-output      : "./testing/cell/pdfs" (Directory to save PDF files)
#   --json-output     : "./testing/cell/json" (Directory to save JSON metadata files)
#   --max-papers      : 2 (Limits to a maximum of 2 papers to keep the test quick)
#
# Expected output:
#   - PDFs will be downloaded into `./testing/cell/pdfs/cell/`
#   - JSON metadata will be downloaded into `./testing/cell/json/cell/`
# ==========================================

echo "Starting Cell.com crawler test..."

export PYTHONPATH="d:/FUV/papersCrawler/src"
export PYTHONIOENCODING="utf-8"

python -m papers_crawler.cell.crawl_cell \
    --journal-slugs cell \
    --year-from 2024 --year-to 2024 \
    --pdf-output ./testing/cell/pdfs \
    --json-output ./testing/cell/json \
    --max-papers 2

echo "Cell.com crawler test completed."
