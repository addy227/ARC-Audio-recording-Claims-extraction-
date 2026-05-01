"""
VoiclaimBot Claim Extraction Script (Finetuned Model Version)

Extracts structured healthcare claim data from cleaned speech-to-text (STT) transcript files using a locally loaded finetuned LLM model (Llama3.2).

Features:
- Cleans and normalizes transcript text.
- Uses a strict prompt to instruct the LLM to extract claims grouped by provider tax ID.
- Parses and validates the LLM's JSON output, handling errors gracefully.
- Saves both the full claims list and individual claims as JSON files in an organized folder structure.
- Tracks successes and failures, and generates a summary report for monitoring and debugging.
- Supports parallel processing for efficient batch extraction.
- Logs detailed information for each step and error.

All configuration is managed via YAML files in `config_manager/`.
"""
from __future__ import annotations

# =========================
# Imports
# =========================
import os
import sys
import json
import time
import re
import torch
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from tqdm import tqdm
from pydantic import BaseModel, Extra
from transformers import AutoTokenizer, AutoModelForCausalLM
from scripts.audio_file_process.blob_storage_handler import upload_file_to_blob
from utils.analytics import save_metrics
from utils import report_metrics
from utils.config_loader import load_pipeline_config
from utils.logging_utils import get_logger
from utils.util_master import (
    get_project_path,
    sanitize_claim,
    extract_json_from_text,
    log_claim_metric,
    clean_transcript,
    get_transcript_files,
    Duplicate_check,
)
from utils.exceptions import (
    ClaimExtractionError,
    FileProcessingError,
    ValidationError,
    RetryableError,
    NonRetryableError,
)

logger = get_logger(__name__)
config = load_pipeline_config()
paths = config.get("paths", {})
stt = config.get("stt", {})
llm = config.get("llm", {})
audio_cleaner_cfg = config.get("audio_cleaner", {})

# === CONFIGURATION ===
# Use same hardcoded paths as pipeline.py to ensure consistency
RAW_AUDIO_DIR = get_project_path(paths["raw_audio_dir"])

CLEANED_AUDIO_DIR = get_project_path(paths["cleaned_audio_dir"])
TRANSCRIPTS_DIR = get_project_path(paths["transcripts_dir"])
EXTRACTED_CLAIMS_DIR = get_project_path(paths["extracted_claims_dir"])
LOG_DIR = get_project_path(paths["log_dir"])
METRICS_DIR = get_project_path(paths["metrics_dir"])
MODEL_DIR = get_project_path(paths["model_dir"])
# Finetuned model configuration
FINETUNED_MODEL_PATH = llm.get('finetuned_model_path', MODEL_DIR)
MODEL_NAME = llm.get('model_name', 'llama3.1-finetuned') if 'finetuned_model_path' in llm else (llm.get('model_name', 'llama3') if 'model_name' in llm else "llama3")

# Device configuration
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32

REPORT_FILE = os.path.join(METRICS_DIR, "summary_report.json")
METRICS_FILE = os.path.join(METRICS_DIR, "claim_extraction_metrics.csv")

