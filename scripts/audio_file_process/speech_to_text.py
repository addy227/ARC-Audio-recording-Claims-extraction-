"""
Script Name: speech_to_text.py
Description:
    Processes cleaned audio files and transcribes them into text using OpenAI's Whisper model.
    Each audio file is transcribed and saved as both plain text (.txt) and structured JSON (.json).

Pipeline Stage:
    Stage 2 - Speech-to-Text (STT)
Usage:
    Run standalone:
        python speech_to_text.py
    Or import:
        from speech_to_text import transcribe_folder
        transcribe_folder(input_dir, output_dir)

Dependencies:
    - Python 3.10+
    - openai-whisper
    - pydub
    - scipy
    - noisereduce
    - ffmpeg (system dependency)
"""

import csv
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Tuple

import numpy as np
import torchaudio
import torch
import whisper

from utils.analytics import save_metrics
from utils import report_metrics

# from faster_whisper import WhisperModel  # Uncomment if needed

from utils.config_loader import load_pipeline_config
from utils.logging_utils import get_logger
from utils.util_master import get_project_path

# =======================
# CONFIGURATION
# =======================
config = load_pipeline_config()
paths = config["paths"]
stt = config.get("stt", {})

CLEANED_AUDIO_DIR = get_project_path(paths["cleaned_audio_dir"])
TRANSCRIPTS_DIR = get_project_path(paths["transcripts_dir"])

LOG_DIR = get_project_path(paths["log_dir"])
# TRANSCRIPTS_DIR = "/CosmosAI/voicedata/local_data_source/transcripts/"
# CLEANED_AUDIO_DIR = "/CosmosAI/voicedata/local_data_source/cleaned_audio/"

STT_AUDIO_SUPPORTED_FORMAT: Tuple[str, ...] = (
    tuple(stt["supported_format_stt"])
    if isinstance(stt["supported_format_stt"], list)
    else (stt["supported_format_stt"],)
)

MODEL_SIZE = stt.get("model_size", "base")

# Ensure we always work with Path objects
METRICS_DIR = Path(get_project_path(paths["metrics_dir"]))
METRICS_DIR.mkdir(parents=True, exist_ok=True)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

logger = get_logger("speech_to_text")
logger.info(f"⚙️ Using device: {DEVICE.upper()}")
logger.info(
    f"CUDA available: {torch.cuda.is_available()}, CUDA version: {torch.version.cuda or 'N/A'}"
)


# =======================
# GPU MEMORY MONITORING
# =======================
def get_gpu_memory_info(device: str = "cuda") -> Dict[str, Any]:
    """
    Get GPU memory usage information.
    
    Args:
        device: Device to check (default: "cuda")
    
    Returns:
        Dictionary with GPU memory stats or None if CUDA not available
    """
    if not torch.cuda.is_available() or device != "cuda":
        return None
    
    try:
        # Get memory stats for the default GPU (device 0)
        allocated = torch.cuda.memory_allocated(0) / (1024**3)  # GB
        reserved = torch.cuda.memory_reserved(0) / (1024**3)  # GB
        max_allocated = torch.cuda.max_memory_allocated(0) / (1024**3)  # GB
        total_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)  # GB
        
        return {
            "allocated_gb": round(allocated, 2),
            "reserved_gb": round(reserved, 2),
            "max_allocated_gb": round(max_allocated, 2),
            "total_gb": round(total_memory, 2),
            "free_gb": round(total_memory - reserved, 2),
            "utilization_pct": round((reserved / total_memory) * 100, 1) if total_memory > 0 else 0
        }
    except Exception as e:
        logger.warning(f"Failed to get GPU memory info: {e}")
        return None

def log_gpu_memory(context: str, device: str = "cuda"):
    """Log GPU memory usage with a context label."""
    mem_info = get_gpu_memory_info(device)
    if mem_info:
        logger.info(
            f"💾 [Whisper] GPU Memory [{context}]: "
            f"Allocated={mem_info['allocated_gb']}GB, "
            f"Reserved={mem_info['reserved_gb']}GB, "
            f"Free={mem_info['free_gb']}GB, "
            f"Utilization={mem_info['utilization_pct']}%, "
            f"Max_Allocated={mem_info['max_allocated_gb']}GB"
        )
    else:
        logger.debug(f"💾 [Whisper] GPU Memory [{context}]: Not available (CPU mode)")


# =======================
# AUDIO LOADING
# =======================
def load_audio_for_whisper(path: str, min_duration_sec: float = 1.0) -> np.ndarray:
    """
    Load and preprocess audio for Whisper transcription.

    Args:
        path: Path to audio file.
        min_duration_sec: Minimum duration in seconds.

    Returns:
        Normalized waveform (numpy array).

    Raises:
        ValueError: If audio is too short.
        Exception: If file cannot be loaded.
    """
    logger.info(f"📂 Loading audio: {os.path.basename(path)}")

    try:
        waveform, sr = torchaudio.load(path)
        duration_sec = waveform.shape[1] / sr
        logger.info(
            f"🔊 Original - Channels: {waveform.shape[0]}, SR: {sr}, Duration: {duration_sec:.2f}s"
        )

        if sr != 16000:
            logger.info(f"🎼 Resampling {sr} Hz → 16000 Hz")
            waveform = torchaudio.transforms.Resample(sr, 16000)(waveform)
            sr = 16000

        if waveform.dtype != torch.float32:
            waveform = waveform.to(torch.float32)

        waveform = waveform.squeeze()
        duration_sec = waveform.shape[0] / sr

        if duration_sec < min_duration_sec:
            raise ValueError(f"Audio too short ({duration_sec:.2f}s)")

        if torch.isnan(waveform).any():
            raise ValueError("Audio contains NaNs after preprocessing")

        return waveform.numpy()

    except Exception as e:
        logger.error(f"Error loading {path}: {e}", exc_info=True)
        raise


