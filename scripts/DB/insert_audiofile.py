# import uuid  # Unused import removed
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import os
import requests
import json
import re

from utils.config_loader import load_pipeline_config
import argparse
from utils.logging_utils import get_logger
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Setup logger
logger = get_logger("db_logger")

# ----------------------------
# Load config & API settings
# ----------------------------
config = load_pipeline_config()

# API Configuration - All values from .env file
API_BASE_URL = os.getenv("DB_INSERT_API_BASE_URL")
API_ENDPOINT_AUDIO_RECORDINGS = os.getenv("DB_INSERT_API_AUDIO_URL")
API_ENDPOINT_RECORDING_EXTRACTS = os.getenv("DB_INSERT_API_EXTRACTS_URL")
API_TIMEOUT = int(os.getenv("API_TIMEOUT_SEC", "30"))



# API Headers - configurable via environment variables
API_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# Add authentication headers if configured
auth_token = os.getenv("DB_INSERT_API_AUTH_TOKEN")
if auth_token:
    API_HEADERS["Authorization"] = f"Bearer {auth_token}"

api_key = os.getenv("DB_INSERT_API_KEY")
if api_key:
    API_HEADERS["x-api-key"] = api_key

# ----------------------------
# API Client Functions
# ----------------------------
def make_api_request(payload: Any, endpoint: str) -> Optional[requests.Response]:
    """
    Makes an API request to the specified endpoint with the given payload.
    
    Args:
        payload: The data to send to the API
        endpoint: The API endpoint URL (required - use API_ENDPOINT_AUDIO_RECORDINGS or API_ENDPOINT_RECORDING_EXTRACTS)
        
    Returns:
        requests.Response if successful, None if failed
    """
    try:
        logger.info(f"📤 Making API request to: {endpoint}")
        logger.debug(f"📤 Payload: {json.dumps(payload, indent=2, default=str)}")
        
        response = requests.post(
            endpoint,
            json=payload,
            headers=API_HEADERS,
            timeout=API_TIMEOUT
        )
        
        logger.info(f"✅ API responded with status {response.status_code}")
        
        # Log response in simple format
        api_name = "InsertDataClaimCallAudioRecordings" if "AudioRecordings" in endpoint else "InsertDataClaimCallRecordingExtracts"
        logger.info(f"📋 [{api_name}] Response: Status={response.status_code}")
        
        try:
            response_json = response.json()
            # Log key fields simply
            for key, value in response_json.items():
                if isinstance(value, (str, int, float, bool, type(None))):
                    logger.info(f"   {key}: {value}")
                elif isinstance(value, (dict, list)):
                    logger.info(f"   {key}: {type(value).__name__}")
        except json.JSONDecodeError:
            logger.warning(f"   ⚠️ Response is not valid JSON: {response.text[:200]}")
        except Exception as e:
            logger.warning(f"   ⚠️ Error parsing response: {e}")
        
        # Log error details for non-200 status
        if response.status_code != 200:
            try:
                error_details = response.json()
                logger.warning(f"⚠️ API Error: {error_details}")
            except:
                logger.warning(f"⚠️ API Error Response: {response.text[:200]}")
        
        return response
        
    except requests.RequestException as e:
        logger.error(f"❌ API request failed: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"❌ Unexpected error during API request: {e}", exc_info=True)
        return None


def parse_api_response(response: requests.Response) -> bool:
    """
    Parse API response and determine if the operation was successful.
    
    Args:
        response: The API response object
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        if response.status_code not in [200, 201]:
            logger.error(f"❌ API returned non-success status code: {response.status_code}")
            # Log additional error details for 400 Bad Request
            if response.status_code == 400:
                try:
                    error_details = response.json()
                    logger.error(f"❌ Bad Request Details: {json.dumps(error_details, indent=2)}")
                except:
                    logger.error(f"❌ Bad Request Response: {response.text}")
            return False
            
        # Try to parse JSON response
        try:
            data = response.json()
            
            # Handle different response types
            if isinstance(data, bool):
                # API returned a boolean directly (true = success, false = failure)
                logger.info(f"   Response: {data}")
                return data
            
            elif isinstance(data, dict):
                # Log key values simply
                for key, value in data.items():
                    if isinstance(value, (str, int, float, bool, type(None))):
                        logger.info(f"   {key}: {value}")
                    elif isinstance(value, list):
                        logger.info(f"   {key}: array with {len(value)} entries")
                    elif isinstance(value, dict):
                        logger.info(f"   {key}: object with {len(value)} fields")
                
                # Check for failure status first (explicit failure)
                status = data.get("status") or data.get("Status")
                if status and isinstance(status, str) and status.lower() == "failure":
                    status_message = data.get("statusMessage") or data.get("StatusMessage") or "Unknown error"
                    logger.error(f"❌ API returned failure status: {status_message}")
                    return False
                
                # Check for success indicators in the response
                # Adjust these conditions based on your API's response format
                success_indicators = ["success", "Success", "status", "Status"]
                for indicator in success_indicators:
                    if indicator in data:
                        value = data[indicator]
                        if isinstance(value, bool):
                            return value
                        elif isinstance(value, str):
                            return value.lower() in ["success", "ok", "true"]
                        elif isinstance(value, int):
                            return value == 1
                
                # If no specific success indicator found, assume success for 200/201 status
                return True
                
            elif isinstance(data, list):
                # API returned a list - log it and assume success for 200/201 status
                logger.info(f"   Response: array with {len(data)} entries")
                return True
                
            elif isinstance(data, (str, int, float)):
                # API returned a primitive value - log it and assume success for 200/201 status
                logger.info(f"   Response: {data}")
                return True
                
            else:
                # Unknown response type - log it and assume success for 200/201 status
                logger.info(f"   Response: {type(data).__name__}")
                return True
            
        except json.JSONDecodeError:
            # If response is not JSON, check if it's a successful HTTP status
            logger.warning("⚠️ API response is not JSON, checking HTTP status only")
            return response.status_code in [200, 201]
            
    except Exception as e:
        logger.error(f"❌ Error parsing API response: {e}", exc_info=True)
        return False


# ----------------------------
# API-based Insert functions
# ----------------------------
def _extract_file_processed_date(file_name: str) -> Optional[str]:
    """
    Extracts a processed date from the audio file name.

    Expected pattern (underscore-separated), e.g.:
        12345678-5678-1234-ab12-1234567890987_98765_123456789_01012026_123456.mp3

    Where the 4th token is an 8-digit date in DDMMYYYY format:
        01012026 -> 2026-01-01

    Returns ISO date string (YYYY-MM-DD) or None if parsing fails.
    """
    try:
        base = os.path.splitext(os.path.basename(file_name))[0]
        parts = base.split("_")
        if len(parts) < 4:
            return None

        date_token = parts[3]
        if not re.fullmatch(r"\d{8}", date_token):
            return None

        dt = datetime.strptime(date_token, "%d%m%Y")
        return dt.date().isoformat()
    except Exception:
        return None


def insert_claim_audio_record(
    file_name: str,
    processed_status: str,
    elapsed_time: Optional[float] = None,
    stage: Optional[str] = None,
    extracted_claims: Optional[list] = None,
    total_claim_count: Optional[int] = None,
    processing_start: Optional[datetime] = None,
    processing_end: Optional[datetime] = None,

    audio_blob_id: Optional[str] = None,
    transcript_blob_id: Optional[str] = None,
) -> bool:
    """
    Inserts a record into ClaimCallAudioRecordings table via API.
    Returns: bool - True if inserted successfully, False otherwise
    """
    if not file_name:
        raise ValueError("file_name is required and cannot be empty")
    if not processed_status:
        raise ValueError("processed_status is required and cannot be empty")

    now = datetime.now(timezone.utc)
    processing_start = processing_start or now
    processing_end = processing_end or now

    # If claims are passed, count them
    if extracted_claims:
        total_claim_count = sum(
            len(rec.get("ARRecordingDetails", {}).get("ClaimsList", [])) for rec in extracted_claims
        )

    # Prepare API payload - wrap in the required field name
    audio_record = {
        "FileName": file_name,
        "ProcessedStatus": processed_status,
        "TotalClaimCount": total_claim_count,
        "FileProcessedDate": _extract_file_processed_date(file_name),
        "ProcessingStartDate": processing_start.isoformat(),
        "ProcessingEndDate": processing_end.isoformat(),
        "UpdatedDate": now.isoformat(),
        "AudioBlobId": audio_blob_id,
        "TranscriptBlobId": transcript_blob_id,
    }

    # Try sending as array directly (some APIs expect this)
    payload = [audio_record]

    try:
        response = make_api_request(payload, API_ENDPOINT_AUDIO_RECORDINGS)
        if response is None:
            logger.error(f"❌ API request failed for {file_name}")
            return False

        success = parse_api_response(response)
        if success:
            logger.info(
                f"🟢 API Insert Success →  filename={file_name}, "
                f"status={processed_status}, stage={stage}, elapsed={elapsed_time}, "
                f"claims={total_claim_count}"
            )
            return True
        else:
            logger.error(f"❌ API Insert Failed for {file_name}: Invalid response")
            return False

    except Exception as e:
        logger.error(f"❌ API Insert Failed for {file_name}: {e}", exc_info=True)
        return False


# ----------------------------
# Insert extracted claim record via API
# ----------------------------
def insert_extracted_claim_record(
    status: str,
    claim_json_blob_id: Optional[str] = None,
    audio_file_name: Optional[str] = None,
    claim_number: Optional[str] = None,
    created_date: Optional[datetime] = None,
    updated_date: Optional[datetime] = None,
) -> bool:
    """
    Inserts a row into ClaimCallRecordingExtracts to track application/API outcomes per claim via API.

    Args:
        status: Processing status (e.g., 'success', 'failed').
        claim_json_blob_id: Storage ID for the claim JSON. Can be any length (no truncation).
        audio_file_name: Audio file name (e.g., 'file.mp3'). API will look up the audio record by this.
        claim_number: ClaimNumber from POST_PROCESS_URL_PROD response (when claim is successfully attached).
        created_date: Optional override for CreatedDate. Defaults to UTC now if not provided.
        updated_date: Optional override for UpdatedDate. Defaults to UTC now if not provided.

    Returns:
        bool: True if insert succeeded, False otherwise.
    
    Note:
        We don't send audioFileId because:
        1. API expects integer (database ID from ClaimCallAudioRecordings table), not UUID string
        2. API can look up the audio record by audioFileName
    """
    if not status:
        raise ValueError("status is required and cannot be empty")

    now_utc = datetime.now(timezone.utc)
    created_date = created_date or now_utc
    updated_date = updated_date or now_utc

    # Prepare API payload for ClaimCallRecordingExtracts
    # Note: audioFileId should be an integer (database ID), not a UUID string
    # Since we don't have the actual DB ID, we omit it and let the API look it up by audioFileName
    extract_record = {
        "audioFileName": audio_file_name,
        # Omit audioFileId - API will look it up by audioFileName
        # If we had the actual database ID (integer), we would include: "audioFileId": <int>
        "status": status,
        "claimJsonBlobId": claim_json_blob_id,
        "createdDate": created_date.isoformat(),
        "updatedDate": updated_date.isoformat(),
    }
    
    # Include ClaimNumber if available (from POST_PROCESS_URL_PROD response)
    if claim_number:
        extract_record["claimNumber"] = claim_number
        logger.info(f"📋 Including ClaimNumber in payload: {claim_number}")
    
    # API expects a list/array directly (not wrapped in an object)
    # The error message indicates: "could not be converted to System.Collections.Generic.List"
    # This means the endpoint expects: [extract_record] not {"claimCallRecordingExtracts": [extract_record]}
    payload = [extract_record]

    try:
        response = make_api_request(payload, API_ENDPOINT_RECORDING_EXTRACTS)
        if response is None:
            logger.error(f"❌ API request failed for claim extract with status: {status}")
            return False

        success = parse_api_response(response)
        
        if success:
            logger.info(f"🟢 API Insert Success → status={status}")
            return True
        else:
            logger.error(f"❌ API Insert Failed for claim extract: Invalid response")
            return False

    except Exception as e:
        logger.error(f"❌ API Insert Failed for claim extract: {e}", exc_info=True)
        return False


# ----------------------------
# Example usage / test
# ----------------------------
def test_api_with_minimal_data() -> bool:
    """
    Test API with minimal valid data to identify required fields.
    """
    logger.info("🧪 Testing API with minimal data...")
    
    # Test audio recordings API with minimal data
    minimal_audio_record = {
        "FileName": "test_connection.wav",
        "ProcessedStatus": "Pending",
        "TotalClaimCount": 3,
        "FileProcessedDate": "2025-01-01",
        "ProcessingStartDate": "2025-01-01T00:00:00Z",
        "ProcessingEndDate": "2025-01-01T00:01:00Z",
        "UpdatedDate": "2025-01-01T00:01:00Z"
    }
    minimal_audio_payload = [minimal_audio_record]
    
    # Test recording extracts API with minimal data
    minimal_extract_record = {
        "AudioFileName": "test_connection.wav",
        "Status": "Processed",
        "ClaimJsonBlobId": "test_claim_blob_minimal",
        "CreatedDate": "2025-01-01T00:00:00Z",
        "UpdatedDate": "2025-01-01T00:01:00Z"
    }
    minimal_extracts_payload = [minimal_extract_record]
    
    logger.info("🎵 Testing Audio Recordings API with minimal data...")
    audio_response = make_api_request(minimal_audio_payload, API_ENDPOINT_AUDIO_RECORDINGS)
    
    logger.info("📋 Testing Recording Extracts API with minimal data...")
    extracts_response = make_api_request(minimal_extracts_payload, API_ENDPOINT_RECORDING_EXTRACTS)
    
    return audio_response is not None or extracts_response is not None


def test_api_connection() -> bool:
    """
    Test API connection by making a simple request to both endpoints.
    Returns True if at least one connection is successful, False otherwise.
    """
    try:
        # Test payload for audio recordings endpoint - matches expected API format
        test_audio_record = {
            "FileName": "test_connection.wav",
            "ProcessedStatus": "test",
            "TotalClaimCount": 4,
            "FileProcessedDate": datetime.now(timezone.utc).date().isoformat(),
            "ProcessingStartDate": datetime.now(timezone.utc).isoformat(),
            "ProcessingEndDate": datetime.now(timezone.utc).isoformat(),
            "UpdatedDate": datetime.now(timezone.utc).isoformat(),
            "JsonBlobId": "test_json_blob",
            "AudioBlobId": "test_audio_blob",
            "TranscriptBlobId": "test_transcript_blob",
        }
        test_payload_audio = [test_audio_record]
        
        # Test payload for recording extracts endpoint - matches expected API format
        test_extract_record = {
            "Status": "Processed",
            "AudioFileName": "test_connection.wav",
            "ClaimJsonBlobId": "test_claim_blob_123",
            "CreatedDate": datetime.now(timezone.utc).isoformat(),
            "UpdatedDate": datetime.now(timezone.utc).isoformat(),
        }
        test_payload_extracts = [test_extract_record]
        
        # Test both endpoints
        audio_response = make_api_request(test_payload_audio, API_ENDPOINT_AUDIO_RECORDINGS)
        extracts_response = make_api_request(test_payload_extracts, API_ENDPOINT_RECORDING_EXTRACTS)
        
        if audio_response is not None or extracts_response is not None:
            logger.info("🟢 API connection test succeeded (at least one endpoint accessible).")
            return True
        else:
            logger.error("❌ API connection test failed for both endpoints.")
            return False
            
    except Exception as e:
        logger.error(f"❌ API connection test failed with exception: {e}")
        return False


if __name__ == "__main__":
    # Test API connection
    logger.info("🔍 Testing API connection...")
    test_api_connection()
    
    # Test with minimal data to identify issues
    logger.info("\n🔍 Testing with minimal data...")
    test_api_with_minimal_data()
    
    # Example usage of the API-based functions
    logger.info("\n📝 Example usage:")
    logger.info("insert_claim_audio_record('test_file.wav', 'success')")
    logger.info("insert_extracted_claim_record('success', 'blob_id_123')")
