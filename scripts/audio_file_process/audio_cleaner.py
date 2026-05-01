"""
audio_cleaner.py

This script is part of the Voiclaim pipeline and is responsible for cleaning and preprocessing raw audio files.
It performs the following operations:
    - Loads and converts various audio formats to WAV.
    - Removes silence from the audio to focus on speech segments.
    - Applies noise reduction to improve audio quality.
    - Saves cleaned audio files in WAV format.
    - Logs processing metadata and errors for each file.

Use Cases:
    - Preprocessing audio files for downstream speech-to-text (STT) or machine learning tasks.
    - Cleaning up user-uploaded or field-recorded audio for insurance claim processing.
    - Batch processing large datasets of audio files for research or production pipelines.

Typical Usage:
    1. As a standalone script:
        $ python audio_cleaner.py
       This will process all supported audio files in the configured raw audio directory.

    2. As a module in a larger pipeline:
        from audio_cleaner import batch_process_audio
        summary = batch_process_audio(files=['call1.mp3', 'call2.wav'])

    3. For auditing and debugging:
        - Per-file JSON reports and error logs are saved for traceability.
        - Metrics are recorded for monitoring pipeline performance.

Dependencies:
    - Python 3.10+
    - pydub
    - numpy
    - scipy
    - noisereduce
    - ffmpeg (must be installed and accessible)

Author: Akash GR
Created On: 2025-06-23
Last Modified: 2025-06-23
"""

# =========================
# Imports
# =========================
import os
# import json  # Unused import removed
import sys
import time
import traceback
from datetime import datetime
from typing import List, Optional

from pydub import AudioSegment, silence  # Audio processing
import numpy as np  # For array operations
from scipy.io import wavfile  # For reading/writing wav files
import noisereduce as nr  # For noise reduction
from scipy.io.wavfile import write  # For saving wav files

from utils.analytics import save_metrics
from utils.config_loader import load_pipeline_config  # Loads pipeline config from YAML
from pydantic import BaseModel, Field, ValidationError  # For data validation
from utils.logging_utils import get_logger  # Custom logger
from utils.util_master import get_project_path  # Utility for path resolution

# =========================
# Configuration Loading
# =========================
config = load_pipeline_config()  # Load all pipeline config from YAML file
paths = config.get("paths", {})
stt = config.get("stt", {})
audio_cleaner_cfg = config.get("audio_cleaner", {})

logger = get_logger("audio_cleaner")  # Logger for this script

# === CONFIGURATION ===
# All paths are loaded from config for flexibility and security
RAW_AUDIO_DIR = get_project_path(paths["raw_audio_dir"])
CLEANED_AUDIO_DIR = get_project_path(paths["cleaned_audio_dir"])
TRANSCRIPTS_DIR = get_project_path(paths["transcripts_dir"])
EXTRACTED_CLAIMS_DIR = get_project_path(paths["extracted_claims_dir"])
LOG_DIR = get_project_path(paths["log_dir"])
METRICS_DIR = get_project_path(paths["metrics_dir"])

# Supported audio formats (from config)
AUDIO_SUPPORTED_FORMATS = (
    tuple(stt["supported_formats"])
    if isinstance(stt["supported_formats"], list)
    else stt["supported_formats"]
)

# Audio cleaning parameters with defaults and config override
MIN_SILENCE_LEN_MS = audio_cleaner_cfg.get(
    "min_silence_len_ms", 1000
)  # Minimum silence to split (ms)
SILENCE_THRESH_DBFS = audio_cleaner_cfg.get("silence_thresh_dbfs", -12)  # Silence threshold (dBFS)
KEEP_SILENCE_MS = audio_cleaner_cfg.get("keep_silence_ms", 800)  # How much silence to keep (ms)
NOISE_REDUCTION_PARAMS = audio_cleaner_cfg.get(
    "noise_reduction_params", {"prop_decrease": 0.85, "stationary": "false"}
)  # Noise reduction params

# Timeout per file in seconds (None or 0 means no timeout)
FILE_PROCESSING_TIMEOUT_SEC = audio_cleaner_cfg.get("file_processing_timeout_sec", None)

METRICS_FILE = os.path.join(METRICS_DIR, f"stt_metrics_{datetime.now().strftime('%Y%m%d')}.csv")
os.makedirs(METRICS_DIR, exist_ok=True)