# Ensure directories exist
os.makedirs(RAW_AUDIO_DIR, exist_ok=True)
os.makedirs(CLEANED_AUDIO_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
os.makedirs(EXTRACTED_CLAIMS_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(METRICS_DIR, exist_ok=True)

FALLBACK_TAX_ID = audio_cleaner_cfg.get("fallback_tax_id", None)  # Use None (null) instead of "NA"
FALLBACK_FIRSTNAME = audio_cleaner_cfg.get("fallback_firstname", None)  # Use None (null) instead of "NA"
FALLBACK_LASTNAME = audio_cleaner_cfg.get("fallback_lastname", None)  # Use None (null) instead of "NA"
FALLBACK_MEMBER_ID = audio_cleaner_cfg.get("fallback_member_id", None)  # Use None (null) instead of "NA"
LLAMA3_8B_CONTEXT_LIMIT = 8192

# === GPU MEMORY MONITORING ===
def get_gpu_memory_info(device: str = "cuda") -> Optional[Dict[str, Any]]:
    """
    Get GPU memory usage information.
    
    Args:
        device: Device to check (default: "cuda")
    
    Returns:
        Dictionary with GPU memory stats or None if CUDA not available
    """
    if not torch.cuda.is_available() or device != "cuda":
        return None
    
    try:
        # Get memory stats for the default GPU (device 0)
        allocated = torch.cuda.memory_allocated(0) / (1024**3)  # GB
        reserved = torch.cuda.memory_reserved(0) / (1024**3)  # GB
        max_allocated = torch.cuda.max_memory_allocated(0) / (1024**3)  # GB
        total_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)  # GB
        
        return {
            "allocated_gb": round(allocated, 2),
            "reserved_gb": round(reserved, 2),
            "max_allocated_gb": round(max_allocated, 2),
            "total_gb": round(total_memory, 2),
            "free_gb": round(total_memory - reserved, 2),
            "utilization_pct": round((reserved / total_memory) * 100, 1) if total_memory > 0 else 0
        }
    except Exception as e:
        logger.warning(f"Failed to get GPU memory info: {e}")
        return None

def log_gpu_memory(context: str, device: str = "cuda"):
    """Log GPU memory usage with a context label."""
    mem_info = get_gpu_memory_info(device)
    if mem_info:
        logger.info(
            f"💾 [Finetuned Model] GPU Memory [{context}]: "
            f"Allocated={mem_info['allocated_gb']}GB, "
            f"Reserved={mem_info['reserved_gb']}GB, "
            f"Free={mem_info['free_gb']}GB, "
            f"Utilization={mem_info['utilization_pct']}%, "
            f"Max_Allocated={mem_info['max_allocated_gb']}GB"
        )
    else:
        logger.debug(f"💾 [Finetuned Model] GPU Memory [{context}]: Not available (CPU mode)")

# === MODEL LOADING ===
logger.info(f"Loading finetuned model from: {FINETUNED_MODEL_PATH}")
logger.info(f"Using device: {DEVICE.upper()}")
log_gpu_memory("Before Model Load", DEVICE)

try:
    # Load tokenizer for Llama3.1 8B instruct
    tokenizer = AutoTokenizer.from_pretrained(FINETUNED_MODEL_PATH, use_fast=True)

    # Configure tokenizer for Llama3.2 (set pad_token if not present)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        logger.debug("Set pad_token to eos_token for Llama3.2")

    logger.info(f"Tokenizer loaded. Chat template available: {tokenizer.chat_template is not None}")
    if tokenizer.chat_template:
        logger.debug("Using model's chat template for Llama3.2 instruct format")

    # Load model with appropriate settings
    # Note: Using manual device placement instead of device_map="auto" to avoid requiring accelerate
    model = AutoModelForCausalLM.from_pretrained(
        FINETUNED_MODEL_PATH,
        dtype=torch_dtype,  # Use dtype instead of deprecated torch_dtype
        low_cpu_mem_usage=True,
        trust_remote_code=True
    )

    # Manually move model to device (works for both CPU and CUDA without accelerate)
    model = model.to(DEVICE)
    log_gpu_memory("After Model Load", DEVICE)

    model.eval()  # Set to evaluation mode
    logger.info(f"✅ Finetuned Llama3.1 8B instruct model loaded successfully on {DEVICE.upper()}")

except Exception as e:
    logger.error(f"❌ Failed to load finetuned model: {e}", exc_info=True)
    logger.warning("Falling back to default tokenizer for token counting only")
    tokenizer = AutoTokenizer.from_pretrained("openlm-research/open_llama_3b", use_fast=True)
    model = None

## Constants for metrics
CLAIM_EXTRACTION_METRICS_FILE = "claim_extraction.csv"



def count_tokens(text: str) -> int:
    """
    Count tokens in text using the configured tokenizer.

    This function uses the actual tokenizer from the LLM model to provide
    accurate token counting for text processing and chunking.

    Args:
        text (str): The text to count tokens for.

    Returns:
        int: Actual token count from the tokenizer.

    Raises:
        Exception: If tokenizer fails to process the text.
    """
    tokens = tokenizer(text)
    return len(tokens['input_ids'])

def safe_filename(value: str) -> str:
    """
    Sanitize strings for filenames (alphanumeric + underscores).

    This function converts a string into a safe filename by replacing
    non-alphanumeric characters with underscores.

    Args:
        value (str): The string to sanitize for filename use.

    Returns:
        str: Sanitized string safe for use as a filename.

    Example:
        >>> safe_filename("John Doe - Claim #123")
        "John_Doe___Claim__123"
    """
    return "".join(c if c.isalnum() else "_" for c in value)

# === Prompt Template ===

PROMPT_TEMPLATE = PROMPT_TEMPLATE = """You are a data extraction assistant. Your task is to extract **structured medical patient-related claim information** from a given conversation transcript and - Strictly do not extract provider-only information.
. The transcript may contain **details for multiple claims**, possibly involving different providers or patients. Your focus is strictly on **patient-related claim information**.

### Your Responsibilities:
- Extract **only patient-related claim information** from the transcript. Ignore provider-only or administrative details not directly related to patient claims.
- Identify and separate **individual claim contexts** within the transcript. A new claim usually corresponds to a change in patient, member ID, or date of service.
- Group claims by **Tax ID**. If the Tax ID is unavailable, use `"tax_id": null`.
- Extract all relevant fields **exactly as spoken in the transcript** — do NOT infer, assume, or generate any information not explicitly present in the transcript.
- Include the **complete transcription snippet** corresponding to each claim.
- Respond strictly in **valid JSON format only** — no explanations or additional commentary.
- Be careful not to confuse different ID types: do NOT misclassify Tax ID, Member ID, or NPI.
- For each claim, generate a brief summary regarding whether the claim is accepted or denied

---

### Extraction Rules:
- One claim corresponds to one complete set of patient-related data (patient name, member ID, dates, billed amount, etc.).
- Strictly do not extract provider-only information.
- Group claims under their respective **Tax ID**; multiple claims with the same Tax ID should be grouped within the same `"claims"` array.
- If the transcript includes **multiple claims**, output each claim separately within the appropriate Tax ID group.
- Avoid merging or splitting claims incorrectly; be cautious with overlapping or ambiguous details.
- If any field is missing or unclear, set its value to `null` (JSON null, without quotes).
- Extract **only patient-related claim details**; ignore claims related solely to providers without patient information.
- Ensure all strings, especially transcription text, are properly JSON escaped.

---

### Disambiguation & Validation Rules:
- **ID Formats:**
  - `"tax_id"`: exactly 9-digit numeric string (no letters), usually a provider or organization identifier.
  - `"npi_id"`: exactly 10-digit numeric string, typically introduced with terms like "NPI" or "provider ID". It can be written as "NTI" or "and PI" or "in PI"
  - `"member_id"`: an Alphanumerical Identifier consisting of numbers and/or letters that may include letters spelled out as words; Use NATO phonetic alphabet to spell alphabets accuretly. Do NOT extract or include invalid IDs or misclassify similar-looking numbers.
- If any ambiguity arises, assign `null` rather than guessing.

### Field Pattern Hints:
- Tax ID: 9-digit numeric, no letters.
- NPI ID: 10-digit numeric.
- Member ID: an identifier consisting of numbers and letters (Alphanumerical Value),Use NATO phonetic alphabet to spell alphabets accuretly *MAKE SURE TO INCLUDE ALPHABETS ALONG WITH NUMBERS* .
- patient_first_name and patient_last_name: Extract exactly as spoken in context, do not assume or guess names. More information regarding names can be written in the form of phonetic alphabets 
- Dates: Use ISO format `MM-DD-YYYY`.
- Amounts: Extract exactly as spoken.
- Names: Extract exactly as spoken. Do not Merge 2 names together at any cost
- summary: summary of the claim status, if the claim was rejected or denied and why 
---

### Fields to Extract Per Claim:
- tax_id
- npi_id
- patient_first_name
- patient_last_name
- date_of_birth (format: MM-DD-YYYY or null)
- billed_amount
- date_of_service (format: MM-DD-YYYY or null)
- member_id
- summary
---
IMPORTANT: Every claim object MUST include a "summary" field. Do not omit it under any circumstances.
### Input:
{transcript}

### Output Format:
[
  {{
    "tax_id": "string or null",
    "claims": [
      {{
        "npi_id": "string or null",
        "patient_first_name": "string or null",
        "patient_last_name": "string or null",
        "date_of_birth": "MM-DD-YYYY or null",
        "billed_amount": "string or null",
        "date_of_service": "MM-DD-YYYY or null",
        "member_id": "string or null",
        "summary":"string or null"
      }}
    ]
  }}
]

*Respond ONLY with the JSON array.*
"""

class Claim(BaseModel, extra="allow"):
    """
       Schema representing a single claim record.

       Fields are optional strings representing claim details.
       Extra fields are allowed and ignored by default.
       """
    member_id: Optional[str]
    npi_id: Optional[str]
    patient_first_name: Optional[str]
    patient_last_name: Optional[str]
    date_of_birth: Optional[str]
    date_of_service: Optional[str]
    billed_amount: Optional[str]
    summary: Optional[str]



class TaxGroup(BaseModel):
    """
        Grouping of claims associated with a tax ID.

        Attributes:
            tax_id: Optional tax identification number as a string.
            claims: List of Claim objects.
        """
    tax_id: Optional[str]
    claims: List[Claim]


def sliding_window_chunks(text: str, max_tokens: int = 3000, overlap: int = 300) -> List[Tuple[int, int, str]]:
    """Split transcript tokens into overlapping windows for chunked extraction.

    Args:
        text: Full transcript text.
        max_tokens: Maximum tokens per chunk (window size).
        overlap: Overlap in tokens between consecutive chunks.

    Returns:
        List of chunk dicts with token and character boundaries.
    """
    tokens = tokenizer(text)["input_ids"]
    chunks = []
    start = 0

    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_text = tokenizer.decode(chunk_tokens)
        logger.debug(chunk_text)  # was: print(chunk_text)

        char_start = len(tokenizer.decode(tokens[:start]))
        char_end = len(tokenizer.decode(tokens[:end]))

        chunks.append({
            "chunk_text": chunk_text,
            "token_start": start,
            "token_end": end,
            "char_start": char_start,
            "char_end": char_end,
        })

        start += max_tokens - overlap

    return chunks


def _strip_model_artifact_tokens(text: str) -> str:
    """Remove common Llama/chat template tokens that break regex/bracket slicing."""
    for tok in (
        "<|eot_id|>",
        "<|end_of_text|>",
        "<|endoftext|>",
        "<|redacted_start_header_id|>",
        "<|redacted_end_header_id|>",
        "<|begin_of_text|>",
    ):
        text = text.replace(tok, "")
    return text.strip()


def extract_json_improved(output_text: str) -> list | None:
    """
    Improved JSON extraction that handles various formats including markdown code blocks,
    Python dict syntax, and mixed formats.

    Args:
        output_text: Text output potentially containing a JSON array or Python dict.

    Returns:
        Parsed list of dictionaries if a valid JSON array is found, else None.
    """
    if not output_text or not output_text.strip():
        logger.warning("Empty output text provided")
        return None

    output_text = _strip_model_artifact_tokens(output_text)

    # Strategy A: JSONDecoder.raw_decode from first '[' or '{' — handles nested
    # "claims": [...] correctly. Trailing junk (e.g. <|eot_id|>) is ignored.
    # Using output_text[first_bracket : rfind(']')+1] is wrong: rfind(']') often
    # points at the inner claims array, truncating to a single group or invalid JSON.
    try:
        decoder = json.JSONDecoder()
        lb = output_text.find("[")
        if lb != -1:
            parsed, _end = decoder.raw_decode(output_text, lb)
            if isinstance(parsed, list) and len(parsed) > 0:
                logger.debug(
                    "extract_json_improved: raw_decode JSON array (%d top-level groups)",
                    len(parsed),
                )
                return parsed
        fb = output_text.find("{")
        if fb != -1:
            parsed, _end = decoder.raw_decode(output_text, fb)
            if isinstance(parsed, dict) and "claims" in parsed:
                logger.debug("extract_json_improved: raw_decode single object wrapped as list")
                return [parsed]
    except json.JSONDecodeError as e:
        logger.debug(f"raw_decode strategy failed: {e}")
        # Truncated root array (common at max_new_tokens): missing final ']'.
        lb = output_text.find("[")
        if lb != -1:
            tail = output_text[lb:].rstrip()
            for suffix in ("]", "}]"):
                try:
                    parsed, _end = decoder.raw_decode(tail + suffix)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        logger.info(
                            "extract_json_improved: parsed array after appending suffix %r (%d groups)",
                            suffix,
                            len(parsed),
                        )
                        return parsed
                except json.JSONDecodeError:
                    continue

    # Strategy 0: Try to extract Python dict from embedded string values
    # The model sometimes outputs JSON with Python dict syntax embedded in string values
    # Example: {"patient_first_name": "[{'tax_id': 'null', ...}]"}
    # We need to extract the Python dict from inside the string
    try:
        import ast
        # Look for Python dict/list syntax that might be embedded in JSON strings
        # The pattern: find where Python dict starts (usually after a quote: "[{'tax_id'")
        # Look for the pattern: "[{'tax_id'" or similar Python dict start
        python_dict_start_pattern = r'\[.*?\{.*?[\'"]tax_id[\'"]'
        match = re.search(python_dict_start_pattern, output_text, re.DOTALL)
        if match:
            # Found start of Python dict, now extract from the opening bracket
            start_pos = match.start()
            # Find the opening bracket before the match
            bracket_start = output_text.rfind('[', 0, start_pos + 1)
            if bracket_start == -1:
                bracket_start = start_pos

            # Now find the matching closing bracket
            bracket_count = 0
            for i in range(bracket_start, len(output_text)):
                if output_text[i] == '[':
                    bracket_count += 1
                elif output_text[i] == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        # Found complete structure
                        python_text = output_text[bracket_start:i+1]
                        try:
                            # Use ast.literal_eval to safely parse Python literals
                            parsed = ast.literal_eval(python_text)
                            if isinstance(parsed, list) and len(parsed) > 0:
                                return parsed
                        except (ValueError, SyntaxError) as e:
                            logger.debug(f"ast.literal_eval failed on embedded dict: {e}")
                        break

        # Fallback: Try to find any Python dict structure with single quotes
        # Look for pattern: [{'key': 'value'}] anywhere in the text
        # This pattern is more flexible and will match Python dicts even if incomplete
        python_list_pattern = r"\[.*?\{.*?'tax_id'.*?\}.*?\]"
        match = re.search(python_list_pattern, output_text, re.DOTALL)
        if match:
            python_text = match.group(0)
            # If the string is incomplete (missing closing bracket), try to fix it
            if python_text.count('[') > python_text.count(']'):
                # Add missing closing brackets
                missing = python_text.count('[') - python_text.count(']')
                python_text += ']' * missing
            try:
                parsed = ast.literal_eval(python_text)
                if isinstance(parsed, list) and len(parsed) > 0:
                    return parsed
            except (ValueError, SyntaxError) as e:
                logger.debug(f"ast.literal_eval failed on general Python dict: {e}")
                # Try one more time with a simpler pattern - just extract the inner dict
                try:
                    # Extract just the inner dict structure: {'tax_id': ..., 'claims': [...]}
                    inner_dict_pattern = r"\{.*?'tax_id'.*?'claims'.*?\}"
                    inner_match = re.search(inner_dict_pattern, python_text, re.DOTALL)
                    if inner_match:
                        inner_dict_text = inner_match.group(0)
                        # Try to complete it if needed
                        if inner_dict_text.count('{') > inner_dict_text.count('}'):
                            inner_dict_text += '}' * (inner_dict_text.count('{') - inner_dict_text.count('}'))
                        inner_parsed = ast.literal_eval(inner_dict_text)
                        # Wrap it in a list with tax_id structure
                        if isinstance(inner_parsed, dict):
                            result = [{"tax_id": inner_parsed.get('tax_id'), "claims": inner_parsed.get('claims', [])}]
                            return result
                except Exception as inner_e:
                    logger.debug(f"Inner dict extraction also failed: {inner_e}")
    except ImportError:
        pass  # ast is in standard library, but handle gracefully
    except Exception as e:
        logger.debug(f"Embedded Python dict parsing strategy failed: {e}")

    # Strategy 0.5: Try to parse as complete Python literal using ast.literal_eval
    # This handles cases where the model outputs Python dicts instead of JSON
    try:
        import ast
        # Look for Python list/dict syntax: [{'key': 'value'}, ...]
        # Try to find a complete Python list structure
        first_bracket = output_text.find('[')
        if first_bracket != -1:
            # Try to find the matching closing bracket
            bracket_count = 0
            for i in range(first_bracket, len(output_text)):
                if output_text[i] == '[':
                    bracket_count += 1
                elif output_text[i] == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        # Found complete structure
                        python_text = output_text[first_bracket:i+1]
                        try:
                            # Use ast.literal_eval to safely parse Python literals
                            parsed = ast.literal_eval(python_text)
                            if isinstance(parsed, list):
                                logger.debug("Successfully extracted JSON from Python dict syntax using ast.literal_eval")
                                return parsed
                        except (ValueError, SyntaxError) as e:
                            logger.debug(f"ast.literal_eval failed: {e}")
                        break
    except ImportError:
        pass  # ast is in standard library, but handle gracefully
    except Exception as e:
        logger.debug(f"Python dict parsing strategy failed: {e}")

    # Try multiple extraction strategies
    strategies = [
        # Strategy 1: Look for JSON in markdown code blocks (```json ... ```)
        (r"```(?:json)?\s*(\[\s*\{.*?\}\s*\])", re.DOTALL),
        # Strategy 2: Look for JSON array directly
        (r"(\[\s*\{.*?\}\s*\])", re.DOTALL),
        # Strategy 3: Look for JSON array with more flexible whitespace
        (r"\[\s*(\{.*?\})\s*\]", re.DOTALL),
    ]

    for pattern, flags in strategies:
        try:
            match = re.search(pattern, output_text, flags)
            if match:
                json_text = match.group(1) if len(match.groups()) > 0 else match.group(0)

                # Handle mixed Python/JSON syntax in the extracted text
                # Replace Python None with JSON null
                json_text = json_text.replace('None', 'null')
                json_text = json_text.replace('True', 'true')
                json_text = json_text.replace('False', 'false')

                # Clean up common issues
                json_text = re.sub(r",\s*}", "}", json_text)  # Remove trailing commas in objects
                json_text = re.sub(r",\s*]", "]", json_text)  # Remove trailing commas in arrays
                json_text = re.sub(r",\s*,", ",", json_text)  # Remove duplicate commas

                # Fix unterminated strings (common issue with mixed formats)
                # Remove any incomplete string values that might break JSON
                # This handles cases where Python dict syntax is embedded in JSON strings
                # Pattern 1: String values containing Python dict syntax like: "key": "[{'python': 'dict'}]"
                json_text = re.sub(r':\s*"[^"]*\[.*?\{.*?\}.*?\][^"]*"', ': null', json_text, flags=re.DOTALL)
                # Pattern 2: Unterminated strings at end of line
                json_text = re.sub(r':\s*"[^"]*\[.*?\]', ': null', json_text, flags=re.DOTALL)
                json_text = re.sub(r':\s*"[^"]*$', ': null', json_text, flags=re.MULTILINE)
                # Pattern 3: String values containing Python dict syntax with curly braces
                json_text = re.sub(r':\s*"[^"]*\{[^}]*\}[^"]*"', ': null', json_text)

                # Try to parse
                parsed = json.loads(json_text)
                if isinstance(parsed, list):
                    logger.debug("Successfully extracted JSON using pattern strategy")
                    return parsed
        except (json.JSONDecodeError, AttributeError) as e:
            logger.debug(f"JSON extraction strategy failed: {e}")
            continue

    # Strategy 4: Try to find and extract JSON from the entire text by finding the first [ and last ]
    try:
        first_bracket = output_text.find('[')
        last_bracket = output_text.rfind(']')
        if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
            json_text = output_text[first_bracket:last_bracket + 1]

            # Handle Python dict syntax
            json_text = json_text.replace('None', 'null')
            json_text = json_text.replace('True', 'true')
            json_text = json_text.replace('False', 'false')

            # Clean up
            json_text = re.sub(r",\s*}", "}", json_text)
            json_text = re.sub(r",\s*]", "]", json_text)

            # Fix unterminated strings - this is critical for malformed JSON
            # Remove any string values that contain Python dict syntax or are unterminated
            # Pattern: "key": "unterminated or [{'python': 'dict'}]" -> "key": null
            json_text = re.sub(r':\s*"[^"]*\[.*?\]', ': null', json_text, flags=re.DOTALL)
            json_text = re.sub(r':\s*"[^"]*$', ': null', json_text, flags=re.MULTILINE)
            # Also handle cases where the string value itself contains Python dict syntax
            json_text = re.sub(r':\s*"[^"]*\{[^}]*\}[^"]*"', ': null', json_text)

            parsed = json.loads(json_text)
            if isinstance(parsed, list):
                logger.debug("Successfully extracted JSON using bracket matching")
                return parsed
    except (json.JSONDecodeError, ValueError) as e:
        logger.debug(f"Bracket matching strategy failed: {e}")

    logger.warning(f"⚠️ Could not extract valid JSON from output. First 500 chars: {output_text[:500]}")
    return None


def call_finetuned_model(transcript_text: str) -> str:
    """Call finetuned Llama3.1 8B instruct model with system + user + gpt structure.

    Args:
        transcript_text: Raw transcript text only (no prompt template).

    Returns:
        Model response as a string with special tokens removed.
    """
    if model is None:
        raise RetryableError("Model not loaded", error_code="MODEL_NOT_LOADED")

    try:
        logger.info(f"Model name {MODEL_NAME} (Llama3.1 8B Instruct)")
        log_gpu_memory("Before Inference", DEVICE)

        # ── 3-turn structure matching training data: system → user → gpt ──────
        messages = [
            {"role": "system", "content": PROMPT_TEMPLATE},   # instructions only
            {"role": "user",   "content": transcript_text},    # raw transcript only
        ]

        if hasattr(tokenizer, 'apply_chat_template') and tokenizer.chat_template is not None:
            formatted_prompt = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )
            logger.debug("Applied Llama3.1 instruct chat template")
            logger.debug(f"Formatted prompt preview (first 300 chars): {formatted_prompt[:300]}")
        else:
            logger.warning("Chat template not found, using manual Llama3.1 format")
            formatted_prompt = (
                f"<|start_header_id|>system<|end_header_id|>\n\n{PROMPT_TEMPLATE}<|eot_id|>"
                f"<|start_header_id|>user<|end_header_id|>\n\n{transcript_text}<|eot_id|>"
                f"<|start_header_id|>assistant<|end_header_id|>\n\n"
            )

        # Tokenize
        inputs = tokenizer(
            formatted_prompt,
            return_tensors="pt",
            truncation=True,
            max_length=LLAMA3_8B_CONTEXT_LIMIT
        )
        inputs = {k: v.to(DEVICE) for k, v in inputs.items()}
        prompt_tokens = inputs['input_ids'].shape[1]
        logger.debug(f"Prompt tokenized: {prompt_tokens} tokens")

        # Generate
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=2048,
                temperature=0.1,
                do_sample=True,
                pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )

        log_gpu_memory("After Inference", DEVICE)

        # Decode only generated tokens (exclude prompt)
        generated_tokens = outputs[0][prompt_tokens:]
        response = tokenizer.decode(generated_tokens, skip_special_tokens=False)

        # Strip Llama special tokens
        for token in ["<|eot_id|>", "<|start_header_id|>", "<|end_header_id|>", "<|begin_of_text|>"]:
            response = response.replace(token, "")

        response = response.strip()

        # Do NOT "repair" JSON by slicing on bracket counts. That breaks real output:
        # - Brackets inside string fields (e.g. summaries) make count('[') > count(']')
        #   even when JSON is valid.
        # - If the root array is missing a final ']', rfind(']') is the inner "claims"
        #   closer; backward matching then keeps only that nested array → one claim
        #   (often the last patient). Parsing is handled in extract_json_improved
        #   (raw_decode + fallbacks); consider raising max_new_tokens if truncated.

        logger.debug(f"Raw model response (first 1000 chars): {response[:1000]}")
        logger.info(f"Model response length: {len(response)} characters")

        response_tokens = len(generated_tokens)
        total_tokens = prompt_tokens + response_tokens
        logger.info(f"Prompt tokens: {prompt_tokens} | Response tokens: {response_tokens} | Total: {total_tokens}")

        if total_tokens > LLAMA3_8B_CONTEXT_LIMIT:
            logger.warning("Token count exceeds model context window!")

        return response

    except Exception as e:
        logger.error(f"Finetuned model error: {e}", exc_info=True)
        raise RetryableError(f"LLM processing failed: {e}", error_code="LLM_ERROR", details={"error": str(e)})



