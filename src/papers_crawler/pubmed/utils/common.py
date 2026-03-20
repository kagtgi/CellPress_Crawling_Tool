import json
import logging
import os
import pandas as pd
from typing import Dict, List

logger = logging.getLogger(__name__)

async def save_json_to_file(json_content: Dict, file_path: str) -> bool:
    """Save extracted content to a .json file.
    
    Args:
        json_content: The JSON content to save
        file_path: Absolute path where the file should be saved
        
    Returns:
        bool: True if saved successfully, False otherwise
    """
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(json_content, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved JSON to: {file_path}")
        return True
    except Exception as e:
        logger.error(f" Failed to save JSON file: {e}")
        return False

def read_pmc_ids_from_file(filepath: str) -> List[str]:
    """Reads PMC IDs from a CSV, Excel, or JSONL file.
    Expects a 'pmc_id' column or attribute.
    """
    pmc_ids = []
    ext = os.path.splitext(filepath)[1].lower()
    
    try:
        if ext == '.csv':
            df = pd.read_csv(filepath)
            if 'pmc_id' in df.columns:
                pmc_ids = df['pmc_id'].dropna().astype(str).tolist()
        elif ext in ['.xls', '.xlsx']:
            df = pd.read_excel(filepath)
            if 'pmc_id' in df.columns:
                pmc_ids = df['pmc_id'].dropna().astype(str).tolist()
        elif ext == '.jsonl':
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    data = json.loads(line)
                    if 'pmc_id' in data and data['pmc_id']:
                        pmc_ids.append(str(data['pmc_id']))
                        
        # Normalize to just numbers if PMC prefix is included, or add it?
        # The URL expects PMC12345. Let's make sure they all start with PMC.
        normalized = []
        for pmid in pmc_ids:
            p = pmid.strip()
            if not p: continue
            if not p.upper().startswith('PMC'):
                p = 'PMC' + p
            normalized.append(p.upper())
            
        return normalized
    except Exception as e:
        logger.error(f"Failed to read PMC IDs from {filepath}: {e}")
        return []
