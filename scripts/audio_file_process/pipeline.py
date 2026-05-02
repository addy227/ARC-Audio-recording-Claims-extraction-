"""
Voiclaim Pipeline
-----------------
This script orchestrates the Voiclaim audio-to-claim pipeline, including:
    1. Audio cleaning
    2. Speech-to-text transcription
    3. Claim extraction
    4. API integration

Stages are modular, robust, and support retries. File movement is tracked for each stage.
"""

import csv

# ==============================
# Standard library imports
# ==============================

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, List, Optional
import warnings

from scripts.DB.insert_audiofile import insert_claim_audio_record

warnings.simplefilter("always", category=FutureWarning)  # show full warnings


from utils.analytics import save_analytical_metrics, save_metadata

# ==============================
# Third-party imports
# ==============================
from dotenv import load_dotenv

# ==============================
# Local project utilities
# ==============================
from utils.pipeline_util import (
    timed_stage,
    stage_timings,
    get_all_audio_files,
    safe_move,
    retry_stage,
    cleanup_old_files,
    move_entire_audio_package,
    compute_audio_file_id,
)
from utils.util_master import get_project_path, load_processed_files_index, PROCESSED_INDEX_FILE, \
    append_processed_file_index
from utils.config_loader import load_pipeline_config
from utils.logging_utils import get_logger
from utils import report_metrics
from scripts.audio_file_process.audio_cleaner import batch_process_audio
from scripts.audio_file_process.speech_to_text import transcribe_folder
from scripts.audio_file_process.new_claim_extractor import process_all_transcripts

# ==============================
# Configuration Loading
# ==============================
config = load_pipeline_config()
paths = config.get("paths", {})
PIPELINE_CFG = config.get("pipeline", {})

# Ensure all required paths are present in config
REQUIRED_PATHS = [
    "transcripts_dir",
    "extracted_claims_dir",
    "log_dir",
    "cleaned_audio_dir",
    "raw_audio_dir",
    "metrics_dir",
    "processing_dir",
    "processed_dir",
    "failed_dir",
    "success_dir",
]
missing_paths = [p for p in REQUIRED_PATHS if p not in paths]
if missing_paths:
    raise KeyError(f"Missing required path configs: {missing_paths}")

# Path resolution
TRANSCRIPTS_DIR = get_project_path(paths["transcripts_dir"])
EXTRACTED_CLAIMS_DIR = get_project_path(paths["extracted_claims_dir"])
CLEANED_AUDIO_DIR = get_project_path(paths["cleaned_audio_dir"])
RAW_AUDIO_DIR = get_project_path(paths["raw_audio_dir"])
PROCESSING_DIR = get_project_path(paths["processing_dir"])
PROCESSED_DIR = get_project_path(paths["processed_dir"])
FAILED_DIR = get_project_path(paths["failed_dir"])
SUCCESS_DIR = get_project_path(paths["success_dir"])
LOG_DIR = get_project_path(paths["log_dir"])
METRICS_DIR = Path(get_project_path(paths["metrics_dir"]))
# ==============================
# Defaults & Pipeline Params
# ==============================
DEFAULTS = {
    "max_workers": 2,
    "retry_attempts": 3,
    "retry_backoff_sec": 2,
    "api_timeout_sec": 30,
}
PIPE_PARAMS = {**DEFAULTS, **PIPELINE_CFG}

# ==============================
# Logging Setup
# ==============================
metrics_lock = threading.Lock()
logger = get_logger("pipeline_logs")
load_dotenv()

VERSION = "1.0.0"
logger.info(f"Voiclaim Pipeline version: {VERSION}")

METRICS_DIR.mkdir(parents=True, exist_ok=True)

# Define metrics file path and stable CSV schema
METRICS_CSV = METRICS_DIR / "pipeline_lifecycle_metrics.csv"
METRICS_FIELDS = [
    "timestamp",
    "file_id",
    "file_name",
    "total_elapsed_sec",
    "overall_success",
    "status",
    "errors",
    "stages",
]


# ==============================
# Helper Functions
# ==============================
def ensure_dirs(*dirs: str) -> None:
    """
    Ensure all specified directories exist.

    Args:
        *dirs: Arbitrary list of directory paths to create.
    """
    for d in dirs:
        os.makedirs(d, exist_ok=True)
        logger.debug(f"Ensured directory exists: {d}")


