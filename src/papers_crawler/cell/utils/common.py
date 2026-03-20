import sys
import time
import json
import logging
from typing import Dict

IN_COLAB = 'google.colab' in sys.modules

try:
    if IN_COLAB:
        from tqdm.notebook import tqdm
    else:
        from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

logger = logging.getLogger(__name__)

class CLIProgressTracker:
    """CLI progress tracker with optional tqdm support."""
    
    def __init__(self, use_tqdm: bool = True, min_refresh_interval: float = 0.5):
        self.use_tqdm = use_tqdm and TQDM_AVAILABLE
        self.pbar = None
        self.total = 0
        self.current = 0
        self.min_refresh_interval = min_refresh_interval  # Minimum seconds between updates
        self.last_update_time = 0
        
    def start(self, total: int):
        """Initialize progress tracking."""
        self.total = total
        self.current = 0
        self.last_update_time = time.time()
        if self.use_tqdm and total > 0:
            self.pbar = tqdm(
                total=total,
                desc="Downloading PDFs",
                unit="file",
                bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
                file=sys.stdout,
                mininterval=0.5,  # Minimum 0.5 seconds between updates
                maxinterval=2.0,  # Maximum 2 seconds between updates
            )
        elif total > 0:
            print(f"\n Starting download: 0/{total} files (0%)")
    
    def update(self, current: int, total: int, status: str = "", file_size: int = 0, speed_kbps: float = 0, stage: str = "", force: bool = False):
        """Update progress display with throttling to prevent too frequent updates."""
        current_time = time.time()
        time_since_last_update = current_time - self.last_update_time
        
        # Skip update if too soon (unless forced, final update, or stage change)
        if not force and time_since_last_update < self.min_refresh_interval and current < total:
            return
        
        self.current = current
        self.total = total
        self.last_update_time = current_time
        
        if self.use_tqdm and self.pbar:
            # Update progress bar
            if current > self.pbar.n:
                self.pbar.n = current
                self.pbar.refresh()  # Always refresh to show updates
                
            # Show status in postfix
            postfix = {}
            if speed_kbps > 0:
                if speed_kbps > 1024:
                    postfix['speed'] = f"{speed_kbps/1024:.1f} MB/s"
                else:
                    postfix['speed'] = f"{speed_kbps:.1f} KB/s"
            if status:
                postfix['status'] = status[:30]
            if postfix:
                self.pbar.set_postfix(postfix, refresh=False)
        else:
            # Simple text progress (throttled)
            if total > 0:
                percentage = (current / total) * 100
                status_text = f"\r Progress: {current}/{total} files ({percentage:.1f}%)"
                
                if speed_kbps > 0:
                    if speed_kbps > 1024:
                        status_text += f" | {speed_kbps/1024:.1f} MB/s"
                    else:
                        status_text += f" | {speed_kbps:.1f} KB/s"
                
                if status:
                    status_text += f" | {status[:40]}"
                
                print(status_text, end='')
    
    def close(self):
        """Finalize progress display."""
        if self.use_tqdm and self.pbar:
            self.pbar.close()
        else:
            print()  # New line after progress



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
            json.dump(json_content, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved JSON to: {file_path}")
        return True
    except Exception as e:
        logger.error(f" Failed to save JSON file: {e}")
        return False

