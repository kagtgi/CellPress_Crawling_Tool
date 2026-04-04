import asyncio
import io
import logging
import os
import random
import tarfile
import traceback
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urljoin
from playwright.async_api import Page

logger = logging.getLogger(__name__)

async def download_pdf_pubmed(page: Page, pmc_id: str, pdf_out_folder: str) -> Optional[str]:
    """Download a PubMed Central article PDF using the Open Access API Service.
    
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
            
        print(f"Resting to respect NCBI rate limits...")
        await asyncio.sleep(random.uniform(1.0, 2.0))
        
        oa_api_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmc_id}"
        logger.info(f"Querying PubMed OA API: {oa_api_url}")
        print(f"Querying OA API for {oa_api_url}...")
        
        response = await page.request.get(oa_api_url, timeout=30000)
        
        if not response.ok:
            logger.error(f"Failed to query OA API. HTTP status: {response.status}")
            return None
            
        xml_data = await response.text()
        
        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError:
            logger.error(f"Failed to parse XML from OA API for {pmc_id}")
            return None
        
        error_node = root.find('.//error')
        if error_node is not None:
            err_code = error_node.attrib.get('code', 'Unknown')
            err_msg = error_node.text or 'No error message provided'
            logger.error(f"OA API returned error for {pmc_id}: [{err_code}] {err_msg}")
            print(f"OA API returned error for {pmc_id}: [{err_code}] {err_msg}")
            return None
            
        pdf_url = None
        tgz_url = None
        
        for link in root.findall('.//link'):
            fmt = link.attrib.get('format', '').lower()
            href = link.attrib.get('href', '')
            if fmt == 'pdf':
                pdf_url = href
            elif fmt == 'tgz':
                tgz_url = href
        
        os.makedirs(pdf_out_folder, exist_ok=True)
        filename = f"{pmc_id}.pdf"
        pdf_path = os.path.join(pdf_out_folder, filename)
        
        if pdf_url:
            pdf_url = pdf_url.replace("ftp://", "https://")
            logger.info(f"Downloading PDF from: {pdf_url}")
            print(f"Downloading PDF from: {pdf_url}")
            pdf_resp = await page.request.get(pdf_url, timeout=120000)
            if not pdf_resp.ok:
                logger.error(f"Failed to download PDF. HTTP status: {pdf_resp.status}")
                return None
            
            pdf_data = await pdf_resp.body()
            
            if b"<!doctype html>" in pdf_data[:50].lower() or b"recaptcha" in pdf_data[:500].lower():
                logger.error(f"Received HTML instead of PDF for {pmc_id}.")
                return None
                
            with open(pdf_path, 'wb') as f:
                f.write(pdf_data)
                
            file_size_kb = os.path.getsize(pdf_path) / 1024
            if file_size_kb < 5:
                logger.error(f"Downloaded PDF {pmc_id} is suspiciously small ({file_size_kb:.1f} KB). Deleting.")
                os.remove(pdf_path)
                return None
                
            logger.info(f"Saved PDF: {filename} ({file_size_kb:.1f} KB)")
            return pdf_path
            
        elif tgz_url:
            tgz_url = tgz_url.replace("ftp://", "https://")
            logger.info(f"No direct PDF link. Downloading TGZ payload from: {tgz_url}")
            print(f"No direct PDF link found. Extracting from TGZ payload: {tgz_url}")
            
            tgz_resp = await page.request.get(tgz_url, timeout=180000)
            if not tgz_resp.ok:
                logger.error(f"Failed to download TGZ. HTTP status: {tgz_resp.status}")
                return None
            
            tgz_data = await tgz_resp.body()
            
            extracted_pdf_data = None

            try:
                pdf_files = []
                with tarfile.open(fileobj=io.BytesIO(tgz_data), mode="r:gz") as tar:
                    for member in tar.getmembers():
                        member_name = member.name.lower()
                        if member.name.lower().endswith('.pdf'):
                            pdf_files.append((member_name, member))

                    if not pdf_files:
                        logger.error(f"No PDF files found inside the TGZ archive for {pmc_id}.")
                        return None

                    if len(pdf_files) == 1:
                        extracted_pdf_data = tar.extractfile(pdf_files[0][1]).read()
                    else:
                        target_pdf = [pdf for pdf in pdf_files if '_article_' in pdf[0]][0]
                        print(f"The target PDF file: ({target_pdf[0]}, {target_pdf[1]})")
                        extracted_pdf_data = tar.extractfile(target_pdf[1]).read()

            except tarfile.TarError as e:
                logger.error(f"Failed to untar payload for {pmc_id}: {e}")
                return None
            
            if not extracted_pdf_data:
                logger.error(f"No PDF file found inside the TGZ archive for {pmc_id}.")
                return None
            
            with open(pdf_path, 'wb') as f:
                f.write(extracted_pdf_data)
                
            file_size_kb = os.path.getsize(pdf_path) / 1024
            if file_size_kb < 5:
                logger.error(f"Extracted PDF {pmc_id} is suspiciously small ({file_size_kb:.1f} KB). Deleting.")
                os.remove(pdf_path)
                return None
                
            logger.info(f"Extracted and saved PDF: {filename} ({file_size_kb:.1f} KB)")
            return pdf_path
            
        else:
            logger.warning(f"No PDF or TGZ links found in OA API response for {pmc_id}.")
            print(f"No PDF or TGZ links found for {pmc_id}.")
            return None
        
    except Exception as e:
        logger.error(f"Failed to process PMC PDF {pmc_id}: {e}")
        logger.debug(traceback.format_exc())
        return None