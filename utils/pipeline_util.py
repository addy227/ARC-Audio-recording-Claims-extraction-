import glob
import os
import shutil
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any

from utils.logging_utils import get_logger
from utils.util_master import get_project_path, paths
from utils.config_loader import load_pipeline_config

stage_timings = {"cleaning": 0, "transcription": 0, "extraction": 0, "integration": 0}

logger = get_logger(__name__)

# Load file filtering configuration
config = load_pipeline_config()
file_filter_config = config.get("file_filtering", {})
exclude_prefix = file_filter_config.get("exclude_prefix", "13")  # Default to "13" if not configured
allowed_extensions = tuple(file_filter_config.get("allowed_extensions", [".wav", ".mp3", ".flac", ".m4a", ".gsm"]))  # File extensions to process
allowed_codes = file_filter_config.get("allowed_codes", [])  # List of allowed codes for parts[1]
DAY_OFFSET = config.get("day_offset", 1)  # Default to 1 (yesterday) if not configured
# Load configuration and validate required paths
required_paths = [
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
missing_paths = [p for p in required_paths if p not in paths]
if missing_paths:
    raise KeyError(f"Missing required path configs: {missing_paths}")

# Directory path constants
TRANSCRIPTS_DIR = get_project_path(paths["transcripts_dir"])
EXTRACTED_CLAIMS_DIR = get_project_path(paths["extracted_claims_dir"])
LOG_DIR = get_project_path(paths["log_dir"])
CLEANED_AUDIO_DIR = get_project_path(paths["cleaned_audio_dir"])
RAW_AUDIO_DIR = get_project_path(paths["raw_audio_dir"])
METRICS_DIR = get_project_path(paths["metrics_dir"])
PROCESSING_DIR = get_project_path(paths["processing_dir"])
PROCESSED_DIR = get_project_path(paths["processed_dir"])
FAILED_DIR = get_project_path(paths["failed_dir"])
SUCCESS_DIR = get_project_path(paths["success_dir"])
ARCHIVE_DIR = get_project_path(paths["archive_dir"])


# ==============Comment the below lines and use the above lines to get paths from config==========

# +============================================================================================


def timed_stage(stage: str, func, *args, **kwargs):
    """Time a pipeline stage and accumulate its duration."""
    t0 = time.time()
    result = func(*args, **kwargs)
    elapsed = time.time() - t0
    stage_timings[stage] += elapsed
    return result, elapsed


def process_stage(stage_name, func, *args, **kwargs):
    """Run and time a single pipeline stage, and return result + elapsed time."""
    result, elapsed = timed_stage(stage_name, func, *args, **kwargs)
    return result, elapsed


def archive_processed_files(
    file_id: str,
    audio_path: Path,
    transcript_path: Path,
    all_claims_json: Path,
    single_claims_dir: Path,
    archive_root: str = None,
) -> None:
    """
    Move processed files (audio, transcript, all claims JSON, individual claims folder)
    into an archive directory under a folder named with the file_id for future reference.

    Args:
        file_id (str): Unique ID for the file processing.
        audio_path (Path): Path to the audio file.
        transcript_path (Path): Path to the transcript (.txt).
        all_claims_json (Path): Path to the all claims JSON file.
        single_claims_dir (Path): Path to the folder containing individual claim JSON files.
        archive_root (str, optional): Root directory where archives are saved.
            Defaults to a folder named "archive" at project root.
    """
    if archive_root is None:
        # Default archive dir from config
        archive_root = get_project_path(paths["archive_dir"])

    os.makedirs(archive_root, exist_ok=True)

    # Create a unique archive subfolder for this file
    archive_dir = Path(archive_root) / f"{file_id}_{audio_path.stem}"
    os.makedirs(archive_dir, exist_ok=True)

    def safe_move_to_archive(src: Path):
        if src.exists():
            dest = archive_dir / src.name
            # Handle potential name collisions
            counter = 1
            while dest.exists():
                dest = archive_dir / f"{src.stem}_{counter}{src.suffix}"
                counter += 1
            shutil.move(str(src), str(dest))
            logger.info(f"📦 Archived {src} → {dest}")
        else:
            logger.warning(f"⚠️ File/folder to archive does not exist: {src}")

    # Move audio file
    safe_move_to_archive(audio_path)
    # Move transcript file
    safe_move_to_archive(transcript_path)
    # Move all claims json
    safe_move_to_archive(all_claims_json)
    # Move individual claims folder (if it exists)
    if single_claims_dir.exists() and single_claims_dir.is_dir():
        dest_dir = archive_dir / single_claims_dir.name
        # If dest_dir exists, rename to avoid overwrite
        counter = 1
        while dest_dir.exists():
            dest_dir = archive_dir / f"{single_claims_dir.name}_{counter}"
            counter += 1
        shutil.move(str(single_claims_dir), str(dest_dir))
        logger.info(f"📦 Archived folder {single_claims_dir} → {dest_dir}")
    else:
        logger.warning(f"⚠️ Individual claims folder does not exist: {single_claims_dir}")


def retry_stage(stage_name: str, max_attempts: int = 3, delay_sec: int = 5):
    """
    Decorator to retry a function up to max_attempts times with delay_sec between attempts.
    Logs each attempt and failure.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(f"[{stage_name}] Attempt {attempt}")
                    return func(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"[{stage_name}] Attempt {attempt} failed: {e}")
                    if attempt == max_attempts:
                        logger.error(f"[{stage_name}] Failed after {max_attempts} attempts.")
                        raise
                    time.sleep(delay_sec)

        return wrapper

    return decorator


def get_all_audio_files(day_offset: int = None) -> list[str]:
    """Find all valid audio files in a date folder offset by N days from today.

    Args:
        day_offset (int, optional): 0 = today, 1 = yesterday, 2 = day before yesterday, etc.
            If not provided, uses DAY_OFFSET from config file.

    Returns:
        list[str]: List of valid audio file paths.
    """
    # Use provided day_offset or fall back to config value
    if day_offset is None:
        day_offset = DAY_OFFSET
        logger.info(f"Using DAY_OFFSET from config: {day_offset}")
    else:
        logger.info(f"Using provided day_offset: {day_offset}")
    
    files = []
    skipped_count = 0

    # Calculate target date using the day_offset parameter
    target_date = datetime.today() - timedelta(days=day_offset)
    month_folder = target_date.strftime("%B").lower()  # lowercase full month name
    day_folder = target_date.strftime("%d")  # two-digit day

    # Compose folder path
    target_dir = os.path.join(RAW_AUDIO_DIR, month_folder, day_folder)

    # Log human-readable date info
    logger.info(
        "🔍 Looking for audio files for date: %s in folder: %s",
        target_date.strftime("%Y-%m-%d"),
        target_dir,
    )

    if not os.path.exists(target_dir):
        logger.error("❌ Directory does not exist: %s", target_dir)
        return []

    for root, _, filenames in os.walk(target_dir):
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            full_path = os.path.join(root, f)

            # Skip non-audio files
            if ext not in allowed_extensions:
                logger.debug("[SKIP] ❌ Not an audio file: %s", f)
                skipped_count += 1
                continue

            # Skip empty or 0 KB files
            if os.path.getsize(full_path) <= 0:
                logger.debug("[SKIP] ⚠ Empty file (0 KB): %s", f)
                skipped_count += 1
                continue

            # Check filename parts: exclude files where second part starts with configured prefix
            parts = f.split("_")
            if len(parts) > 1:
                # Exclude if parts[1] starts with exclude_prefix (only if exclude_prefix is not empty)
                if exclude_prefix and parts[1].startswith(exclude_prefix):
                    logger.debug("[SKIP] 🚫 File filtered due to name rule (starts with %s): %s", exclude_prefix, f)
                    skipped_count += 1
                    continue

                # If allowed_codes is configured, only process files where parts[1] exactly matches one of them
                if allowed_codes:
                    if parts[1] in allowed_codes:
                        # File matches allowed_codes - capture and include
                        logger.info("[PROCESS] ✅ File matched allowed_code '%s': %s", parts[1], f)
                        files.append(full_path)
                    else:
                        # File does not match any allowed_code - skip
                        logger.debug("[SKIP] 🚫 File filtered - parts[1] (%s) NOT in allowed_codes: %s",
                                     parts[1], f)
                        skipped_count += 1
                    continue  # Move to next file after allowed_codes check

            # If no underscore or allowed_codes not configured, include all files (except excluded by exclude_prefix)
            if not allowed_codes:
                logger.debug("[OK] ✅ Included: %s", f)
                files.append(full_path)

    logger.info("Found %d files; skipped %d", len(files), skipped_count)
    if allowed_codes:
        logger.info("📋 Processing files with allowed_codes: %s", allowed_codes)
        logger.info("📁 Total files matching allowed_codes: %d", len(files))

    return files


def safe_move(src: str, dest_dir: str) -> str:
    """
    Move file or directory to dest_dir with a unique name if a collision occurs.
    Returns the new file/directory path.
    """
    if not os.path.exists(src):
        logger.error(f"safe_move: Source path '{src}' does not exist.")
        raise FileNotFoundError(f"Source path '{src}' does not exist.")

    is_dir = os.path.isdir(src)
    os.makedirs(dest_dir, exist_ok=True)
    base_name = os.path.basename(src)
    dest_path = os.path.join(dest_dir, base_name)
    logger.info(f"safe_move: Moving {'directory' if is_dir else 'file'} '{src}' to '{dest_path}'")

    try:
        if not os.path.exists(dest_path):
            shutil.move(src, dest_path)
            logger.info(f"Moved '{src}' to '{dest_path}'")
            return dest_path

        name, ext = os.path.splitext(base_name)
        counter = 1
        while True:
            new_name = f"{name}_{counter}{ext}"
            new_dest = os.path.join(dest_dir, new_name)
            if not os.path.exists(new_dest):
                shutil.move(src, new_dest)
                logger.info(f"Moved '{src}' to '{new_dest}' (renamed to avoid collision)")
                return new_dest
            counter += 1
    except Exception as e:
        logger.error(f"Failed to move '{src}' to '{dest_dir}': {e}", exc_info=True)
        raise


def move_entire_audio_package(
    base_name: str, claims_extracted_foldername: str, json_files: list[str], success: bool
):
    """
    Move package files to SUCCESS_DIR or FAILED_DIR:
    - MP3/raw audio
    - Cleaned WAV
    - Transcript TXT
    - api_integration_status.json
    - Claims JSON and related assets (all directly inside base_name folder, no subfolders)
    """
    dest_root = Path(SUCCESS_DIR) if success else Path(FAILED_DIR)
    dest_folder = dest_root / base_name
    dest_folder.mkdir(parents=True, exist_ok=True)

    # Move raw audio (any extension)
    for raw_audio_path in Path(PROCESSING_DIR).glob(f"{base_name}.*"):
        safe_move(str(raw_audio_path), str(dest_folder))

    # Move cleaned audio
    cleaned_audio_path = Path(CLEANED_AUDIO_DIR) / f"{base_name}.wav"
    if cleaned_audio_path.exists():
        safe_move(str(cleaned_audio_path), str(dest_folder))

    # Move transcript
    transcript_path = Path(TRANSCRIPTS_DIR) / f"{base_name}.txt"
    if transcript_path.exists():
        safe_move(str(transcript_path), str(dest_folder))

    # Move claims JSON
    claims_json_path = Path(EXTRACTED_CLAIMS_DIR) / f"{base_name}_claims.json"
    if claims_json_path.exists():
        safe_move(str(claims_json_path), str(dest_folder))

    # Move metadata
    metadata_path = (
        Path(EXTRACTED_CLAIMS_DIR) / claims_extracted_foldername / "api_integration_status.json"
    )
    if metadata_path.exists():
        safe_move(str(metadata_path), str(dest_folder))

    claims_dest_folder = dest_folder / "claims"
    claims_dest_folder.mkdir(exist_ok=True)

    # Move claim JSON files directly into dest_folder (no subfolder)
    for json_path_str in json_files:
        json_path = Path(json_path_str)
        if json_path.exists():
            safe_move(str(json_path), str(claims_dest_folder))

    # Copy other related claim assets directly into dest_folder (no subfolder)
    src_claims_folder = Path(EXTRACTED_CLAIMS_DIR) / claims_extracted_foldername
    if src_claims_folder.exists():
        for item in src_claims_folder.iterdir():
            if item.suffix.lower() not in [".json"]:  # Skip unrelated JSONs
                try:
                    if item.is_file():
                        shutil.copy2(item, claims_dest_folder / item.name)
                    elif item.is_dir():
                        shutil.copytree(item, claims_dest_folder / item.name, dirs_exist_ok=True)
                except Exception as e:
                    logger.warning(f"[COPY] Failed to copy {item} to {claims_dest_folder}: {e}")

        # After moving/copying, preserve the folder structure even if empty
        # Do NOT remove empty folders - this preserves organization in extracted_claims
        # The folder structure is maintained for tracking and reference purposes
        if not any(src_claims_folder.iterdir()):  # folder is empty
            logger.debug(f"Preserving empty folder structure: {src_claims_folder}")


def archive_audio_package_with_originals(
    base_name: str, claims_extracted_foldername: str, json_files: list[str], success: bool
):
    """
    Archive a package (success or failed) with:
    - MP3
    - Transcript TXT
    - api_integration_status.json
    - Relevant claim JSON files
    """

    subfolder = "success" if success else "failed"
    archive_root = Path(ARCHIVE_DIR) / subfolder
    archive_folder = archive_root / base_name
    archive_folder.mkdir(parents=True, exist_ok=True)

    # --- Copy original audio ---
    for raw_audio_path in Path(PROCESSING_DIR).glob(f"{base_name}.*"):
        if raw_audio_path.is_file():
            shutil.copy2(raw_audio_path, archive_folder / raw_audio_path.name)

    # --- Copy cleaned WAV (if exists) ---
    cleaned_audio_path = Path(CLEANED_AUDIO_DIR) / f"{base_name}.wav"
    if cleaned_audio_path.exists():
        shutil.copy2(cleaned_audio_path, archive_folder / cleaned_audio_path.name)

    # --- Copy transcript ---
    transcript_path = Path(TRANSCRIPTS_DIR) / f"{base_name}.txt"
    if transcript_path.exists():
        shutil.copy2(transcript_path, archive_folder / transcript_path.name)

    # --- Copy claims json ---
    claims_json_path = Path(EXTRACTED_CLAIMS_DIR) / f"{base_name}_claims.json"
    if claims_json_path.exists():
        shutil.copy2(claims_json_path, archive_folder / claims_json_path.name)

    # --- Copy metadata ---
    metadata_path = (
        Path(EXTRACTED_CLAIMS_DIR) / claims_extracted_foldername / "api_integration_status.json"
    )
    if metadata_path.exists():
        shutil.copy2(metadata_path, archive_folder / metadata_path.name)

    # --- Copy only relevant JSON claims ---
    claims_dest_folder = archive_folder / "claims"
    claims_dest_folder.mkdir(exist_ok=True)
    for json_path_str in json_files:
        json_path = Path(json_path_str)
        if json_path.exists():
            shutil.move(
                str(json_path), str(claims_dest_folder / json_path.name)
            )  # Move instead of copy

    logger.info(
        f"[ARCHIVE] Archived {'success' if success else 'failed'} package to {archive_folder}"
    )


def cleanup_old_files(days: int) -> None:
    """
    Delete files based on the 'days' parameter:
    - days == 0: delete files modified today.
    - days == 1: delete files modified yesterday.
    - days > 1: delete files older than 'days' days.

    Note: For EXTRACTED_CLAIMS_DIR, only JSON files are deleted (not folders).
    Folder structure is preserved to maintain organization.
    """
    now = datetime.now()

    if days == 0:
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
        date_desc = f"today ({start_date.date()})"
    elif days == 1:
        start_date = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
        date_desc = f"yesterday ({start_date.date()})"
    else:
        cutoff_date = (now - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        date_desc = f"before {cutoff_date.date()}"

    logger.info(f"🧹 Starting cleanup for files modified {date_desc}")

    targets = [
        (CLEANED_AUDIO_DIR, ("*.wav", "*.mp3", "*.flac", "*.m4a")),
        (TRANSCRIPTS_DIR, ("*.txt",)),
        (EXTRACTED_CLAIMS_DIR, ("*.json",)),  # Only JSON files deleted, folders preserved
    ]

    for dir_path, patterns in targets:
        for pattern in patterns:
            files = glob.glob(os.path.join(dir_path, pattern))
            for file_path in files:
                try:
                    if os.path.isfile(file_path):
                        mtime = datetime.fromtimestamp(os.path.getmtime(file_path))

                        if days in (0, 1):
                            if start_date <= mtime < end_date:
                                os.remove(file_path)
                                logger.info(f"🧹 Deleted {date_desc} file: {file_path}")
                        else:
                            if mtime < cutoff_date:
                                os.remove(file_path)
                                logger.info(
                                    f"🧹 Deleted file older than {cutoff_date.date()}: {file_path}"
                                )

                except Exception as e:
                    logger.warning(f"Failed to delete {file_path}: {e}")

        # 🔥 Additional step: delete folders inside EXTRACTED_CLAIMS_DIR
        # MODIFIED: Only delete old JSON files, NOT folders (preserve folder structure)
        # Folders in extracted_claims are preserved to maintain organization
        if dir_path == EXTRACTED_CLAIMS_DIR:
            # Skip folder deletion - only JSON files are deleted above (lines 456-476)
            # This preserves the folder structure even for old files
            logger.debug(f"Skipping folder deletion in {EXTRACTED_CLAIMS_DIR} - preserving folder structure")
            pass


def compute_audio_file_id(audio_file_storage_id: Any, filename: str) -> str:
    """Return a stable AudioFileId given storage id or fallback to filename stem."""
    try:
        if audio_file_storage_id:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, str(audio_file_storage_id)))
    except Exception:
        pass
    return Path(filename).stem
