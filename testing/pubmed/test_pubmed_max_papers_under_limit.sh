#!/bin/bash
echo "Testing PubMed crawler with max-papers under limit (e.g. 3)"
export PYTHONPATH="d:/FUV/papersCrawler/src"
python -m papers_crawler.pubmed.crawl_pubmed \
    --journals "Nature" \
    --year-from 2024 --year-to 2024 \
    --out-folder ./testing/pubmed/csv/under_limit \
    --max-papers 12 --chunk-size 6 \
    --time-measurement-output ./testing/pubmed/time_measurement
echo "Complete!"
