"""
app.py

CLI to send extracted claim JSONs to an external API.
Reads configured directories, allows sending a single test file, or
iterates a folder. Logs a summary and returns a non-zero code on failures.
"""

import argparse
import time
from dotenv import load_dotenv
import os
from scripts.API_Handler.api_handler import (
    process_api_integration_sequential,
    send_json_file_to_api,
    parse_api_response,
)
from utils.config_loader import load_pipeline_config
from utils.util_master import get_project_path
from utils.logging_utils import get_logger
from utils import report_metrics

# ----------------------------------------------------------------------
# Load Config
# ----------------------------------------------------------------------
config = load_pipeline_config()
paths = config.get("paths", {})
PIPELINE_CFG = config.get("pipeline", {})

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


ARCHIVE_DIR = "/CosmosAI/voicedata/local_data_source/archive/"

# ----------------------------------------------------------------------
# Setup Logging
# ----------------------------------------------------------------------
logger = get_logger("app")


load_dotenv()

# ----------------------------------------------------------------------
# API Config
# ----------------------------------------------------------------------
# API Configuration - Standardized environment variable naming
# Use POST_PROCESS_URL for consistency across the codebase
API_URL = os.getenv("POST_PROCESS_URL_PROD", "").strip()
API_HEADERS = {
    "Content-Type": os.getenv("CONTENT_TYPE_PROD", "application/json"),
    "x-va-deployment-key": os.getenv("DEPLOYMENT_KEY_PROD", ""),
    "x-va-senderagent-id": os.getenv("X_VA_SENDERAGENT_ID_PROD", ""),
}


# ----------------------------------------------------------------------
# CLI Entrypoint
# ----------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Run API integration for claim JSON files.")
    parser.add_argument(
        "--base_folder",
        type=str,
        default=str(EXTRACTED_CLAIMS_DIR),
        help="Base folder containing extracted claim JSONs",
    )
    parser.add_argument(
        "--test_file",
        type=str,
        help="Optional single test JSON file to send",
    )
    args = parser.parse_args()

    if args.test_file:
        logger.info(f"▶️ Running in TEST mode with file: {args.test_file}")
        response = send_json_file_to_api(args.test_file)
        if response:
            success_flag, claim_number = parse_api_response(response)
            if success_flag:
                logger.info("✅ Test claim processed successfully.")
                if claim_number:
                    logger.info(f"✅ ClaimNumber: {claim_number}")
                exit(0)
            else:
                logger.error("❌ Test claim failed.")
                exit(1)
        else:
            logger.error("❌ Test claim failed - no response from API.")
            exit(1)

    else:
        if not API_URL:
            logger.error("❌ API URL is missing. Set POST_PROCESS_URL environment variable.")
            exit(1)
        app_start_time = time.time()
        summary = process_api_integration_sequential(
            base_folder=args.base_folder,
            api_url=API_URL,
            file_id_prefix="CLAIM",
        )
        app_elapsed_sec = time.time() - app_start_time
        total_claims = summary.get("processed", 0)
        claims_attached_success = summary.get("success", 0)
        logger.info(
            f"📊 Pipeline Summary: processed={total_claims} "
            f"success={claims_attached_success} failed={summary.get('failed')}"
        )
        logger.info(
            f"APP_RUN_COMPLETE total_elapsed_sec={app_elapsed_sec:.2f} "
            f"total_claims_extracted={total_claims} claims_attached_success={claims_attached_success}"
        )
        report_metrics.record_app_run(total_claims, claims_attached_success)
        exit(0 if summary["failed"] == 0 else 2)


if __name__ == "__main__":
    main()
