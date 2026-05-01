import json
import threading
from collections import defaultdict
from pathlib import Path
import re
from datetime import datetime
import os
import csv
import logging
from typing import Dict, Optional, List, Tuple
import pandas as pd
from dateutil import parser

from utils.config_loader import load_pipeline_config
from utils.logging_utils import get_logger

# === CONFIGURATION  Paths ===
# Detect project root dynamically
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config_manager" / "config_pipeline.yaml"

logger = get_logger(__name__)
config = load_pipeline_config()
paths = config.get("paths", {})


# Required fields in each claim
REQUIRED_CLAIM_FIELDS = [
    "patient_first_name",
    "patient_last_name",
    "billed_amount",
    "date_of_birth",
]


def get_project_path(subfolder: str) -> str:
    """
    Returns the absolute path to a subfolder or file within the project root.
    Args:
        subfolder (str): Relative path from project root.
    Returns:
        str: Absolute path.
    """
    base_dir = Path(__file__).resolve().parents[1]
    full_path = os.path.join(base_dir, subfolder)
    return str(full_path)


METRICS_DIR = get_project_path(paths["metrics_dir"])

METRICS_FILE = os.path.join(METRICS_DIR, "claim_extraction_metrics.csv")

# To keep track of processed files
PROCESSED_INDEX_FILE = str(Path(METRICS_DIR) / "processed_files_index.txt")
# One lock for safe writes from multiple threads
_processed_index_lock = threading.Lock()

# === Utilities ===
def clean_transcript(text: str) -> str:
    """
    Cleans and normalizes transcript text for further processing.

    Performs the following transformations:
    - Converts text to lowercase.
    - Removes common filler phrases and marketing boilerplate.
    - Removes speaker tags.
    - Removes common speech fillers.
    - Normalizes some date patterns.
    - Normalizes dollar amounts by removing '$' and commas.
    - Removes excessive whitespace.

    Args:
        text: Raw transcript text.

    Returns:
        Cleaned and normalized transcript text.
    """
    # Lowercase everything
    text = text.lower()

    # Remove filler/marketing boilerplate
    text = re.sub(
        r"(music|thank you for calling|please hold|your call may be monitored.*?|try api.*?|visit www\..*?)",
        " ",
        text,
    )

    # Remove speaker tags (agent, customer, etc.)
    text = re.sub(r"\b(speaker \d+|agent|advocate|customer|virtual assistant)\b:?", "", text)

    # Remove common speech fillers
    text = re.sub(r"\b(uh|um|you know|like|okay|yeah|alright|so|well)\b", "", text)

    # Normalize date patterns (keep original format for now)
    text = re.sub(r"\b(\d{1,2})(st|nd|rd|th)?\s+(of\s+)?([a-z]+),?\s+(\d{4})", r"\1 \4 \5", text)

    # Normalize dollar amounts (e.g., $1,921.83 → 1921.83)
    text = re.sub(r"\$[\s]?(?=\d)", "", text)
    text = re.sub(r",(?=\d{3})", "", text)

    # Remove excessive spaces
    text = re.sub(r"\s{2,}", " ", text)

    return text.strip()


def normalize_claim(claim: dict) -> dict:
    """
    Filters and returns only expected claim fields from a raw claim dictionary.

    Args:
        claim: Dictionary potentially containing many fields.

    Returns:
        Dictionary containing only fields defined in EXPECTED_CLAIM_FIELDS.
    """
    # === Claim Schema ===
    EXPECTED_CLAIM_FIELDS = {
        "member_id",
        "npi_id",
        "patient_first_name",
        "patient_last_name",
        "date_of_birth",
        "date_of_service",
        "billed_amount",
    }
    return {field: claim.get(field) for field in EXPECTED_CLAIM_FIELDS}


def seconds_to_hhmmss(time_val: int | str) -> Optional[str]:
    """
    Convert seconds (int or str) to HH:MM:SS formatted string.
    Returns None if input invalid.
    Args:
        time_val (int | str): Number of seconds to convert.
    Returns:
        str | None: Time in HH:MM:SS format or None if invalid.
    """
    try:
        total_seconds = int(time_val)
        if total_seconds < 0:
            return None
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except (ValueError, TypeError):
        return None


