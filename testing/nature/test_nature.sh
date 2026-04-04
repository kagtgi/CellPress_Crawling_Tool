#!/bin/bash

# ==========================================
# Testing Script: Nature Crawler Module
# ==========================================
# This script tests the `papers_crawler.nature` module by extracting
# full-text JSON and PDFs for open-access Nature.com papers.
#
# Parameters used in this test:
#   --journal-slugs   : "nature-medicine" (Targets Nature Medicine)
#   --year-from       : 2024 (Limits to papers from 2024)
#   --year-to         : 2024 (Limits to papers to 2024)
#   --pdf-output      : "./testing/nature/pdfs" (Directory to save PDF files)
#   --json-output     : "./testing/nature/json" (Directory to save JSON metadata files)
#   --max-papers      : 2 (Limits to a maximum of 2 papers to keep the test quick)
#
# Expected output:
#   - PDFs will be downloaded into `./testing/nature/pdfs/nature-medicine/`
#   - JSON metadata will be downloaded into `./testing/nature/json/nature-medicine/`
# ==========================================

echo "Starting Nature.com crawler test..."

export PYTHONPATH="d:/FUV/papersCrawler/src"
export PYTHONIOENCODING="utf-8"

python -m papers_crawler.nature.crawl_nature \
    --journal-slugs bdj \
    --year-from 2024 --year-to 2024 \
    --pdf-output ./testing/nature/pdfs \
    --json-output ./testing/nature/json \
    --max-papers 2

echo "Nature.com crawler test completed."
