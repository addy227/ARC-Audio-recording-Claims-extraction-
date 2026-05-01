"""
Constants and configuration values for VoicePilot.

This module centralizes all constant values used across the application,
improving maintainability and reducing code duplication.
"""

# ============================================================================
# File Processing Constants
# ============================================================================
DEFAULT_CHUNK_SIZE = 8192
MAX_FILE_SIZE_MB = 100
SUPPORTED_AUDIO_FORMATS = [".mp3", ".wav", ".m4a", ".flac", ".ogg"]
SUPPORTED_TEXT_FORMATS = [".txt", ".json"]

# ============================================================================
# API Configuration Constants
# ============================================================================
DEFAULT_API_TIMEOUT = 30
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5

# ============================================================================
# Database Constants
# ============================================================================
DEFAULT_DB_TIMEOUT = 30
MAX_CONNECTION_POOL_SIZE = 10

# ============================================================================
# Logging Constants
# ============================================================================
DEFAULT_LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ============================================================================
# Error Messages
# ============================================================================
ERROR_MESSAGES = {
    "FILE_NOT_FOUND": "File not found: {file_path}",
    "INVALID_FORMAT": "Invalid file format: {format}",
    "API_REQUEST_FAILED": "API request failed: {error}",
    "DATABASE_CONNECTION_FAILED": "Database connection failed: {error}",
    "CONFIG_LOAD_FAILED": "Configuration loading failed: {error}",
    "AUDIO_PROCESSING_FAILED": "Audio processing failed: {error}",
    "CLAIM_EXTRACTION_FAILED": "Claim extraction failed: {error}",
}

# ============================================================================
# Success Messages
# ============================================================================
SUCCESS_MESSAGES = {
    "FILE_PROCESSED": "File processed successfully: {file_path}",
    "CLAIMS_EXTRACTED": "Claims extracted successfully: {count} claims",
    "API_REQUEST_SUCCESS": "API request successful: {status_code}",
    "DATABASE_INSERT_SUCCESS": "Database insert successful: {record_id}",
}

# ============================================================================
# Fallback Values
# ============================================================================
FALLBACK_VALUES = {
    "FIRSTNAME": "UNKNOWN",
    "LASTNAME": "UNKNOWN",
    "MEMBER_ID": "UNKNOWN",
    "PROVIDER_TAX_ID": "UNKNOWN",
    "CLAIM_AMOUNT": 0.0,
    "CLAIM_DATE": None,
}

# ============================================================================
# Validation Patterns
# ============================================================================
VALIDATION_PATTERNS = {
    "PHONE_NUMBER": r"^\+?1?[-.\s]?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})$",
    "EMAIL": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
    "TAX_ID": r"^\d{2}-?\d{7}$",
    "MEMBER_ID": r"^[A-Za-z0-9]{6,12}$",
}

# ============================================================================
# File Paths
# ============================================================================
DEFAULT_PATHS = {
    "LOGS_DIR": "logs",
    "TEMP_DIR": "temp",
    "BACKUP_DIR": "backup",
    "CONFIG_DIR": "config_manager",
    "DATA_DIR": "local_data_source",
}
