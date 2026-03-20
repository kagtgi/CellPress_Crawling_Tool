import argparse
import asyncio
import os

from .crawl_pubmed_async import crawl_pubmed_journals_async

async def main():
    parser = argparse.ArgumentParser(description="Crawl PubMed papers titles and metadata")
    parser.add_argument("--year-from", type=int, default=2024, help="Start year")
    parser.add_argument("--year-to", type=int, default=2025, help="End year")
    parser.add_argument("--out-folder", type=str, default="./data/pubmed", help="CSV output directory")
    parser.add_argument("--chunk-size", type=int, default=6, help="Chunk size in months for splitting time ranges (default: 6)")
    parser.add_argument("--max-papers", type=int, default=None, help="Maximum number of papers per journal")
    parser.add_argument("--journals", nargs="+", help="Space-separated journal names")
    parser.add_argument("--keywords", type=str, default="", help="Additional search keywords")
    parser.add_argument("--api-key", type=str, default=None, help="NCBI API key for higher rate limits")
    
    args = parser.parse_args()
    
    if not args.journals:
        print("Please provide at least one journal with --journals")
        return

    await crawl_pubmed_journals_async(
        journals=args.journals,
        year_from=args.year_from,
        year_to=args.year_to,
        keywords=args.keywords,
        out_folder=args.out_folder,
        chunk_size_months=args.chunk_size,
        limit_per_journal=args.max_papers,
        api_key=args.api_key,
    )

if __name__ == "__main__":
    asyncio.run(main())
