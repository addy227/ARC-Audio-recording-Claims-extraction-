"""
blob_storage_handler.py

Handles uploading and downloading files to/from blob storage for the Voiclaim pipeline.
Includes metrics logging and robust error handling for auditing and monitoring.

Typical usage:
    - As a module for uploading claim files to blob storage.
    - As a script for manual upload/download testing.

Author: Your Name
Created: YYYY-MM-DD
"""

import os
# import base64  # Unused import removed
import csv
import logging
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

from utils.config_loader import load_pipeline_config
from utils.util_master import get_project_path

# ---------------------------------------------------------------------
# Module Setup
# ---------------------------------------------------------------------

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

load_dotenv()

# Load pipeline configuration
config = load_pipeline_config()
paths = config["paths"]
llm = config.get("llm", {})

# Directories
TRANSCRIPTS_DIR = get_project_path(paths["transcripts_dir"])
EXTRACTED_CLAIMS_DIR = get_project_path(paths["extracted_claims_dir"])
LOG_DIR = get_project_path(paths["log_dir"])
CLEANED_AUDIO_DIR = get_project_path(paths["cleaned_audio_dir"])
METRICS_DIR = get_project_path(paths["metrics_dir"])

# Files
REPORT_FILE = os.path.join(EXTRACTED_CLAIMS_DIR, "summary_report.json")
UPLOAD_METRICS_FILE = os.path.join(METRICS_DIR, "blob_upload_metrics.csv")
DOWNLOAD_METRICS_FILE = os.path.join(METRICS_DIR, "blob_download_metrics.csv")

# Environment Variables
UPLOAD_URL = os.getenv("Prod_UPLOAD_URL")
DOWNLOAD_URL = os.getenv("Prod_DOWNLOAD_URL")
UPLOAD_HEADERS = {
    "x-va-hash": os.getenv("Prod_X_VA_HASH"),
    "x-va-transaction-id": os.getenv("Prod_X_VA_TRANSACTION_ID"),
    "x-va-senderagent-id": os.getenv("Prod_X_VA_SENDERAGENT_ID"),
    "Content-Type": os.getenv("CONTENT_TYPE"),
    "CONTAINERNAME": os.getenv("Prod_CONTAINERNAME"),
}
# UPLOAD_URL = os.getenv("TEST_UPLOAD_URL")
# DOWNLOAD_URL = os.getenv("TEST_DOWNLOAD_URL")
# UPLOAD_HEADERS = {
#     "x-va-hash": os.getenv("Prod_X_VA_HASH"),
#     "x-va-transaction-id": os.getenv("TEST_X_VA_TRANSACTION_ID"),
#     "x-va-senderagent-id": os.getenv("TEST_X_VA_SENDERAGENT_ID"),
#     "Content-Type": os.getenv("CONTENT_TYPE"),
#     "CONTAINERNAME":os.getenv("Prod_CONTAINERNAME")
# }
# Validate blob storage configuration on module load
if not UPLOAD_URL:
    logger.warning("⚠️ WARNING: Prod_UPLOAD_URL environment variable is not set. Blob storage uploads will fail.")
if not all(UPLOAD_HEADERS.values()):
    missing = [k for k, v in UPLOAD_HEADERS.items() if not v]
    logger.warning(f"⚠️ WARNING: Missing blob storage header environment variables: {missing}. Blob storage uploads will fail.")



# Test data
TEST_FILE_PATH = os.path.join(
    TRANSCRIPTS_DIR, "39d50467-6ecb-43f1-aab7-f1151100680d_5165_8662234347_06062025_151317.txt"
)
DOWNLOAD_SAVE_DIR = os.path.join(TRANSCRIPTS_DIR, "downloads")


# ---------------------------------------------------------------------
# Metrics Logging
# ---------------------------------------------------------------------


