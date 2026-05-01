import logging
import os
import shutil
import smtplib
import yaml
from datetime import datetime
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler

from utils.constants import LOG_FORMAT

# === Load Config ===
CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config_manager", "config_logging.yaml"
)
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r") as f:
        CONFIG = yaml.safe_load(f).get("logging", {})
else:
    # Use basic logging if config file not found
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    logging.warning(f"Config file not found at {CONFIG_PATH}. Using default logging settings.")
    CONFIG = {}

# === Settings ===
# Use project root for logs, not os.getcwd()
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOG_BASE_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_RETENTION_DAYS = CONFIG.get("retention_days", 7)
MAX_LOG_SIZE = CONFIG.get("max_log_size_mb", 5) * 1024 * 1024
BACKUP_COUNT = CONFIG.get("backup_count", 3)
ALERT_EMAIL_ENABLED = CONFIG.get("email_alerts", False)
ALERT_LEVEL = CONFIG.get("alert_email_level", "ERROR").upper()

SMTP_CONFIG = CONFIG.get("smtp", {})


def get_today_log_dir():
    """Return today's dated log directory, creating it if needed."""
    today_str = datetime.today().strftime("%Y-%m-%d")
    today_path = os.path.join(LOG_BASE_DIR, today_str)
    os.makedirs(today_path, exist_ok=True)
    return today_path


def cleanup_old_logs(base_dir=LOG_BASE_DIR, days=LOG_RETENTION_DAYS):
    """Delete dated log folders older than the configured retention period."""
    now = datetime.now()
    for folder in os.listdir(base_dir):
        folder_path = os.path.join(base_dir, folder)
        try:
            folder_date = datetime.strptime(folder, "%Y-%m-%d")
            if (now - folder_date).days > days:
                shutil.rmtree(folder_path)
                logging.info(f"[LOG CLEANUP] Removed: {folder_path}")
        except ValueError:
            continue


def mask_sensitive_data(message: str) -> str:
    """Mask sensitive data like patient/member IDs in log messages."""
    import re

    # Example: Mask 9+ digit numbers (IDs)
    return re.sub(r"(\b\d{9,}\b)", "[MASKED_ID]", message)


class MaskingFormatter(logging.Formatter):
    def format(self, record):
        original = super().format(record)
        return mask_sensitive_data(original)


class EmailAlertHandler(logging.Handler):
    """Logging handler that emails log records at or above ALERT_LEVEL."""

    def emit(self, record):
        if not ALERT_EMAIL_ENABLED:
            return
        try:
            subject = f"[Voiclaim Alert] {record.levelname} in {record.name}"
            body = self.format(record)
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = SMTP_CONFIG["sender"]
            msg["To"] = SMTP_CONFIG["receiver"]

            with smtplib.SMTP(SMTP_CONFIG["server"], SMTP_CONFIG["port"]) as server:
                server.starttls()
                server.login(SMTP_CONFIG["username"], SMTP_CONFIG["password"])
                server.sendmail(SMTP_CONFIG["sender"], [SMTP_CONFIG["receiver"]], msg.as_string())
        except Exception as e:
            logging.getLogger("voiclaim.email").exception("[Email Alert Error]")


def get_logger(name: str, level=None) -> logging.Logger:
    logger = logging.getLogger(name)
    # Allow override from env
    env_level = os.getenv("VOICLAIM_LOG_LEVEL")
    if level is None:
        level = getattr(logging, env_level.upper(), logging.INFO) if env_level else logging.INFO
    logger.setLevel(level)

    if logger.hasHandlers():
        # Handlers already configured; still allow level to be updated above.
        return logger  # Avoid duplicates

    log_dir = get_today_log_dir()
    cleanup_old_logs()

    log_file_path = os.path.join(log_dir, "voiclaim.log")

    # File Handler with masking
    file_handler = RotatingFileHandler(
        log_file_path, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT
    )
    file_format = MaskingFormatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_format)

    # Console Handler
    console_handler = logging.StreamHandler()
    console_format = logging.Formatter("[%(levelname)s] %(message)s")
    console_handler.setFormatter(console_format)

    # Email Handler
    if ALERT_EMAIL_ENABLED:
        email_handler = EmailAlertHandler()
        email_handler.setLevel(getattr(logging, ALERT_LEVEL))
        email_handler.setFormatter(file_format)
        logger.addHandler(email_handler)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
