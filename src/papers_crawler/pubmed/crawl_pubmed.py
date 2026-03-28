import argparse
import asyncio
import os
import traceback
from playwright.async_api import async_playwright
try:
    from .crawl_pubmed_async import crawl_pubmed_journals_async
    from .utils.common import read_pmc_ids_from_file, save_json_to_file
    from .utils.text import extract_fulltext_pubmed_as_json
    from .utils.pdf import download_pdf_pubmed
except ImportError:
    from src.papers_crawler.pubmed.crawl_pubmed_async import crawl_pubmed_journals_async
    from src.papers_crawler.pubmed.utils.common import read_pmc_ids_from_file, save_json_to_file
    from src.papers_crawler.pubmed.utils.text import extract_fulltext_pubmed_as_json
    from src.papers_crawler.pubmed.utils.pdf import download_pdf_pubmed

async def process_pmc_articles(pmc_ids, pdf_output=None, json_output=None):
    if not pdf_output and not json_output:
        print("No output directories specified. Use --pdf-output or --json-output.")
        return

    if not pmc_ids:
        print("No PMC IDs provided to process.")
        return

    print(f"Starting to process {len(pmc_ids)} open-access articles.")

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context(
            accept_downloads=True,
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0',
        )
        page = await context.new_page()

        for idx, pmc_id in enumerate(pmc_ids, 1):
            print(f"\n[{idx}/{len(pmc_ids)}] Processing {pmc_id}...")
            
            try:
                if json_output:
                    os.makedirs(json_output, exist_ok=True)
                    json_path = os.path.join(json_output, f"{pmc_id}.json")
                    if os.path.exists(json_path) and os.path.getsize(json_path) > 100:
                        print(f"JSON already exists: {json_path}")
                    else:
                        json_data = await extract_fulltext_pubmed_as_json(page, pmc_id)
                        if json_data:
                            await save_json_to_file(json_data, json_path)
                            print(f"Saved JSON: {json_path}")
                        else:
                            print(f"Failed to extract JSON for {pmc_id}")

                if pdf_output:
                    os.makedirs(pdf_output, exist_ok=True)
                    pdf_path = os.path.join(pdf_output, f"{pmc_id}.pdf")
                    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
                        print(f"PDF already exists: {pdf_path}")
                    else:
                        result = await download_pdf_pubmed(page, pmc_id, pdf_output)
                        if result:
                            print(f"Downloaded PDF: {result}")
                        else:
                            print(f"Failed to download PDF for {pmc_id}")
                            
            except Exception as e:
                print(f"Error processing {pmc_id}: {e}")
                traceback.print_exc()

            await asyncio.sleep(2)  # Polite delay

        await page.close()
        await context.close()
        await browser.close()
        
async def main():
    parser = argparse.ArgumentParser(description="Crawl PubMed papers metadata and fulltext (JSON/PDF)")
    parser.add_argument("--use-input-file", type=str, choices=['y', 'n'], default='n', help="Use input file to provide PMC IDs")
    parser.add_argument("--input-file", type=str, help="Path to input file (CSV, Excel, JSONL)")
    parser.add_argument("--pdf-output", type=str, help="Directory to save downloaded PDFs")
    parser.add_argument("--json-output", type=str, help="Directory to save extracted JSON files")
    
    # Fixed arguments for metadata search
    parser.add_argument("--year-from", type=int, default=2024, help="Start year")
    parser.add_argument("--year-to", type=int, default=2025, help="End year")
    parser.add_argument("--out-folder", type=str, default="./data/pubmed", help="CSV output directory for metadata summary")
    parser.add_argument("--chunk-size", type=int, default=6, help="Chunk size in months for splitting time ranges (default: 6)")
    parser.add_argument("--max-papers", type=int, default=None, help="Maximum number of papers per journal")
    parser.add_argument("--journals", nargs="+", help="Space-separated journal names")
    parser.add_argument("--keywords", type=str, default="", help="Additional search keywords")
    parser.add_argument("--api-key", type=str, default=None, help="NCBI API key for higher rate limits")
    
    args = parser.parse_args()
    
    pmc_ids = []
    
    if args.use_input_file == 'y':
        if not args.input_file:
            print("Error: --input-file is required when --use-input-file is 'y'")
            return
        print(f"Reading PMC IDs from {args.input_file}...")
        pmc_ids = read_pmc_ids_from_file(args.input_file)
        if not pmc_ids:
            print(f"No valid PMC IDs found in {args.input_file}")
            return
        print(f"Found {len(pmc_ids)} PMC IDs in the input file.")
        
        # If input file is used, process the PMIDs immediately to fetch PDF/JSON
        await process_pmc_articles(pmc_ids, args.pdf_output, args.json_output)
    else:
        if not args.journals:
            print("Error: Please provide at least one journal with --journals when --use-input-file is 'n'")
            return
            
        print("Fetching PMIDs and metadata using fixed arguments...")
        all_articles, oa_articles, pa_articles = await crawl_pubmed_journals_async(
            journals=args.journals,
            year_from=args.year_from,
            year_to=args.year_to,
            keywords=args.keywords,
            out_folder=args.out_folder,
            chunk_size_months=args.chunk_size,
            limit_per_journal=args.max_papers,
            api_key=args.api_key,
        )
        
        pmc_ids = [art['pmc_id'] for art in oa_articles if art.get('pmc_id')]
        print(f"Found {len(pa_articles)} public-access articles with PMC IDs from search.")
        print(f"Found {len(oa_articles)} open-access articles from search.")
        
        # Only process if pdf_output or json_output is specified
        if args.pdf_output or args.json_output:
            if pmc_ids:
                await process_pmc_articles(pmc_ids, args.pdf_output, args.json_output)
            else:
                print("No open-access articles or PMC IDs found to process full text.")

if __name__ == "__main__":
    asyncio.run(main())