class AudioFileInfo(BaseModel):
    """
    Data model for storing information about each processed audio file.

    Attributes:
        file_name (str): Name of the processed audio file.
        original_duration_sec (Optional[float]): Duration of the original audio in seconds.
        cleaned_duration_sec (Optional[float]): Duration after silence removal in seconds.
        status (str): Processing status ("success" or "error").
        errors (List[str]): List of error messages encountered during processing.
        timestamp (Optional[str]): ISO timestamp of when processing occurred.
        final_output (Optional[str]): Path to the final cleaned audio file.

    Use Cases:
        - Used for generating per-file JSON reports.
        - Enables traceability and debugging of audio processing steps.
    """

    file_name: str
    original_duration_sec: Optional[float] = None
    cleaned_duration_sec: Optional[float] = None
    status: str = "success"
    errors: List[str] = Field(default_factory=list)
    timestamp: Optional[str] = None  # Added for report traceability
    final_output: Optional[str] = None  # Path to final cleaned file


def process_audio_file(audio_path: str) -> dict:
    """
    Processes a single audio file by performing the following steps:
        1. Loads the audio file (supports multiple formats).
        2. Converts it to WAV format for processing.
        3. Removes silence based on configurable thresholds.
        4. Applies noise reduction to enhance audio quality.
        5. Saves the cleaned audio file and logs metadata/errors.

    Args:
        audio_path (str): Path to the input audio file.

    Returns:
        dict: Serializable processing report (fields mirror AudioFileInfo),
            including status, durations, errors, timestamps, and final output path.

    Raises:
        RuntimeError: If any step in the processing pipeline fails.

    Use Cases:
        - Called by batch_process_audio for each file in a dataset.
        - Can be used independently for single-file cleaning and validation.
    Notes:
        - Silence threshold is computed as audio.dBFS + SILENCE_THRESH_DBFS (relative offset).
          Ensure configuration values are tuned per environment.
    """
    basename = os.path.basename(audio_path)
    file_info = AudioFileInfo(
        file_name=basename, timestamp=datetime.utcnow().isoformat(), status="success", errors=[]
    )

    temp_wav_path = None
    clean_wav_path = None

    try:
        # Step 1: Load audio
        try:
            audio = AudioSegment.from_file(audio_path)
            if audio.channels > 1:
                audio = audio.set_channels(1)  # convert stereo/multi to mono
            file_info.original_duration_sec = round(len(audio) / 1000, 2)
        except Exception as e:
            raise RuntimeError(f"Failed to load audio file: {e}")

        # Step 2: Export to temp WAV for further processing
        temp_wav_path = os.path.join(CLEANED_AUDIO_DIR, f"{os.path.splitext(basename)[0]}_temp.wav")
        try:
            audio.export(temp_wav_path, format="wav")
        except Exception as e:
            raise RuntimeError(f"Failed to export temp WAV: {e}")

        # Step 3: Remove silence
        try:
            silence_threshold = audio.dBFS + SILENCE_THRESH_DBFS
            non_silent_chunks = silence.split_on_silence(
                audio,
                min_silence_len=MIN_SILENCE_LEN_MS,
                silence_thresh=silence_threshold,
                keep_silence=KEEP_SILENCE_MS,
            )
            if not non_silent_chunks:
                logger.warning(f"No non-silent audio found in {basename}, output will be empty.")
            clean_audio = AudioSegment.empty()
            for chunk in non_silent_chunks:
                clean_audio += chunk
            file_info.cleaned_duration_sec = round(len(clean_audio) / 1000, 2)
            logger.info(
                f"Silence removal params: min_silence_len_ms={MIN_SILENCE_LEN_MS}, "
                f"silence_thresh_dbfs={SILENCE_THRESH_DBFS}, keep_silence_ms={KEEP_SILENCE_MS}, "
                f"effective_thresh={silence_threshold:.2f}, chunks={len(non_silent_chunks)}"
            )
        except Exception as e:
            raise RuntimeError(f"Silence removal failed: {e}")

        # Step 4: Noise reduction requires WAV file
        clean_wav_path = os.path.join(
            CLEANED_AUDIO_DIR, f"{os.path.splitext(basename)[0]}_cleaned.wav"
        )
        try:
            clean_audio.export(clean_wav_path, format="wav")
        except Exception as e:
            raise RuntimeError(f"Failed to export cleaned WAV: {e}")

        try:
            rate, data = wavfile.read(clean_wav_path)
            if len(data.shape) > 1:
                data = data.mean(axis=1).astype(np.int16)  # stereo to mono
        except Exception as e:
            raise RuntimeError(f"Failed to read cleaned WAV: {e}")

        try:
            # Convert to float32 in [-1, 1] for noise reduction, then convert back to int16
            float_data = (data.astype(np.float32) / 32768.0).clip(-1.0, 1.0)
            reduced_noise_float = nr.reduce_noise(y=float_data, sr=rate, **NOISE_REDUCTION_PARAMS)
            reduced_noise = (np.clip(reduced_noise_float, -1.0, 1.0) * 32767.0).astype(np.int16)
        except Exception as e:
            raise RuntimeError(f"Noise reduction failed: {e}")

        # Step 5: Save final cleaned audio
        final_path = os.path.join(CLEANED_AUDIO_DIR, f"{os.path.splitext(basename)[0]}.wav")
        try:
            write(final_path, rate, reduced_noise.astype(np.int16))
            file_info.final_output = final_path
            logger.info(f"✅ Cleaned file saved: {final_path}")
        except Exception as e:
            raise RuntimeError(f"Failed to save final cleaned audio: {e}")

    except Exception as e:
        file_info.status = "error"
        file_info.errors.append(str(e))
        error_log_path = os.path.join(LOG_DIR, f"{os.path.splitext(basename)[0]}_error.txt")
        with open(error_log_path, "w", encoding="utf-8") as ef:
            ef.write(traceback.format_exc())
        logger.error(
            f"❌ Error processing {basename}: {e}. Details logged in {error_log_path}",
            exc_info=True,
        )

    finally:
        # Cleanup temp files
        for f in [temp_wav_path, clean_wav_path]:
            if f is not None:
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except Exception as cleanup_err:
                    logger.warning(f"Failed to cleanup temp file {f}: {cleanup_err}")
        # Save JSON report
        try:
            file_info_dict = file_info.model_dump()
            AudioFileInfo(**file_info_dict)  # Validate before saving
        except ValidationError as ve:
            logger.error(f"Validation error when saving report for {basename}: {ve}", exc_info=True)
            file_info_dict = file_info.dict()
        except Exception as e:
            logger.error(f"Failed to save report for {basename}: {e}", exc_info=True)
            file_info_dict = file_info.dict()
    return file_info_dict


