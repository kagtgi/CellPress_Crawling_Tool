import argparse
import asyncio
import os

from .crawl_nature_async import crawl_text_nature_async
from .crawl_nature_using_input_file import crawl_nature_from_file_async

async def main():
    parser = argparse.ArgumentParser(description="Crawl Nature.com papers (PDF + JSON)")
    parser.add_argument("--year-from", type=int, default=2025, help="Start year")
    parser.add_argument("--year-to", type=int, default=2025, help="End year")
    parser.add_argument("--pdf-output", type=str, default="./data/pdfs", help="PDF output directory")
    parser.add_argument("--json-output", type=str, default="./data/json", help="JSON output directory")
    parser.add_argument("--max-papers", type=int, default=None, help="Maximum number of papers per journal")
    parser.add_argument("--journal-slugs", nargs="+", help="Space-separated journal identifiers")
    parser.add_argument("--use-input-file", type=str, choices=["y", "n"], default="n", help="Whether to use an input file (y/n)")
    parser.add_argument("--input-file", type=str, default=None, help="Path to the input file (CSV/Excel/JSONL) containing URLs")
    
    args = parser.parse_args()
    
    if args.use_input_file == "y":
        if not args.input_file:
            print("Error: --input-file must be provided when --use-input-file is 'y'")
            return
        if not os.path.exists(args.input_file):
            print(f"Error: Input file '{args.input_file}' does not exist.")
            return

        await crawl_nature_from_file_async(
            input_file=args.input_file,
            out_folder=args.json_output,
            pdf_out_folder=args.pdf_output,
        )
    else:
        if not args.journal_slugs:
            print("Please provide at least one journal slug with --journal-slugs")
            return

        await crawl_text_nature_async(
            year_from=args.year_from,
            year_to=args.year_to,
            out_folder=args.json_output,
            pdf_out_folder=args.pdf_output,
            limit=args.max_papers,
            journal_slugs=args.journal_slugs,
        )

if __name__ == "__main__":
    asyncio.run(main())
