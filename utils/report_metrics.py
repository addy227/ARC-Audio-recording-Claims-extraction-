"""
Global report metrics for the email summary.

Pipeline and app update these metrics; email_summary reads them.
State is persisted to metrics/report_metrics.json so the email step
can run in a separate process and still have pipeline + app data.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from utils.config_loader import load_pipeline_config
from utils.util_master import get_project_path

# -----------------------------------------------------------------------------
# Global state (in-memory; also persisted to file)
# -----------------------------------------------------------------------------

FILE_PROCESSED_DATE: Optional[str] = None
TOTAL_FILES_PROCESSED: int = 0
SUCCESSFUL_FILES: int = 0
FAILED_FILES: int = 0
TOTAL_PROCESSING_TIME_SEC: float = 0.0
TOTAL_CLAIMS_EXTRACTED: Optional[int] = None
CLAIMS_ATTACHED_SUCCESS: Optional[int] = None
TRANSCRIPTION_VRAM_GB: Optional[float] = None
EXTRACTION_VRAM_GB: Optional[float] = None

_metrics_file: Optional[Path] = None


def _get_metrics_file() -> Path:
    global _metrics_file
    if _metrics_file is None:
        config = load_pipeline_config()
        paths = config.get("paths", {})
        metrics_dir = Path(get_project_path(paths.get("metrics_dir", "metrics/")))
        metrics_dir.mkdir(parents=True, exist_ok=True)
        _metrics_file = metrics_dir / "report_metrics.json"
    return _metrics_file


def set_file_processed_date(date_str: str) -> None:
    """Set the date of files being processed (YYYY-MM-DD). Pipeline calls at start."""
    global FILE_PROCESSED_DATE
    FILE_PROCESSED_DATE = date_str


def record_pipeline_run(
    total_files: int,
    successful: int,
    failed: int,
    total_processing_time_sec: float,
) -> None:
    """Record pipeline run summary. Pipeline calls at end of run_pipeline_parallel."""
    global TOTAL_FILES_PROCESSED, SUCCESSFUL_FILES, FAILED_FILES, TOTAL_PROCESSING_TIME_SEC
    TOTAL_FILES_PROCESSED = total_files
    SUCCESSFUL_FILES = successful
    FAILED_FILES = failed
    TOTAL_PROCESSING_TIME_SEC = total_processing_time_sec
    save_to_file()


def set_transcription_vram_gb(vram_gb: Optional[float]) -> None:
    """Set average/latest GPU VRAM usage for transcription (GB). speech_to_text calls this."""
    load_from_file()
    global TRANSCRIPTION_VRAM_GB
    TRANSCRIPTION_VRAM_GB = vram_gb
    save_to_file()


def set_extraction_vram_gb(vram_gb: Optional[float]) -> None:
    """Set average/latest GPU VRAM usage for claim extraction (GB). new_claim_extractor calls this."""
    load_from_file()
    global EXTRACTION_VRAM_GB
    EXTRACTION_VRAM_GB = vram_gb
    save_to_file()


def record_app_run(total_claims_extracted: int, claims_attached_success: int) -> None:
    """Record app run summary (POST_PROCESS_URL_PROD). App calls at end. Merges with existing state."""
    load_from_file()
    global TOTAL_CLAIMS_EXTRACTED, CLAIMS_ATTACHED_SUCCESS
    TOTAL_CLAIMS_EXTRACTED = total_claims_extracted
    CLAIMS_ATTACHED_SUCCESS = claims_attached_success
    save_to_file()


def get_stats() -> Dict[str, Any]:
    """
    Return merged stats for the email report.
    Loads from file first so we have latest pipeline + app data when email runs in another process.
    """
    load_from_file()
    total = TOTAL_FILES_PROCESSED
    success_rate = (SUCCESSFUL_FILES / total * 100.0) if total else 0.0
    avg_processing_time_sec = (TOTAL_PROCESSING_TIME_SEC / total) if total else 0.0
    total_claims = TOTAL_CLAIMS_EXTRACTED or 0
    avg_time_per_claim_sec = (
        (TOTAL_PROCESSING_TIME_SEC / total_claims) if total_claims else None
    )
    return {
        "file_processed_date": FILE_PROCESSED_DATE or datetime.now().strftime("%Y-%m-%d"),
        "total_files_processed": total,
        "successful_files": SUCCESSFUL_FILES,
        "failed_files": FAILED_FILES,
        "success_rate": success_rate,
        "total_processing_time_sec": TOTAL_PROCESSING_TIME_SEC,
        "avg_processing_time_sec": avg_processing_time_sec,
        "total_claims_extracted": TOTAL_CLAIMS_EXTRACTED,
        "claims_attached_success": CLAIMS_ATTACHED_SUCCESS,
        "avg_time_per_claim_sec": avg_time_per_claim_sec,
        "transcription_vram_gb": TRANSCRIPTION_VRAM_GB,
        "extraction_vram_gb": EXTRACTION_VRAM_GB,
    }


def load_from_file() -> None:
    """Load state from metrics/report_metrics.json into module globals."""
    global FILE_PROCESSED_DATE, TOTAL_FILES_PROCESSED, SUCCESSFUL_FILES, FAILED_FILES
    global TOTAL_PROCESSING_TIME_SEC, TOTAL_CLAIMS_EXTRACTED, CLAIMS_ATTACHED_SUCCESS
    global TRANSCRIPTION_VRAM_GB, EXTRACTION_VRAM_GB
    path = _get_metrics_file()
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        FILE_PROCESSED_DATE = data.get("file_processed_date")
        TOTAL_FILES_PROCESSED = data.get("total_files_processed", 0)
        SUCCESSFUL_FILES = data.get("successful_files", 0)
        FAILED_FILES = data.get("failed_files", 0)
        TOTAL_PROCESSING_TIME_SEC = data.get("total_processing_time_sec", 0.0)
        TOTAL_CLAIMS_EXTRACTED = data.get("total_claims_extracted")
        CLAIMS_ATTACHED_SUCCESS = data.get("claims_attached_success")
        TRANSCRIPTION_VRAM_GB = data.get("transcription_vram_gb")
        EXTRACTION_VRAM_GB = data.get("extraction_vram_gb")
    except (json.JSONDecodeError, OSError):
        pass


def save_to_file() -> None:
    """Persist current state to metrics/report_metrics.json."""
    path = _get_metrics_file()
    data = {
        "file_processed_date": FILE_PROCESSED_DATE,
        "total_files_processed": TOTAL_FILES_PROCESSED,
        "successful_files": SUCCESSFUL_FILES,
        "failed_files": FAILED_FILES,
        "total_processing_time_sec": TOTAL_PROCESSING_TIME_SEC,
        "total_claims_extracted": TOTAL_CLAIMS_EXTRACTED,
        "claims_attached_success": CLAIMS_ATTACHED_SUCCESS,
        "transcription_vram_gb": TRANSCRIPTION_VRAM_GB,
        "extraction_vram_gb": EXTRACTION_VRAM_GB,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass
