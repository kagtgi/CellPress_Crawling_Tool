#!/bin/bash
echo "Testing PubMed crawler with no max-papers (should fetch all for a short time range)"
export PYTHONPATH="d:/FUV/papersCrawler/src"
python -m papers_crawler.pubmed.crawl_pubmed \
    --journals "Cell" \
    --year-from 2024 --year-to 2024 \
    --out-folder ./testing/pubmed/csv/no_limit \
    --chunk-size 3
echo "Complete!"
