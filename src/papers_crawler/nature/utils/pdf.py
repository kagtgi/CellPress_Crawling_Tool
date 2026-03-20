import asyncio
import logging
import os
import re
import traceback
from typing import Optional
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

async def download_pdf_nature(article_url: str, pdf_out_folder: str) -> Optional[str]:
    """Download a Nature article PDF via direct HTTP GET.
    
    Constructs the PDF URL from the article URL using the pattern:
    https://www.nature.com/articles/<article-id>.pdf
    
    Follows redirects automatically and saves the file using the
    filename from the server response (Content-Disposition or final URL).
    
    Args:
        article_url: The article page URL (e.g. https://www.nature.com/articles/s41586-024-07238-x)
        pdf_out_folder: Directory to save the downloaded PDF
        
    Returns:
        str: Path to the saved PDF file, or None if download failed
    """
    try:
        # Extract article ID from URL
        # e.g. /articles/s41586-024-07238-x -> s41586-024-07238-x
        match = re.search(r'/articles/([^/?#]+)', article_url)
        if not match:
            logger.error(f" Could not extract article ID from URL: {article_url}")
            return None
        
        article_id = match.group(1)
        pdf_url = f"https://www.nature.com/articles/{article_id}.pdf"
        
        logger.info(f"Downloading PDF from: {pdf_url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            'Accept': 'application/pdf,application/octet-stream,*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        }
        
        os.makedirs(pdf_out_folder, exist_ok=True)
        
        # Use urllib.request which follows redirects by default
        req = Request(pdf_url, headers=headers)
        
        # Run the blocking HTTP request in a thread to keep async compatibility
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: urlopen(req, timeout=60))
        
        pdf_data = await loop.run_in_executor(None, response.read)
        
        # Determine filename from Content-Disposition header or final URL
        filename = None
        content_disposition = response.headers.get('Content-Disposition', '')
        if content_disposition:
            cd_match = re.search(r'filename[*]?=["\']?([^"\';]+)', content_disposition)
            if cd_match:
                filename = cd_match.group(1).strip()
        
        if not filename:
            # Use the final URL path as filename (after redirects)
            final_url = response.url if hasattr(response, 'url') else pdf_url
            final_path = str(final_url).split('/')[-1].split('?')[0]
            if final_path and final_path.endswith('.pdf'):
                filename = final_path
            else:
                filename = f"{article_id}.pdf"
        
        pdf_path = os.path.join(pdf_out_folder, filename)
        
        with open(pdf_path, 'wb') as f:
            f.write(pdf_data)
        
        file_size_kb = os.path.getsize(pdf_path) / 1024
        
        if file_size_kb < 1:  # Less than 1KB is likely an error page
            logger.error(f" Downloaded PDF is too small ({file_size_kb:.1f} KB): {pdf_path}")
            os.remove(pdf_path)
            return None
        
        logger.info(f"Saved PDF: {filename} ({file_size_kb:.1f} KB)")
        print(f"PDF saved: {filename} ({file_size_kb:.1f} KB)", flush=True)
        return pdf_path
        
    except Exception as e:
        logger.error(f" Failed to download PDF for {article_url}: {e}")
        logger.debug(traceback.format_exc())
        return None