def extract_claims_from_chunk(chunk: str, filename: str | None = None) -> list[tuple[str | None, dict]]:
    """
    Extract claims from a transcript chunk using the prompt and model.

    This function processes a single transcript chunk through the LLM to extract
    structured healthcare claim data. It handles errors gracefully and returns
    a list of extracted claims with their associated provider tax IDs.

    Args:
        chunk (str): Transcript chunk text to process for claim extraction.
        filename (str | None, optional): Optional source filename for logging and debugging.
            Defaults to None.

    Returns:
        list[tuple[str | None, dict]]: List of (tax_id, claim_dict) tuples where:
            - tax_id: Provider tax ID (str) or None if not found
            - claim_dict: Dictionary containing extracted claim data
        Returns an empty list on parse failure.

    Raises:
        Exception: If LLM processing fails or returns invalid data.

    Note:
        This function uses the configured LLM model and prompt from the pipeline config.
        Claims are extracted using a structured prompt that instructs the LLM to
        return JSON-formatted data grouped by provider tax ID.

    Example:
        >>> chunk = "Patient John Doe, Provider ID 12345, Amount $100"
        >>> claims = extract_claims_from_chunk(chunk, "test.txt")
        >>> len(claims) > 0
        True
    """

    raw_text_token_count = count_tokens(chunk)
    logger.info(f"Raw transcript token count: {raw_text_token_count}")

    # ── Pass transcript directly — call_finetuned_model handles system/user split ──
    start_time = datetime.now()
    output = call_finetuned_model(chunk)  # ← no PROMPT_TEMPLATE.format() here
    latency = (datetime.now() - start_time).total_seconds()

    # Log the output for debugging
    logger.debug(f"Model output for {filename}: {output[:500] if output else 'EMPTY'}")

    try:
        # Try improved extraction first, fallback to original
        parsed_data = extract_json_improved(output)
        if not parsed_data:
            # Fallback to original extraction method
            parsed_data = extract_json_from_text(output)

        if not parsed_data:
            # Log the full output when JSON extraction fails
            logger.warning(f"⚠️ Failed to extract JSON from model output. Output preview: {output[:1000]}")
            logger.warning(f"⚠️ Full output length: {len(output)} characters")
            # Save problematic output to a file for debugging
            if filename:
                debug_file = Path(LOG_DIR) / f"failed_extraction_{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                try:
                    with open(debug_file, "w", encoding="utf-8") as f:
                        f.write(f"Prompt:\n{chunk}\n\n" + "="*80 + f"\n\nModel Output:\n{output}")
                    logger.info(f"Saved failed extraction output to: {debug_file}")
                except Exception as e:
                    logger.error(f"Failed to save debug file: {e}")
            raise ValidationError("No parsed data from LLM output", error_code="NO_DATA")
    except ValidationError as e:
        logger.warning(f"Validation error: {e}")
        if filename:
            log_claim_metric(None, None, "fail", latency, MODEL_NAME, filename, extra="validation_error")
        return []
    except Exception as e:
        logger.warning(f"Failed to parse chunk JSON: {e}")
        if filename:
            log_claim_metric(None, None, "fail", latency, MODEL_NAME, filename, extra="parse_error")
        return []

    all_claims: list[tuple[str | None, dict]] = []
    # Extract claims from parsed JSON
    for idx, group in enumerate(parsed_data):
        # Handle both dict and other types
        if not isinstance(group, dict):
            # Try to convert if it's a list with one dict
            if isinstance(group, list) and len(group) > 0 and isinstance(group[0], dict):
                group = group[0]
            else:
                continue

        # Handle tax_id - it might be 'null' string, None, or actual value
        tax_id_value = group.get("tax_id")
        if tax_id_value == 'null' or tax_id_value is None:
            tax_id = None
        elif isinstance(tax_id_value, str):
            tax_id = tax_id_value
        else:
            tax_id = str(tax_id_value) if tax_id_value else None

        raw_claims = group.get("claims", [])

        # Check if this is a flat claim structure (claim fields directly in group dict)
        # Expected claim fields: patient_first_name, patient_last_name, member_id, etc.
        claim_field_names = ['patient_first_name', 'patient_last_name', 'member_id', 'npi_id',
                            'date_of_birth', 'date_of_service', 'billed_amount','summary']
        has_claim_fields = any(key in group for key in claim_field_names)

        if len(raw_claims) == 0 and has_claim_fields:
            # This is a flat structure - the group dict itself contains claim fields
            # Extract tax_id and create a claim dict from the remaining fields
            claim_dict = {k: v for k, v in group.items() if k != 'tax_id'}
            raw_claims = [claim_dict]

        if not isinstance(raw_claims, list):
            # Try to convert if it's a single dict
            if isinstance(raw_claims, dict):
                raw_claims = [raw_claims]
            else:
                continue

        if len(raw_claims) == 0:
            continue

        for claim in raw_claims:
            if isinstance(claim, dict):
                sanitized_claim = sanitize_claim(claim)
                all_claims.append((tax_id, sanitized_claim))
                if filename:
                    log_claim_metric(
                        claim_id=sanitized_claim.get("member_id"),
                        tax_id=tax_id,
                        status="success",
                        latency=latency,
                        model_name=MODEL_NAME,
                        filename=filename,
                        extra="chunk_claim"
                    )

    return all_claims



