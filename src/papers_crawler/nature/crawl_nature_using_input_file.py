import asyncio
import json
import logging
import os
import re
import traceback
import zipfile
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

try:
    from ..cell.crawl_cell_pdf_async import CLIProgressTracker
except ImportError:
    from src.papers_crawler.cell.crawl_cell_pdf_async import CLIProgressTracker

from .crawl_nature_async import extract_fulltext_nature_as_json, download_pdf_nature, save_json_to_file

logger = logging.getLogger(__name__)

async def crawl_nature_from_file_async(
    input_file: str,
    out_folder: str = "papers_nature",
    headless: bool = True,
    pdf_out_folder: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Crawl Nature.com articles from a CSV, Excel, or JSON Lines file.
    
    Reads a list of URLs from the `url` column/field of the provided file.
    Follows DOIs or direct Nature article URLs to extract JSON and PDF context.
    
    Args:
        input_file: Path to the input CSV, Excel, or JSONL file.
        out_folder: Folder to save extracted JSON metadata.
        headless: Run browser in headless mode.
        pdf_out_folder: Folder to save extracted PDFs.
        
    Returns:
        Tuple[List[str], List[str]]: (saved_file_paths, open_access_article_names)
    """
    os.makedirs(out_folder, exist_ok=True)
    if pdf_out_folder:
        os.makedirs(pdf_out_folder, exist_ok=True)

    urls = []
    
    # Read the input file
    items = []
    try:
        if input_file.endswith('.csv'):
            df = pd.read_csv(input_file)
            if 'url' in df.columns:
                for _, row in df.iterrows():
                    if pd.notna(row['url']):
                        item = {'url': row['url']}
                        if 'title' in df.columns and pd.notna(row['title']):
                            item['title'] = str(row['title'])
                        items.append(item)
        elif input_file.endswith('.xlsx') or input_file.endswith('.xls'):
            df = pd.read_excel(input_file)
            if 'url' in df.columns:
                for _, row in df.iterrows():
                    if pd.notna(row['url']):
                        item = {'url': row['url']}
                        if 'title' in df.columns and pd.notna(row['title']):
                            item['title'] = str(row['title'])
                        items.append(item)
        elif input_file.endswith('.jsonl') or input_file.endswith('.json'):
            with open(input_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        if 'url' in record and record['url']:
                            item = {'url': record['url']}
                            if 'title' in record and record['title']:
                                item['title'] = str(record['title'])
                            items.append(item)
                    except json.JSONDecodeError:
                        continue
        else:
            print(f"Unsupported file format: {input_file}. Supported formats: .csv, .xlsx, .jsonl")
            return [], []
    except Exception as e:
        print(f"Error reading input file {input_file}: {e}")
        return [], []

    if not items:
        print("No URLs found in the input file. Make sure there is a 'url' column or field.")
        return [], []

        
    stealth = Stealth(navigator_languages_override=("en-US", "en"), init_scripts_only=True)
    cli_progress = CLIProgressTracker(use_tqdm=True)
    
    saved_files = []
    open_access_articles = []
    article_metadata = []
    
    print(f"Nature.com crawler (from file) initialized")
    print(f"Input file: {input_file} ({len(items)} URLs found)")
    print(f"Output folder: {out_folder}")
    if pdf_out_folder:
        print(f"PDF Output folder: {pdf_out_folder}")

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=headless)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
        )
        await stealth.apply_stealth_async(context)
        page = await context.new_page()

        for item in items:
            original_url = str(item['url']).strip()
            provided_title = item.get('title')
            print(f"\nProcessing URL: {original_url}", flush=True)

            try:
                # Navigate to URL, Playwright should handle DOI redirect to nature.com
                await page.goto(original_url, timeout=30000)
                await page.wait_for_timeout(2000)
                
                # After redirection, get the current URL
                final_url = page.url
                
                # Check if it actually went to nature.com/articles/
                if "/articles/" not in final_url:
                    print(f"Skipping {original_url}: Final URL doesn't seem to be a Nature article page ({final_url})", flush=True)
                    continue
                
                # Extract Article ID from final URL (e.g. s41586-025-09018-y)
                match = re.search(r'/articles/([^/?#]+)', final_url)
                if not match:
                    print(f"Skipping {original_url}: Could not extract article ID from {final_url}", flush=True)
                    continue
                    
                article_id = match.group(1)
                print(f"Article ID: {article_id}")

                # Accept cookies if any
                try:
                    cookie_selectors = [
                        'button:has-text("Accept")', 'button:has-text("Accept all")',
                        'button:has-text("I Accept")', 'button:has-text("Agree")',
                        'button[id*="accept"]', 'button[class*="accept"]',
                    ]
                    for selector in cookie_selectors:
                        button = page.locator(selector).first
                        if await button.is_visible(timeout=1000):
                            await button.click()
                            await page.wait_for_timeout(500)
                            break
                except Exception:
                    pass

                print(f"Extracting full-text from: {final_url}", flush=True)
                json_data = await extract_fulltext_nature_as_json(page, final_url)
                
                if not json_data:
                    logger.warning(f"Failed to extract content from {final_url}")
                    continue

                # Get title and date
                article_title = json_data.get("title")
                if not article_title:
                    article_title = provided_title if provided_title else f"Article_{article_id}"
                
                article_date = json_data.get("publication_date", "")
                
                # Ensure title attribute is explicitly kept/added
                json_data["title"] = article_title
                json_data["open_access"] = True
                json_data["year"] = int(article_date[:4]) if article_date else datetime.now().year
                json_data["date"] = article_date
                
                # Save JSON file by article ID
                json_filename = f"{article_id}.json"
                json_path = os.path.join(out_folder, json_filename)
                
                if await save_json_to_file(json_data, json_path):
                    saved_files.append(json_path)
                    open_access_articles.append(article_title)
                    article_metadata.append((json_path, article_title, article_date, article_id))
                    print(f"Saved JSON: {json_filename}", flush=True)

                # Download PDF if requested
                if pdf_out_folder:
                    pdf_path = await download_pdf_nature(final_url, pdf_out_folder)
                    # For PDF, the function `download_pdf_nature` might save it as ID or Title.
                    # We want to ensure it's saved as ID. Let's rename the file if it's not ID.
                    if pdf_path and os.path.exists(pdf_path):
                        expected_pdf_path = os.path.join(pdf_out_folder, f"{article_id}.pdf")
                        if os.path.abspath(pdf_path) != os.path.abspath(expected_pdf_path):
                            # Replace if already exists?
                            if os.path.exists(expected_pdf_path):
                                os.remove(expected_pdf_path)
                            os.rename(pdf_path, expected_pdf_path)
                            print(f"Renamed PDF to: {article_id}.pdf", flush=True)
                        else:
                            print(f"Saved PDF: {article_id}.pdf", flush=True)
                    else:
                        logger.warning(f"PDF download failed for: {article_title[:60]}")

            except Exception as e:
                logger.error(f"Error processing URL {original_url}: {e}")
                logger.debug(traceback.format_exc())
            
            # Rate limiting
            await asyncio.sleep(1)

        await browser.close()
        
    cli_progress.close()
    
    print(f"\nExtracted {len(saved_files)} JSON files to {out_folder}")
    
    # Create CSV summary and ZIP archive
    if saved_files:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # CSV summary
        csv_filename = f"extraction_summary_inputfile_{timestamp}.csv"
        csv_path = os.path.join(out_folder, csv_filename)

        print(f"\nCreating extraction summary CSV: {csv_filename}")
        try:
            import csv
            with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['Number', 'Article ID', 'Article Name', 'Publish Date', 'File Path', 'File Size (KB)'])

                for idx, (file_path, article_name, publish_date, article_id) in enumerate(article_metadata, 1):
                    file_size_kb = os.path.getsize(file_path) / 1024 if os.path.exists(file_path) else 0
                    writer.writerow([idx, article_id, article_name, publish_date, file_path, f"{file_size_kb:.2f}"])
        except Exception as e:
            logger.error(f"Failed to create CSV summary: {e}")

        # ZIP archive
        zip_filename = f"nature_inputfile_json_{timestamp}.zip"
        zip_path = os.path.join(out_folder, zip_filename)

        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in saved_files:
                    arcname = os.path.relpath(file_path, out_folder)
                    zipf.write(file_path, arcname)
        except Exception as e:
            logger.error(f"Failed to create ZIP archive: {e}")

    return saved_files, open_access_articles
