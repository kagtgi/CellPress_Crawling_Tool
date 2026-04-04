#!/bin/bash
echo "Testing PubMed crawler with an input file"

export PYTHONPATH="d:/FUV/papersCrawler/src"
export PYTHONIOENCODING="utf-8"

python -m papers_crawler.pubmed.crawl_pubmed \
    --use-input-file y \
    --input-file ../../testing/pubmed/input/filtered_input.csv\
    --pdf-output ../../testing/pubmed/pdf \
    --time-measurement-output ../../testing/pubmed/time_measurement \
    --batch-size 10
    # --json-output ./testing/pubmed/json \
echo "Complete!"