def filter_and_deduplicate_claims(claim_list: List[Tuple[str | None, dict]]) -> List[Dict]:
    """
    Remove empty claims, deduplicate, and group by tax_id.

    This function processes a list of claims to remove duplicates and filter out
    invalid or incomplete claims. It ensures data quality by validating claim
    structure and removing redundant entries based on provider tax ID.

    Args:
        claim_list (List[Tuple[str | None, dict]]): List of (tax_id, claim_dict) tuples.
            Each tuple contains a provider tax ID and associated claim data.

    Returns:
        List[Dict]: List of grouped claim dictionaries with structure:
            [{"tax_id": str, "claims": [claim_dict, ...]}]
            Returns empty list if input is invalid.

    Raises:
        TypeError: If claim_list is not a list.
        ValueError: If claim_list contains invalid data structures.

    Note:
        Empty claims are defined as having null/empty names, null/zero billed amount, and null dates.
        Claims sharing the same member_id and date_of_service are collapsed to the richest row.
        Exact duplicate dicts are then skipped. Results are grouped by tax_id.

    Example:
        >>> claims = [("12345", {"amount": 100}), ("12345", {"amount": 100})]
        >>> filtered = filter_and_deduplicate_claims(claims)
        >>> len(filtered) == 1
        True
    """
    filtered_pairs: List[Tuple[str | None, dict]] = []
    for tax_id, claim in claim_list:
        if not isinstance(claim, dict):
            continue  # Skip malformed claim entries

        # Skip empty claims (check for None/null values instead of "UNKNOWN")
        if (
            (claim.get("patient_first_name") is None or claim.get("patient_first_name") == "")
            and (claim.get("patient_last_name") is None or claim.get("patient_last_name") == "")
            and (claim.get("billed_amount") is None or claim.get("billed_amount") == "0.00")
            and claim.get("date_of_service") is None
            and claim.get("date_of_birth") is None
            and claim.get("summary") is None
        ):
            continue

        filtered_pairs.append((tax_id, claim))

    deduped_pairs = Duplicate_check(filtered_pairs)

    grouped = defaultdict(list)
    seen_claims = set()

    for tax_id, claim in deduped_pairs:
        tax_id_key = str(tax_id).strip() if tax_id else "null"
        claim_tuple = tuple(sorted(claim.items()))  # For exact deduplication

        if claim_tuple not in seen_claims:
            grouped[tax_id_key].append(claim)
            seen_claims.add(claim_tuple)
        else:
            logger.info(f"🔁 Duplicate claim skipped: member_id={claim.get('member_id')}")

    return [{"tax_id": tax_id, "claims": claims} for tax_id, claims in grouped.items()]



