#!/bin/bash

# ==========================================
# Testing Script: PubMed Crawler Module
# ==========================================
# This script tests the `papers_crawler.pubmed` module by extracting
# titles, authors, and PubMed metadata (no full-text PDFs).
#
# Parameters used in this test:
#   --journals        : "Cell" (Targets the exact journal name "Cell")
#   --year-from       : 2024 (Limits to papers from 2024)
#   --year-to         : 2024 (Limits to papers to 2024)
#   --out-folder      : "./testing/pubmed/csv" (Directory to save CSV outputs)
#   --max-papers      : 5 (Limits to a maximum of 5 papers to keep the test quick)
#
# Expected output:
#   - A CSV summary containing the fetched metadata will be saved
#     inside `./testing/pubmed/csv/Cell/`
# ==========================================

echo "Starting PubMed crawler test..."

export PYTHONPATH="d:/FUV/papersCrawler/src"
export PYTHONIOENCODING="utf-8"

python -m papers_crawler.pubmed.crawl_pubmed \
    --journals "Nature" \
    --year-from 2025 --year-to 2025 \
    --out-folder d:/FUV/papersCrawler/testing/pubmed/csv \
    --chunk-size 6 \
    --batch-size 100 \
    --time-measurement-output d:/FUV/papersCrawler/testing/pubmed/time_measurement 
echo "PubMed crawler test completed."