@retry_stage("Cleaning")
def clean_audio(files: List[str]) -> None:
    """
    Run batch audio cleaning on the provided files.

    Args:
        files (List[str]): List of audio file paths to clean.
    """
    logger.debug(f"Starting audio cleaning for {len(files)} file(s).")
    batch_process_audio(files=files)
    logger.debug("Audio cleaning completed.")


@retry_stage("Transcription")
def transcribe_audio(cleaned_audio_files: List[str], transcript_dir: str) -> None:
    """
    Transcribe cleaned audio files to text in the transcript directory.

    Args:
        cleaned_audio_files (List[str]): List of cleaned audio file paths.
        transcript_dir (str): Directory where transcripts will be saved.
    """
    logger.debug(f"Starting transcription for {len(cleaned_audio_files)} file(s).")
    transcribe_folder(cleaned_audio_files, transcript_dir)
    logger.debug("Transcription completed.")


@retry_stage("Claim Extraction")
def extract_claims(transcript_files: List[str]) -> Any:
    """
    Extract claims from transcript files.

    Args:
        transcript_files (List[str]): List of transcript file paths.

    Returns:
        Any: Results from the claim extraction stage.
    """
    logger.debug(f"Starting claim extraction for {len(transcript_files)} file(s).")
    result = process_all_transcripts(files=transcript_files)
    logger.debug("Claim extraction completed.")
    return result


def should_upload_to_blob(transcript_path: Path, has_claims: bool = False) -> tuple[bool, str]:
    """
    Filter function to determine if files should be uploaded to blob storage.
    
    Filter logic (priority order):
    1. FIRST: If file has extracted claims → PASS (regardless of transcript size)
    2. ELSE: If transcript file > 1000 bytes → PASS
    3. ELSE: FAIL (no upload)
    
    Args:
        transcript_path: Path to the transcript file
        has_claims: Whether the file has extracted claims (default: False for failure cases)
    
    Returns:
        Tuple of (should_upload: bool, reason: str)
    """
    # Check condition 1: FIRST check if it has extracted claims
    if has_claims:
        # If it has claims, it passes regardless of transcript size
        return True, "Has extracted claims (passes filter regardless of transcript size)"
    
    # Condition 2: If no claims, check transcript file size
    if not transcript_path.exists():
        return False, "No claims extracted AND transcript file does not exist"
    
    transcript_size = transcript_path.stat().st_size
    if transcript_size > 1000:
        # No claims but transcript is large enough
        return True, f"No claims extracted but transcript size acceptable ({transcript_size} bytes > 1000 bytes)"
    else:
        # No claims AND transcript too small → FAIL
        return False, f"No claims extracted AND transcript file too small ({transcript_size} bytes <= 1000 bytes)"


def append_metrics_csv(row: dict) -> None:
    """Append a single row to the lifecycle metrics CSV."""
    METRICS_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not METRICS_CSV.exists()

    with METRICS_CSV.open("a", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=METRICS_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k) for k in METRICS_FIELDS})


