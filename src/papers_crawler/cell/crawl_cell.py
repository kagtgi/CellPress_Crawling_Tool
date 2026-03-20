import argparse
import asyncio
import os

from .crawl_cell_pdf_async import crawl_async
from .crawl_cell_text_async import crawl_text_async

async def main():
    parser = argparse.ArgumentParser(description="Crawl Cell.com papers (PDF + JSON)")
    parser.add_argument("--year-from", type=int, default=2025, help="Start year")
    parser.add_argument("--year-to", type=int, default=2025, help="End year")
    parser.add_argument("--pdf-output", type=str, default="./data/pdfs", help="PDF output directory")
    parser.add_argument("--json-output", type=str, default="./data/json", help="JSON output directory")
    parser.add_argument("--max-papers", type=int, default=None, help="Maximum number of papers per journal")
    parser.add_argument("--journal-slugs", nargs="+", help="Space-separated journal identifiers")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF extraction")
    parser.add_argument("--no-json", action="store_true", help="Skip JSON extraction")
    
    args = parser.parse_args()
    
    if not args.journal_slugs:
        print("Please provide at least one journal slug with --journal-slugs")
        return

    if not args.no_pdf:
        print("Starting PDF extraction...")
        await crawl_async(
            year_from=args.year_from,
            year_to=args.year_to,
            out_folder=args.pdf_output,
            limit=args.max_papers,
            journal_slugs=args.journal_slugs,
        )

    if not args.no_json:
        print("Starting JSON extraction...")
        await crawl_text_async(
            year_from=args.year_from,
            year_to=args.year_to,
            out_folder=args.json_output,
            limit=args.max_papers,
            journal_slugs=args.journal_slugs,
        )

if __name__ == "__main__":
    asyncio.run(main())
