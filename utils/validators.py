"""
Validation utilities for VoicePilot.

This module provides validation functions for various data types and formats
used throughout the application.
"""

import re
from typing import Any, Dict, List, Optional, Union
from utils.constants import VALIDATION_PATTERNS, FALLBACK_VALUES


def validate_phone_number(phone: str) -> bool:
    """
    Validate phone number format.
    
    Args:
        phone (str): Phone number to validate.
        
    Returns:
        bool: True if valid, False otherwise.
    """
    if not phone or not isinstance(phone, str):
        return False
    return bool(re.match(VALIDATION_PATTERNS["PHONE_NUMBER"], phone.strip()))


def validate_email(email: str) -> bool:
    """
    Validate email format.
    
    Args:
        email (str): Email to validate.
        
    Returns:
        bool: True if valid, False otherwise.
    """
    if not email or not isinstance(email, str):
        return False
    return bool(re.match(VALIDATION_PATTERNS["EMAIL"], email.strip()))


def validate_tax_id(tax_id: str) -> bool:
    """
    Validate provider tax ID format.
    
    Args:
        tax_id (str): Tax ID to validate.
        
    Returns:
        bool: True if valid, False otherwise.
    """
    if not tax_id or not isinstance(tax_id, str):
        return False
    return bool(re.match(VALIDATION_PATTERNS["TAX_ID"], tax_id.strip()))


def validate_member_id(member_id: str) -> bool:
    """
    Validate member ID format.
    
    Args:
        member_id (str): Member ID to validate.
        
    Returns:
        bool: True if valid, False otherwise.
    """
    if not member_id or not isinstance(member_id, str):
        return False
    return bool(re.match(VALIDATION_PATTERNS["MEMBER_ID"], member_id.strip()))


def validate_claim_data(claim: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and sanitize claim data.
    
    Args:
        claim (Dict[str, Any]): Claim data to validate.
        
    Returns:
        Dict[str, Any]: Validated and sanitized claim data.
    """
    if not isinstance(claim, dict):
        return {}
    
    validated_claim = {}
    
    # Validate required fields
    required_fields = [
        "patient_first_name",
        "patient_last_name", 
        "member_id",
        "provider_tax_id",
        "billed_amount",
        "claim_date"
    ]
    
    for field in required_fields:
        value = claim.get(field)
        if value is None or value == "":
            validated_claim[field] = FALLBACK_VALUES.get(field.upper(), "UNKNOWN")
        else:
            validated_claim[field] = str(value).strip()
    
    # Validate specific fields
    if "provider_tax_id" in validated_claim:
        if not validate_tax_id(validated_claim["provider_tax_id"]):
            validated_claim["provider_tax_id"] = FALLBACK_VALUES["PROVIDER_TAX_ID"]
    
    if "member_id" in validated_claim:
        if not validate_member_id(validated_claim["member_id"]):
            validated_claim["member_id"] = FALLBACK_VALUES["MEMBER_ID"]
    
    # Validate numeric fields
    if "billed_amount" in validated_claim:
        try:
            amount = float(validated_claim["billed_amount"])
            validated_claim["billed_amount"] = max(0.0, amount)
        except (ValueError, TypeError):
            validated_claim["billed_amount"] = FALLBACK_VALUES["CLAIM_AMOUNT"]
    
    return validated_claim


def validate_file_path(file_path: str, allowed_extensions: List[str] = None) -> bool:
    """
    Validate file path and extension.
    
    Args:
        file_path (str): File path to validate.
        allowed_extensions (List[str], optional): List of allowed extensions.
        
    Returns:
        bool: True if valid, False otherwise.
    """
    if not file_path or not isinstance(file_path, str):
        return False
    
    if allowed_extensions is None:
        allowed_extensions = [".txt", ".json", ".mp3", ".wav"]
    
    file_path = file_path.strip()
    if not file_path:
        return False
    
    # Check if file has valid extension
    return any(file_path.lower().endswith(ext.lower()) for ext in allowed_extensions)


def validate_config_data(config: Dict[str, Any]) -> bool:
    """
    Validate configuration data structure.
    
    Args:
        config (Dict[str, Any]): Configuration data to validate.
        
    Returns:
        bool: True if valid, False otherwise.
    """
    if not isinstance(config, dict):
        return False
    
    required_sections = ["paths", "stt", "llm", "audio_cleaner"]
    return all(section in config for section in required_sections)


def sanitize_string(value: Any, max_length: int = 255) -> str:
    """
    Sanitize string value.
    
    Args:
        value (Any): Value to sanitize.
        max_length (int): Maximum length of the string.
        
    Returns:
        str: Sanitized string.
    """
    if value is None:
        return ""
    
    sanitized = str(value).strip()
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
    
    return sanitized


def validate_audio_file(file_path: str) -> bool:
    """
    Validate audio file format and existence.
    
    Args:
        file_path (str): Path to audio file.
        
    Returns:
        bool: True if valid, False otherwise.
    """
    import os
    from utils.constants import SUPPORTED_AUDIO_FORMATS
    
    if not file_path or not os.path.exists(file_path):
        return False
    
    return any(file_path.lower().endswith(ext) for ext in SUPPORTED_AUDIO_FORMATS)
