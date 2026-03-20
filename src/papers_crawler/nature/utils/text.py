import logging
import re
import traceback
from datetime import datetime
from typing import Dict, Optional

from bs4 import BeautifulSoup
from playwright.async_api import Page

logger = logging.getLogger(__name__)

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
        logger.info(f"Navigating to Nature article: {fulltext_url}")
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
        logger.error(f" Failed to extract Nature article: {e}")
        logger.debug(traceback.format_exc())
        return None
