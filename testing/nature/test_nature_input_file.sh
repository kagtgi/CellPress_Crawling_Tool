#!/bin/bash

echo "Starting Nature.com crawler using input file test..."

export PYTHONPATH="d:/FUV/papersCrawler/src"
export PYTHONIOENCODING="utf-8"

python -m papers_crawler.nature.crawl_nature \
    --journal-slugs bdj \
    --year-from 2025 --year-to 2025 \
    --pdf-output ./testing/nature/pdfs \
    --json-output ./testing/nature/json \
    --max-papers 2 \
    --use-input-file y \
    --input-file ./testing/nature/input/input.csv

echo "Nature.com crawler test completed."