def process_single_file_with_metrics(
    audio_file: str, api_url: str = None
) -> tuple[bool, float, dict, dict]:
    """
    Process a single audio file through all pipeline stages, collecting per-stage metrics.

    Returns:
        tuple: (success_flag, total_elapsed, per_stage_metrics, result_dict)
    """
    raw_uuid = uuid.uuid4()
    file_id = str(raw_uuid)  # full UUID for DB
    short_id = file_id.split("-")[0]  # first 8 chars for logs
    audio_path = Path(audio_file)
    filename = audio_path.name
    base_name = audio_path.stem
    processing_path = Path(PROCESSING_DIR) / filename

    logger.info(f"[{file_id}] 🚀 Starting processing for: {filename}")
    processing_start_dt = datetime.utcnow()
    start_time = time.time()
    per_stage = {}
    processed_result = {}

    try:
        safe_move(audio_file, PROCESSING_DIR)
    except Exception as e:
        logger.error(f"[{file_id}] ❌ Failed to move file to processing dir: {e}", exc_info=True)
        return False, 0.0, {}, {}

    # Helper to handle stage failures in a consistent way
    def fail_stage(stage_name: str, error: Exception):
        logger.error(
            f"[{file_id}] ❌ {stage_name.capitalize()} failed for {filename}: {error}",
            exc_info=True,
        )
        total_elapsed = time.time() - start_time
        processing_end_dt = datetime.utcnow()

        # Upload audio and transcript to blob storage BEFORE moving files
        # This ensures they're available in the database for failed extractions
        # IMPORTANT: Upload before move_entire_audio_package, otherwise files won't exist
        audio_blob_id = None
        transcript_blob_id = None
        
        cleaned_audio_path = Path(CLEANED_AUDIO_DIR) / f"{base_name}.wav"
        transcript_path = Path(TRANSCRIPTS_DIR) / f"{base_name}.txt"
        # Capture before move: status is based on whether transcription was generated
        transcription_generated = transcript_path.exists()

        # Apply filter before uploading (for failures, has_claims is False)
        should_upload, filter_reason = should_upload_to_blob(transcript_path, has_claims=False)
        
        if not should_upload:
            logger.warning(f"[{file_id}] 🚫 Blob upload FILTERED OUT for failed file: {filter_reason}")
            logger.warning(f"[{file_id}]    File will NOT appear on https://artranscriptionapi.vitalaxis.com")
            logger.warning(f"[{file_id}]    Transcript size: {transcript_path.stat().st_size if transcript_path.exists() else 0} bytes")
            audio_blob_id = None
            transcript_blob_id = None
        else:
            # Upload audio file if it exists (cleaning succeeded)
            audio_blob_id = None
            if cleaned_audio_path.exists():
                try:
                    from scripts.audio_file_process.blob_storage_handler import upload_file_to_blob
                    logger.info(f"[{file_id}] 📤 Uploading audio file for failed extraction: {cleaned_audio_path}")
                    audio_blob_id = upload_file_to_blob(str(cleaned_audio_path))
                    if audio_blob_id:
                        logger.info(f"[{file_id}] ✅ Audio file uploaded. Blob ID length: {len(audio_blob_id)}")
                    else:
                        logger.warning(f"[{file_id}] ⚠️ Audio file upload returned None")
                except Exception as e:
                    logger.error(f"[{file_id}] ❌ Failed to upload audio file: {e}", exc_info=True)
            else:
                logger.warning(f"[{file_id}] ⚠️ Audio file does not exist for upload: {cleaned_audio_path}")
            
            # Upload transcript file if it exists (transcription succeeded)
            transcript_blob_id = None
            if transcript_path.exists():
                try:
                    from scripts.audio_file_process.blob_storage_handler import upload_file_to_blob
                    logger.info(f"[{file_id}] 📤 Uploading transcript file for failed extraction: {transcript_path}")
                    transcript_blob_id = upload_file_to_blob(str(transcript_path))
                    if transcript_blob_id:
                        logger.info(f"[{file_id}] ✅ Transcript file uploaded. Blob ID length: {len(transcript_blob_id)}")
                    else:
                        logger.warning(f"[{file_id}] ⚠️ Transcript file upload returned None")
                except Exception as e:
                    logger.error(f"[{file_id}] ❌ Failed to upload transcript file: {e}", exc_info=True)
            else:
                logger.warning(f"[{file_id}] ⚠️ Transcript file does not exist for upload: {transcript_path}")

        # Now move files to failed directory
        move_entire_audio_package(
            base_name, claims_extracted_foldername="", json_files=[], success=False
        )
        save_metadata(file_id, filename, False, per_stage, Path(FAILED_DIR) / base_name)
        total_elapsed = time.time() - start_time
        processing_end_dt = datetime.utcnow()

        # processed_status is based on whether transcription was generated (captured before move)
        status_for_api = "Completed" if transcription_generated else "failed"

        # Only insert record to API if blob upload filter passed
        # If filter failed (no claims AND transcript too small), skip API call
        if should_upload:
        # Insert record with blob IDs (even if None, they'll be stored as null in DB)
            insert_claim_audio_record(
            file_name=filename,
            processed_status=status_for_api,
            elapsed_time=total_elapsed,
            stage=stage_name,
            extracted_claims=[],
            processing_start=processing_start_dt,
            processing_end=processing_end_dt,
            total_claim_count=0,
            audio_blob_id=audio_blob_id,
            transcript_blob_id=transcript_blob_id,
        )
        else:
            logger.info(
                f"[{file_id}] 🚫 Skipping API insert for failed file (filtered out): {filename}"
            )
        logger.info(
            f"[{file_id}] FILE_PROCESSING_COMPLETE file={filename} total_elapsed_sec={total_elapsed:.2f}"
        )
        return False, total_elapsed, per_stage, {}

    try:
        # Stage 1: Cleaning
        try:
            _, t_clean = timed_stage("cleaning", clean_audio, [processing_path])
            per_stage["cleaning_sec"] = t_clean
        except Exception as e:
            return fail_stage("cleaning", e)

        # Stage 2: Transcription
        cleaned_audio = Path(CLEANED_AUDIO_DIR) / f"{base_name}.wav"
        try:
            _, t_trans = timed_stage(
                "transcription", transcribe_audio, [cleaned_audio], TRANSCRIPTS_DIR
            )
            per_stage["transcription_sec"] = t_trans
        except Exception as e:
            return fail_stage("transcription", e)

            # Stage 3: Claim Extraction
        transcript_path = Path(TRANSCRIPTS_DIR) / f"{base_name}.txt"
        try:
            processed_result, t_extract = timed_stage(
                "extraction", extract_claims, [transcript_path]
            )
            processed_result["elapsed_sec"] = t_extract
            per_stage["extraction_sec"] = t_extract
            extracted_claims = processed_result.get("extracted_data", [])
            total_extracted_claims = processed_result.get(
                "total_extracted_claims", len(extracted_claims)
            )
            per_stage["total_extracted_claims"] = total_extracted_claims
            num_claims = len(extracted_claims)
            logger.info(f"[{file_id}] 📝 Extracted {num_claims} claim(s) from transcript.")
            if num_claims == 0:
                logger.warning(
                    f"[{file_id}] ⚠️ No claims extracted for base={base_name}. Moving to failed folder (API status=Completed, transcription was generated)."
                )
                return fail_stage("extraction", Exception("no claims extracted"))

            processed_path = Path(PROCESSED_DIR) / filename
            safe_move(processing_path, processed_path)
            logger.info(f"[{file_id}] ✅ Successfully moved file to processed folder")
            success_flag = True
        except Exception as e:
            return fail_stage("extraction", e)

        total_elapsed = time.time() - start_time
        processing_end_dt = datetime.utcnow()

        """
           Insert one row per audio file into ClaimCallAudioRecordings.
           All claims from the same audio file are aggregated into a single insert call.
           """

        # Extract audio and transcript blob IDs from the first record (same for all claims from same audio file)
        audio_file_storage_id = None
        transcript_storage_id = None
        if extracted_claims and len(extracted_claims) > 0:
            first_record = extracted_claims[0]
            ar_details = first_record.get("ARRecordingDetails", {})
            audio_file_storage_id = ar_details.get("audio_file_storage_id")
            transcript_storage_id = ar_details.get("original_transcript_storage_id")

        # Insert once per audio file with aggregated claim count
        audio_file_id = compute_audio_file_id(audio_file_storage_id, filename)
        inserted = insert_claim_audio_record(
            file_name=filename,
            processed_status="Completed",
            elapsed_time=total_elapsed,
            processing_start=processing_start_dt,
            processing_end=processing_end_dt,
            total_claim_count=total_extracted_claims,
            audio_blob_id=audio_file_storage_id,
            transcript_blob_id=transcript_storage_id,
        )
        logger.info(
            f"[{audio_file_id}] {'✅' if inserted else '⚠️'} DB insert | file={filename} "
            f"total_claims={total_extracted_claims} "
            f"audio_blob_len={len(audio_file_storage_id) if audio_file_storage_id else 0} "
            f"transcript_blob_len={len(transcript_storage_id) if transcript_storage_id else 0}"
        )
        logger.info(
            f"[{file_id}] FILE_PROCESSING_COMPLETE file={filename} total_elapsed_sec={total_elapsed:.2f}"
        )
        return success_flag, total_elapsed, per_stage, processed_result

    except Exception as e:
        logger.error(f"[{file_id}] 🔥 Unexpected error: {e}", exc_info=True)
        total_elapsed = time.time() - start_time
        processing_end_dt = datetime.utcnow()

        # Upload audio and transcript to blob storage BEFORE moving files
        # This ensures they're available in the database even on unexpected errors
        audio_blob_id = None
        transcript_blob_id = None
        
        cleaned_audio_path = Path(CLEANED_AUDIO_DIR) / f"{base_name}.wav"
        transcript_path = Path(TRANSCRIPTS_DIR) / f"{base_name}.txt"
        
        # Apply filter before uploading (for exceptions, has_claims is False)
        should_upload, filter_reason = should_upload_to_blob(transcript_path, has_claims=False)
        
        if not should_upload:
            logger.warning(f"[{file_id}] 🚫 Blob upload FILTERED OUT after unexpected error: {filter_reason}")
            logger.warning(f"[{file_id}]    File will NOT appear on https://artranscriptionapi.vitalaxis.com")
            logger.warning(f"[{file_id}]    Transcript size: {transcript_path.stat().st_size if transcript_path.exists() else 0} bytes")
            audio_blob_id = None
            transcript_blob_id = None
        else:
            # Upload audio file if it exists
            audio_blob_id = None
            if cleaned_audio_path.exists():
                try:
                    from scripts.audio_file_process.blob_storage_handler import upload_file_to_blob
                    logger.info(f"[{file_id}] 📤 Uploading audio file after unexpected error: {cleaned_audio_path}")
                    audio_blob_id = upload_file_to_blob(str(cleaned_audio_path))
                    if audio_blob_id:
                        logger.info(f"[{file_id}] ✅ Audio file uploaded. Blob ID length: {len(audio_blob_id)}")
                    else:
                        logger.warning(f"[{file_id}] ⚠️ Audio file upload returned None")
                except Exception as upload_err:
                    logger.error(f"[{file_id}] ❌ Failed to upload audio file: {upload_err}", exc_info=True)
            else:
                logger.warning(f"[{file_id}] ⚠️ Audio file does not exist for upload: {cleaned_audio_path}")
            
            # Upload transcript file if it exists
            transcript_blob_id = None
            if transcript_path.exists():
                try:
                    from scripts.audio_file_process.blob_storage_handler import upload_file_to_blob
                    logger.info(f"[{file_id}] 📤 Uploading transcript file after unexpected error: {transcript_path}")
                    transcript_blob_id = upload_file_to_blob(str(transcript_path))
                    if transcript_blob_id:
                        logger.info(f"[{file_id}] ✅ Transcript file uploaded. Blob ID length: {len(transcript_blob_id)}")
                    else:
                        logger.warning(f"[{file_id}] ⚠️ Transcript file upload returned None")
                except Exception as upload_err:
                    logger.error(f"[{file_id}] ❌ Failed to upload transcript file: {upload_err}", exc_info=True)
            else:
                logger.warning(f"[{file_id}] ⚠️ Transcript file does not exist for upload: {transcript_path}")

        # Now move files to failed directory
        if processing_path.exists():
            safe_move(str(processing_path), FAILED_DIR)

        # processed_status is based on whether transcription was generated
        transcription_generated = transcript_path.exists()
        status_for_api = "Completed" if transcription_generated else "failed"

        # Only insert record to API if blob upload filter passed
        # If filter failed (no claims AND transcript too small), skip API call
        if should_upload:
            insert_claim_audio_record(
            file_name=filename,
            processed_status=status_for_api,
            elapsed_time=total_elapsed,
            stage="unexpected",
            extracted_claims=[],
            processing_start=processing_start_dt,
            processing_end=processing_end_dt,
            total_claim_count=0,
            audio_blob_id=audio_blob_id,
            transcript_blob_id=transcript_blob_id,
        )
        else:
            logger.info(
            f"[{file_id}] 🚫 Skipping API insert after unexpected error (filtered out): {filename}"
            )
        logger.info(
            f"[{file_id}] FILE_PROCESSING_COMPLETE file={filename} total_elapsed_sec={total_elapsed:.2f}"
        )
        return False, total_elapsed, per_stage, {}


