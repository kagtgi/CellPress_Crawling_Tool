import logging
import traceback
from datetime import datetime
from typing import Dict, Optional

from bs4 import BeautifulSoup
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def _download_text(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.text

async def extract_fulltext_pubmed_as_json(client: httpx.AsyncClient, pmc_id: str) -> Optional[Dict]:
    """Navigate to PMC article page and extract full text into JSON format identically to Nature.
    
    Args:
        client: httpx async client
        pmc_id: PMC ID (e.g. PMC8754117 or 8754117)
        
    Returns:
        JSON Dict with sections (Title, Authors, Abstract, etc.)
    """
    try:
        if not pmc_id.upper().startswith("PMC"):
            pmc_id = f"PMC{pmc_id}"
            
        id_numeric = pmc_id.replace('PMC', '')
        url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={id_numeric}"
        
        logger.info(f"Navigating to PMC E-utilities: {url}")
        
        print(f"Loading XML {url}...")
        xml_content = await _download_text(client, url)
        
        soup = BeautifulSoup(xml_content, "html.parser")

        print("XML Got! Extract content...")
        
        json_data = {
            "url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/",
            "extracted_at": datetime.now().isoformat()
        }
        
        # Title
        title_elem = soup.find('article-title')
        if title_elem:
            json_data["title"] = title_elem.get_text(separator=' ', strip=True)

        # Authors
        contrib_groups = soup.find_all('contrib-group')
        authors = []
        for group in contrib_groups:
            contrib_list = group.find_all('contrib', attrs={'contrib-type': 'author'})
            for contrib in contrib_list:
                name_elem = contrib.find('name')
                if name_elem:
                    surname = name_elem.find('surname')
                    given_names = name_elem.find('given-names')
                    if surname and given_names:
                        authors.append(f"{given_names.get_text(strip=True)} {surname.get_text(strip=True)}")
        if authors:
            json_data["authors"] = ", ".join(dict.fromkeys(authors)) # unique preserve order

        # DOI
        doi_elem = soup.find('article-id', attrs={'pub-id-type': 'doi'})
        if doi_elem:
            json_data["doi"] = doi_elem.get_text(strip=True)

        # Date
        pub_dates = soup.find_all('pub-date')
        if pub_dates:
            pub_date = pub_dates[-1] # Usually the most relevant date
            year = pub_date.find('year')
            month = pub_date.find('month')
            day = pub_date.find('day')
            date_parts = []
            for part in (year, month, day):
                if part:
                    date_parts.append(part.get_text(strip=True))
            if date_parts:
                json_data["publication_date"] = "-".join(date_parts)

        # Abstract
        abstract_elems = soup.find_all('abstract')
        for idx, abstract in enumerate(abstract_elems):
            abstract_text = abstract.get_text(" ", strip=True)
            if abstract_text:
                key = f'Abstract_{idx+1}' if len(abstract_elems) > 1 else 'Abstract'
                json_data[key] = abstract_text

        # Main Texts - PMC uses <sec>
        sec_elements = soup.find_all('sec')
        for sec in sec_elements:
            header_elem = sec.find('title', recursive=False)
            if not header_elem:
                continue
                
            sec_title = header_elem.get_text(strip=True)
            if not sec_title or sec_title.lower() in ['abstract', 'acknowledgments', 'references', 'abbreviations']:
                continue
                
            sec_text = []
            for child in sec.find_all(['p', 'sec'], recursive=False):
                if child.name == 'p':
                    text_content = child.get_text(" ", strip=True)
                    if text_content:
                        sec_text.append(text_content)
                elif child.name == 'sec':
                    # Handle nested section titles
                    nested_title = child.find('title')
                    if nested_title:
                        sec_text.append(f"\n## {nested_title.get_text(strip=True)}\n")
                    # Handle paragraphs within nested section
                    for nested_p in child.find_all('p', recursive=False):
                        nested_text = nested_p.get_text(" ", strip=True)
                        if nested_text:
                            sec_text.append(nested_text)
                            
            if sec_text:
                # Handle duplicated heading keys
                base_title = sec_title
                counter = 1
                while sec_title in json_data:
                    sec_title = f"{base_title}_{counter}"
                    counter += 1
                json_data[sec_title] = "\n\n".join(sec_text)

        # Figures
        figures = soup.find_all('fig')
        if figures:
            fig_data = []
            for idx, fig in enumerate(figures, 1):
                caption = fig.find('caption')
                if caption:
                    fig_data.append(f"{idx}. {caption.get_text(' ', strip=True)}")
            if fig_data:
                json_data["Figures"] = "\n\n".join(fig_data)

        # References
        ref_list = soup.find('ref-list')
        if ref_list:
            refs = []
            for idx, ref in enumerate(ref_list.find_all('ref'), 1):
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
