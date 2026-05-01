"""
Parallel (multi-process) claim extraction from transcript files.

Reuses the same pipeline as ``new_claim_extractor`` (``process_single_transcript``):
cleaning, LLM extraction, deduplication, JSON output, and blob uploads.

Modes
-----
* ``--workers 1`` (default): Calls ``process_all_transcripts`` — one model in memory,
  same metrics and GPU reporting as running ``new_claim_extractor`` directly.
* ``--workers N`` (N > 1): Runs ``process_single_transcript`` in N processes. Each
  process loads the finetuned model. **Expect roughly N× GPU VRAM** unless you use
  CPU or separate GPUs (e.g. ``CUDA_VISIBLE_DEVICES`` per host process). On a single
  consumer GPU, keep ``workers`` at 1.

Effective concurrency is ``min(--workers, pipeline.max_concurrent_transcript_extractions)``
from ``config_manager/config_pipeline.yaml`` (default cap ``1``).

Run from the project root (or any path; project root is added to ``sys.path``).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Project root (…/ARCall-Entity-Extractor-1)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.analytics import save_metrics
from utils import report_metrics
from utils.config_loader import load_pipeline_config
from utils.logging_utils import get_logger
from utils.util_master import get_project_path, get_transcript_files

logger = get_logger("parallel_claim_extractor")

Task = Tuple[str, str, str]  # (filename, transcripts_dir, output_dir)


def max_concurrent_transcript_extractions_from_config(config: Dict[str, Any]) -> int:
    """
    Upper bound on parallel transcript extractions from ``config_pipeline.yaml``:
    ``pipeline.max_concurrent_transcript_extractions`` (default ``1``, minimum ``1``).
    """
    pipeline = config.get("pipeline") or {}
    raw = pipeline.get("max_concurrent_transcript_extractions", 1)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid pipeline.max_concurrent_transcript_extractions=%r; using 1",
            raw,
        )
        n = 1
    return max(1, n)


def effective_transcript_workers(requested: int, config: Dict[str, Any]) -> Tuple[int, int]:
    """Return ``(effective_workers, config_cap)`` where effective = min(requested, cap), both >= 1."""
    cap = max_concurrent_transcript_extractions_from_config(config)
    req = max(1, int(requested))
    return min(req, cap), cap


def _worker_extract(task: Task) -> Dict[str, Any]:
    """
    Execute in a child process. Lazy-imports ``new_claim_extractor`` so the parent
    process never initializes CUDA / the LLM.
    """
    filename, transcripts_dir, output_dir = task
    from scripts.audio_file_process.new_claim_extractor import process_single_transcript

    summary: Dict[str, List[Any]] = {"success": [], "failed": [], "raw_saved": []}
    try:
        _claims, _paths, claim_folder, total_claims, elapsed = process_single_transcript(
            filename,
            transcripts_dir,
            output_dir,
            summary,
        )
        return {
            "filename": filename,
            "error": None,
            "total_claims": total_claims,
            "elapsed": elapsed,
            "success": list(summary.get("success", [])),
            "failed": list(summary.get("failed", [])),
            "raw_saved": list(summary.get("raw_saved", [])),
            "claim_folder": str(claim_folder) if claim_folder else None,
        }
    except Exception as e:
        return {
            "filename": filename,
            "error": str(e),
            "total_claims": 0,
            "elapsed": 0.0,
            "success": [],
            "failed": [{"file": filename, "error": str(e)}],
            "raw_saved": [],
            "claim_folder": None,
        }


def _merge_gpu_columns() -> Dict[str, Any]:
    """Match claim_extraction.csv GPU columns from ``process_all_transcripts``."""
    try:
        import torch

        if not torch.cuda.is_available():
            return {
                "gpu_allocated_gb": "",
                "gpu_reserved_gb": "",
                "gpu_max_allocated_gb": "",
                "gpu_total_gb": "",
                "gpu_free_gb": "",
                "gpu_utilization_pct": "",
            }
        allocated = torch.cuda.memory_allocated(0) / (1024**3)
        reserved = torch.cuda.memory_reserved(0) / (1024**3)
        max_allocated = torch.cuda.max_memory_allocated(0) / (1024**3)
        total_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        free_gb = total_memory - reserved
        util = round((reserved / total_memory) * 100, 1) if total_memory > 0 else 0
        return {
            "gpu_allocated_gb": round(allocated, 2),
            "gpu_reserved_gb": round(reserved, 2),
            "gpu_max_allocated_gb": round(max_allocated, 2),
            "gpu_total_gb": round(total_memory, 2),
            "gpu_free_gb": round(free_gb, 2),
            "gpu_utilization_pct": util,
        }
    except Exception:
        return {
            "gpu_allocated_gb": "",
            "gpu_reserved_gb": "",
            "gpu_max_allocated_gb": "",
            "gpu_total_gb": "",
            "gpu_free_gb": "",
            "gpu_utilization_pct": "",
        }


def _write_merged_metrics(
    metrics_dir: str,
    results: List[Dict[str, Any]],
    last_claim_folder: Optional[str],
) -> None:
    gpu_cols = _merge_gpu_columns()
    if isinstance(gpu_cols.get("gpu_max_allocated_gb"), (int, float)):
        report_metrics.set_extraction_vram_gb(gpu_cols.get("gpu_max_allocated_gb"))
    else:
        report_metrics.set_extraction_vram_gb(None)

    for r in results:
        fn = r["filename"]
        if r.get("error"):
            row = {
                "timestamp": datetime.now().isoformat(),
                "file_name": fn,
                "stage": "Claim Extraction (parallel)",
                "success": False,
                "elapsed_sec": r.get("elapsed"),
                "error": r["error"],
                "output_dir": last_claim_folder or "",
            }
            row.update(gpu_cols)
            save_metrics(metrics_dir=metrics_dir, data=row, filename="claim_extraction.csv")
            continue

        for saved_name in r.get("success", []):
            row = {
                "timestamp": datetime.now().isoformat(),
                "file_name": saved_name,
                "stage": "Claim Extraction (parallel)",
                "success": True,
                "elapsed_sec": r.get("elapsed"),
                "error": "",
                "output_dir": last_claim_folder or "",
            }
            row.update(gpu_cols)
            save_metrics(metrics_dir=metrics_dir, data=row, filename="claim_extraction.csv")

        for fail in r.get("failed", []):
            if isinstance(fail, dict):
                file_name = fail.get("file", fn)
                error = fail.get("error", "")
            else:
                file_name = str(fail)
                error = ""
            row = {
                "timestamp": datetime.now().isoformat(),
                "file_name": file_name,
                "stage": "Claim Extraction (parallel)",
                "success": False,
                "elapsed_sec": r.get("elapsed"),
                "error": error,
                "output_dir": last_claim_folder or "",
            }
            row.update(gpu_cols)
            save_metrics(metrics_dir=metrics_dir, data=row, filename="claim_extraction.csv")


def process_transcripts_parallel(
    files: List[str],
    transcripts_dir: str,
    output_dir: str,
    workers: int,
    metrics_dir: str,
) -> Dict[str, Any]:
    if workers < 1:
        raise ValueError("workers must be >= 1")

    if workers == 1:
        from scripts.audio_file_process.new_claim_extractor import process_all_transcripts

        return process_all_transcripts(files)

    tasks: List[Task] = [(f, transcripts_dir, output_dir) for f in files]
    logger.warning(
        "workers=%s: each process loads the full finetuned model. "
        "Ensure sufficient GPU memory (often one worker per GPU).",
        workers,
    )

    start = time.time()
    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(_worker_extract, t): t[0] for t in tasks}
        for fut in as_completed(future_map):
            try:
                results.append(fut.result())
            except Exception as e:
                fn = future_map[fut]
                logger.error("Worker failed for %s: %s", fn, e, exc_info=True)
                results.append(
                    {
                        "filename": fn,
                        "error": str(e),
                        "total_claims": 0,
                        "elapsed": 0.0,
                        "success": [],
                        "failed": [{"file": fn, "error": str(e)}],
                        "raw_saved": [],
                        "claim_folder": None,
                    }
                )

    results.sort(key=lambda x: x["filename"])
    total_claims = sum(r["total_claims"] for r in results)
    merged_success: List[Any] = []
    merged_failed: List[Any] = []
    merged_raw: List[Any] = []
    last_folder: Optional[str] = None
    for r in results:
        merged_success.extend(r.get("success", []))
        merged_failed.extend(r.get("failed", []))
        merged_raw.extend(r.get("raw_saved", []))
        if r.get("claim_folder"):
            last_folder = r["claim_folder"]

    _write_merged_metrics(metrics_dir, results, last_folder)

    elapsed = time.time() - start
    err_count = sum(1 for r in results if r.get("error"))
    return {
        "stage": "Claim Extraction (parallel)",
        "status": "success" if err_count == 0 and not merged_failed else "partial",
        "total_files": len(files),
        "workers": workers,
        "succeeded": len(merged_success),
        "failed": len(merged_failed),
        "elapsed_sec": elapsed,
        "claim_folder": last_folder,
        "total_extracted_claims": total_claims,
    }


def main() -> int:
    config = load_pipeline_config()
    paths = config.get("paths", {})
    default_transcripts = get_project_path(paths["transcripts_dir"])
    default_output = get_project_path(paths["extracted_claims_dir"])
    default_metrics = get_project_path(paths["metrics_dir"])

    default_workers = int(os.getenv("CLAIM_EXTRACTION_WORKERS", "1"))

    parser = argparse.ArgumentParser(
        description="Extract claims from transcripts in parallel (multi-process) or sequential (workers=1)."
    )
    parser.add_argument(
        "--transcripts-dir",
        type=str,
        default=default_transcripts,
        help="Directory containing transcript .txt files",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=default_output,
        help="Directory for extracted claim JSON (same layout as new_claim_extractor)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=default_workers,
        help=(
            "Desired process count; capped by pipeline.max_concurrent_transcript_extractions "
            "in config_pipeline.yaml. 1 = single model (same as new_claim_extractor)."
        ),
    )
    args = parser.parse_args()

    effective_workers, concurrency_cap = effective_transcript_workers(args.workers, config)
    if args.workers > concurrency_cap:
        logger.info(
            "Transcript concurrency capped by config: requested_workers=%s effective_workers=%s "
            "(pipeline.max_concurrent_transcript_extractions=%s)",
            args.workers,
            effective_workers,
            concurrency_cap,
        )

    transcripts_dir = Path(args.transcripts_dir)
    if not transcripts_dir.is_dir():
        logger.error("Transcripts directory does not exist: %s", args.transcripts_dir)
        return 1

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    Path(default_metrics).mkdir(parents=True, exist_ok=True)

    files = get_transcript_files(str(transcripts_dir))
    if not files:
        logger.warning("No transcript files found in %s", transcripts_dir)
        return 0

    logger.info(
        "Found %s transcript(s); effective_workers=%s (config cap=%s)",
        len(files),
        effective_workers,
        concurrency_cap,
    )

    try:
        stats = process_transcripts_parallel(
            files=files,
            transcripts_dir=str(transcripts_dir),
            output_dir=args.output_dir,
            workers=effective_workers,
            metrics_dir=default_metrics,
        )
        logger.info("Done: %s", stats)
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