def run_pipeline_parallel(max_workers: int = 1, api_url: str = None, day: int = 1) -> dict:
    """
    Run the Voiclaim pipeline in parallel for all audio files in the raw directory.
    Each file is processed through all stages, with metrics and error handling.
    Metrics are saved per-file and summary in a single CSV for efficiency.

    Args:
        max_workers (int): Number of parallel workers.
        api_url (str, optional): API endpoint for claim integration.
        day (int): Offset in days for selecting audio files.

    Returns:
        dict: Pipeline metrics summary.
    """
    logger.info("\n🚀 Voiclaim Pipeline (Parallel Mode) Started")
    ensure_dirs(
        PROCESSING_DIR,
        PROCESSED_DIR,
        FAILED_DIR,
        CLEANED_AUDIO_DIR,
        TRANSCRIPTS_DIR,
        EXTRACTED_CLAIMS_DIR,
        LOG_DIR,
    )

    metrics = {
        "processed": 0,
        "success": 0,
        "failed": 0,
        "timings": {"total_time_sec": 0.0, "total_time_min": 0.0, "stage_times": {}},
        "per_file": {},
    }

    # 1) Load processed base_names once at startup
    processed_base_names = load_processed_files_index(PROCESSED_INDEX_FILE)

    # 2) Get all candidate audio files
    # Use day parameter if provided, otherwise use DAY_OFFSET from config
    day_to_use = day if day is not None else config.get("day_offset", 1)
    target_date = (datetime.today() - timedelta(days=day_to_use)).date()
    report_metrics.set_file_processed_date(target_date.strftime("%Y-%m-%d"))
    audio_files = get_all_audio_files(day_offset=day_to_use)
    logger.info(f"🎧 Found {len(audio_files)} audio file(s) to process for date: {target_date}.")
    if not audio_files:
        logger.warning("⚠️ No audio files found in the input directory. Exiting pipeline.")
        report_metrics.record_pipeline_run(0, 0, 0, 0.0)
        return metrics

    start_all = time.time()

    # 3) Filter out files whose base_name is already in the processed index
    filtered_files = []
    for f in audio_files:
        base_name = Path(f).stem
        if base_name in processed_base_names:
            logger.info("Skipping already processed file: %s", base_name)
            continue
        filtered_files.append(f)

    audio_files = filtered_files

    if not audio_files:
        logger.info("No new audio files to process after filtering.")
        report_metrics.record_pipeline_run(0, 0, 0, 0.0)
        return metrics

    # Process files in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {
            executor.submit(process_single_file_with_metrics, audio_file, api_url): audio_file
            for audio_file in audio_files
        }

        for future in as_completed(future_to_file):
            audio_file = future_to_file[future]
            file_name = os.path.basename(audio_file)
            file_id = Path(audio_file).stem
            start_time = time.time()
            try:
                success, elapsed, per_stage, processed_result = future.result()
            except Exception as e:
                logger.error(f"❌ Exception processing {file_name}: {e}", exc_info=True)
                success = False
                elapsed = None
                per_stage = {"error": str(e)}

            # Mark successful file as processed (update in-memory set + index file)
            if success:
                base_name = Path(audio_file).stem
                if base_name not in processed_base_names:
                    processed_base_names.add(base_name)
                    append_processed_file_index(base_name, PROCESSED_INDEX_FILE)

            # Update metrics dict
            with metrics_lock:
                metrics["processed"] += 1
                metrics["success"] += int(success)
                metrics["failed"] += int(not success)
                metrics["per_file"][file_name] = {
                    "success": success,
                    "elapsed_sec": elapsed,
                    **per_stage,
                }

            # Save per-file analytical row
            file_metrics = {
                "timestamp": datetime.utcnow().isoformat(),
                "file_id": file_id,
                "file_name": file_name,
                "stages": per_stage,
                "total_elapsed_sec": elapsed,
                "overall_success": success,
                "status": "success" if success else "failed",
                "errors": [] if success else ["Stage failed"],
            }
            save_analytical_metrics(METRICS_DIR, file_metrics, per_file=True)

        # Final timing calculations
    total_time_sec = time.time() - start_all
    metrics["timings"]["total_time_sec"] = total_time_sec
    metrics["timings"]["total_time_min"] = total_time_sec / 60
    metrics["timings"]["stage_times"] = stage_timings.copy()

    # Save overall summary row
    summary_row = {
        "timestamp": datetime.utcnow().isoformat(),
        "file_name": "TOTAL",
        "processed": metrics["processed"],
        "success": metrics["success"],
        "failed": metrics["failed"],
        "total_time_sec": total_time_sec,
        "total_time_min": total_time_sec / 60,
        "avg_stage_times": stage_timings,
    }
    save_analytical_metrics(METRICS_DIR, summary_row, per_file=False)

    # Use wall-clock time for report (pipeline run duration), not sum of per-file times
    report_metrics.record_pipeline_run(
        metrics["processed"],
        metrics["success"],
        metrics["failed"],
        total_time_sec,
    )

    logger.info(
        f"✅ Pipeline completed. Processed: {metrics['processed']}, "
        f"Success: {metrics['success']}, Failed: {metrics['failed']}, "
        f"Total time: {metrics['timings']['total_time_min']:.2f} min"
    )
    logger.info(f"📊 Stage timings (min): {metrics['timings']['stage_times']}")

    return metrics