def _log_metric(
    file_path: str,
    file_id: Optional[str],
    status_code: str,
    success: bool,
    metrics_file: str,
    error_msg: str = "",
) -> None:
    """Append a row to a metrics CSV file."""
    fieldnames = ["timestamp", "file_name", "file_id", "status_code", "success", "error_msg"]
    row = {
        "timestamp": datetime.now().isoformat(),
        "file_name": os.path.basename(file_path) if file_path else "",
        "file_id": file_id or "",
        "status_code": status_code,
        "success": success,
        "error_msg": error_msg or "",
    }
    write_header = not os.path.exists(metrics_file)
    with open(metrics_file, "a", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------
# Upload Function
# ---------------------------------------------------------------------


def upload_file_to_blob(file_path: str) -> Optional[str]:
    """
    Upload a file to the blob storage endpoint and return the file ID.

    Args:
        file_path: Path to the file to upload.

    Returns:
        File ID string if upload is successful, None otherwise.
    """
    if not os.path.isfile(file_path):
        logger.error(f"File not found: {file_path}")
        return None

    # Validate environment variables are set
    if not UPLOAD_URL:
        logger.error("❌ UPLOAD_URL environment variable (Prod_UPLOAD_URL) is not set. Cannot upload to blob storage.")
        return None
    
    missing_headers = [key for key, value in UPLOAD_HEADERS.items() if not value]
    if missing_headers:
        logger.error(f"❌ Missing required blob storage headers: {missing_headers}. Cannot upload to blob storage.")
        return None

    file_name = os.path.basename(file_path)
    logger.info(f"📤 Uploading file to blob storage: {file_name}")

    try:
        with open(file_path, "rb") as f:
            file_content = f.read()

        if not file_content:
            logger.warning(f"⚠️ File is empty: {file_name}")
            return None

        logger.debug(f"Uploading to: {UPLOAD_URL}")
        response = requests.post(UPLOAD_URL, headers=UPLOAD_HEADERS, data=file_content, timeout=30)
        
        # Log response in simple format
        if response.status_code >= 400:
            try:
                error_json = response.json()
                import json
                logger.error(f"❌ [Blob Storage Upload] Error: Status={response.status_code}, {error_json}")
            except:
                logger.error(f"❌ [Blob Storage Upload] Error: Status={response.status_code}, {response.text[:200]}")
        else:
            try:
                result = response.json()
                import json
                logger.info(f"📋 [Blob Storage Upload] Response: Status={response.status_code}")
                for key, value in result.items():
                    if isinstance(value, (str, int, float, bool, type(None))):
                        logger.info(f"   {key}: {value}")
            except json.JSONDecodeError:
                logger.warning(f"   ⚠️ Response is not valid JSON")
            except Exception as e:
                logger.warning(f"   ⚠️ Error parsing response: {e}")
        
        response.raise_for_status()

        result = response.json()
        file_id = result.get("fileid")

        if file_id:
            # Strip whitespace and newlines from blob ID (common issue)
            file_id = file_id.strip()
            logger.info(f"✅ Upload successful: {file_name}")
            _log_metric(file_path, file_id, str(response.status_code), True, UPLOAD_METRICS_FILE)
            return file_id
        else:
            logger.warning(f"⚠️ No file ID returned in response for {file_name}")
            _log_metric(
                file_path,
                None,
                str(response.status_code),
                False,
                UPLOAD_METRICS_FILE,
                "No file ID returned",
            )
            return None

    except requests.exceptions.Timeout:
        logger.error(f"❌ Upload timeout for {file_name} - blob storage endpoint did not respond in time")
        _log_metric(file_path, None, "TIMEOUT", False, UPLOAD_METRICS_FILE, "Request timeout")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.error(f"❌ Connection error uploading {file_name} - cannot reach blob storage endpoint: {e}")
        _log_metric(file_path, None, "CONNECTION_ERROR", False, UPLOAD_METRICS_FILE, f"Connection error: {e}")
        return None
    except requests.RequestException as e:
        status_code = getattr(e.response, "status_code", "N/A") if hasattr(e, "response") else "N/A"
        response_text = getattr(e.response, "text", "") if hasattr(e, "response") and e.response else ""
        
        # Log error response for 400 errors
        if status_code == 400 and hasattr(e, "response") and e.response:
            try:
                error_json = e.response.json()
                logger.error(f"❌ Error Response: {error_json}")
            except:
                logger.error(f"❌ Error Response: {response_text[:200]}")
        
        logger.error(
            f"❌ Upload failed for {file_name}: {e} | Status: {status_code}",
            exc_info=True
        )
        _log_metric(
            file_path,
            None,
            str(status_code),
            False,
            UPLOAD_METRICS_FILE,
            str(e),
        )
        return None
    except Exception as e:
        logger.error(f"❌ Unexpected error during upload for {file_name}: {e}", exc_info=True)
        _log_metric(file_path, None, "N/A", False, UPLOAD_METRICS_FILE, str(e))
        return None


# ---------------------------------------------------------------------
# Download Function
# ---------------------------------------------------------------------


def download_file_from_blob(file_id: str, save_dir: str = "./downloads") -> Optional[str]:
    """
    Download a file from blob storage using the file ID and save it locally.

    Args:
        file_id: The file ID to download.
        save_dir: Directory to save the downloaded file.

    Returns:
        Path to the saved file, or None on failure.
    """
    if not file_id:
        logger.error("No file ID provided for download.")
        return None

    payload = {"id": file_id}
    file_name = os.path.basename(TEST_FILE_PATH)
    logger.info(f"Uploading file: {file_name}")

    try:
        response = requests.post(DOWNLOAD_URL, json=payload)
        
        # Log response in simple format
        logger.info(f"📋 [Blob Storage Download] Response: Status={response.status_code}, Size={len(response.content)} bytes")
        
        # Try to parse JSON if possible
        try:
            response_json = response.json()
            import json
            for key, value in response_json.items():
                if isinstance(value, (str, int, float, bool, type(None))):
                    logger.info(f"   {key}: {value}")
        except json.JSONDecodeError:
            logger.info(f"   Response is binary/raw data (not JSON)")
        except Exception as e:
            logger.warning(f"   ⚠️ Error parsing response: {e}")
        
        response.raise_for_status()

        # get raw bytes (not JSON / not base64)
        file_content = response.content

        # Make sure the parent directory exists
        os.makedirs(save_dir, exist_ok=True)

        file_path = os.path.join(save_dir, file_name)

        with open(file_path, "wb") as f:
            f.write(file_content)

        logger.info(f"File downloaded to: {file_path}")
        _log_metric(file_name, file_id, str(response.status_code), True, DOWNLOAD_METRICS_FILE)
        return file_path

    except requests.RequestException as e:
        logger.error(f"Download failed for file ID {file_id}: {e}", exc_info=True)
        _log_metric(
            file_id,
            None,
            getattr(e.response, "status_code", "N/A"),
            False,
            DOWNLOAD_METRICS_FILE,
            str(e),
        )
    except Exception as e:
        logger.error(f"Unexpected error during download for file ID {file_id}: {e}", exc_info=True)
        _log_metric(file_id, None, "N/A", False, DOWNLOAD_METRICS_FILE, str(e))
    return None


# ---------------------------------------------------------------------
# CLI Test Utilities
# ---------------------------------------------------------------------


def run_upload() -> Optional[str]:
    """Test upload with a predefined file path."""
    if not os.path.exists(TEST_FILE_PATH):
        logger.error(f"Test file not found: {TEST_FILE_PATH}")
        return None
    return upload_file_to_blob(TEST_FILE_PATH)


def run_download(file_id: str) -> None:
    """Test download given a file ID."""
    if not file_id:
        logger.error("No file ID provided for download test.")
        return
    download_file_from_blob(file_id, save_dir=DOWNLOAD_SAVE_DIR)


if __name__ == "__main__":
    # fid = run_upload()
    # if fid:
    star_time = datetime.now()
    fid = "cWM1ajdGT1NWcVdMSTk1UG5naEJDa3JtWE1VemU2OFN5TnFvbWlDdGhPRDRyeitVTzlRbCtIKzcxYmZXZzlaL2ZLWFFSRFNGUURsUE9hVHYrRkp2cGV3VjJHT3Z1MlpBTFlVTGhGL1huZWh0RXZFSGJ0eTlraEtZNTBhRVhwT3lRRFlVa0lGZEVFd3ZablFtRVZmN2hUclZQQzBhbVhwajlJeTFRdlRuQXFBNnJuN3dKN3YyRUhmcUVqQ0hLa0F3SlptRGthcGUzZ0QzOGxxWm15QjhYbDJIdVNPUnYwSGI4RnI2VDIxZm1CRTliUklaaVNleXBzSXBJclRGWlB3VWcyTFowQkd2aklvTzhicFFSRTMzT1BNMkdBeEx1WThVN0VORzFGYzh2Tm5QN0dCSnFLdTVxcDg1VUg2VkZGN0ZTSHNWc3RERVh5d3pBZkxmaW8zY2c4UEdPQ24xWGF6eWM0ZXVxR3VreU1oRjhlOGN4dFpFNmRqZ25vbGg1Z1hWMFJST2s3c0NiTFdmVlRITW5Lb28wMURjbnhNVHpaSTVrTmdacUJydWUyWDU5TXV5aFRLRVVFQ1Nic05B"
    run_download(fid)
    end_time = datetime.now()
    print(f"Download time: {end_time - star_time}")
