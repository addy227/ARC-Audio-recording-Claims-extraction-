import csv
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from utils.logging_utils import get_logger
from utils.util_master import get_project_path, paths

stage_timings = {"cleaning": 0, "transcription": 0, "extraction": 0, "integration": 0}

logger = get_logger(__name__)

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


def save_run_summary_json(metrics: dict) -> None:
    """
    Save pipeline metrics to a date-stamped JSON file in the metrics directory.
    Also saves a 'latest' version for quick access.
    Converts all time values from seconds to minutes.
    """
    os.makedirs(METRICS_DIR, exist_ok=True)

    now = datetime.utcnow()
    metrics["last_updated"] = now.isoformat()

    # Convert timing data from seconds to minutes
    if "timings" in metrics:
        if "total_time_sec" in metrics["timings"]:
            metrics["timings"]["total_time_min"] = round(
                metrics["timings"]["total_time_sec"] / 60, 4
            )
            del metrics["timings"]["total_time_sec"]
        if "stage_times" in metrics["timings"]:
            for k, v in list(metrics["timings"]["stage_times"].items()):
                if v is not None:
                    metrics["timings"]["stage_times"][k + "_min"] = round(v / 60, 4)
                del metrics["timings"]["stage_times"][k]

    # Convert per-file timing data
    if "per_file" in metrics:
        for file_metrics in metrics["per_file"].values():
            if "elapsed_sec" in file_metrics and file_metrics["elapsed_sec"] is not None:
                file_metrics["elapsed_min"] = round(file_metrics["elapsed_sec"] / 60, 4)
                del file_metrics["elapsed_sec"]
            for stage in ["cleaning_sec", "transcription_sec", "extraction_sec", "integration_sec"]:
                if stage in file_metrics and file_metrics[stage] is not None:
                    file_metrics[stage.replace("_sec", "_min")] = round(file_metrics[stage] / 60, 4)
                    del file_metrics[stage]

    # Generate filenames
    date_str = now.strftime("%Y-%m-%d")
    dated_metrics_path = os.path.join(METRICS_DIR, f"pipeline_metrics_{date_str}.json")
    latest_metrics_path = os.path.join(METRICS_DIR, "pipeline_metrics_latest.json")

    # Save the metrics to both dated and latest files
    with open(dated_metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    with open(latest_metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    logger.info(
        "✅ Metrics saved to: dated=%s latest=%s",
        dated_metrics_path,
        latest_metrics_path,
    )


def save_metadata(file_id, filename, success, per_stage, dest_dir):
    """
    Save metadata JSON atomically to the dest_dir.
    """
    metadata = {
        "file_id": file_id,
        "filename": filename,
        "status": "success" if success else "failed",
        "per_stage_sec": per_stage,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    dest_dir_path = Path(dest_dir)
    dest_dir_path.mkdir(parents=True, exist_ok=True)

    meta_path = dest_dir_path / f"{Path(filename).stem}_meta.json"
    try:
        # Write to temp file first for atomic write
        with tempfile.NamedTemporaryFile(
            "w", delete=False, dir=str(dest_dir_path), prefix="meta_", suffix=".json"
        ) as tmpf:
            json.dump(metadata, tmpf, indent=2)
            tmp_meta_path = tmpf.name
        # Rename temp file to final metadata filename atomically
        os.replace(tmp_meta_path, meta_path)
        logger.info(f"Saved metadata to '{meta_path}'")
    except Exception as e:
        logger.error(f"Failed to save metadata to '{meta_path}': {e}", exc_info=True)


def save_analytical_metrics(
    metrics_dir: str,
    data: Dict[str, Any],
    per_file: bool = True,
    filename: str = "pipeline_analytics.csv",
) -> None:
    """
    Save pipeline metrics in a CSV file for analytics.

    Args:
        metrics_dir (str): Directory to store the CSV.
        data (Dict): Metrics data to save.
        per_file (bool): True if this is a per-file row; False for summary row.
        filename (str): CSV filename.
    """
    os.makedirs(metrics_dir, exist_ok=True)
    metrics_file = Path(metrics_dir) / filename

    # Flatten nested 'stages' dict if present
    row = {}
    for k, v in data.items():
        if k == "stages" and isinstance(v, dict):
            for stage_name, elapsed in v.items():
                row[f"{stage_name}_sec"] = elapsed
        else:
            row[k] = v

    # Determine CSV header
    write_header = not metrics_file.exists() or metrics_file.stat().st_size == 0
    header_fields = list(row.keys()) if write_header else None

    try:
        with open(metrics_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=header_fields or row.keys())
            if write_header:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:
        logger.error("❌ Failed to save metrics row: %s", e)


def save_metrics(
    metrics_dir: str,
    data: Dict,
    filename: str,
    aggregate_and_log: bool = True,
    aggregate_fields: Optional[Dict[str, str]] = None,
) -> None:
    """
    Append a dictionary as a row to a CSV file inside metrics_dir.
    Creates the directory if missing.
    Writes CSV header once.
    Optionally aggregates and logs summary based on provided aggregation fields.

    Args:
        metrics_dir (str): Directory path where CSV is stored.
        data (Dict): Dictionary of key-value pairs representing one row of metrics.
        filename (str): Filename for the CSV file (default: 'metrics.csv').
        aggregate_and_log (bool): Whether to aggregate and log summary metrics.
        aggregate_fields (Optional[Dict[str, str]]): Optional mapping for aggregation logic.
            Keys are column names in CSV.
            Values specify aggregation type: 'count', 'sum', 'mean', or 'status_count:<value>'.
            Example:
              {
                "status": "status_count:success",
                "latency_sec": "mean",
                "errors": "sum"
              }
            If None, default aggregation for columns 'status' and 'latency_sec' is used.

    Usage:
        save_metrics("/path/to/dir", data_dict)
    """
    os.makedirs(metrics_dir, exist_ok=True)
    metrics_file = os.path.join(metrics_dir, filename)

    # Check if file exists and if header needs to be written
    write_header = not os.path.exists(metrics_file) or os.stat(metrics_file).st_size == 0

    try:
        with open(metrics_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=data.keys())
            if write_header:
                writer.writeheader()
            writer.writerow(data)
        logger.info(f"📄 Metrics saved: {metrics_file}")
    except Exception as e:
        logger.error(f"Failed to save metrics: {e}")
        return

    if aggregate_and_log:
        try:
            df = pd.read_csv(metrics_file)
            total = len(df)
            summary_parts = [f"total={total}"]

            if aggregate_fields is None:
                # Default aggregation for common fields
                if "status" in df.columns:
                    success_count = (df["status"] == "success").sum()
                    fail_count = (df["status"] == "fail").sum()
                    summary_parts.append(f"success={success_count}")
                    summary_parts.append(f"fail={fail_count}")
                if "latency_sec" in df.columns and total > 0:
                    avg_latency = df["latency_sec"].mean()
                    summary_parts.append(f"avg_latency={avg_latency:.2f}s")
            else:
                # Custom aggregation based on aggregate_fields dict
                for col, agg_type in aggregate_fields.items():
                    if col not in df.columns:
                        continue
                    if agg_type == "count":
                        summary_parts.append(f"{col}_count={df[col].count()}")
                    elif agg_type == "sum":
                        summary_parts.append(f"{col}_sum={df[col].sum()}")
                    elif agg_type == "mean":
                        summary_parts.append(f"{col}_mean={df[col].mean():.2f}")
                    elif agg_type.startswith("status_count:"):
                        status_val = agg_type.split(":", 1)[1]
                        count_val = (df[col] == status_val).sum()
                        summary_parts.append(f"{col}_{status_val}={count_val}")

            logger.info(f"[METRICS] Summary: {', '.join(summary_parts)}")
        except Exception as e:
            logger.error(f"Failed to aggregate metrics: {e}")
