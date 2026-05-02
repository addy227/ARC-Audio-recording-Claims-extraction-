"""
Script Name: speech_to_text.py
Description:
    Processes cleaned audio files and transcribes them into text using Microsoft's VibeVoice-ASR model.
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
    - transformers
    - torchaudio
    - torch
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
from transformers import AutoProcessor, VibeVoiceAsrForConditionalGeneration

from utils.analytics import save_metrics
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

STT_AUDIO_SUPPORTED_FORMAT: Tuple[str, ...] = (
    tuple(stt["supported_format_stt"])
    if isinstance(stt["supported_format_stt"], list)
    else (stt["supported_format_stt"],)
)

METRICS_DIR = Path(get_project_path(paths["metrics_dir"]))
METRICS_DIR.mkdir(parents=True, exist_ok=True)

VIBEVOICE_MODEL_NAME = "microsoft/VibeVoice-ASR-HF"
TARGET_SR = 24000  # VibeVoice-ASR requires 24kHz

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

logger = get_logger("speech_to_text")
logger.info(f"⚙️ Using device: {DEVICE.upper()}")
logger.info(f"CUDA available: {torch.cuda.is_available()}, CUDA version: {torch.version.cuda or 'N/A'}")

CONTEXT_PROMPT = (
    "Key terms: member ID, date of service, date of birth, billed amount, tax ID, NPI, Patient first name, Patient last name, as in, denied, approved, claim number "
    "P4 Clinical llc, P4 Physicians, Therenostix "
    "Alfa, Bravo, Charlie, Delta, Echo, Foxtrot, Golf, Hotel, India, Juliett, Kilo, Lima, Mike, November, Oscar, Papa, Quebec, Romeo, Sierra, Tango, Uniform, Victor, Whiskey, Xray, Yankee, Zulu"
    "one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, zero: 0"
)

# =======================
# GPU MEMORY MONITORING
# =======================
def get_gpu_memory_info(device: str = "cuda") -> Dict[str, Any]:
    if not torch.cuda.is_available() or device != "cuda":
        return None
    try:
        allocated = torch.cuda.memory_allocated(0) / (1024**3)
        reserved = torch.cuda.memory_reserved(0) / (1024**3)
        max_allocated = torch.cuda.max_memory_allocated(0) / (1024**3)
        total_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
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
    mem_info = get_gpu_memory_info(device)
    if mem_info:
        logger.info(
            f"💾 [VibeVoice] GPU Memory [{context}]: "
            f"Allocated={mem_info['allocated_gb']}GB, "
            f"Reserved={mem_info['reserved_gb']}GB, "
            f"Free={mem_info['free_gb']}GB, "
            f"Utilization={mem_info['utilization_pct']}%, "
            f"Max_Allocated={mem_info['max_allocated_gb']}GB"
        )
    else:
        logger.debug(f"💾 [VibeVoice] GPU Memory [{context}]: Not available (CPU mode)")


# =======================
# AUDIO LOADING
# =======================
def load_audio_for_vibevoice(path: str, min_duration_sec: float = 1.0) -> np.ndarray:
    """
    Load and preprocess audio for VibeVoice-ASR transcription.

    Args:
        path: Path to audio file.
        min_duration_sec: Minimum duration in seconds.

    Returns:
        Normalized waveform as numpy array at 24kHz mono.

    Raises:
        ValueError: If audio is too short or contains NaNs.
        Exception: If file cannot be loaded.
    """
    logger.info(f"📂 Loading audio: {os.path.basename(path)}")

    try:
        waveform, sr = torchaudio.load(path)
        duration_sec = waveform.shape[1] / sr
        logger.info(f"🔊 Original - Channels: {waveform.shape[0]}, SR: {sr}, Duration: {duration_sec:.2f}s")

        # Convert to mono
        if waveform.shape[0] > 1:
            waveform = torch.mean(waveform, dim=0, keepdim=True)

        # Resample to 24kHz
        if sr != TARGET_SR:
            logger.info(f"🎼 Resampling {sr}Hz → {TARGET_SR}Hz")
            waveform = torchaudio.transforms.Resample(sr, TARGET_SR)(waveform)

        if waveform.dtype != torch.float32:
            waveform = waveform.to(torch.float32)

        waveform = waveform.squeeze()
        duration_sec = waveform.shape[0] / TARGET_SR

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
    model: Any,
    processor: Any,
    file_path: str,
    output_dir: str,
) -> None:
    """Transcribe a single audio file using VibeVoice-ASR and save transcript."""
    base_name = Path(file_path).stem
    transcript_file = Path(output_dir) / f"{base_name}.txt"

    try:
        if not Path(file_path).exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        log_gpu_memory(f"Before Transcribe: {base_name}", DEVICE)

        # Load and preprocess audio
        audio_numpy = load_audio_for_vibevoice(file_path)

        # Prepare inputs using VibeVoice processor
        inputs = processor.apply_transcription_request(
            audio=audio_numpy,
            sampling_rate=TARGET_SR,
            prompt=CONTEXT_PROMPT,
        ).to(model.device, model.dtype)

        # Run inference
        with torch.no_grad():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=16384,
            )  # increase from default to handle long audio

        log_gpu_memory(f"After Transcribe: {base_name}", DEVICE)

        # Strip prompt tokens and decode
        generated_ids = output_ids[:, inputs["input_ids"].shape[1]:]
        try:
            full_transcript = processor.decode(generated_ids, return_format="transcription_only")[0]
        except Exception as e:
            logger.warning(f"transcription_only decode failed: {e}, falling back to raw decode")
            try:
                full_transcript_parsed = processor.decode(generated_ids, return_format="parsed")[0]
                full_transcript = " ".join(
                    seg.get("text", "") or seg.get("content", "")
                    for seg in full_transcript_parsed
                    if isinstance(seg, dict)
                ).strip()
            except Exception as e2:
                logger.warning(f"parsed decode also failed: {e2}, using raw text decode")
                raw = processor.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)
                full_transcript = raw[0].strip() if raw else ""

        # Save transcript - this now runs regardless of which decode path succeeded
        if full_transcript:
            transcript_file.write_text(full_transcript, encoding="utf-8")
            logger.info(f"📄 Saved transcript: {transcript_file}")
            logger.info(f"📝 Transcript preview: {full_transcript[:200]}")
        else:
            logger.warning(f"⚠️ Empty transcript for {file_path}")
        raw_text = processor.tokenizer.batch_decode(generated_ids, skip_special_tokens=False)
        logger.info(f"Raw model output (first 500 chars): {raw_text[0][:500] if raw_text else 'EMPTY'}")
        logger.info(f"Raw model output length: {len(raw_text[0]) if raw_text else 0} chars")

    except Exception as e:
        logger.error(f"❌ Error processing {file_path}: {e}", exc_info=True)


# =======================
# BATCH PROCESSING
# =======================
def transcribe_folder(
    batch_files: List[str],
    output_folder: str,
) -> Dict[str, Any]:
    """Transcribe multiple audio files using VibeVoice-ASR."""
    start = time.time()
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    try:
        logger.info(f"📦 Loading VibeVoice-ASR model: {VIBEVOICE_MODEL_NAME}")
        log_gpu_memory("Before VibeVoice Load", DEVICE)

        processor = AutoProcessor.from_pretrained(VIBEVOICE_MODEL_NAME)
        model = VibeVoiceAsrForConditionalGeneration.from_pretrained(
            VIBEVOICE_MODEL_NAME,
            device_map="auto"
        )
        model.eval()

        log_gpu_memory("After VibeVoice Load", DEVICE)
        logger.info(f"✅ Model loaded on {model.device} with dtype {model.dtype}")

    except Exception as e:
        logger.error(f"Model load failed: {e}", exc_info=True)
        return {"processed": 0, "failed": len(batch_files), "time_sec": 0}

    failed_files = []
    for file_path in batch_files:
        try:
            transcribe_audio(model, processor, file_path, str(output_path))
        except Exception:
            failed_files.append(Path(file_path).name)

    duration = time.time() - start
    log_gpu_memory("After Batch Processing", DEVICE)

    save_metrics(
        METRICS_DIR,
        {
            "timestamp": datetime.now().isoformat(),
            "model_name": VIBEVOICE_MODEL_NAME,
            "total_files": len(batch_files),
            "failed_files": len(failed_files),
            "success_files": len(batch_files) - len(failed_files),
            "latency_sec": duration,
            "failed_files_list": failed_files,
        },
        "speech_to_text.csv",
    )

    return {"processed": len(batch_files), "failed": len(failed_files), "time_sec": duration}


# =======================
# ENTRY POINT
# =======================
if __name__ == "__main__":
    logger.info("📁 STT Pipeline Started (VibeVoice-ASR)")
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
