"""
Catch-up Processor Script

Processes audio files from a start date to today.
Useful for backfilling missing data or processing historical files.

Usage:
    python catchup_processor.py --start-date 2026-01-01
    python catchup_processor.py --start-date 2026-01-01 --end-date 2026-01-10
"""

import sys
import subprocess
import argparse
from datetime import datetime, timedelta, date
from pathlib import Path
from utils.logging_utils import get_logger

logger = get_logger(__name__)

# Get project root directory
PROJECT_ROOT = Path(__file__).parent.absolute()
MAIN_SCRIPT = PROJECT_ROOT / "main.py"
APP_SCRIPT = PROJECT_ROOT / "app.py"


def calculate_day_offset(target_date: date) -> int:
    """
    Calculate day offset from today to target date.
    
    Args:
        target_date: Target date to process
        
    Returns:
        int: Day offset (positive number)
    """
    today = datetime.today().date()
    delta = today - target_date
    return delta.days


def run_main_for_date(target_date: date, max_workers: int = 4) -> tuple[bool, str]:
    """
    Run main.py for a specific date.
    
    Args:
        target_date: Date to process
        max_workers: Number of parallel workers
        
    Returns:
        tuple: (success: bool, output: str)
    """
    day_offset = calculate_day_offset(target_date)
    
    cmd = [
        sys.executable,
        str(MAIN_SCRIPT),
        "--day", str(day_offset),
        "--max-workers", str(max_workers),
        "--cleanup-days", "0",  # Skip cleanup during catch-up
    ]
    
    logger.info(f"🚀 Processing date {target_date} (day_offset={day_offset})")
    logger.info(f"Command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=7200,  # 2 hour timeout per day
        )
        
        if result.stdout:
            logger.info(f"📤 Output:\n{result.stdout}")
        if result.stderr:
            logger.warning(f"⚠️ Stderr:\n{result.stderr}")
        
        success = result.returncode == 0
        if success:
            logger.info(f"✅ Date {target_date} processed successfully")
        else:
            logger.error(f"❌ Date {target_date} failed with exit code: {result.returncode}")
        
        return success, result.stdout + result.stderr
        
    except subprocess.TimeoutExpired:
        logger.error(f"⏱️ Date {target_date} timed out")
        return False, "Timeout"
    except Exception as e:
        logger.exception(f"🔥 Exception processing date {target_date}: {e}")
        return False, str(e)


def run_app() -> tuple[bool, str]:
    """Run app.py to send claims to API."""
    cmd = [sys.executable, str(APP_SCRIPT)]
    
    logger.info("🚀 Running app.py (API Integration)")
    logger.info(f"Command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=3600,  # 1 hour timeout
        )
        
        if result.stdout:
            logger.info(f"📤 Output:\n{result.stdout}")
        if result.stderr:
            logger.warning(f"⚠️ Stderr:\n{result.stderr}")
        
        success = result.returncode == 0
        if success:
            logger.info("✅ app.py completed successfully")
        else:
            logger.error(f"❌ app.py failed with exit code: {result.returncode}")
        
        return success, result.stdout + result.stderr
        
    except Exception as e:
        logger.exception(f"🔥 Exception running app.py: {e}")
        return False, str(e)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Catch-up processor for Voiclaim Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process from Jan 1st to today
  python catchup_processor.py --start-date 2026-01-01
  
  # Process specific date range
  python catchup_processor.py --start-date 2026-01-01 --end-date 2026-01-10
  
  # Process with custom workers
  python catchup_processor.py --start-date 2026-01-01 --max-workers 8
        """
    )
    
    parser.add_argument(
        "--start-date",
        type=str,
        required=True,
        help="Start date in YYYY-MM-DD format (e.g., 2026-01-01)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="End date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)",
    )
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="Skip running app.py after processing (default: False)",
    )
    parser.add_argument(
        "--run-api-once",
        action="store_true",
        help="Run app.py only once at the end instead of after each day (default: False)",
    )
    
    return parser.parse_args()


def parse_date(date_str: str) -> date:
    """Parse date string in YYYY-MM-DD format."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        logger.error(f"❌ Invalid date format: {date_str}. Use YYYY-MM-DD format.")
        sys.exit(1)