def log_claim_metric(claim_id, tax_id, status, latency, model_name, filename, extra=None):
    """
    Logs claim processing metrics to a CSV file.

    Writes a new row with timestamp, IDs, status, latency, model name, filename, and extra info.
    Creates the CSV file and writes headers if the file does not already exist.

    Args:
        claim_id: Identifier of the claim being processed.
        tax_id: Tax ID associated with the claim.
        status: Status string (e.g., 'success', 'fail').
        latency: Processing latency in seconds.
        model_name: Name of the model used.
        filename: Source filename related to the claim data.
        extra: Optional additional info string.

    Raises:
        IOError if file writing fails.
    """
    fieldnames = [
        "timestamp",
        "claim_id",
        "tax_id",
        "status",
        "latency_sec",
        "model_name",
        "filename",
        "extra",
    ]
    row = {
        "timestamp": datetime.now().isoformat(),
        "claim_id": claim_id,
        "tax_id": tax_id,
        "status": status,
        "latency_sec": latency,
        "model_name": model_name,
        "filename": filename,
        "extra": extra or "",
    }
    write_header = not os.path.exists(METRICS_FILE)
    with open(METRICS_FILE, "a", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# --- Sub-functions for modularity ---
def get_transcript_files(transcripts_dir: str, extension: str = ".txt") -> List[str]:
    """
    Return a list of transcript files in the directory with the specified extension.

    Args:
        transcripts_dir (str): Directory path to search for transcript files.
        extension (str): File extension to filter by (default: ".txt").

    Returns:
        List[str]: List of transcript filenames.
    """
    try:
        p = Path(transcripts_dir)
        if not p.is_dir():
            logger.warning(
                f"Transcripts directory does not exist or is not a directory: {transcripts_dir}"
            )
            return []
        files = [
            f.name for f in p.iterdir() if f.is_file() and f.suffix.lower() == extension.lower()
        ]
        return files
    except Exception as e:
        logger.error(f"Failed to list transcript files in {transcripts_dir}: {e}", exc_info=True)
        return []


def extract_json_from_text(output_text: str) -> list | None:
    """
    Extracts the first JSON array from a string that may contain extra text.

    Args:
        output_text: Text output potentially containing a JSON array.

    Returns:
        Parsed list of dictionaries if a valid JSON array is found, else None.

    Raises:
        None explicitly, but logs JSONDecodeError if JSON parsing fails.
    """
    try:
        match = re.search(r"\[\s*\{.*\}\s*\]", output_text, re.DOTALL)
        if match:
            json_text = match.group(0)
            return json.loads(json_text)
        # Match JSON array between square brackets
        # match = re.search(r"\[\s*\{.*?\}\s*\]", output_text, re.DOTALL)
        # if not match:
        #     logger.warning("⚠️ No JSON array found in text")
        #     return None
        #
        # json_text = match.group(0)
        #
        # # Attempt to clean common trailing commas or invalid quotes
        # json_text = re.sub(r",\s*}", "}", json_text)  # remove trailing commas in objects
        # json_text = re.sub(r",\s*]", "]", json_text)  # remove trailing commas in arrays

    except json.JSONDecodeError as e:
        logger.error("JSON Decode Error: %s", e)

    return None


def sanitize_claim(claim: dict) -> dict:
    def normalize_amount(value):
        if not value or str(value).strip().lower() in {"null", "none"}:
            return None  # Return None (null) instead of "0.00"
        try:
            return "{:.2f}".format(float(value))
        except (ValueError, TypeError):
            return None  # Return None (null) instead of "0.00"

            # Normalize NPI: keep letters and digits only

    def normalize_npi(value, fallback=None):
        if not value or str(value).strip().lower() in {"null", "none"}:
            return fallback
        value = str(value)
        cleaned = re.sub(r"[^a-zA-Z0-9]", "", value)
        return cleaned if cleaned else fallback

    def normalize_date(value: str, fallback: str = None) -> str | None:
        """
        Normalize any common date string format to MM-DD-YYYY.

        Args:
            value (str): Date string in any common format.
            fallback (str): Value to return if parsing fails (default: None).

        Returns:
            str | None: Date string formatted as MM-DD-YYYY or fallback.
        """
        if not value or str(value).strip().lower() in {"null", "none"}:
            return fallback

        try:
            # Parse date string with dateutil parser
            date = parser.parse(value)
            # Format date as MM-DD-YYYY
            return date.strftime("%m-%d-%Y")
        except (ValueError, TypeError):
            return fallback

    def normalize_name(value: str) -> str | None:
        if not value or str(value).strip().lower() in {"null", "none"}:
            return None  # Return None (null) instead of "UNKNOWN"

        # Remove unwanted characters including spaces
        cleaned = re.sub(r"[,\-_/\\\.'\s]", "", value)

        # Return None if cleaned is empty, otherwise capitalize
        return cleaned.title() if cleaned else None

    def fallback(value, default=None, lower: bool = True):
        if value is None or str(value).strip().lower() in {"", "null", "none"}:
            return default  # Return None (null) instead of "UNKNOWN"
        result = str(value).strip()
        return result.lower() if lower else result

    # Normalize provider_tax_id: digits only
    def normalize_provider_tax_id(value: str, fallback: str = None) -> str | None:
        if not value or str(value).strip().lower() in {"null", "none"}:
            return fallback
        value = str(value)
        digits_only = re.sub(r"[^0-9]", "", value)
        return digits_only if digits_only else fallback

    # Normalize member_id: alphanumeric only (strip separators/spaces; preserve letters + digits)
    def normalize_member_id(value: str, fallback: str = None) -> str | None:
        if not value or str(value).strip().lower() in {"null", "none"}:
            return fallback
        value = str(value)
        cleaned = re.sub(r"[^a-zA-Z0-9]", "", value)
        return cleaned if cleaned else fallback

    return {
        "provider_tax_id": normalize_provider_tax_id(claim.get("provider_tax_id")),
        "patient_first_name": normalize_name(claim.get("patient_first_name")),
        "patient_last_name": normalize_name(claim.get("patient_last_name")),
        "billed_amount": normalize_amount(claim.get("billed_amount")),
        "member_id": normalize_member_id(claim.get("member_id")),
        "provider_npi_id": normalize_npi(claim.get("npi_id")),
        "date_of_service": normalize_date(claim.get("date_of_service")),
        "date_of_birth": (
            normalize_date(claim.get("date_of_birth")) if claim.get("date_of_birth") else None
        ),
        "claim_transcript_file_name": fallback(claim.get("claim_transcript_file_name")),
        "claim_transcript_storage_id": fallback(claim.get("claim_transcript_storage_id")),
        "claim_json_attributes_file_name": fallback(claim.get("claim_json_attributes_file_name")),
        "claim_json_attributes_storage_id": claim.get("claim_json_attributes_storage_id") or None,
        "summary": claim.get("summary") or claim.get("Summary"),  # handle both casings
    }


def _claim_summary_text(claim: dict) -> str:
    s = claim.get("summary") or claim.get("Summary")
    return str(s).strip() if s is not None else ""


def claim_richness_score(claim: dict) -> int:
    """
    Heuristic: how much structured detail is present (higher = keep when deduping).
    Used to choose between claims with the same member_id and date_of_service.
    """
    score = 0
    if claim.get("patient_first_name"):
        score += 2
    if claim.get("patient_last_name"):
        score += 2
    if claim.get("member_id"):
        score += 1
    ba = claim.get("billed_amount")
    if ba is not None and str(ba).strip() not in ("", "0.00", "null", "None"):
        score += 2
    if claim.get("provider_npi_id"):
        score += 2
    if claim.get("npi_id"):
        score += 1
    if claim.get("date_of_birth"):
        score += 1
    if claim.get("date_of_service"):
        score += 1
    st = _claim_summary_text(claim)
    if st:
        score += min(6, 1 + len(st) // 100)
    return score


def Duplicate_check(
    claim_pairs: List[Tuple[Optional[str], dict]],
) -> List[Tuple[Optional[str], dict]]:
    """
    For claims with the same member_id and date_of_service, keep the richest
    by claim_richness_score (tie-break: longer summary text).

    Rows with no member_id are passed through unchanged.
    Scope: single source transcript only.
    """
    buckets: Dict[Tuple[str, str], List[Tuple[Optional[str], dict]]] = defaultdict(list)
    no_member: List[Tuple[Optional[str], dict]] = []

    for tax_id, claim in claim_pairs:
        if not isinstance(claim, dict):
            continue
        mid = str(claim.get("member_id", "") or "").strip()
        if not mid:
            no_member.append((tax_id, claim))
            continue
        dos = str(claim.get("date_of_service", "") or "").strip()
        buckets[(mid.lower(), dos)].append((tax_id, claim))

    out: List[Tuple[Optional[str], dict]] = []
    for (mid_k, dos_k), items in buckets.items():
        best = max(
            items,
            key=lambda x: (claim_richness_score(x[1]), len(_claim_summary_text(x[1]))),
        )
        out.append(best)
        if len(items) > 1:
            logger.info(
                "🔁 Deduped %d claims (member_id=%s, dos=%r) → kept richest score=%s",
                len(items), mid_k, dos_k or None, claim_richness_score(best[1]),
            )

    out.extend(no_member)
    return out


def is_valid_claim(claim):
    """Return True if all required fields are present and non-empty."""
    for field in REQUIRED_CLAIM_FIELDS:
        value = claim.get(field)
        if value in [None, "", "null"]:
            return False
    return True


def filter_valid_records(parsed_output):
    """
    Given a list of records (each with tax_id and claims),
    filter out invalid claims and remove any records with no valid claims.
    """
    valid_records = []

    for record in parsed_output:
        claims = record.get("claims", [])
        valid_claims = [claim for claim in claims if is_valid_claim(claim)]

        if valid_claims:
            valid_record = {"tax_id": record.get("tax_id"), "claims": valid_claims}
            valid_records.append(valid_record)

    return valid_records

def load_processed_files_index(index_path: str = PROCESSED_INDEX_FILE) -> set[str]:
    """
    Load processed base_names from a text file.
    One name per line.
    Returns a Python set for O(1) membership checks.
    """
    processed: set[str] = set()

    if not os.path.exists(index_path):
        # Nothing processed yet
        return processed

    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            name = line.strip()
            if name:
                processed.add(name)

    return processed


def append_processed_file_index(
    base_name: str, index_path: str = PROCESSED_INDEX_FILE
) -> None:
    """
    Append a single processed base_name to the index file.
    Thread-safe via a lock.
    """
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(index_path), exist_ok=True)

    with _processed_index_lock:
        # Just append a line; cheap even for large files
        with open(index_path, "a", encoding="utf-8") as f:
            f.write(base_name + "\n")