def extract_claims_from_long_transcript(transcript_text: str, filename: str | None = None) -> List[dict]:
    """Extract claims from long transcripts (chunking if enabled).

    Currently processes a single chunk for simplicity; re-enable sliding windows for
    very long transcripts by uncommenting the chunking logic.
    """
    # chunks = sliding_window_chunks(transcript_text)
    # all_claims = []
    #
    # for idx, chunk_info in enumerate(chunks):
    #     chunk_text = chunk_info["chunk_text"]  # Adjust if your chunk structure differs
    #     logger.info(f"🧩 Processing chunk {idx + 1}/{len(chunks)}")
    #     chunk_claims = extract_claims_from_chunk(chunk_text, filename=filename)
    #     all_claims.extend(chunk_claims)
    #
    all_claims = extract_claims_from_chunk(transcript_text, filename=filename)

    # Deduplicate and format the output as needed
    deduped_claims = filter_and_deduplicate_claims(all_claims)

    return deduped_claims if deduped_claims else [{"tax_id": None, "claims": []}]


def save_main_claims_json(
    claims_data: List[Dict[str, Any]],
    filename: str,
    output_dir: str,
    summary: Optional[Dict[str, List[str]]] = None
) -> Dict[str, List[str]]:
    """
    Save the full claims_data as a JSON file and update summary dict.

    Args:
        claims_data: List of claim dictionaries.
        filename: Original transcript filename.
        output_dir: Directory to save the JSON file.
        summary: Optional dict to track saved and failed files.
                 Will be created if not provided.

    Returns:
        The updated summary dictionary.
    """
    if summary is None:
        summary = {}
    summary.setdefault("success", [])
    summary.setdefault("failed", [])
    summary.setdefault("raw_saved", [])

    try:
        output_dir_path = Path(output_dir)
        output_dir_path.mkdir(parents=True, exist_ok=True)

        base_name = Path(filename).stem
        main_json_filename = f"{base_name}_claims.json"
        main_json_path = output_dir_path / main_json_filename

        with open(main_json_path, "w", encoding="utf-8") as main_json_file:
            json.dump(claims_data, main_json_file, indent=2)

        summary.setdefault("raw_saved", []).append(main_json_filename)
        logger.info(f"📦 Main claims JSON saved: {main_json_path}")

    except (OSError, json.JSONDecodeError) as e:
        logger.error(
            f"❌ Failed to save main claims JSON {main_json_filename}: {e}",
            exc_info=True
        )
        summary.setdefault("failed", []).append(main_json_filename)

    return summary