def main():
    """Main catch-up processing function."""
    args = parse_args()
    
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date) if args.end_date else datetime.today().date()
    
    if start_date > end_date:
        logger.error("❌ Start date must be before or equal to end date")
        sys.exit(1)
    
    if start_date > datetime.today().date():
        logger.error("❌ Start date cannot be in the future")
        sys.exit(1)
    
    logger.info("=" * 80)
    logger.info("📅 Catch-up Processor Started")
    logger.info("=" * 80)
    logger.info(f"Start Date: {start_date}")
    logger.info(f"End Date: {end_date}")
    logger.info(f"Max Workers: {args.max_workers}")
    logger.info(f"Skip API: {args.skip_api}")
    logger.info(f"Run API Once: {args.run_api_once}")
    logger.info("=" * 80)
    
    # Validate scripts exist
    if not MAIN_SCRIPT.exists():
        logger.error(f"❌ main.py not found at {MAIN_SCRIPT}")
        sys.exit(1)
    if not APP_SCRIPT.exists() and not args.skip_api:
        logger.error(f"❌ app.py not found at {APP_SCRIPT}")
        sys.exit(1)
    
    # Generate list of dates to process
    current_date = start_date
    dates_to_process = []
    while current_date <= end_date:
        dates_to_process.append(current_date)
        current_date += timedelta(days=1)
    
    logger.info(f"\n📋 Will process {len(dates_to_process)} day(s)")
    logger.info(f"Dates: {dates_to_process[0]} to {dates_to_process[-1]}\n")
    
    # Process each date
    overall_start_time = datetime.now()
    results = {
        "total": len(dates_to_process),
        "success": 0,
        "failed": 0,
        "failed_dates": [],
    }
    
    for idx, target_date in enumerate(dates_to_process, 1):
        logger.info("\n" + "=" * 80)
        logger.info(f"Processing Date {idx}/{len(dates_to_process)}: {target_date}")
        logger.info("=" * 80)
        
        success, _ = run_main_for_date(target_date, max_workers=args.max_workers)
        
        if success:
            results["success"] += 1
        else:
            results["failed"] += 1
            results["failed_dates"].append(target_date)
        
        # Run app.py after each day (unless --run-api-once is set)
        if not args.skip_api and not args.run_api_once:
            logger.info("\n" + "-" * 80)
            logger.info(f"Running app.py for date {target_date}")
            logger.info("-" * 80)
            app_success, _ = run_app()
            if not app_success:
                logger.warning(f"⚠️ app.py failed for date {target_date}, but continuing...")
    
    # Run app.py once at the end if requested
    if not args.skip_api and args.run_api_once:
        logger.info("\n" + "=" * 80)
        logger.info("Running app.py once at the end")
        logger.info("=" * 80)
        app_success, _ = run_app()
        if not app_success:
            logger.warning("⚠️ app.py failed at the end")
    
    # Final summary
    overall_end_time = datetime.now()
    duration = overall_end_time - overall_start_time
    
    logger.info("\n" + "=" * 80)
    logger.info("📊 Catch-up Processor Summary")
    logger.info("=" * 80)
    logger.info(f"Start Date: {start_date}")
    logger.info(f"End Date: {end_date}")
    logger.info(f"Total Dates: {results['total']}")
    logger.info(f"Success: {results['success']}")
    logger.info(f"Failed: {results['failed']}")
    if results['failed_dates']:
        logger.info(f"Failed Dates: {results['failed_dates']}")
    logger.info(f"Total Duration: {duration}")
    logger.info("=" * 80)
    
    if results["failed"] > 0:
        logger.error(f"❌ Catch-up completed with {results['failed']} failed date(s)")
        sys.exit(1)
    
    logger.info("✅ Catch-up processing completed successfully!")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.warning("⚠️ Catch-up processor interrupted by user (KeyboardInterrupt)")
        sys.exit(130)
    except Exception as e:
        logger.exception("🔥 Catch-up processor failed due to unexpected error")
        sys.exit(1)