def batch_process_audio(files: List[str]) -> dict:
    """
    Batch processes a list of audio files, cleaning and saving results sequentially.

    Args:
        files (list[str]): List of audio file names (not full paths) to process
            from RAW_AUDIO_DIR.

    Returns:
        dict: Summary containing:
            - success (list[str]): successfully processed file names
            - failed (list[str]): failed file names
            - total_files (int): number of input files
            - time_sec (float): total elapsed seconds

    Use Cases:
        - Main entry point for pipeline integration or standalone batch cleaning.
        - Can be used for parallel or distributed processing with minor modifications.
        - Logs all actions and errors for monitoring and debugging.
    """

    if not files:
        logger.warning("No files to process.")
        return {"success": [], "failed": [], "total_files": 0, "time_sec": 0}

    logger.info(f"🎧 Starting batch processing of {len(files)} audio files sequentially.")

    success_files = []
    failed_files = []
    start_time = time.time()

    for filename in files:
        full_path = os.path.join(RAW_AUDIO_DIR, filename)
        logger.info(f"🔄 Processing: {filename}")
        try:
            processed_info = process_audio_file(full_path)
            success_files.append(filename)
            try:
                save_metrics(METRICS_DIR, processed_info, "audio_cleaner.csv")
            except Exception as metric_err:
                logger.error(f"Failed to save metrics for {filename}: {metric_err}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected error processing {filename}: {e}", exc_info=True)
            failed_files.append(filename)

    total_time = time.time() - start_time
    logger.info(
        f"Batch complete. Success: {len(success_files)}, Failed: {len(failed_files)}, Time: {total_time:.2f}s"
    )

    if failed_files:
        logger.warning(f"Failed files: {failed_files}")

    return {
        "success": success_files,
        "failed": failed_files,
        "total_files": len(files),
        "time_sec": total_time,
    }


if __name__ == "__main__":
    """
    Script entry point.

    Loads configuration, validates input directories, and processes all supported audio files found in the raw audio directory.
    Logs a summary of the batch processing results.

    Use Cases:
        - Run directly to clean all new raw audio files as part of a scheduled job or manual invocation.
        - Ensures all necessary directories exist before processing.
        - Provides clear logging for operational monitoring.
    """

    if not os.path.isdir(RAW_AUDIO_DIR):
        logger.error(
            f"Raw audio directory does not exist: {RAW_AUDIO_DIR}. Please check your pipeline config for 'raw_audio_dir'."
        )
        sys.exit(1)

    os.makedirs(CLEANED_AUDIO_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(METRICS_DIR, exist_ok=True)

    files = [f for f in os.listdir(RAW_AUDIO_DIR) if f.lower().endswith(AUDIO_SUPPORTED_FORMATS)]
    if not files:
        logger.warning(f"No supported audio files found in {RAW_AUDIO_DIR}. Nothing to process.")
        sys.exit(0)

    summary = batch_process_audio(files=files)
    logger.info(f"Batch processing summary: {summary}")
