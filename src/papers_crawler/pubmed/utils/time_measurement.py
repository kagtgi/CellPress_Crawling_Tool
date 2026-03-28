import csv
import os
from datetime import datetime

class TimeTracker:
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        if self.output_dir:
            os.makedirs(self.output_dir, exist_ok=True)
            self._init_csv_files()

    def _init_csv_files(self):
        metadata_file = os.path.join(self.output_dir, "metadata_time.csv")
        fulltext_file = os.path.join(self.output_dir, "fulltext_time.csv")
        pdf_file = os.path.join(self.output_dir, "pdf_time.csv")

        if not os.path.exists(metadata_file):
            with open(metadata_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["batch_id", "batch_size", "crawling_date", "start_time", "end_time", "duration"])

        if not os.path.exists(fulltext_file):
            with open(fulltext_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["pmc_id", "crawling_date", "start_time", "end_time", "duration"])

        if not os.path.exists(pdf_file):
            with open(pdf_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["pmc_id", "crawling_date", "start_time", "end_time", "duration"])

    def record_metadata(self, batch_id: int, batch_size: int, start_datetime: datetime, end_datetime: datetime, duration: float):
        if not self.output_dir: return
        file_path = os.path.join(self.output_dir, "metadata_time.csv")
        self._append_row(file_path, [
            batch_id,
            batch_size,
            start_datetime.strftime("%Y-%m-%d"),
            start_datetime.strftime("%H:%M:%S"),
            end_datetime.strftime("%H:%M:%S"),
            f"{duration:.2f}"
        ])

    def record_fulltext(self, pmc_id: str, start_datetime: datetime, end_datetime: datetime, duration: float):
        if not self.output_dir: return
        file_path = os.path.join(self.output_dir, "fulltext_time.csv")
        self._append_row(file_path, [
            pmc_id,
            start_datetime.strftime("%Y-%m-%d"),
            start_datetime.strftime("%H:%M:%S"),
            end_datetime.strftime("%H:%M:%S"),
            f"{duration:.2f}"
        ])

    def record_pdf(self, pmc_id: str, start_datetime: datetime, end_datetime: datetime, duration: float):
        if not self.output_dir: return
        file_path = os.path.join(self.output_dir, "pdf_time.csv")
        self._append_row(file_path, [
            pmc_id,
            start_datetime.strftime("%Y-%m-%d"),
            start_datetime.strftime("%H:%M:%S"),
            end_datetime.strftime("%H:%M:%S"),
            f"{duration:.2f}"
        ])

    def _append_row(self, file_path: str, row: list):
        try:
            with open(file_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(row)
        except Exception as e:
            print(f"Error writing to time measurement file {file_path}: {e}")
