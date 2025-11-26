"""Full-text extraction module for Nature.com articles.

This module provides functions to extract plain text content from Nature.com
article HTML pages, including title, authors, abstract, main text, figures,
and references.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import sys
import time
import traceback
import zipfile
from collections import deque
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin
from datetime import datetime

from bs4 import BeautifulSoup, Tag, NavigableString
from playwright.async_api import async_playwright, Page

from playwright_stealth import Stealth

# Import CLIProgressTracker from crawler_async
try:
    from .crawler_async import CLIProgressTracker
except ImportError:
    # Fallback if relative import fails
    from crawler_async import CLIProgressTracker

logger = logging.getLogger(__name__)


async def discover_journals_nature_async(force_refresh: bool = False) -> List[Tuple[str, str]]:
    """Async discover journals from Nature.com's site index page.

    Parses the journals A-Z page at https://www.nature.com/siteindex to extract
    journal slugs and display names.

    Args:
        force_refresh: If True, bypass cache and fetch fresh data

    Returns:
        List of (slug, display_name) tuples. Caches results in .cache/papers_crawler/journals_nature.json
    """
    cache_dir = os.path.join(os.getcwd(), ".cache", "papers_crawler")
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, "journals_nature.json")
    
    if not force_refresh and os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
                print(f"📚 Loaded {len(cached_data)} Nature journals from cache")
                return [(j['slug'], j['name']) for j in cached_data]
        except Exception:
            pass

    results: List[Tuple[str, str]] = []
    
    print("🌐 Fetching journals from Nature.com site index...")
    
    # Initialize stealth mode
    stealth = Stealth(
        navigator_languages_override=("en-US", "en"),
        init_scripts_only=True
    )
    
    try:
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
            )
            await stealth.apply_stealth_async(context)
            
            page = await context.new_page()
            
            # Navigate to Nature.com site index
            await page.goto("https://www.nature.com/siteindex", timeout=30000)
            await page.wait_for_timeout(3000)
            
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            
            # Find all journal sections (journals-A, journals-B, etc.)
            journal_sections = soup.find_all("div", id=re.compile(r"^journals-[A-Z]$"))
            
            for section in journal_sections:
                # Find all journal links in this section
                links = section.find_all("a", href=True)
                for link in links:
                    href = link.get("href", "")
                    name = link.get_text(strip=True)
                    
                    # Extract slug from URL: /slug/ or https://www.nature.com/slug/
                    # Example: /aps/ -> aps
                    # Example: /cr/ -> cr
                    # Example: https://www.nature.com/nature/ -> nature
                    match = re.search(r"/([a-z0-9-]+)/?$", href)
                    if match and name:
                        slug = match.group(1)
                        results.append((slug, name))
            
            await browser.close()
            
            print(f"✅ Found {len(results)} Nature journals")
            
            # Cache the results
            try:
                cache_data = [{"slug": slug, "name": name} for slug, name in results]
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, indent=2, ensure_ascii=False)
                print(f"💾 Cached Nature journals to: {cache_file}")
            except Exception as e:
                logger.warning(f"Failed to cache journals: {e}")
            
            return results
                
    except Exception as e:
        print(f"❌ Failed to discover journals from Nature.com: {e}")
        raise Exception(f"Could not load journals from Nature.com. Error: {str(e)}. Please check your internet connection and try again.")


async def extract_fulltext_nature_as_json(page: Page, fulltext_url: str) -> Optional[Dict]:
    """Navigate to Nature article page and extract all text content as JSON.
    
    Extracts content from Nature.com articles including:
    - Article header (title, authors, publication date)
    - Abstract
    - All article sections (Introduction, Methods, Results, Discussion, etc.)
    - Figure captions
    - References
    
    Args:
        page: Playwright page object for navigation
        fulltext_url: URL of the article page
        
    Returns:
        Dict: JSON structure with sections as keys and content as values, or None if extraction fails
    """
    try:
        logger.info(f"📖 Navigating to Nature article: {fulltext_url}")
        await page.goto(fulltext_url, timeout=30000)
        await page.wait_for_timeout(2000)
        
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Remove unwanted UI elements
        for unwanted in soup.find_all(['script', 'style', 'nav', 'button', 'aside', 'header', 'footer', 'iframe']):
            unwanted.decompose()
        
        json_data = {
            "url": fulltext_url,
            "extracted_at": datetime.now().isoformat()
        }
        
        # Extract title
        title_elem = soup.find("h1", {"class": "c-article-title"})
        if title_elem:
            json_data["title"] = title_elem.get_text(strip=True)
        
        # Extract authors
        authors_list = soup.find("ul", {"class": "c-article-author-list"})
        if authors_list:
            authors = []
            for author_item in authors_list.find_all("li", {"class": "c-article-author-list__item"}):
                author_text = author_item.get_text(strip=True)
                if author_text and not any(skip in author_text.lower() for skip in ["view all", "show more", "show less"]):
                    authors.append(author_text)
            if authors:
                json_data["authors"] = ", ".join(authors)
        
        # Extract publication date
        date_meta = soup.find("time", {"itemprop": "datePublished"})
        if date_meta and date_meta.get("datetime"):
            json_data["publication_date"] = date_meta.get("datetime")
        
        # Extract DOI
        doi_meta = soup.find("meta", {"name": "citation_doi"})
        if doi_meta:
            json_data["doi"] = doi_meta.get("content", "")
        
        # Extract Abstract
        abstract_section = soup.find("section", {"aria-labelledby": "Abs1"})
        if abstract_section:
            abstract_content = abstract_section.find("div", {"class": "c-article-section__content"})
            if abstract_content:
                abstract_text = []
                for p in abstract_content.find_all("p"):
                    text = p.get_text(" ", strip=True)
                    if text:
                        abstract_text.append(text)
                if abstract_text:
                    json_data["Abstract"] = "\n\n".join(abstract_text)
        
        # Extract all main article sections (Introduction, Methods, Results, Discussion, etc.)
        # Nature.com uses <section data-title="..."> for main sections
        main_sections = soup.find_all("section", {"data-title": True})
        
        for section in main_sections:
            section_title = section.get("data-title")
            if not section_title or section_title == "Abstract":
                continue
            
            # Find the section content
            section_div = section.find("div", {"class": "c-article-section__content"})
            if not section_div:
                continue
            
            section_text = []
            
            # Extract all text content from the section
            for elem in section_div.find_all(['p', 'h3', 'h4', 'h5', 'h6', 'li']):
                text = elem.get_text(" ", strip=True)
                
                # Skip unwanted phrases
                if any(skip in text.lower() for skip in [
                    "full size image", "view figure", "download", "cite this", 
                    "search for articles", "crossref", "pubmed", "google scholar"
                ]):
                    continue
                
                if text:
                    # Add heading markers for subsections
                    if elem.name in ['h3', 'h4', 'h5', 'h6']:
                        section_text.append(f"\n## {text}\n")
                    else:
                        section_text.append(text)
            
            if section_text:
                json_data[section_title] = "\n\n".join(section_text)
        
        # Extract Figures with captions and descriptions
        # Look for figures within article sections (more reliable than just <figure> tags)
        figure_sections = soup.find_all("div", {"class": "c-article-section__figure"})
        if figure_sections:
            figures_data = []
            for idx, fig_section in enumerate(figure_sections, 1):
                # Get the figure title from figcaption
                figcaption = fig_section.find("figcaption")
                caption_title = ""
                if figcaption:
                    caption_title = figcaption.get_text(" ", strip=True)
                
                # Get the detailed description
                fig_description = fig_section.find("div", {"class": "c-article-section__figure-description"})
                description_text = ""
                if fig_description:
                    description_text = fig_description.get_text(" ", strip=True)
                    # Remove "Full size image" and similar UI text
                    if "full size image" in description_text.lower():
                        # Split and take only the actual description part
                        description_text = description_text.split("Full size image")[0].strip()
                
                # Combine title and description
                if caption_title or description_text:
                    full_caption = caption_title
                    if description_text and description_text != caption_title:
                        full_caption = f"{caption_title}\n{description_text}"
                    
                    if full_caption:
                        figures_data.append(f"{idx}. {full_caption}")
            
            if figures_data:
                json_data["Figures"] = "\n\n".join(figures_data)
        
        # Extract References
        references_section = soup.find("section", {"aria-labelledby": "Bib1"})
        if references_section:
            references = []
            ref_items = references_section.find_all("li", {"class": "c-article-references__item"})
            
            for idx, ref_item in enumerate(ref_items, 1):
                ref_text_elem = ref_item.find("p", {"class": "c-article-references__text"})
                if ref_text_elem:
                    ref_text = ref_text_elem.get_text(" ", strip=True)
                    if ref_text:
                        references.append(f"{idx}. {ref_text}")
            
            if references:
                json_data["References"] = "\n\n".join(references)
        
        if len(json_data) > 2:  # More than just url and extracted_at
            return json_data
        else:
            logger.warning("No substantial content extracted from Nature article")
            return None
            
    except Exception as e:
        logger.error(f"❌ Failed to extract Nature article: {e}")
        logger.debug(traceback.format_exc())
        return None


async def save_json_to_file(json_content: Dict, file_path: str) -> bool:
    """Save extracted content to a .json file.
    
    Args:
        json_content: The JSON content to save (dict with sections as keys)
        file_path: Absolute path where the file should be saved
        
    Returns:
        bool: True if saved successfully, False otherwise
    """
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(json_content, f, indent=2, ensure_ascii=False)
        logger.info(f"💾 Saved JSON to: {file_path}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to save JSON file: {e}")
        return False


async def crawl_text_nature_async(
    keywords: str = "",
    year_from: int = 2020,
    year_to: int = 2024,
    out_folder: str = "papers_nature",
    headless: bool = True,
    limit: Optional[int] = None,
    journal_slugs: Optional[List[str]] = None,
    progress_callback=None,
    total_progress_callback=None,
    crawl_archives: bool = False,
) -> Tuple[List[str], List[str]]:
    """Async crawl Nature.com for articles and extract full-text HTML as plain text.
    
    Crawls Nature.com journals' research articles pages and extracts open access
    articles within the specified year range.
    
    Args:
        keywords: Search keywords (currently unused, reserved for future)
        year_from: Start year for article filtering
        year_to: End year for article filtering
        out_folder: Output folder for JSON files
        headless: Run browser in headless mode
        limit: Maximum number of articles to extract per journal
        journal_slugs: List of journal slugs to crawl
        progress_callback: Called with (filename, filepath) after each file is saved
        total_progress_callback: Called with (current, total, status, file_size, speed, stage)
        crawl_archives: If True, also crawl archive pages for older articles
    
    Returns:
        Tuple[List[str], List[str]]: (saved_file_paths, open_access_article_names)
    """
    
    os.makedirs(out_folder, exist_ok=True)
    saved_files = []
    open_access_articles = []
    article_metadata = []
    total_articles_found = 0
    
    # Initialize CLI progress tracker (only if no callbacks provided)
    cli_progress = None
    if not progress_callback and not total_progress_callback:
        cli_progress = CLIProgressTracker(use_tqdm=True)

    # Initialize stealth mode for playwright
    stealth = Stealth(
        navigator_languages_override=("en-US", "en"),
        init_scripts_only=True
    )

    async def handle_cookie_consent(page):
        """Try to accept cookie consent if it appears."""
        try:
            cookie_selectors = [
                'button:has-text("Accept")',
                'button:has-text("Accept all")',
                'button:has-text("I Accept")',
                'button:has-text("Agree")',
                'button[id*="accept"]',
                'button[class*="accept"]',
            ]
            
            for selector in cookie_selectors:
                try:
                    button = page.locator(selector).first
                    if await button.is_visible(timeout=2000):
                        await button.click()
                        await page.wait_for_timeout(1000)
                        logger.debug(f"Clicked cookie consent: {selector}")
                        return True
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"No cookie consent found: {e}")
        
        return False

    found_count = 0

    print(f"🔍 Nature.com crawler initialized")
    print(f"📂 Output folder: {out_folder}")
    print(f"📅 Year range: {year_from} - {year_to}")
    
    if journal_slugs:
        print(f"📚 Target journals: {', '.join(journal_slugs)}")
        
        if total_progress_callback:
            total_progress_callback(0, 0, "Scanning Nature journals for open access articles...", 0, 0, "scanning")
        elif cli_progress:
            print(f"🔍 Scanning {len(journal_slugs)} Nature journal(s) for open access articles...", flush=True)
        
        async with async_playwright() as p:
            for slug in journal_slugs:
                print(f"\n📚 Crawling journal: {slug}", flush=True)
                journal_folder = os.path.join(out_folder, slug)
                os.makedirs(journal_folder, exist_ok=True)
                
                # Launch browser for this journal
                print(f"🚀 Launching Firefox for journal: {slug}...", flush=True)
                browser = await p.firefox.launch(headless=headless)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
                )
                await stealth.apply_stealth_async(context)
                page = await context.new_page()
                
                print(f"✅ Firefox browser ready for {slug}", flush=True)
                print(f"📂 Journal folder: {journal_folder}", flush=True)
                
                # Navigate to research articles page with pagination support
                # Loop through each year for efficient server-side filtering
                base_articles_url = f"https://www.nature.com/{slug}/research-articles"
                print(f"🔎 Crawling journal: {slug} at {base_articles_url}", flush=True)
                
                oa_count = 0
                
                try:
                    # Loop through each year in the range (newest to oldest)
                    for year in range(year_to, year_from - 1, -1):
                        if limit and found_count >= limit:
                            print(f"✋ Reached limit of {limit} articles", flush=True)
                            break
                        
                        print(f"\n📅 Crawling year: {year}", flush=True)
                        page_num = 1
                        
                        # Pagination loop for current year
                        while True:
                            # Check if we've reached the limit
                            if limit and found_count >= limit:
                                print(f"✋ Reached limit of {limit} articles", flush=True)
                                break
                            
                            # Build URL for current page with year filter
                            if page_num == 1:
                                articles_url = f"{base_articles_url}?year={year}"
                            else:
                                articles_url = f"{base_articles_url}?searchType=journalSearch&sort=PubDate&year={year}&page={page_num}"
                            
                            print(f"📖 Loading page {page_num}: {articles_url}", flush=True)
                            
                            await page.goto(articles_url, timeout=30000)
                            await page.wait_for_timeout(3000)
                            
                            if page_num == 1:
                                await handle_cookie_consent(page)
                            
                            # Get page HTML
                            html = await page.content()
                            soup = BeautifulSoup(html, "html.parser")
                            
                            # Find all article cards
                            articles = soup.find_all("article", {"class": "c-card", "itemtype": "http://schema.org/ScholarlyArticle"})
                            
                            if not articles:
                                print(f"📭 No more articles found on page {page_num}", flush=True)
                                break
                            
                            print(f"📄 Found {len(articles)} articles on page {page_num}", flush=True)
                            
                            page_oa_found = 0  # Track OA articles found on this specific page
                            for art in articles:
                                if limit and found_count >= limit:
                                    print(f"✋ Reached limit of {limit} articles", flush=True)
                                    break
                                
                                # Check if article is open access
                                meta_section = art.find("div", {"class": "c-meta"})
                                if not meta_section:
                                    continue
                                
                                # Look for "Open Access" label
                                oa_label = meta_section.find("span", {"data-test": "open-access"})
                                if not oa_label:
                                    continue
                                
                                # Extract article date
                                date_elem = meta_section.find("time", {"datetime": True})
                                if not date_elem:
                                    continue
                                
                                article_date = date_elem.get("datetime", "")
                                # Extract year from ISO date (YYYY-MM-DD)
                                try:
                                    article_year = int(article_date[:4])
                                except:
                                    logger.debug(f"Could not parse year from date: {article_date}")
                                    continue
                                
                                # Filter by year
                                if not (year_from <= article_year <= year_to):
                                    logger.debug(f"Skipping article from {article_year} (outside range {year_from}-{year_to})")
                                    continue
                                
                                # Extract article URL
                                article_link = art.find("a", {"class": "c-card__link"})
                                if not article_link:
                                    continue
                                
                                article_href = article_link.get("href", "")
                                if not article_href:
                                    continue
                                
                                # Build full URL
                                full_url = urljoin("https://www.nature.com", article_href)
                                
                                # Extract article title
                                title_elem = art.find("h3", {"class": "c-card__title"})
                                article_title = title_elem.get_text(strip=True) if title_elem else f"Article_{found_count + 1}"
                                
                                oa_count += 1
                                page_oa_found += 1
                                print(f"📄 Found open-access article ({article_year}): {article_title[:60]}...", flush=True)
                                
                                try:
                                    # Extract full-text content from the article page
                                    print(f"📖 Extracting full-text from: {full_url}", flush=True)
                                    json_data = await extract_fulltext_nature_as_json(page, full_url)
                                    
                                    if not json_data:
                                        logger.warning(f"Failed to extract content from {full_url}")
                                        continue
                                    
                                    # Add metadata
                                    json_data["journal"] = slug
                                    json_data["open_access"] = True
                                    json_data["year"] = article_year
                                    json_data["date"] = article_date
                                    
                                    # Save JSON file
                                    safe_title = re.sub(r'[<>:"/\\|?*]', '_', article_title[:100])
                                    json_filename = f"{safe_title}_{article_year}.json"
                                    json_path = os.path.join(journal_folder, json_filename)
                                    
                                    if await save_json_to_file(json_data, json_path):
                                        saved_files.append(json_path)
                                        open_access_articles.append(article_title)
                                        article_metadata.append((json_path, article_title, article_date))
                                        found_count += 1
                                        
                                        if progress_callback:
                                            progress_callback(json_filename, json_path)
                                        
                                        print(f"✅ Saved: {json_filename}", flush=True)
                                    else:
                                        logger.error(f"Failed to save JSON for: {article_title}")
                                
                                except Exception as e:
                                    logger.error(f"❌ Error processing article {article_title}: {e}")
                                    logger.debug(traceback.format_exc())
                                
                                # Brief delay between articles
                                await asyncio.sleep(1)
                            
                            # After processing all articles on this page
                            # Check if we should move to next page
                            if limit and found_count >= limit:
                                print(f"✋ Reached limit of {limit} articles", flush=True)
                                break
                            
                            # Just log if no OA articles found, but continue to next page
                            if page_oa_found == 0:
                                print(f"📭 No open access articles found on page {page_num} for year {year}, continuing to next page...", flush=True)
                            
                            # Move to next page
                            page_num += 1
                            print(f"➡️  Moving to page {page_num} for year {year}...", flush=True)
                    
                    print(f"📚 Found {oa_count} open access articles in {slug} (filtered by year {year_from}-{year_to})", flush=True)
                    
                except Exception as e:
                    logger.error(f"❌ Failed to crawl journal {slug}: {e}")
                    logger.debug(traceback.format_exc())
                
                finally:
                    print(f"🔒 Closing browser for journal: {slug}", flush=True)
                    await browser.close()
    
    if cli_progress:
        cli_progress.close()
    
    print(f"\n🎉 Extracted {found_count} JSON files to {out_folder}")
    
    # Create CSV summary
    if saved_files:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"extraction_summary_{timestamp}.csv"
        csv_path = os.path.join(out_folder, csv_filename)
        
        print(f"\n📄 Creating extraction summary CSV: {csv_filename}")
        
        try:
            import csv
            with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['Number', 'Journal', 'Article Name', 'Publish Date', 'File Path', 'File Size (KB)'])
                
                for idx, (file_path, article_name, publish_date) in enumerate(article_metadata, 1):
                    journal_name = os.path.basename(os.path.dirname(file_path))
                    file_size_kb = os.path.getsize(file_path) / 1024 if os.path.exists(file_path) else 0
                    writer.writerow([idx, journal_name, article_name, publish_date, file_path, f"{file_size_kb:.2f}"])
            
            logger.info(f"✅ CSV summary saved to: {csv_path}")
        except Exception as e:
            logger.error(f"❌ Failed to create CSV summary: {e}")
    
    # Create ZIP archive
    if saved_files:
        print(f"\n📦 Creating ZIP archive with all extracted JSON files...")
        
        zip_filename = f"all_nature_journals_json_{timestamp}.zip"
        zip_path = os.path.join(out_folder, zip_filename)
        
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in saved_files:
                    arcname = os.path.relpath(file_path, out_folder)
                    zipf.write(file_path, arcname)
            
            zip_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
            logger.info(f"✅ Created ZIP archive: {zip_filename} ({zip_size_mb:.1f} MB)")
        except Exception as e:
            logger.error(f"❌ Failed to create ZIP archive: {e}")
    
    return saved_files, open_access_articles
