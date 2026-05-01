"""
Custom exceptions for VoicePilot.

This module defines custom exception classes for better error handling
and more specific error reporting throughout the application.
"""


class VoicePilotError(Exception):
    """Base exception class for VoicePilot application."""
    
    def __init__(self, message: str, error_code: str = None, details: dict = None):
        """
        Initialize VoicePilot error.
        
        Args:
            message (str): Error message.
            error_code (str, optional): Error code for categorization.
            details (dict, optional): Additional error details.
        """
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}


class ConfigurationError(VoicePilotError):
    """Raised when configuration loading or validation fails."""
    pass


class FileProcessingError(VoicePilotError):
    """Raised when file processing operations fail."""
    pass


class AudioProcessingError(FileProcessingError):
    """Raised when audio processing operations fail."""
    pass


class ClaimExtractionError(VoicePilotError):
    """Raised when claim extraction operations fail."""
    pass


class APIError(VoicePilotError):
    """Raised when API operations fail."""
    pass


class DatabaseError(VoicePilotError):
    """Raised when database operations fail."""
    pass


class ValidationError(VoicePilotError):
    """Raised when data validation fails."""
    pass


class BlobStorageError(VoicePilotError):
    """Raised when blob storage operations fail."""
    pass


class PipelineError(VoicePilotError):
    """Raised when pipeline operations fail."""
    pass


class RetryableError(VoicePilotError):
    """Raised when an operation fails but can be retried."""
    pass


class NonRetryableError(VoicePilotError):
    """Raised when an operation fails and should not be retried."""
    pass
