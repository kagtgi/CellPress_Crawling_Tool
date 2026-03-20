import asyncio
import logging
import os
import traceback
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import urljoin
from playwright.async_api import Page

logger = logging.getLogger(__name__)

async def download_pdf_pubmed(page: Page, pmc_id: str, pdf_out_folder: str) -> Optional[str]:
    """Download a PubMed Central article PDF.
    
    Navigates to the PMC page to find the exact PDF link, then downloads it.
    
    Args:
        page: Playwright page object
        pmc_id: PMC ID (e.g. PMC8754117 or 8754117)
        pdf_out_folder: output folder
    
    Returns:
        str: path to saved PDF, or None if failed.
    """
    try:
        if not pmc_id.upper().startswith("PMC"):
            pmc_id = f"PMC{pmc_id}"
            
        url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/"
        logger.info(f"Navigating to PMC page for PDF link: {url}")
        
        await page.goto(url, timeout=30000)
        await page.wait_for_timeout(2000)
        
        # Look for PDF links on the PMC page
        hrefs = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll('a'))
                        .map(a => a.getAttribute('href'))
                        .filter(h => h && h.toLowerCase().includes('.pdf') && h.includes('/pmc/articles/'));
        }''')
        
        pdf_url = None
        if hrefs:
            pdf_url = hrefs[0]
            if not pdf_url.startswith('http'):
                pdf_url = urljoin('https://www.ncbi.nlm.nih.gov', pdf_url)
        else:
            # Fallback for "PDF" text links without obvious structure
            pdf_link_loc = page.locator('a:has-text("PDF"), a.int-view').first
            if await pdf_link_loc.is_visible():
                pdf_url = await pdf_link_loc.get_attribute('href')
                if pdf_url and not pdf_url.startswith('http'):
                    pdf_url = urljoin('https://www.ncbi.nlm.nih.gov', pdf_url)
                    
        if not pdf_url:
            logger.error(f"Could not find PDF link for {pmc_id}")
            return None
            
        logger.info(f"Downloading PDF from: {pdf_url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            'Accept': 'application/pdf,application/octet-stream,*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        }
        
        os.makedirs(pdf_out_folder, exist_ok=True)
        
        req = Request(pdf_url, headers=headers)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: urlopen(req, timeout=60))
        
        pdf_data = await loop.run_in_executor(None, response.read)
        
        filename = f"{pmc_id}.pdf"
        pdf_path = os.path.join(pdf_out_folder, filename)
        
        with open(pdf_path, 'wb') as f:
            f.write(pdf_data)
            
        file_size_kb = os.path.getsize(pdf_path) / 1024
        if file_size_kb < 1:
            logger.error(f" Downloaded PDF {pmc_id} is too small ({file_size_kb:.1f} KB)")
            os.remove(pdf_path)
            return None
            
        logger.info(f"Saved PDF: {filename} ({file_size_kb:.1f} KB)")
        return pdf_path
        
    except Exception as e:
        logger.error(f"Failed to download PMC PDF {pmc_id}: {e}")
        logger.debug(traceback.format_exc())
        return None