def should_upload_to_blob(transcript_path: Path, claims_data: List[dict]) -> tuple[bool, str]:
    """
    Filter function to determine if files should be uploaded to blob storage.

    Filter logic (priority order):
    1. FIRST: If file has extracted claims → PASS (regardless of transcript size)
    2. ELSE: If transcript file > 1000 bytes → PASS
    3. ELSE: FAIL (no upload)

    Args:
        transcript_path: Path to the transcript file
        claims_data: List of claim dictionaries (grouped by tax_id)

    Returns:
        Tuple of (should_upload: bool, reason: str)
    """
    # Check condition 1: FIRST check if it has extracted claims
    has_claims = False
    if claims_data:
        for group in claims_data:
            claims = group.get("claims", [])
            if isinstance(claims, list) and len(claims) > 0:
                has_claims = True
                break

    if has_claims:
        # If it has claims, it passes regardless of transcript size
        return True, "Has extracted claims (passes filter regardless of transcript size)"

    # Condition 2: If no claims, check transcript file size
    if not transcript_path.exists():
        return False, "No claims extracted AND transcript file does not exist"

    transcript_size = transcript_path.stat().st_size
    if transcript_size > 1000:
        # No claims but transcript is large enough
        return True, f"No claims extracted but transcript size acceptable ({transcript_size} bytes > 1000 bytes)"
    else:
        # No claims AND transcript too small → FAIL
        return False, f"No claims extracted AND transcript file too small ({transcript_size} bytes <= 1000 bytes)"