# =======================
# TRANSCRIPTION
# =======================
def transcribe_audio(
    model: Any, file_path: str, output_dir: str, use_fast_whisper: bool = False
) -> None:
    """Transcribe a single audio file and save transcript."""
    base_name = Path(file_path).stem
    transcript_file = Path(output_dir) / f"{base_name}.txt"

    try:
        if not Path(file_path).exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        log_gpu_memory(f"Before Transcribe: {base_name}", DEVICE)
        waveform = load_audio_for_whisper(file_path)

        if use_fast_whisper:
            segments, _ = model.transcribe(waveform, beam_size=5, word_timestamps=True)
            full_transcript = " ".join(s.text.strip() for s in segments)
        else:
            result = model.transcribe(waveform, language="en")
            full_transcript = (result.get("text") or "").strip()

        log_gpu_memory(f"After Transcribe: {base_name}", DEVICE)
        transcript_file.write_text(full_transcript, encoding="utf-8")
        logger.info(f"📄 Saved transcript: {transcript_file}")

    except Exception as e:
        logger.error(f"❌ Error processing {file_path}: {e}", exc_info=True)


# =======================
# BATCH PROCESSING
# =======================
def transcribe_folder(
    batch_files: List[str], output_folder: str, model_size: str = MODEL_SIZE #Whisper Medium
) -> Dict[str, Any]:
    """Transcribe multiple audio files in a folder."""
    start = time.time()
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    if torch.cuda.is_available() and DEVICE == "cuda":
        torch.cuda.init()
        torch.cuda.reset_peak_memory_stats(torch.cuda.current_device())

    try:
        logger.info(f"📦 Loading Whisper model: {model_size}")
        log_gpu_memory("Before Whisper Load", DEVICE)
        model = whisper.load_model(model_size, device=DEVICE)
        log_gpu_memory("After Whisper Load", DEVICE)
    except Exception as e:
        logger.error(f"Model load failed ({model_size}): {e}", exc_info=True)
        return {"processed": 0, "failed": len(batch_files), "time_sec": 0}

    failed_files = []
    for file_path in batch_files:
        try:
            transcribe_audio(model, file_path, str(output_path))
        except Exception:
            failed_files.append(Path(file_path).name)

    duration = time.time() - start
    log_gpu_memory("After Batch Processing", DEVICE)
    row = {
        "timestamp": datetime.now().isoformat(),
        "model_name": model_size,
        "total_files": len(batch_files),
        "failed_files": len(failed_files),
        "success_files": len(batch_files) - len(failed_files),
        "latency_sec": duration,
        "failed_files_list": failed_files,
    }
    gpu_info = get_gpu_memory_info(DEVICE)
    if gpu_info:
        row["gpu_allocated_gb"] = gpu_info.get("allocated_gb")
        row["gpu_reserved_gb"] = gpu_info.get("reserved_gb")
        row["gpu_max_allocated_gb"] = gpu_info.get("max_allocated_gb")
        row["gpu_total_gb"] = gpu_info.get("total_gb")
        row["gpu_free_gb"] = gpu_info.get("free_gb")
        row["gpu_utilization_pct"] = gpu_info.get("utilization_pct")
        vram_gb = gpu_info.get("max_allocated_gb")
        report_metrics.set_transcription_vram_gb(vram_gb)
    else:
        row["gpu_allocated_gb"] = row["gpu_reserved_gb"] = row["gpu_max_allocated_gb"] = ""
        row["gpu_total_gb"] = row["gpu_free_gb"] = row["gpu_utilization_pct"] = ""
        report_metrics.set_transcription_vram_gb(None)
    save_metrics(METRICS_DIR, row, "speech_to_text.csv")

    return {"processed": len(batch_files), "failed": len(failed_files), "time_sec": duration}


# =======================
# ENTRY POINT
# =======================
if __name__ == "__main__":
    logger.info("📁 STT Pipeline Started")
    try:
        files = [
            str(Path(CLEANED_AUDIO_DIR) / f)
            for f in os.listdir(CLEANED_AUDIO_DIR)
            if f.lower().endswith(STT_AUDIO_SUPPORTED_FORMAT)
        ]
        if not files:
            logger.warning(f"No supported audio files in {CLEANED_AUDIO_DIR}")
        else:
            stats = transcribe_folder(files, TRANSCRIPTS_DIR)
            logger.info(f"Pipeline stats: {stats}")
    except Exception as e:
        logger.error(f"STT Pipeline failed: {e}", exc_info=True)
    finally:
        logger.info("✅ STT Pipeline Finished")
