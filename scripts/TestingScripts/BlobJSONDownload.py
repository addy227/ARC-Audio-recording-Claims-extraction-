import os
import json
import requests
from dotenv import load_dotenv

# ==============================
# LOAD ENV VARIABLES
# ==============================

load_dotenv()

DOWNLOAD_URL        = os.getenv("Prod_DOWNLOAD_URL")
X_VA_HASH           = os.getenv("Prod_X_VA_HASH")
X_VA_TRANSACTION_ID = os.getenv("Prod_X_VA_TRANSACTION_ID")
X_VA_SENDERAGENT_ID = os.getenv("Prod_X_VA_SENDERAGENT_ID")

# ── Input / Output paths ──────────────────────────────────────────────────────
BLOB_IDS_FILE = "/home/nalabotalaadvait/Documents/DATA/Finetuning files/ClaimJsonBlob.txt"
OUTPUT_DIR    = "/home/nalabotalaadvait/Documents/DATA/Finetuning files/Temp"

# ==============================
# HELPERS
# ==============================

def setup_output_folder(folder_path: str):
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"Created folder: {folder_path}")

def load_blob_ids(filepath: str) -> list:
    """Read blob IDs from a text file, one ID per line. Skips empty lines."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Blob IDs file not found: '{filepath}'")
    with open(filepath, "r", encoding="utf-8") as f:
        ids = [line.strip() for line in f if line.strip()]
    if not ids:
        raise ValueError(f"No blob IDs found in '{filepath}'.")
    return ids

def build_headers() -> dict:
    return {
        "x-va-hash":           X_VA_HASH,
        "x-va-transaction-id": X_VA_TRANSACTION_ID,
        "x-va-senderagent-id": X_VA_SENDERAGENT_ID,
        "Accept":              "application/json",
    }

def make_unique_filename(folder: str, filename: str) -> str:
    """Append a counter to filename if it already exists in folder."""
    base, ext = os.path.splitext(filename)
    counter = 1
    new_filename = filename
    while os.path.exists(os.path.join(folder, new_filename)):
        new_filename = f"{base}_{counter}{ext}"
        counter += 1
    return new_filename

# ==============================
# FILENAME RESOLUTION
# ==============================

def extract_timestamp_root(audio_file_name: str) -> str:
    """
    Extract timestamp from audio filename.
    Expected format: {uuid}_{agent}_{phone}_{date}_{time}.wav
    Returns e.g. '17022026_141051', or 'unknown' if not parseable.
    """
    try:
        parts = audio_file_name.rsplit(".", 1)[0].split("_")
        return f"{parts[-2]}_{parts[-1]}"
    except Exception:
        return "unknown"

def resolve_filename(claim: dict, ar_details: dict) -> str:
    """
    Determine the output filename for a claim's JSON file.
    - Uses claim_json_attributes_file_name if present and non-null.
    - Falls back to: {provider_tax_id}_{first}_{last}_{member_id}_{timestamp_root}.json
    """
    json_filename = claim.get("claim_json_attributes_file_name")
    if json_filename:
        if not json_filename.lower().endswith(".json"):
            json_filename += ".json"
        return json_filename

    provider_tax_id = claim.get("provider_tax_id", "unknown")
    first_name      = claim.get("patient_first_name", "unknown")
    last_name       = claim.get("patient_last_name", "unknown")
    member_id       = claim.get("member_id", "unknown")
    timestamp_root  = extract_timestamp_root(ar_details.get("audio_file_name", ""))

    return f"{provider_tax_id}_{first_name}_{last_name}_{member_id}_{timestamp_root}.json"

# ==============================
# CORE DOWNLOAD LOGIC
# ==============================

def download_and_process_blob(blob_id: str):
    try:
        full_url = f"{DOWNLOAD_URL}/{blob_id}"
        response = requests.get(full_url, headers=build_headers(), timeout=30)

        if response.status_code != 200:
            print(f"[✗] Failed ({response.status_code}) → {blob_id}")
            return

        raw_json_text = response.text

        # Parse to extract structure
        data = json.loads(raw_json_text)

        ar_details  = data.get("ARRecordingDetails", {})
        claims_list = ar_details.get("ClaimsList", [])

        if not claims_list:
            print(f"[!] No claims found in blob: {blob_id}")
            return

        for idx, claim in enumerate(claims_list, start=1):
            filename = resolve_filename(claim, ar_details)
            filename = make_unique_filename(OUTPUT_DIR, filename)
            local_path = os.path.join(OUTPUT_DIR, filename)

            # Save raw JSON exactly as downloaded
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(raw_json_text)

            print(f"[✓] Claim {idx}/{len(claims_list)} saved → {filename}")

    except Exception as e:
        print(f"[✗] Error processing {blob_id}: {str(e)}")

# ==============================
# MAIN
# ==============================

def main():
    if not DOWNLOAD_URL:
        raise ValueError("Prod_DOWNLOAD_URL is not set in your .env file.")

    setup_output_folder(OUTPUT_DIR)
    blob_ids = load_blob_ids(BLOB_IDS_FILE)

    print(f"Starting download — {len(blob_ids)} blob ID(s) found in '{BLOB_IDS_FILE}'\n")

    for blob_id in blob_ids:
        download_and_process_blob(blob_id)

    print("\n── All done ──")

if __name__ == "__main__":
    main()