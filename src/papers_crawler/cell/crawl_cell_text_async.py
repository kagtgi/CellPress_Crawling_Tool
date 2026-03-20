"""Full-text extraction module for Cell.com articles.

This module provides functions to extract plain text content from Cell.com
article HTML pages, including title, authors, abstract, main text, figures,
and references.
"""
from __future__ import annotations

import os
import sys
import time
import logging
import csv
import zipfile
import asyncio
import re
import json
import traceback
from collections import deque
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin
from datetime import datetime

from bs4 import BeautifulSoup, Tag, NavigableString
from playwright.async_api import async_playwright, Page

from playwright_stealth import Stealth

# Import CLIProgressTracker from crawler_async

try:
    from .utils.common import CLIProgressTracker, save_json_to_file
    from .utils.text import extract_fulltext_as_json
except ImportError:
    from src.papers_crawler.cell.utils.common import CLIProgressTracker, save_json_to_file
    from src.papers_crawler.cell.utils.text import extract_fulltext_as_json

logger = logging.getLogger(__name__)



async def crawl_text_async(
    keywords: str = "",
    year_from: int = 2020,
    year_to: int = 2024,
    out_folder: str = "papers",
    headless: bool = True,
    limit: Optional[int] = None,
    journal_slugs: Optional[List[str]] = None,
    progress_callback=None,
    total_progress_callback=None,
    crawl_archives: bool = False,
) -> Tuple[List[str], List[str]]:
    """Async crawl Cell.com for articles and extract full-text HTML as plain text.
    
    This function works exactly like crawl_async but extracts text content from
    /fulltext/ pages instead of downloading PDFs.
    
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
        crawl_archives: If True, also crawl /issue pages for archived articles
    
    Returns:
        Tuple[List[str], List[str]]: (saved_file_paths, open_access_article_names)
    """
    
    os.makedirs(out_folder, exist_ok=True)
    saved_files = []
    open_access_articles = []
    article_metadata = []  # Store (file_path, article_title, publish_date)
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
                'button:has-text("Accept All")',
                'button:has-text("I Accept")',
                'button:has-text("I agree")',
                'button:has-text("Agree")',
                'button:has-text("OK")',
                'button[id*="accept"]',
                'button[class*="accept"]',
                'a:has-text("Accept")',
                '#onetrust-accept-btn-handler',
                '.optanon-alert-box-button-middle',
            ]
            
            for selector in cookie_selectors:
                try:
                    if await page.locator(selector).is_visible(timeout=2000):
                        await page.click(selector, timeout=3000)
                        await page.wait_for_timeout(1000)
                        return True
                except Exception:
                    continue
                    
        except Exception as e:
            logger.debug(f"No cookie consent found or already accepted: {e}")
        
        return False

    found_count = 0
    
    async def crawl_issue_page(page, issue_url: str, journal_folder: str, journal_download_count: int, is_open_archive: bool = False, issue_date: str = "Unknown"):
        """Crawl a specific issue page for articles and extract text."""
        nonlocal found_count, saved_files, open_access_articles, article_metadata
        
        print(f"Loading issue: {issue_url}", flush=True)
        print(f"Issue date (from list): {issue_date}", flush=True)
        await page.goto(issue_url, timeout=30000)
        await page.wait_for_timeout(2000)
        
        await handle_cookie_consent(page)
        
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        if issue_date == "Unknown":
            logger.warning(f" No date provided for issue, attempting to extract from page...")
            date_selectors = [
                ("div", {"class": "issue-item__title"}),
                ("span", {"class": "volume-issue"}),
                ("h1", {"class": "issue-item__title"}),
                ("div", {"class": "issue-item__detail"}),
                ("div", {"class": "u-cloak-me"}),
            ]
            
            for tag, attrs in date_selectors:
                elem = soup.find(tag, attrs)
                if elem:
                    text = elem.get_text(strip=True)
                    if text and text != "Unknown":
                        issue_date = text
                        print(f"Extracted date from page: {issue_date}", flush=True)
                        break
        
        articles = soup.select(".articleCitation")
        print(f"Found {len(articles)} articles in issue", flush=True)
        
        for art in articles:
            if limit and journal_download_count >= limit:
                logger.info(f"✋ Reached journal limit of {limit} extractions")
                return journal_download_count, True
            
            oa_label = art.find(class_="OALabel")
            if not is_open_archive and not oa_label:
                continue
            
            # Find Full-Text HTML link
            fulltext_link = None
            for link in art.find_all("a", href=True):
                if "Full-Text HTML" in link.get_text() or "/fulltext/" in link.get("href", ""):
                    fulltext_link = link.get("href", "")
                    break
            
            if not fulltext_link:
                continue
            
            # Make absolute URL
            if not fulltext_link.startswith("http"):
                fulltext_link = f"https://www.cell.com{fulltext_link}"
            
            title_elem = art.find(class_="toc__item__title")
            article_title = title_elem.get_text(strip=True) if title_elem else f"Article {found_count + 1}"
            publish_date = issue_date
            
            print(f"Found {'open-archive' if is_open_archive else 'open-access'} article: {article_title[:60]}...", flush=True)
            
            try:
                safe_title = "".join(c for c in article_title if c.isalnum() or c in (' ', '-', '_')).strip()
                safe_title = safe_title[:100]
                filename = f"{safe_title}.json"
                dest_path = os.path.join(journal_folder, filename)
                
                if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
                    logger.info(f"Skipping already extracted: {filename}")
                    continue
                
                if total_progress_callback:
                    total_progress_callback(found_count, found_count + 1, f"Extracting: {article_title[:50]}...", 0, 0, "starting")
                elif cli_progress:
                    cli_progress.update(found_count, found_count + 1, f"📝 {article_title[:30]}...", 0, 0, "starting", force=True)
                else:
                    logger.info(f"📝 Start extracting text: {article_title[:50]}...")
                
                extract_start_time = time.time()
                
                print(f"Navigating to full-text: {fulltext_link[:80]}...", flush=True)
                
                # Extract JSON from full-text page
                json_content = await extract_fulltext_as_json(page, fulltext_link)
                
                print(f"Extraction completed. Sections: {len(json_content) if json_content else 0}", flush=True)
                
                if json_content:
                    # Save to JSON file
                    success = await save_json_to_file(json_content, dest_path)
                    
                    extract_time = time.time() - extract_start_time
                    
                    if success and os.path.exists(dest_path):
                        file_size = os.path.getsize(dest_path)
                        file_size_kb = file_size / 1024
                        
                        if extract_time > 0:
                            speed_kbps = file_size_kb / extract_time
                        else:
                            speed_kbps = 0
                        
                        if cli_progress is None:
                            print(f"Extracted {file_size_kb:.1f} KB in {extract_time:.1f}s ({speed_kbps:.1f} KB/s)", flush=True)
                        
                        saved_files.append(dest_path)
                        open_access_articles.append(article_title)
                        article_metadata.append((dest_path, article_title, publish_date))
                        found_count += 1
                        journal_download_count += 1
                        
                        if progress_callback:
                            progress_callback(filename, dest_path)
                        
                        if total_progress_callback:
                            total_progress_callback(found_count, found_count, f"Saved: {article_title[:50]}...", file_size, speed_kbps, "completed")
                        elif cli_progress:
                            cli_progress.update(found_count, found_count, f" {article_title[:30]}...", file_size, speed_kbps, "completed")
                    else:
                        print(f"Failed to save JSON file: {dest_path}", flush=True)
                else:
                    print(f"Extracted JSON is empty or invalid", flush=True)
                    
            except Exception as e:
                print(f"Failed to extract text for '{article_title[:50]}': {e}", flush=True)
                print(traceback.format_exc(), flush=True)
            
            await asyncio.sleep(1)
        
        return journal_download_count, False

    if journal_slugs:
        if total_progress_callback:
            total_progress_callback(0, 0, "Scanning journals for open access articles...", 0, 0, "scanning")
        elif cli_progress:
            print(f"Scanning {len(journal_slugs)} journal(s) for open access articles...", flush=True)
        
        async with async_playwright() as p:
            for slug in journal_slugs:
                print(f"\n Launching Firefox for journal: {slug}...", flush=True)
                
                browser = await p.firefox.launch(headless=headless)
                
                context = await browser.new_context(
                    accept_downloads=False,  # Not downloading files
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0',
                    viewport={'width': 1920, 'height': 1080},
                    locale='en-US',
                    timezone_id='America/New_York',
                    permissions=['geolocation'],
                    geolocation={'longitude': -74.0060, 'latitude': 40.7128},
                    color_scheme='light',
                    extra_http_headers={
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                    }
                )
                
                print(f"Firefox browser ready for {slug}", flush=True)
                
                page = await context.new_page()
                
                await stealth.apply_stealth_async(page)
                
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)
                
                journal_folder = os.path.join(out_folder, slug.replace('/', '_'))
                os.makedirs(journal_folder, exist_ok=True)
                print(f"Journal folder: {journal_folder}")
                
                url = f"https://www.cell.com/{slug}/newarticles"
                print(f"🔎 Crawling journal: {slug} at {url}")
                
                if total_progress_callback:
                    total_progress_callback(found_count, total_articles_found, f"Loading journal: {slug}", 0, 0, "loading")
                
                await page.goto(url, timeout=30000)
                await page.wait_for_timeout(3000)
                
                await handle_cookie_consent(page)
                
                page_title = await page.title()
                
                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                articles = soup.select(".articleCitation")
                
                if not articles:
                    print(f"No articles found on {url}. Page title: {page_title}")
                    await page.close()
                    await context.close()
                    await browser.close()
                    continue
                
                oa_count = sum(1 for art in articles if art.find(class_="OALabel"))
                journal_download_count = 0
                journal_target = min(oa_count, limit) if limit else oa_count
                total_articles_found += journal_target
                print(f"Found {oa_count} open access articles in {slug} (will extract up to {journal_target})")
                
                if total_progress_callback:
                    total_progress_callback(found_count, total_articles_found, f"Found {total_articles_found} open access articles", 0, 0, "found")
                elif cli_progress:
                    if cli_progress.total == 0 and total_articles_found > 0:
                        cli_progress.start(total_articles_found)
                    else:
                        cli_progress.total = total_articles_found
                
                for art in articles:
                    if limit and journal_download_count >= limit:
                        print(f"✋ Reached limit of {limit} for journal {slug}", flush=True)
                        break
                    
                    year_tag = art.find(class_="toc__item__date")
                    year_text = year_tag.get_text() if year_tag else ""
                    try:
                        if "," in year_text:
                            year_str = year_text.split(",")[-1].strip()
                        else:
                            year_str = year_text.strip()
                        year = int(re.search(r'\d{4}', year_str).group()) if re.search(r'\d{4}', year_str) else 0
                    except Exception:
                        year = 0
                    
                    if not (year_from <= year <= year_to):
                        continue
                    
                    # Find Full-Text HTML link
                    fulltext_link = None
                    for link in art.find_all("a", href=True):
                        if "Full-Text HTML" in link.get_text() or "/fulltext/" in link.get("href", ""):
                            fulltext_link = link.get("href", "")
                            break
                    
                    if not fulltext_link:
                        continue
                    
                    oa_label = art.find(class_="OALabel")
                    if not oa_label:
                        continue
                    
                    # Make absolute URL
                    if not fulltext_link.startswith("http"):
                        fulltext_link = f"https://www.cell.com{fulltext_link}"
                    
                    title_elem = art.find(class_="toc__item__title")
                    article_title = title_elem.get_text(strip=True) if title_elem else f"Article {found_count + 1}"
                    publish_date = year_text.strip() if year_text else "Unknown"
                    
                    print(f"Found open-access article: {article_title[:60]}...")
                    
                    try:
                        safe_title = "".join(c for c in article_title if c.isalnum() or c in (' ', '-', '_')).strip()
                        safe_title = safe_title[:100]
                        filename = f"{safe_title}.json"
                        dest_path = os.path.join(journal_folder, filename)
                        
                        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
                            logger.info(f"Skipping already extracted: {filename}")
                            continue
                        
                        if total_progress_callback:
                            total_progress_callback(found_count, found_count + 1, f"Extracting: {article_title[:50]}...", 0, 0, "starting")
                        elif cli_progress:
                            cli_progress.update(found_count, found_count + 1, f"📝 {article_title[:30]}...", 0, 0, "starting", force=True)
                        else:
                            logger.info(f"📝 Start extracting text: {article_title[:50]}...")
                        
                        extract_start_time = time.time()
                        
                        print(f"Navigating to full-text: {fulltext_link[:80]}...", flush=True)
                        
                        json_content = await extract_fulltext_as_json(page, fulltext_link)
                        
                        if json_content:
                            # Save to JSON file
                            success = await save_json_to_file(json_content, dest_path)
                            
                            extract_time = time.time() - extract_start_time
                            
                            if success and os.path.exists(dest_path):
                                file_size = os.path.getsize(dest_path)
                                file_size_kb = file_size / 1024
                                
                                if extract_time > 0:
                                    speed_kbps = file_size_kb / extract_time
                                else:
                                    speed_kbps = 0
                                
                                if cli_progress is None:
                                    print(f"Extracted {file_size_kb:.1f} KB in {extract_time:.1f}s ({speed_kbps:.1f} KB/s)", flush=True)
                                
                                saved_files.append(dest_path)
                                open_access_articles.append(article_title)
                                article_metadata.append((dest_path, article_title, publish_date))
                                found_count += 1
                                journal_download_count += 1
                                
                                if progress_callback:
                                    progress_callback(filename, dest_path)
                                
                                if total_progress_callback:
                                    total_progress_callback(found_count, found_count, f"Saved: {article_title[:50]}...", file_size, speed_kbps, "completed")
                                elif cli_progress:
                                    cli_progress.update(found_count, found_count, f" {article_title[:30]}...", file_size, speed_kbps, "completed")
                            else:
                                logger.error(f" Failed to save text file: {dest_path}")
                        else:
                            logger.error(f" Extracted text is too small or empty")
                            
                    except Exception as e:
                        logger.error(f" Failed to extract text for '{article_title[:50]}': {e}")
                        logger.debug(traceback.format_exc())
                    
                    await asyncio.sleep(1)
                
                # Crawl issue archives if requested —
                # Also fall back to crawling issue pages when the /newarticles run
                # produced no saved JSONs for this journal (journal_download_count == 0).
                # This ensures we don't stop early just because the newarticles page
                # didn't yield any extractable JSON.
                should_crawl_archives = crawl_archives or (journal_download_count == 0)
                if should_crawl_archives:
                    print(f"\n Crawling issue archives for journal: {slug}", flush=True)
                    print(f"Creating separate context for archive crawling...", flush=True)
                    
                    archive_context = await browser.new_context(
                        accept_downloads=False,
                        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0',
                        viewport={'width': 1920, 'height': 1080},
                        locale='en-US',
                        timezone_id='America/New_York',
                        permissions=['geolocation'],
                        geolocation={'longitude': -74.0060, 'latitude': 40.7128},
                        color_scheme='light',
                        extra_http_headers={
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                            'Accept-Language': 'en-US,en;q=0.9',
                            'Accept-Encoding': 'gzip, deflate, br',
                            'Connection': 'keep-alive',
                            'Upgrade-Insecure-Requests': '1',
                        }
                    )
                    
                    archive_page = await archive_context.new_page()
                    await stealth.apply_stealth_async(archive_page)
                    
                    await archive_page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined
                        });
                    """)
                    
                    print(f"Archive context ready", flush=True)
                    
                    issue_index_url = f"https://www.cell.com/{slug}/issues"
                    print(f"Loading issue archive index: {issue_index_url}", flush=True)
                    await archive_page.goto(issue_index_url, timeout=30000)
                    await archive_page.wait_for_timeout(3000)
                    
                    await handle_cookie_consent(archive_page)
                    
                    # STEP 1: Expand outer accordion sections (year ranges like "2010-2019")
                    # These are collapsed by default and contain volumes inside
                    try:
                        outer_accordions = archive_page.locator('a.accordion__control')
                        accordion_count = await outer_accordions.count()
                        print(f"Found {accordion_count} year range sections, expanding all...", flush=True)
                        
                        for i in range(accordion_count):
                            try:
                                accordion = outer_accordions.nth(i)
                                # Check if it's expanded (aria-expanded="true")
                                is_expanded = await accordion.get_attribute('aria-expanded')
                                if is_expanded != 'true':
                                    accordion_text = await accordion.text_content()
                                    await accordion.click()
                                    await archive_page.wait_for_timeout(800)
                                    print(f"Expanded section: {accordion_text.strip()}", flush=True)
                            except Exception as e:
                                logger.debug(f"Failed to expand accordion {i}: {e}")
                        
                        # Wait for all accordion content to load
                        await archive_page.wait_for_timeout(1500)
                    except Exception as e:
                        print(f"Failed to expand year range sections: {e}", flush=True)
                    
                    # STEP 2: Expand individual volume toggles for target years
                    # These are <a> tags with class "list-of-issues__group-expand"
                    volumes_to_expand = []
                    try:
                        volume_toggles = archive_page.locator('a.list-of-issues__group-expand')
                        toggle_count = await volume_toggles.count()
                        print(f"Found {toggle_count} volume toggles, identifying target volumes...", flush=True)
                        
                        # First pass: identify which volumes to expand
                        for i in range(toggle_count):
                            try:
                                toggle = volume_toggles.nth(i)
                                volume_text = await toggle.text_content()
                                if volume_text:
                                    year_match = re.search(r'\((\d{4})\)', volume_text)
                                    if year_match:
                                        vol_year = int(year_match.group(1))
                                        if year_from <= vol_year <= year_to + 1:
                                            volumes_to_expand.append((i, volume_text.strip()))
                            except Exception as e:
                                logger.debug(f"Failed to check volume toggle {i}: {e}")
                        
                        # Second pass: click all target volumes
                        print(f"Expanding {len(volumes_to_expand)} volumes...", flush=True)
                        for idx, vol_text in volumes_to_expand:
                            try:
                                toggle = volume_toggles.nth(idx)
                                await toggle.click()
                                print(f"Clicked: {vol_text}", flush=True)
                                await archive_page.wait_for_timeout(500)
                            except Exception as e:
                                logger.debug(f"Failed to click volume {vol_text}: {e}")
                        
                        # Wait for all AJAX content to load
                        if volumes_to_expand:
                            print(f"Waiting for issue lists to load...", flush=True)
                            await archive_page.wait_for_timeout(3000)
                            
                            # Wait for issue links to appear in the DOM
                            try:
                                await archive_page.wait_for_selector('a[href*="/issue?pii="]', timeout=5000, state='attached')
                            except Exception:
                                pass  # Continue even if selector doesn't appear
                                
                    except Exception as e:
                        print(f"Failed to expand volume toggles: {e}", flush=True)

                    html = await archive_page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    
                    print(f"Parsing issue links from page HTML...", flush=True)
                    issue_links = []
                    in_open_archive = False
                    
                    # Broaden selector to catch multiple issue URL patterns.
                    # Some pages may use different href formats for older issues.
                    all_issue_links = soup.select(
                        'a[href*="/issue?pii="]'
                    )
                    print(f"Found {len(all_issue_links)} total issue links on page", flush=True)
                    
                    for link in all_issue_links:
                        href = link.get("href", "")
                        if not href:
                            continue
                        
                        # Check if this is after the Open Archive marker
                        parent_li = link.find_parent("li")
                        if parent_li:
                            open_archive_div = parent_li.find_previous("div", class_="list-of-issues__open-archive")
                            if open_archive_div and not in_open_archive:
                                in_open_archive = True
                                print(f"Entered Open Archive section", flush=True)
                        
                        # Try to extract date/year from the link or its parent <li> text.
                        # Use a robust regex to find a 4-digit year (e.g., 2024).
                        try:
                            link_text = link.get_text(" ", strip=True)
                            # Prefer the parent <li> text when available (it contains issue spans)
                            parent_li = link.find_parent("li")
                            if parent_li:
                                block_text = parent_li.get_text(" ", strip=True)
                            else:
                                block_text = link_text

                            # Normalize whitespace and collapse concatenated tokens
                            block_text = re.sub(r"\s+", " ", block_text)

                            year_match = re.search(r"\b(19|20)\d{2}\b", block_text)
                            if year_match:
                                issue_year = int(year_match.group(0))
                                date_text = block_text
                                if year_from <= issue_year <= year_to:
                                    full_url = urljoin("https://www.cell.com", href)
                                    if (full_url, in_open_archive, date_text) not in issue_links:
                                        issue_links.append((full_url, in_open_archive, date_text))
                                        logger.debug(f" Found issue: {date_text[:50]} ({'Open Archive' if in_open_archive else 'Regular'})")
                                else:
                                    logger.debug(f"  Skipped issue (year {issue_year} not in range): {date_text[:50]}")
                            else:
                                logger.debug(f"  No year found in link text for: {href[:50]}")
                        except Exception as e:
                            logger.debug(f"  Failed to parse date from link {href[:50]} - {e}")
                    
                    print(f"Found {len(issue_links)} issues to crawl for {slug} (filtered by year {year_from}-{year_to})", flush=True)
                    
                    for issue_url, is_open_archive, issue_date in issue_links:
                        if limit and journal_download_count >= limit:
                            print(f"✋ Reached journal limit of {limit}, stopping archive crawl", flush=True)
                            break
                        
                        journal_download_count, should_stop = await crawl_issue_page(archive_page, issue_url, journal_folder, journal_download_count, is_open_archive, issue_date)
                        if should_stop:
                            break
                        
                        await asyncio.sleep(2)
                    
                    print(f"Closing archive context for journal: {slug}", flush=True)
                    await archive_page.close()
                    await archive_context.close()
                
                print(f"Closing browser for journal: {slug}", flush=True)
                await page.close()
                await context.close()
                await browser.close()

    if cli_progress:
        cli_progress.close()
    
    print(f"\n Extracted {found_count} JSON files to {out_folder}")
    
    # Create CSV file with extraction summary
    if saved_files:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"extraction_summary_{timestamp}.csv"
        csv_path = os.path.join(out_folder, csv_filename)
        
        print(f"\n Creating extraction summary CSV: {csv_filename}")
        
        try:
            with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['Number', 'Journal', 'Article Name', 'Publish Date', 'File Path', 'File Size (KB)'])
                
                for idx, (file_path, article_name, publish_date) in enumerate(article_metadata, 1):
                    journal_name = os.path.basename(os.path.dirname(file_path))
                    file_size_kb = os.path.getsize(file_path) / 1024 if os.path.exists(file_path) else 0
                    writer.writerow([idx, journal_name, article_name, publish_date, file_path, f"{file_size_kb:.2f}"])
            
            logger.info(f"CSV summary saved to: {csv_path}")
        except Exception as e:
            logger.error(f" Failed to create CSV summary: {e}")
    
    # Zip all journal subfolders into one archive
    if saved_files:
        print(f"\n Creating ZIP archive with all extracted JSON files...")
        
        zip_filename = f"all_journals_json_{timestamp}.zip"
        zip_path = os.path.join(out_folder, zip_filename)
        
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in saved_files:
                    arcname = os.path.relpath(file_path, out_folder)
                    zipf.write(file_path, arcname)
                
                if os.path.exists(csv_path):
                    zipf.write(csv_path, os.path.basename(csv_path))
            
            zip_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
            logger.info(f"Created ZIP archive: {zip_filename} ({zip_size_mb:.1f} MB)")
            logger.info(f"Archive contains {len(saved_files)} JSON files from {len(set(os.path.dirname(f) for f in saved_files))} journals")
        except Exception as e:
            logger.error(f" Failed to create ZIP archive: {e}")
    
    return saved_files, open_access_articles
