#!/bin/bash
echo "Testing PubMed crawler with an input file"

export PYTHONPATH="/home/giakhiem21042004/papersCrawler/src"
export PYTHONIOENCODING="utf-8"

python -m papers_crawler.pubmed.crawl_pubmed \
    --use-input-file y \
    --input-file /home/giakhiem21042004/papersCrawler/testing/pubmed/input/20-testcase.csv\
    --pdf-output /home/giakhiem21042004/papersCrawler/testing/pubmed/pdf \
    --time-measurement-output /home/giakhiem21042004/papersCrawler/testing/pubmed/time_measurement \
    --batch-size 100
    # --json-output ./testing/pubmed/json \
echo "Complete!"
