#!/bin/bash
echo "Testing PubMed crawler with an input file"

export PYTHONPATH="d:/FUV/papersCrawler/src"
export PYTHONIOENCODING="utf-8"

python -m papers_crawler.pubmed.crawl_pubmed \
    --use-input-file y \
    --input-file d:/FUV/papersCrawler/testing/pubmed/input/input.csv\
    --pdf-output d:/FUV/papersCrawler/testing/pubmed/pdf \
    --time-measurement-output d:/FUV/papersCrawler/testing/pubmed/time_measurement \
    --batch-size 100
    # --json-output ./testing/pubmed/json \
echo "Complete!"
