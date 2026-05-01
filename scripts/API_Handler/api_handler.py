"""
api_handler.py

Handles sending claim JSON files to a configured API endpoint for
post-processing AR call recordings. Can be run standalone for testing
or imported as a module in the pipeline.
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any

import requests
from dotenv import load_dotenv

from utils.analytics import save_metrics
from utils.config_loader import load_pipeline_config
from utils.pipeline_util import retry_stage, compute_audio_file_id
from utils.util_master import get_project_path, sanitize_claim, METRICS_DIR
from utils.logging_utils import get_logger
from scripts.DB.insert_audiofile import insert_extracted_claim_record
# Assume should_insert is determined earlier in your logic
should_insert = True  # or False, based on your condition
# ----------------------------------------------------------------------
# Load Config
# ----------------------------------------------------------------------
config = load_pipeline_config()
paths = config.get("paths", {})
PIPELINE_CFG = config.get("pipeline", {})
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

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


# ----------------------------------------------------------------------
# Setup Logging
# ----------------------------------------------------------------------
logger = get_logger("api_handler")

load_dotenv()

# ----------------------------------------------------------------------
# API Config
# ----------------------------------------------------------------------
# Standardized environment variable names across project
API_URL = os.getenv("POST_PROCESS_URL_PROD", "").strip()
API_HEADERS = {
    "Content-Type": os.getenv("CONTENT_TYPE_PROD", "application/json"),
    "x-va-deployment-key": os.getenv("DEPLOYMENT_KEY_PROD", ""),
    "x-va-senderagent-id": os.getenv("X_VA_SENDERAGENT_ID_PROD", ""),
}


# Request timeout (seconds), override via API_TIMEOUT_SEC env var
API_TIMEOUT_SEC = int(os.getenv("API_TIMEOUT_SEC", "30"))

# ----------------------------------------------------------------------
# Core Functions
# ----------------------------------------------------------------------
@retry_stage("App Integration")
def send_claims_to_api(json_path: str, api_url: str) -> Optional[requests.Response]:
    """
    Send extracted claim JSON to the API endpoint.
    
    This function is a wrapper around send_json_file_to_api specifically designed
    for sending claim data to the configured API endpoint. It includes retry logic
    and error handling for robust API communication.

    Args:
        json_path (str): Path to the JSON file containing claim data to send.
        api_url (str): The API endpoint URL to send the data to.

    Returns:
        Optional[requests.Response]: The HTTP response from the API if successful,
            None if the request failed after all retry attempts.

    Raises:
        FileNotFoundError: If the JSON file doesn't exist.
        requests.RequestException: If the API request fails after retries.
        
    Note:
        This function uses the retry_stage decorator for automatic retry logic
        in case of temporary failures.
    """
    return send_json_file_to_api(json_path, api_url=api_url)


def send_json_file_to_api(json_file_path: str, api_url: str = API_URL) -> Optional[requests.Response]:
    """
    Send a JSON file to the API endpoint.

    This function reads a JSON file and sends its contents to the specified
    API endpoint with proper error handling and retry logic.

    Args:
        json_file_path (str): Path to the JSON file to send.
        api_url (str, optional): The API endpoint URL. Defaults to API_URL.

    Returns:
        Optional[requests.Response]: The HTTP response if successful, None if failed.

    Raises:
        FileNotFoundError: If the JSON file doesn't exist.
        requests.RequestException: If the API request fails.
        json.JSONDecodeError: If the JSON file is malformed.
    """
    if not os.path.isfile(json_file_path):
        logger.error(f"File not found: {json_file_path}")
        return None

    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read JSON file {json_file_path}: {e}")
        return None

    # Extract audio details and compute AudioFileId (required by API)
    ar_details = payload.get("ARRecordingDetails", {})
    audio_file_name = ar_details.get("audio_file_name")
    audio_file_storage_id = ar_details.get("audio_file_storage_id")
    
    # Compute AudioFileId from audio_file_storage_id (API needs this to find the audio record)
    # This is computed the same way as in the pipeline to ensure consistency
    if audio_file_name and audio_file_storage_id:
        computed_audio_file_id = compute_audio_file_id(audio_file_storage_id, audio_file_name)
        # Add AudioFileId to the payload so API can find the audio record
        payload["ARRecordingDetails"]["audio_file_id"] = computed_audio_file_id
        logger.info(f"✅ Computed AudioFileId from audio_file_storage_id: {computed_audio_file_id[:50]}...")
    else:
        logger.warning(f"⚠️ Missing audio_file_name or audio_file_storage_id - cannot compute AudioFileId")
    
    claim_payload = ar_details.get("ClaimsList", [])[0]
    # sanitize the payload
    sanitized_payload = sanitize_claim(claim_payload)
    payload["ARRecordingDetails"]["ClaimsList"][0] = sanitized_payload

    if not isinstance(payload, dict):
        logger.error(f"Invalid payload format in {json_file_path}. Expected a dict.")
        return None

    if not api_url:
        logger.error("API URL is not configured. Set POST_PROCESS_URL.")
        return None

    # Check if claim_json_attributes_storage_id (claim_json_blob_id) is present in the payload
    claim_json_blob_id = sanitized_payload.get("claim_json_attributes_storage_id")
    if claim_json_blob_id:
        blob_id_len = len(claim_json_blob_id)
        logger.info(f"✅ claim_json_blob_id SENT - blob_id_len={blob_id_len}")
    else:
        logger.warning(f"❌ claim_json_blob_id NOT SENT - claim_json_attributes_storage_id is missing in payload")

    logger.info(f"📤 Sending JSON file to API: {json_file_path}")

    try:
        response = requests.post(api_url, json=payload, headers=API_HEADERS, timeout=API_TIMEOUT_SEC)
        logger.info(f"API responded with status {response.status_code}")
        
        # Log response in simple format
        try:
            response_json = response.json()
            logger.info(f"📋 [POST_PROCESS_URL_PROD] Response: Status={response.status_code}")
            for key, value in response_json.items():
                if isinstance(value, (str, int, float, bool, type(None))):
                    logger.info(f"   {key}: {value}")
                elif isinstance(value, list):
                    logger.info(f"   {key}: array with {len(value)} entries")
                elif isinstance(value, dict):
                    logger.info(f"   {key}: object")
        except json.JSONDecodeError:
            logger.warning(f"   ⚠️ Response is not valid JSON")
        except Exception as e:
            logger.warning(f"   ⚠️ Error parsing response: {e}")
        
        # Log error information for 400 Bad Request
        if response.status_code == 400:
            try:
                error_data = response.json()
                logger.error(f"❌ API returned 400 Bad Request: {error_data}")
            except:
                logger.error(f"   Response text: {response.text[:200]}")
        
        return response
    except requests.RequestException as e:
        logger.error(f"Network/API request failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during API request: {e}", exc_info=True)

    return None


def parse_api_response(api_response: requests.Response) -> tuple[bool, Optional[str]]:
    """
    Parse API response and determine claim success.

    success_flag is True only when the first Data entry has Status True and ClaimNumber
    is present (non-null, non-empty). This matches Recordings extract / DB_INSERT semantics.

    Returns:
        tuple[bool, Optional[str]]: (success_flag, claim_number)
        - success_flag: True if claim was attached and ClaimNumber is present
        - claim_number: ClaimNumber from response if available, None otherwise
    """
    claim_number = None
    try:
        data = api_response.json()
        
        # Log response in simple format
        logger.info(f"📋 [POST_PROCESS_URL_PROD] Parsing Response:")
        for key, value in data.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                logger.info(f"   {key}: {value}")
            elif isinstance(value, list):
                logger.info(f"   {key}: array with {len(value)} entries")
                # Log details of first entry if it's a list
                if value and isinstance(value[0], dict):
                    for idx, entry in enumerate(value[:1]):  # Only log first entry
                        logger.info(f"      [{idx}] {', '.join(f'{k}={v}' for k, v in entry.items() if isinstance(v, (str, int, float, bool, type(None))))}")
            elif isinstance(value, dict):
                logger.info(f"   {key}: object")
        
    except Exception as e:
        logger.warning(f"API response not JSON-decodable: {e}")
        return False, None

    if data.get("Status") != "success":
        logger.warning(f"API returned non-success status: {data.get('Status')}")
        return False, None

    claims = data.get("Data", [])
    if isinstance(claims, list) and claims:
        first_claim = claims[0]
        claim_status = first_claim.get("Status")
        logger.info(f"   ✅ Checking first entry Status: {claim_status}")
        
        # Extract ClaimNumber if Status is True (claim successfully attached)
        if claim_status is True:
            claim_number = first_claim.get("ClaimNumber")
            if claim_number:
                logger.info(f"   ✅ ClaimNumber extracted: {claim_number}")
            else:
                claim_status = False
                logger.warning(f"   ⚠️ Status is True but ClaimNumber is missing or None")

        return claim_status , claim_number

    logger.warning("'Data' field missing or empty in API response.")
    return False, None


def process_api_integration_sequential(
    base_folder: str,
    api_url: str,
    file_id_prefix: str = "API",
) -> Dict[str, Any]:
    """Process all JSON files inside subfolders sequentially."""
    base_path = Path(base_folder)
    summary = {"processed": 0, "success": 0, "failed": 0, "per_file": {}}

    for subfolder in base_path.iterdir():
        if not subfolder.is_dir():
            continue

        json_files = list(subfolder.glob("*.json"))
        for json_file in json_files:
            file_id = f"{file_id_prefix}_{json_file.stem}"
            start_time = time.time()
            logger.info(f"[{file_id}] Processing claim JSON: {json_file}")

            api_response = send_json_file_to_api(str(json_file), api_url=api_url)
            success_flag = False
            claim_number: Optional[str] = None
            status_code: Optional[int] = None
            try:
                if api_response:
                    status_code = api_response.status_code
                    success_flag, claim_number = parse_api_response(api_response)
                    if claim_number:
                        logger.info(f"[{file_id}] ✅ ClaimNumber from API: {claim_number}")
            except Exception as e:
                logger.warning(f"[{file_id}] Error parsing API response: {e}")

            elapsed = time.time() - start_time
            summary["processed"] += 1
            if success_flag:
                summary["success"] += 1
                dest_dir = Path(SUCCESS_DIR) / subfolder.name
                logger.info(f"[{file_id}] Claim success. Moving to {dest_dir}")
            else:
                summary["failed"] += 1
                dest_dir = Path(FAILED_DIR) / subfolder.name
                logger.info(f"[{file_id}] Claim failed. Moving to {dest_dir}")

            # Insert application outcome into DB extracts table
            try:
                with open(json_file, "r", encoding="utf-8") as jf:
                    payload = json.load(jf)

                claim_json_blob_id = None
                audiofile_name = None

                ar = payload.get("ARRecordingDetails", {})
                claims_list = ar.get("ClaimsList", [])
                if isinstance(claims_list, list) and claims_list:
                    claim_json_blob_id = claims_list[0].get("claim_json_attributes_storage_id")
                    if claim_json_blob_id:
                        logger.info(f"[{file_id}] ✅ Extracted claim_json_blob_id from JSON")
                    else:
                        logger.warning(f"[{file_id}] ⚠️ claim_json_attributes_storage_id is None or missing in JSON")

                audio_file_name = ar.get("audio_file_name")
                
                if audio_file_name:
                    # Convert .wav to .mp3 to match the original filename stored in database
                    # The JSON stores the cleaned audio filename (.wav), but the database
                    # stores the original audio filename (.mp3)
                    audiofile_name = os.path.splitext(audio_file_name)[0] + ".mp3"
                    logger.debug(f"[{file_id}] Converted audio_file_name: {audio_file_name} -> {audiofile_name}")

                if should_insert:
                    if audiofile_name is None or claim_json_blob_id is None:
                        logger.warning(
                            f"[{file_id}] Missing required field(s): claim_json_blob_id={claim_json_blob_id}, audiofile_name={audiofile_name}"
                        )
                    else:
                        logger.info(f"[{file_id}] 📤 Sending claim_json_blob_id to extracts API: audio_file={audiofile_name}")
                        try:
                            inserted = insert_extracted_claim_record(
                                status="success" if success_flag else "failed",
                                claim_json_blob_id=claim_json_blob_id,
                                audio_file_name=audiofile_name,
                                claim_number=claim_number  # Include ClaimNumber if available
                            )
                            if not inserted:
                                logger.warning(f"[{file_id}] Failed to record API outcome in DB extracts table.")
                            else:
                                logger.info(f"[{file_id}] DB extracts record inserted successfully.")
                        except Exception as e:
                            logger.warning(f"[{file_id}] Exception during DB insert: {e}")
                else:
                    logger.info(f"[{file_id}] DB inserts skipped as per configuration.")

            except Exception as db_err:
                logger.warning(f"[{file_id}] Could not record API outcome in DB: {db_err}", exc_info=True)

            dest_dir.mkdir(parents=True, exist_ok=True)
            json_file.rename(dest_dir / json_file.name)

            summary["per_file"][json_file.name] = {
                "success": success_flag,
                "elapsed_sec": elapsed,
                "status_code": status_code,
            }

        # Preserve folder structure even if empty - do NOT remove folders
        # This maintains organization in extracted_claims directory
        if not any(subfolder.iterdir()):  # folder is empty
            logger.debug(f"Preserving empty folder structure: {subfolder}")

    # Save metrics
    for file_name, metrics_row in summary["per_file"].items():
        row = {"file_name": file_name, **metrics_row}
        save_metrics(METRICS_DIR, row, "API_metrics.csv")

    summary_row = {
        "file_name": "TOTAL",
        "processed": summary["processed"],
        "success": summary["success"],
        "failed": summary["failed"]
    }
    save_metrics(METRICS_DIR, summary_row, "API_metrics.csv")

    logger.info(
        f"API integration completed. Processed: {summary['processed']}, "
        f"Success: {summary['success']}, Failed: {summary['failed']}"
    )
    return summary



if __name__ == "__main__":
    logger.info("Starting API Handler standalone test...")
    EXTRACTED_CLAIMS__test_DIR = get_project_path(paths["extracted_claims_dir"])
    test_summary = process_api_integration_sequential(EXTRACTED_CLAIMS__test_DIR, API_URL)
    logger.info(f"API Handler test completed. Summary: {test_summary}")
