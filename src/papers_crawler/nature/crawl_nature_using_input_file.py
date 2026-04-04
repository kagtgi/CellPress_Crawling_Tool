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

try:
    from .utils.common import save_json_to_file
    from .utils.pdf import download_pdf_nature
    from .utils.text import extract_fulltext_nature_as_json
except ImportError:
    from src.papers_crawler.nature.utils.common import save_json_to_file
    from src.papers_crawler.nature.utils.pdf import download_pdf_nature
    from src.papers_crawler.nature.utils.text import extract_fulltext_nature_as_json

logger = logging.getLogger(__name__)

def yield_nature_items_from_file(input_file: str, batch_size: int):
    ext = os.path.splitext(input_file)[1].lower()
    try:
        if ext == '.csv':
            for chunk in pd.read_csv(input_file, chunksize=batch_size):
                items_chunk = []
                if 'url' in chunk.columns:
                    for _, row in chunk.iterrows():
                        if pd.notna(row['url']):
                            item = {'url': row['url']}
                            if 'title' in chunk.columns and pd.notna(row['title']):
                                item['title'] = str(row['title'])
                            items_chunk.append(item)
                yield items_chunk

        elif ext in ['.xls', '.xlsx']:
            df = pd.read_excel(input_file)
            if 'url' in df.columns:
                for start_idx in range(0, len(df), batch_size):
                    end_idx = min(start_idx + batch_size, len(df))
                    chunk = df.iloc[start_idx:end_idx]
                    items_chunk = []
                    for _, row in chunk.iterrows():
                        if pd.notna(row['url']):
                            item = {'url': row['url']}
                            if 'title' in chunk.columns and pd.notna(row['title']):
                                item['title'] = str(row['title'])
                            items_chunk.append(item)
                    yield items_chunk

        elif ext in ['.jsonl', '.json']:
            with open(input_file, 'r', encoding='utf-8') as f:
                items_chunk = []
                for line in f:
                    if not line.strip(): continue
                    try:
                        record = json.loads(line)
                        if 'url' in record and record['url']:
                            item = {'url': record['url']}
                            if 'title' in record and record['title']:
                                item['title'] = str(record['title'])
                            items_chunk.append(item)
                            if len(items_chunk) >= batch_size:
                                yield items_chunk
                                items_chunk = []
                    except json.JSONDecodeError:
                        continue
                if items_chunk:
                    yield items_chunk
        else:
            print(f"Unsupported file format: {input_file}. Supported formats: .csv, .xlsx, .jsonl")
    except Exception as e:
        print(f"Error reading input file {input_file}: {e}")

async def crawl_nature_from_file_async(
    input_file: str,
    out_folder: str = "papers_nature",
    headless: bool = True,
    pdf_out_folder: Optional[str] = None,
    batch_size: int = 100,
) -> Tuple[List[str], List[str]]:
    """Crawl Nature.com articles from a CSV, Excel, or JSON Lines file in batches to preserve memory.
    """
    os.makedirs(out_folder, exist_ok=True)
    if pdf_out_folder:
        os.makedirs(pdf_out_folder, exist_ok=True)
        
    stealth = Stealth(navigator_languages_override=("en-US", "en"), init_scripts_only=True)
    cli_progress = CLIProgressTracker(use_tqdm=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_filename = f"extraction_summary_inputfile_{timestamp}.csv"
    csv_path = os.path.join(out_folder, csv_filename)
    zip_filename = f"nature_inputfile_json_{timestamp}.zip"
    zip_path = os.path.join(out_folder, zip_filename)

    total_saved_files = []
    total_open_access = []

    print(f"Nature.com crawler (from file, batch size {batch_size}) initialized")
    print(f"Output folder: {out_folder}")

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=headless)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
        )
        await stealth.apply_stealth_async(context)
        page = await context.new_page()

        total_processed = 0

        for batch_items in yield_nature_items_from_file(input_file, batch_size):
            if not batch_items:
                continue

            batch_saved_files = []
            batch_open_access_articles = []
            batch_article_metadata = []

            for item in batch_items:
                original_url = str(item['url']).strip()
                provided_title = item.get('title')
                print(f"\nProcessing URL: {original_url}", flush=True)

                try:
                    await page.goto(original_url, timeout=30000)
                    await page.wait_for_timeout(2000)
                    final_url = page.url
                    if "/articles/" not in final_url:
                        print(f"Skipping: Final URL doesn't seem to be a Nature article page ({final_url})", flush=True)
                        continue
                    
                    match = re.search(r'/articles/([^/?#]+)', final_url)
                    if not match:
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

                    json_data = await extract_fulltext_nature_as_json(page, final_url)
                    if not json_data:
                        logger.warning(f"Failed to extract content from {final_url}")
                        continue

                    article_title = json_data.get("title")
                    if not article_title:
                        article_title = provided_title if provided_title else f"Article_{article_id}"
                    
                    article_date = json_data.get("publication_date", "")
                    json_data["title"] = article_title
                    json_data["open_access"] = True
                    json_data["year"] = int(article_date[:4]) if article_date else datetime.now().year
                    json_data["date"] = article_date
                    
                    json_filename = f"{article_id}.json"
                    json_path = os.path.join(out_folder, json_filename)
                    
                    if await save_json_to_file(json_data, json_path):
                        batch_saved_files.append(json_path)
                        batch_open_access_articles.append(article_title)
                        batch_article_metadata.append((json_path, article_title, article_date, article_id))
                        print(f"Saved JSON: {json_filename}", flush=True)

                    if pdf_out_folder:
                        pdf_path = await download_pdf_nature(final_url, pdf_out_folder)
                        if pdf_path and os.path.exists(pdf_path):
                            expected_pdf_path = os.path.join(pdf_out_folder, f"{article_id}.pdf")
                            if os.path.abspath(pdf_path) != os.path.abspath(expected_pdf_path):
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
                
                await asyncio.sleep(1)

            # --- End of Batch: Flush to files ---
            if batch_saved_files:
                import csv
                file_exists = os.path.exists(csv_path) and os.path.getsize(csv_path) > 0
                with open(csv_path, 'a', newline='', encoding='utf-8') as csvfile:
                    writer = csv.writer(csvfile)
                    if not file_exists:
                        writer.writerow(['Number', 'Article ID', 'Article Name', 'Publish Date', 'File Path', 'File Size (KB)'])
                    
                    for idx, (file_path, article_name, publish_date, article_id) in enumerate(batch_article_metadata, 1):
                        file_size_kb = os.path.getsize(file_path) / 1024 if os.path.exists(file_path) else 0
                        writer.writerow([idx + total_processed, article_id, article_name, publish_date, file_path, f"{file_size_kb:.2f}"])
                
                with zipfile.ZipFile(zip_path, 'a', zipfile.ZIP_DEFLATED) as zipf:
                    for file_path in batch_saved_files:
                        arcname = os.path.relpath(file_path, out_folder)
                        zipf.write(file_path, arcname)

                print(f"Batch completed: flushed {len(batch_saved_files)} records to CSV and ZIP.", flush=True)
                total_processed += len(batch_saved_files)
                total_saved_files.extend(batch_saved_files)
                total_open_access.extend(batch_open_access_articles)

        await browser.close()
        
    cli_progress.close()
    print(f"\nExtracted {len(total_saved_files)} JSON files to {out_folder}")
    return total_saved_files, total_open_access