def save_individual_claims(
    claims_data: List[dict],
    file_path: str,
    output_dir: str,
    summary: Dict[str, list]
) -> Tuple[List[Dict[str, Any]], List[Path], Optional[Path], int]:
    """
    Save each claim as a JSON file (grouped by tax_id), upload audio, transcript, and claim JSON files to blob storage.
    Updates summary dict with success and failure filenames.

    Args:
        claims_data (List[dict]): List of claim dicts with keys 'tax_id' and 'claims'.
        file_path (str): Name of the transcript file.
        output_dir (str): Directory to save claim JSON files.
        summary (Dict[str, list]): Shared dict for tracking success and failures.

    Returns:
        Tuple[List[Dict[str, Any]], List[Path], Optional[Path], int]:
            - All structured claim dicts saved (with storage IDs if available)
            - List of saved claim JSON file paths
            - The last claim folder path (for reference)
            - Total number of claims processed for this transcript
    """
    """Save each claim as a JSON, upload audio/transcript, update summary."""
    if summary is None:
        summary = {}
    summary.setdefault("success", [])
    summary.setdefault("failed", [])
    summary.setdefault("raw_saved", [])

    claim_folder = None

    filename = os.path.basename(file_path)
    base_filename = os.path.splitext(filename)[0]
    audio_file_name = f"{base_filename}.wav"

    timestamp_root = datetime.now().strftime("%Y%m%d%H%M%S%f")  # High precision for folder naming

    saved_paths = []
    all_claims_data = []  # in-memory JSON for DB insert

    # --- Apply Filter Before Upload ---
    transcript_path = Path(TRANSCRIPTS_DIR) / filename
    should_upload, filter_reason = should_upload_to_blob(transcript_path, claims_data)

    if not should_upload:
        logger.warning(f"🚫 Blob upload FILTERED OUT for {filename}: {filter_reason}")
        logger.warning(f"   File will NOT appear on https://artranscriptionapi.vitalaxis.com")
        logger.warning(f"   Transcript size: {transcript_path.stat().st_size if transcript_path.exists() else 0} bytes")
        logger.warning(f"   Claims count: {sum(len(group.get('claims', [])) for group in claims_data)}")
        # Set blob IDs to None - files will not be uploaded
        audio_file_blob_id = None
        transcript_file_blob_id = None
    else:
        logger.info(f"✅ Blob upload filter PASSED for {filename}: {filter_reason}")

        # --- Upload Audio File ---
        audio_path = Path(CLEANED_AUDIO_DIR) / audio_file_name
        audio_file_blob_id = None
        if audio_path.exists():
            try:
                logger.info(f"📤 Attempting to upload audio file: {audio_path}")
                audio_file_blob_id = upload_file_to_blob(str(audio_path))
                if audio_file_blob_id:
                    logger.info(f"✅ Audio file uploaded successfully. Blob ID: {audio_file_blob_id}")
                else:
                    logger.warning(f"⚠️ Audio file upload returned None (upload may have failed - check logs above): {audio_path}")
            except Exception as e:
                logger.error(f"❌ Failed to upload audio file '{audio_path}': {e}", exc_info=True)
        else:
            logger.error(f"❌ Audio file does not exist: {audio_path}")
            logger.error(f"   CLEANED_AUDIO_DIR: {CLEANED_AUDIO_DIR}")
            logger.error(f"   audio_file_name: {audio_file_name}")
            logger.error(f"   base_filename: {base_filename}")
            logger.error(f"   filename: {filename}")

        # --- Upload Transcript File ---
        transcript_file_blob_id = None
        if transcript_path.exists():
            try:
                logger.info(f"📤 Attempting to upload transcript file: {transcript_path}")
                transcript_file_blob_id = upload_file_to_blob(str(transcript_path))
                if transcript_file_blob_id:
                    logger.info(f"✅ Transcript file uploaded successfully. Blob ID: {transcript_file_blob_id}")
                else:
                    logger.warning(f"⚠️ Transcript file upload returned None (upload may have failed - check logs above): {transcript_path}")
            except Exception as e:
                logger.error(f"❌ Failed to upload transcript file '{transcript_path}': {e}", exc_info=True)
        else:
            logger.error(f"❌ Transcript file does not exist: {transcript_path}")
            logger.error(f"   TRANSCRIPTS_DIR: {TRANSCRIPTS_DIR}")
            logger.error(f"   filename: {filename}")


    total_claims = 0
    # Unique suffix per saved file (same patient/member/timestamp would otherwise overwrite)
    claim_file_seq = 0

    # Iterate over each group of claims (grouped by tax_id)
    for group in claims_data:
        tax_id_value = group.get("tax_id")
        # Handle tax_id - it might be 'null' string, None, or actual value
        if tax_id_value == 'null' or tax_id_value is None:
            tax_id = None
        elif isinstance(tax_id_value, str):
            tax_id = tax_id_value.strip() if tax_id_value.strip() else None
        else:
            tax_id = str(tax_id_value).strip() if tax_id_value else None

        # Use FALLBACK_TAX_ID if tax_id is still None
        if tax_id is None:
            tax_id = FALLBACK_TAX_ID

        claims = group.get("claims", [])
        if not isinstance(claims, list):
            logger.warning(f"Invalid claims list for tax_id {tax_id}: {claims}")
            continue

        # Use "null" for folder name if tax_id is None
        folder_name = f"{tax_id if tax_id else 'null'}_{timestamp_root}"
        claim_folder = os.path.join(output_dir, folder_name)
        os.makedirs(claim_folder, exist_ok=True)

        total_claims += len(claims)

        # Iterate over each claim in the group
        for claim in claims:
            claim = sanitize_claim(claim)
            logger.debug(f"🔍 Claim keys after sanitize: {list(claim.keys())}")
            logger.debug(f"🔍 Summary value after sanitize: {claim.get('summary')}")
            # Normalize provider tax ID if needed (preserve original tax_id for folder name)
            # Ensure None values stay as None (not string "null")
            if tax_id is None or tax_id == 'null':
                provider_tax_id = None
            else:
                provider_tax_id = str(tax_id).strip() if tax_id else None

            # Get claim values, using None instead of FALLBACK values
            firstname = claim.get("patient_first_name") if claim.get("patient_first_name") else None
            lastname = claim.get("patient_last_name") if claim.get("patient_last_name") else None
            member_id = claim.get("member_id") if claim.get("member_id") else None


            # Ensure filesystem-safe components (use "null" string for None values in filenames)
            safe_provider = safe_filename(str(provider_tax_id) if provider_tax_id else "null")
            safe_first = safe_filename(str(firstname) if firstname else "null")
            safe_last = safe_filename(str(lastname) if lastname else "null")
            safe_member = safe_filename(str(member_id) if member_id else "null")

            claim_file_seq += 1
            output_filename = (
                f"{safe_provider}_{safe_first}_{safe_last}_{safe_member}_"
                f"{timestamp_root}_{claim_file_seq:04d}.json"
            )
            # Optionally sanitize output_filename if user-supplied values are used
            claim_folder = Path(claim_folder)  # convert to Path if it's a str
            output_path = claim_folder / output_filename

            # Build the structured output for this claim
            structured_output = {
                "ARRecordingDetails": {
                    "audio_file_name": audio_file_name,
                    "audio_file_storage_id": audio_file_blob_id,
                    "original_transcript_file_name": filename,
                    "original_transcript_storage_id": transcript_file_blob_id,
                    "ClaimsList": [
                        {
                            "provider_tax_id": provider_tax_id,
                            "patient_first_name": firstname,
                            "patient_last_name": lastname,
                            "billed_amount": claim.get("billed_amount"),
                            "member_id": member_id,
                            "provider_npi_id": claim.get("provider_npi_id"),
                            "date_of_service": claim.get("date_of_service"),
                            "date_of_birth": claim.get("date_of_birth"),
                            "summary":claim.get("summary"),
                            "claim_transcript_file_name": filename,
                            "claim_transcript_storage_id": transcript_file_blob_id,
                            "claim_json_attributes_file_name": output_filename,
                            "claim_json_attributes_storage_id": None
                        }
                    ]
                }
            }

            try:
                # Save the structured claim JSON to disk (without blob ID initially)
                with open(output_path, "w", encoding="utf-8") as out_file:
                    json.dump(structured_output, out_file, indent=2)

                # Upload the claim JSON file to blob storage only if filter passed
                claim_json_file_id = None
                if should_upload:
                    claim_json_file_id = upload_file_to_blob(output_path)
                else:
                    logger.info(f"🚫 Claim JSON upload SKIPPED (filtered out): {output_filename}")

                if claim_json_file_id:
                    # Update the storage ID in the JSON structure (in-memory and on disk)
                    # This blob ID is what the website (https://artranscription.vitalaxis.com/) will use to retrieve the JSON
                    structured_output["ARRecordingDetails"]["ClaimsList"][0]["claim_json_attributes_storage_id"] = claim_json_file_id
                    
                    # Re-save the JSON file with the blob ID included
                    # Note: The version in blob storage doesn't have the blob ID, but the local file and data sent to API will have it
                    # The website retrieves the JSON using the blob ID, and can get the blob ID from the API response or database
                    with open(output_path, "w", encoding="utf-8") as out_file:
                        json.dump(structured_output, out_file, indent=2)
                    
                    logger.info(f"✅ Claim JSON uploaded to blob storage: {claim_json_file_id}")
                    logger.info(f"   All claim details are stored in blob storage and will be displayed on the website")
                else:
                    logger.warning(f"⚠️ Claim JSON upload failed, but claim data will still be returned with audio/transcript blob IDs")

                # CRITICAL: Always append structured_output to all_claims_data, even if claim JSON upload failed
                # This ensures audio and transcript blob IDs are preserved and returned to the pipeline
                all_claims_data.append(structured_output)

                summary["success"].append(output_filename)
                logger.info(f"✅ Saved: {output_path}")

                saved_paths.append(output_path)  # Add the absolute path to the list for this transcript

            except Exception as e:
                logger.error(f"Failed to save {output_filename}: {e}", exc_info=True)
                summary["failed"].append({"file": output_filename, "error": str(e)})
    logger.info(
        "Unique claims saved for this transcript: %s (after member_id + date_of_service dedupe)",
        total_claims,
    )
    # Return the list of all saved claim JSON file paths for this transcript
    return all_claims_data, saved_paths,  claim_folder, total_claims


def process_single_transcript(
    filename: str,
    transcripts_dir: str,
    output_dir: str,
    summary: Optional[dict] = None
) -> Tuple[List[Dict[str, Any]], List[Path], Optional[Path], int, float]:
    """
    Process a single transcript: read, clean, extract, and save claims.

    Args:
        filename (str): Transcript file name.
        transcripts_dir (str): Directory containing transcripts.
        output_dir (str): Directory to save extracted claim JSONs.
        summary (dict, optional): Shared dict for tracking success and failures.

    Returns:
        Tuple[List[dict], List[Path], Optional[Path], int, float]:
            - List of structured claim dicts
            - List of saved claim JSON file paths
            - Claim folder Path
            - Total number of claims extracted
            - Elapsed seconds for this transcript
    """
    start_time = time.time()

    if summary is None:
        summary = {}
    summary.setdefault("success", [])
    summary.setdefault("failed", [])
    summary.setdefault("raw_saved", [])

    filepath = Path(transcripts_dir) / filename
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            transcript_text = f.read()
    except Exception as e:
        logger.error(f"Failed to read {filename}: {e}", exc_info=True)
        summary["failed"].append(filename)
        return [], [], None, 0, 0.0

    logger.info(f"Processing {filename}...")
    cleaned = clean_transcript(transcript_text)

    all_claims = extract_claims_from_chunk(cleaned, filename=filename)
    claims_data = filter_and_deduplicate_claims(all_claims)

    if not any(group.get("claims") for group in claims_data):
        logger.warning(f"❌ No valid claims extracted for: {filename}")
        summary["failed"].append(filename)
        elapsed = time.time() - start_time
        return [], [], None, 0, elapsed

    # Save individual claims
    all_claims_data, saved_paths, claim_folder, total_claims = save_individual_claims(
        claims_data, filename, output_dir, summary
    )

    elapsed = time.time() - start_time
    return all_claims_data, saved_paths, claim_folder, total_claims, elapsed