def run_main(
    max_workers: int = None,
    api_url: str = None,
    dry_run: bool = False,
    cleanup_days: int = None,
    day: int = None,
    start_date: Optional[date] = None,
    num_days: int = 1,
) -> None:
    """
    Entry point for running the Voiclaim pipeline with optional parameters.
    Supports processing multiple days either by specifying a start date and number of days,
    or by using the legacy day offset parameter.

    Args:
        max_workers (int, optional): Number of parallel workers for processing.
        api_url (str, optional): API endpoint for claim integration. Falls back to env var if not provided.
        dry_run (bool): If True, skips API integration and only processes files.
        cleanup_days (int, optional): Number of days to keep files before cleanup. If None, no cleanup is performed.
        day (int, optional): [DEPRECATED] Day offset to filter input files. Used only if start_date is not provided.
        start_date (date, optional): Start date for processing (YYYY-MM-DD). If provided, processes from this date.
        num_days (int): Number of days to process starting from start_date (default: 1).
    """
    # Step 1: Optional cleanup
    # Use cleanup_days parameter if provided, otherwise use cleanup_days from config
    cleanup_days_to_use = cleanup_days if cleanup_days is not None else config.get("cleanup_days", 20)
    if cleanup_days_to_use is not None and cleanup_days_to_use > 0:
        logger.info(f"🧹 Cleaning up files older than {cleanup_days_to_use} day(s)...")
        try:
            cleanup_old_files(days=cleanup_days_to_use)
            logger.info("✅ Cleanup complete.")
        except Exception as e:
            logger.warning(f"⚠️ Cleanup failed: {e}", exc_info=True)
    elif cleanup_days_to_use == 0:
        logger.info("⏭️ Skipping cleanup (cleanup_days=0)")

    # Step 2: Handle dry run mode
    if dry_run:
        logger.info("🧪 DRY RUN MODE: API integration is disabled.")
        api_url = None
    else:
        # Step 3: Resolve API URL
        if not api_url:
            api_url = os.getenv("POST_PROCESS_URL")
        if not api_url:
            logger.error(
                "❌ API URL is missing. Set 'POST_PROCESS_URL' in the environment or pass it as a parameter."
            )
            raise EnvironmentError("API URL missing")

    # Step 4: Run the main pipeline
    # Use day parameter if provided, otherwise use DAY_OFFSET from config
    day_to_use = day if day is not None else config.get("day_offset", 1)
    try:
        run_pipeline_parallel(max_workers=max_workers or 1, api_url=api_url, day=day_to_use)
        logger.info("🏁 Pipeline execution finished.")
    except Exception as e:
        logger.critical(f"🔥 Pipeline execution failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    # Run the main pipeline function
    # Use day_offset and cleanup_days from config if available
    day_from_config = config.get("day_offset", 1)
    cleanup_days_from_config = config.get("cleanup_days", 20)
    run_main(
        max_workers=PIPE_PARAMS["max_workers"], api_url=None, dry_run=False, cleanup_days=cleanup_days_from_config, day=day_from_config
    )
