"""
Main CLI entrypoint for the Voiclaim audio-to-claim pipeline.

Parses CLI arguments, resolves API URL from flag or environment, invokes the
pipeline controller, and sets process exit codes based on outcomes.
"""

import sys
import argparse
import os
from utils.logging_utils import get_logger
from utils.config_loader import load_pipeline_config
from scripts.audio_file_process.pipeline import run_main
from scripts.email_summary import main as send_log_summary_email

logger = get_logger(__name__)
VERSION = "1.0.0"
config = load_pipeline_config()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Voiclaim Audio-to-Claim Processing Pipeline")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Number of parallel workers for audio file processing (default: 1)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Run without sending results to API (safe mode)"
    )
    # Get default cleanup_days from config
    default_cleanup_days = config.get("cleanup_days", 20)
    parser.add_argument(
        "--cleanup-days",
        type=int,
        default=None,  # None means use config value
        help=f"Clean files older than N days before processing. Default from config: {default_cleanup_days} (0 = skip cleanup)",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=None,
        help="Override API URL for claim integration (default: read from env POST_PROCESS_URL)",
    )
    # Get default day_offset from config
    default_day_offset = config.get("day_offset", 1)
    parser.add_argument(
        "--day",
        type=int,
        default=None,  # None means use config value
        help=f"Day offset to process files: 0 for today, 1 for yesterday, etc. Default from config: {default_day_offset}",
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        help="Send email summary after processing completes (like scheduler does)",
    )
    return parser.parse_args()


def flush_logs() -> None:
    for handler in logger.handlers:
        try:
            handler.flush()
        except Exception:
            pass


def main() -> None:
    logger.info(f"🚀 Starting Voiclaim Pipeline v{VERSION}")

    args = parse_args()
    logger.info(f"Arguments: {args}")

    # Determine API URL priority: CLI arg > ENV > fail if missing (unless dry run)
    api_url = args.api_url or os.getenv("POST_PROCESS_URL")
    if not api_url and not args.dry_run:
        logger.error("❌ API URL missing: set --api-url or POST_PROCESS_URL env variable")
        sys.exit(1)

    try:
        # Use day from args if provided, otherwise None (which will use config value in run_main)
        day_to_use = args.day if args.day is not None else None
        # Use cleanup_days from args if provided, otherwise None (which will use config value in run_main)
        cleanup_days_to_use = args.cleanup_days if args.cleanup_days is not None else None
        run_main(
            max_workers=args.max_workers,
            api_url=api_url,
            dry_run=args.dry_run,
            cleanup_days=cleanup_days_to_use,
            day=day_to_use,
        )
        logger.info("✅ Pipeline completed successfully.")
        
        # Send email summary if requested (like scheduler does)
        if args.send_email:
            logger.info("\n" + "=" * 80)
            logger.info("📧 Sending comprehensive pipeline summary email...")
            logger.info("=" * 80)
            email_sent = send_log_summary_email()
            if email_sent:
                logger.info("✅ Comprehensive summary email sent successfully")
            else:
                logger.warning("⚠️ Failed to send summary email (check email configuration in .env)")
        
        sys.exit(0)

    except KeyboardInterrupt:
        logger.warning("⚠️ Pipeline interrupted by user (KeyboardInterrupt). Exiting.")
        sys.exit(130)

    except Exception as e:
        logger.exception("🔥 Pipeline failed due to an unexpected error.")
        sys.exit(1)

    finally:
        flush_logs()


if __name__ == "__main__":
    main()