def process_all_files(
    files: list,
    transcripts_dir: str,
    output_dir: str,
    summary: Optional[dict] = None
) -> Tuple[List[Dict[str, Any]], List[Path], Optional[Path], int, dict, Dict[str, float]]:
    """
    Sequentially process all transcript files.

    Returns:
        Tuple containing:
        - all_claims_data_all: list of all structured claim dicts across files
        - saved_paths_all: list of all saved JSON Paths
        - last_claim_folder: the last claim folder Path
        - total_claims_all_files: total number of claims processed across all files
        - summary: dict with success/failed/raw_saved counts
        - file_elapsed_times: mapping of filename to elapsed seconds
    """
    if summary is None:
        summary = {"success": [], "failed": [], "raw_saved": []}
    else:
        summary.setdefault("success", [])
        summary.setdefault("failed", [])
        summary.setdefault("raw_saved", [])

    all_claims_data_all = []
    saved_paths_all = []
    total_claims_all_files = 0
    last_claim_folder = None

    file_elapsed_times = {}  # key: filename, value: elapsed seconds

    for filename in tqdm(files, total=len(files), desc="Processing transcripts"):
        try:
            all_claims_data, saved_paths,  claim_folder, total_claims, elapsed = process_single_transcript(
                filename, transcripts_dir, output_dir, summary)

            all_claims_data_all.extend(all_claims_data)
            saved_paths_all.extend(saved_paths)
            total_claims_all_files += total_claims
            last_claim_folder = claim_folder
            file_elapsed_times[filename] = elapsed


        except Exception as e:
            logger.error(f"Error processing {filename}: {e}", exc_info=True)
            summary["failed"].append({"file": filename, "error": str(e)})
            file_elapsed_times[filename] = 0.0

    return all_claims_data_all, saved_paths_all, last_claim_folder, total_claims_all_files, summary, file_elapsed_times


def process_all_transcripts(files):
    """
    Main entry point for claim extraction stage.
    - Called from __main__ or pipeline controller.
    - Prepares output directory, summary, and lock.
    - Calls process_all_files to process all transcripts in parallel.
    - Writes summary report and returns extraction stats.
    """
    output_dir = Path(EXTRACTED_CLAIMS_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not files:
        logger.warning(f"No transcript files found in {TRANSCRIPTS_DIR}. Nothing to process.")
        return {"stage": "Claim Extraction", "status": "skipped", "reason": "No input files"}

    logger.info("📄 Stage 3: Claim Extraction started (Finetuned Model)")
    start_time = time.time()

    if torch.cuda.is_available() and DEVICE == "cuda":
        torch.cuda.reset_peak_memory_stats(0)

    logger.info(f"Found {len(files)} transcript(s). Starting extraction...")


    # Call the parallel file processor to process all transcript files concurrently
    # This will extract claims from each transcript and save results in the output directory
    all_claims_data, saved_paths, claim_folder, total_claims, summary, file_elapsed_times = process_all_files(
        files=files,
        transcripts_dir=TRANSCRIPTS_DIR,
        output_dir=EXTRACTED_CLAIMS_DIR,
        summary=None)

    # Metrics preparation
    metrics_rows = []
    for file_path in summary.get("success", []):
        metrics_rows.append({
            "timestamp": datetime.now().isoformat(),
            "file_name": file_path,
            "stage": "Claim Extraction",
            "success": True,
            "elapsed_sec": None,
            "error": "",
            "output_dir": str(claim_folder) if claim_folder else "",
        })

    for file_info in summary.get("failed", []):
        if isinstance(file_info, dict):
            file_name = file_info.get("file", "unknown")
            error = file_info.get("error", "")
        else:
            file_name = file_info
            error = ""
        metrics_rows.append({
            "timestamp": datetime.now().isoformat(),
            "file_name": file_name,
            "stage": "Claim Extraction",
            "success": False,
            "elapsed_sec": None,
            "error": error,
            "output_dir": str(claim_folder) if claim_folder else "",
        })
    gpu_info = get_gpu_memory_info(DEVICE)
    gpu_cols = {}
    if gpu_info:
        gpu_cols = {
            "gpu_allocated_gb": gpu_info.get("allocated_gb"),
            "gpu_reserved_gb": gpu_info.get("reserved_gb"),
            "gpu_max_allocated_gb": gpu_info.get("max_allocated_gb"),
            "gpu_total_gb": gpu_info.get("total_gb"),
            "gpu_free_gb": gpu_info.get("free_gb"),
            "gpu_utilization_pct": gpu_info.get("utilization_pct"),
        }
        vram_gb = gpu_info.get("max_allocated_gb")
        report_metrics.set_extraction_vram_gb(vram_gb)
    else:
        gpu_cols = {
            "gpu_allocated_gb": "", "gpu_reserved_gb": "", "gpu_max_allocated_gb": "",
            "gpu_total_gb": "", "gpu_free_gb": "", "gpu_utilization_pct": "",
        }
        report_metrics.set_extraction_vram_gb(None)
    for row in metrics_rows:
        row.update(gpu_cols)
        save_metrics(
            metrics_dir=METRICS_DIR,
            data=row,
            filename="claim_extraction.csv"
        )

    elapsed = time.time() - start_time
    total_success = len(summary.get("success", []))
    total_failed = len(summary.get("failed", []))

    total_elapsed = time.time() - start_time
    logger.info(
        f"✅ Stage 3 complete in {total_elapsed:.2f}s | 🟢 Success: {len(summary.get('success', []))} | 🔴 Failed: {len(summary.get('failed', []))}")

    # Return a dictionary summarizing the extraction results for this batch
    return {
        "stage": "Claim Extraction",
        "status": "success" if len(summary.get("failed", [])) == 0 else "partial",
        "total_files": len(files),
        "succeeded": len(summary.get("success", [])),
        "failed": len(summary.get("failed", [])),
        "elapsed_sec": total_elapsed,
        "report_file": REPORT_FILE,
        "extracted_data": all_claims_data,
        "claim_folder": claim_folder,
        "total_extracted_claims": total_claims
    }

if __name__ == "__main__":
    """
    Standalone script entry point.
    Loads transcript files, validates input directory, and runs the claim extraction pipeline.
    Logs summary statistics at the end.
    """
    transcripts_dir = Path(TRANSCRIPTS_DIR)

    # Validate input directory
    if not transcripts_dir.is_dir():
        logger.error(f"Transcripts directory does not exist: {TRANSCRIPTS_DIR}")
        sys.exit(1)

    try:
        # Get a list of all transcript files to process
        files = get_transcript_files(TRANSCRIPTS_DIR)
        if not files:
            logger.warning(f"No transcript files found in {TRANSCRIPTS_DIR}. Exiting.")
            sys.exit(0)

        # Run the claim extraction process on all transcript files
        stats = process_all_transcripts(files=files)
        logger.info(f"Claim Extraction stats: {stats}")
    except Exception as e:
        logger.exception(f"Fatal error during claim extraction: {e}")
        sys.exit(2)

