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

# ==============================
# HARDCODED PATHS
# ==============================

INPUT_FOLDER  = "/home/nalabotalaadvait/Documents/DATA/Finetuning files/NewTemp"   # Folder containing downloaded JSON files
OUTPUT_FOLDER = "/home/nalabotalaadvait/Documents/DATA/Finetuning files/TranscriptFiles"  # Folder to save transcript .txt files

# ==============================
# HELPERS
# ==============================

def setup_output_folder(folder_path: str):
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"Created folder: {folder_path}")

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

def get_json_files(folder: str) -> list:
    """Return all .json files in the input folder."""
    return [
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.lower().endswith(".json")
    ]

# ==============================
# CORE DOWNLOAD LOGIC
# ==============================

def download_transcript(storage_id: str, transcript_filename: str):
    """Download a transcript file using its storage ID and save it with the given filename."""
    try:
        full_url = f"{DOWNLOAD_URL}/{storage_id}"
        response = requests.get(full_url, headers=build_headers(), timeout=30)

        if response.status_code != 200:
            print(f"    [✗] Failed ({response.status_code}) → storage_id: {storage_id}")
            return

        # Ensure .txt extension
        if not transcript_filename.lower().endswith(".txt"):
            transcript_filename += ".txt"

        filename  = make_unique_filename(OUTPUT_FOLDER, transcript_filename)
        save_path = os.path.join(OUTPUT_FOLDER, filename)

        with open(save_path, "w", encoding="utf-8") as f:
            f.write(response.text)

        print(f"    [✓] Saved → {filename}")

    except Exception as e:
        print(f"    [✗] Error downloading transcript '{storage_id}': {str(e)}")


def process_json_file(json_path: str):
    """Read a single JSON file, extract claims, and download each transcript."""
    print(f"\n[→] Reading: {os.path.basename(json_path)}")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"    [✗] Failed to parse JSON: {str(e)}")
        return

    ar_details  = data.get("ARRecordingDetails", {})
    claims_list = ar_details.get("ClaimsList", [])

    if not claims_list:
        print(f"    [!] No claims found.")
        return

    for idx, claim in enumerate(claims_list, start=1):
        storage_id          = claim.get("claim_transcript_storage_id")
        transcript_filename = claim.get("claim_transcript_file_name")

        print(f"    [·] Claim {idx}/{len(claims_list)}: {transcript_filename}")

        if not storage_id:
            print(f"    [!] Skipping — claim_transcript_storage_id is missing or null.")
            continue

        if not transcript_filename:
            # Fallback filename using storage_id if file name is also missing
            transcript_filename = f"{storage_id}.txt"
            print(f"    [!] claim_transcript_file_name missing, using fallback: {transcript_filename}")

        download_transcript(storage_id, transcript_filename)

# ==============================
# MAIN
# ==============================

def main():
    if not DOWNLOAD_URL:
        raise ValueError("Prod_DOWNLOAD_URL is not set in your .env file.")

    setup_output_folder(OUTPUT_FOLDER)

    json_files = get_json_files(INPUT_FOLDER)

    if not json_files:
        print(f"No JSON files found in '{INPUT_FOLDER}'.")
        return

    print(f"Found {len(json_files)} JSON file(s) in '{INPUT_FOLDER}'\n")

    for json_path in json_files:
        process_json_file(json_path)

    print("\n── All done ──")

if __name__ == "__main__":
    main()