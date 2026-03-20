import json
import logging
from typing import Dict

logger = logging.getLogger(__name__)

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
        logger.info(f"Saved JSON to: {file_path}")
        return True
    except Exception as e:
        logger.error(f" Failed to save JSON file: {e}")
        return False
