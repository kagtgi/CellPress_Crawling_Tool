#!/bin/bash
echo "Testing PubMed crawler with max-papers over limit (e.g. 15000)"
export PYTHONPATH="d:/FUV/papersCrawler/src"
python -m papers_crawler.pubmed.crawl_pubmed \
    --journals "Nature" \
    --year-from 1990 --year-to 2024 \
    --out-folder ./testing/pubmed/csv/over_limit \
    --max-papers 15000 --chunk-size 6
echo "Complete!"
