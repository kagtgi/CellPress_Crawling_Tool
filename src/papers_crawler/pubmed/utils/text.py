import logging
import traceback
from datetime import datetime
from typing import Dict, Optional

from bs4 import BeautifulSoup
from playwright.async_api import Page

logger = logging.getLogger(__name__)

async def extract_fulltext_pubmed_as_json(page: Page, pmc_id: str) -> Optional[Dict]:
    """Navigate to PMC article page and extract full text into JSON format identically to Nature.
    
    Args:
        page: Playwright page object
        pmc_id: PMC ID (e.g. PMC8754117 or 8754117)
        
    Returns:
        JSON Dict with sections (Title, Authors, Abstract, etc.)
    """
    try:
        if not pmc_id.upper().startswith("PMC"):
            pmc_id = f"PMC{pmc_id}"
            
        url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/"
        logger.info(f"Navigating to PMC page: {url}")
        
        print(f"Loading page {url}...")
        await page.goto(url, timeout=30000)
        await page.wait_for_timeout(2000)
        
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        print("HTML Got! Extract content...")
        
        json_data = {
            "url": url,
            "extracted_at": datetime.now().isoformat()
        }
        
        # Remove navigation elements
        for unwanted in soup.find_all(['nav', 'script', 'style', 'button', 'noscript', 'aside', 'footer', 'header']):
            unwanted.decompose()
            
        # Title
        title_elem = soup.find(class_='content-title') or soup.find("h1")
        if title_elem:
            json_data["title"] = title_elem.get_text(strip=True)
            
        # Authors
        contrib_groups = soup.find_all(class_='contrib-group')
        if contrib_groups:
            authors = []
            for cg in contrib_groups:
                for author in cg.find_all(class_='contrib-group-name') or cg.find_all('a', class_='name'):
                    if author.get_text(strip=True):
                        authors.append(author.get_text(strip=True))
            if authors:
                json_data["authors"] = ", ".join(dict.fromkeys(authors)) # unique preserve order
                
        # DOI
        doi_meta = soup.find('meta', {'name': 'citation_doi'})
        if doi_meta:
            json_data["doi"] = doi_meta.get("content", "")
            
        # Date
        date_meta = soup.find('meta', {'name': 'citation_date'})
        if date_meta:
            json_data["publication_date"] = date_meta.get("content", "")
            
        # Abstract
        abstract_section = soup.find('div', class_='abstract') or soup.find('div', id='abstract')
        if abstract_section:
            paras = [p.get_text(" ", strip=True) for p in abstract_section.find_all('p')]
            json_data["Abstract"] = "\n\n".join([p for p in paras if p])
            
        # Main text sections - PMC uses div.tsec
        # Sometimes sections are nested, but top-level .tsec is a good heuristic
        tsec_elements = soup.find_all('div', class_='tsec')
        for sec in tsec_elements:
            header = sec.find(['h2', 'h3'])
            if not header:
                continue
                
            sec_title = header.get_text(strip=True)
            if not sec_title or sec_title.lower() in ['abstract', 'acknowledgments', 'references', 'abbreviations']:
                continue
                
            sec_text = []
            for child in sec.find_all(['p', 'h3', 'h4', 'h5']):
                child_text = child.get_text(" ", strip=True)
                if not child_text or child.name in ['h2']:
                    continue # Skip the main header
                if child.name in ['h3', 'h4', 'h5']:
                    sec_text.append(f"\n## {child_text}\n")
                else:
                    sec_text.append(child_text)
                    
            if sec_text:
                json_data[sec_title] = "\n\n".join(sec_text)
                
        # Figures
        figures = soup.find_all('div', class_='fig') or soup.find_all('figure')
        if figures:
            fig_data = []
            for idx, fig in enumerate(figures, 1):
                caption = fig.find('div', class_='caption') or fig.find('figcaption') or fig.find('div', class_='fig-caption')
                if caption:
                    fig_data.append(f"{idx}. {caption.get_text(' ', strip=True)}")
            if fig_data:
                json_data["Figures"] = "\n\n".join(fig_data)
                
        # References
        ref_list = soup.find('div', id='reference-list') or soup.find('div', class_='ref-list')
        if ref_list:
            refs = []
            for idx, ref in enumerate(ref_list.find_all('div', class_='ref-cit-group') or ref_list.find_all('li'), 1):
                refs.append(f"{idx}. {ref.get_text(' ', strip=True)}")
            if refs:
                json_data["References"] = "\n\n".join(refs)
                
        if len(json_data) > 2:
            return json_data
            
        logger.warning(f"No substantial text extracted for PMC {pmc_id}")
        return None
        
    except Exception as e:
        logger.error(f"Failed to extract PMC {pmc_id}: {e}")
        logger.debug(traceback.format_exc())
        return None
