"""
Daily Scheduler Script for Voiclaim Pipeline

This script runs the complete pipeline daily:
1. Runs main.py to process audio files (default: yesterday's files, day=1)
2. After main.py completes successfully, runs app.py to send claims to API

Designed to be run by systemd timer or cron at 10:00 AM daily.
"""

import sys
import subprocess
import os
from datetime import datetime
from pathlib import Path
from utils.logging_utils import get_logger
from utils.config_loader import load_pipeline_config
from scripts.email_summary import main as send_log_summary_email

logger = get_logger(__name__)
config = load_pipeline_config()

# Get project root directory
PROJECT_ROOT = Path(__file__).parent.absolute()
MAIN_SCRIPT = PROJECT_ROOT / "main.py"
APP_SCRIPT = PROJECT_ROOT / "app.py"


def run_command(script_path: Path, args: list = None, description: str = "") -> tuple[bool, str]:
    """
    Run a Python script and return success status and output.

    Args:
        script_path: Path to the Python script to run
        args: List of command-line arguments
        description: Description of what the script does (for logging)

    Returns:
        tuple: (success: bool, output: str)
    """
    if args is None:
        args = []

    cmd = [sys.executable, str(script_path)] + args
    logger.info(f"🚀 Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            # timeout=14400,  # 4 hour timeout
        )

        # Log output
        if result.stdout:
            logger.info(f"📤 {description} stdout:\n{result.stdout}")
        if result.stderr:
            logger.warning(f"⚠️ {description} stderr:\n{result.stderr}")

        success = result.returncode == 0
        if success:
            logger.info(f"✅ {description} completed successfully (exit code: {result.returncode})")
        else:
            logger.error(f"❌ {description} failed with exit code: {result.returncode}")

        return success, result.stdout + result.stderr

    except subprocess.TimeoutExpired:
        logger.error(f"⏱️ {description} timed out after 2 hours")
        return False, "Timeout"
    except Exception as e:
        logger.exception(f"🔥 Exception running {description}: {e}")
        return False, str(e)


def main():
    """Main scheduler function that runs main.py then app.py."""
    start_time = datetime.now()
    logger.info("=" * 80)
    logger.info(f"📅 Daily Pipeline Scheduler Started at {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)

    # Validate scripts exist
    if not MAIN_SCRIPT.exists():
        logger.error(f"❌ main.py not found at {MAIN_SCRIPT}")
        sys.exit(1)
    if not APP_SCRIPT.exists():
        logger.error(f"❌ app.py not found at {APP_SCRIPT}")
        sys.exit(1)

    # Step 1: Run main.py (process files based on day_offset from config)
    # Get day_offset and cleanup_days from config file
    day_offset = config.get("day_offset", 1)  # Default to 1 (yesterday) if not configured
    cleanup_days = config.get("cleanup_days", 20)  # Default to 20 days if not configured
    logger.info(f"Using day_offset from config: {day_offset}")
    logger.info(f"Using cleanup_days from config: {cleanup_days}")
    
    logger.info("\n" + "=" * 80)
    logger.info("STEP 1: Running main.py (Audio Processing Pipeline)")
    logger.info("=" * 80)

    main_args = [
        "--day", str(day_offset),  # Use day_offset from config
        "--max-workers", "4",  # Adjust based on your system
        "--cleanup-days", str(cleanup_days),  # Use cleanup_days from config
    ]

    main_success, main_output = run_command(
        MAIN_SCRIPT,
        args=main_args,
        description="main.py (Audio Processing)"
    )

    if not main_success:
        logger.error("❌ main.py failed. Aborting pipeline. app.py will not run.")
        logger.error("Check logs for details.")
        sys.exit(1)

    # Step 2: Run app.py (API Integration)
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2: Running app.py (API Integration)")
    logger.info("=" * 80)

    app_success, app_output = run_command(
        APP_SCRIPT,
        args=[],
        description="app.py (API Integration)"
    )

    # Final summary
    end_time = datetime.now()
    duration = end_time - start_time

    logger.info("\n" + "=" * 80)
    logger.info("📊 Pipeline Scheduler Summary")
    logger.info("=" * 80)
    logger.info(f"Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"End Time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Total Duration: {duration}")
    logger.info(f"main.py Status: {'✅ SUCCESS' if main_success else '❌ FAILED'}")
    logger.info(f"app.py Status: {'✅ SUCCESS' if app_success else '❌ FAILED'}")
    logger.info("=" * 80)

    # Send comprehensive log summary email with statistics and CSV attachment
    logger.info("\n" + "=" * 80)
    logger.info("📧 Sending comprehensive pipeline summary email...")
    logger.info("=" * 80)

    email_sent = send_log_summary_email()

    if email_sent:
        logger.info("✅ Comprehensive summary email sent successfully")
    else:
        logger.warning("⚠️ Failed to send summary email (check email configuration in .env)")
    
    # Exit with error if app.py failed
    if not app_success:
        logger.error("❌ Pipeline completed with errors. Check logs above.")
        sys.exit(1)
    
    logger.info("✅ Daily pipeline completed successfully!")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.warning("⚠️ Scheduler interrupted by user (KeyboardInterrupt)")
        sys.exit(130)
    except Exception as e:
        logger.exception("🔥 Scheduler failed due to unexpected error")
        sys.exit(1)
