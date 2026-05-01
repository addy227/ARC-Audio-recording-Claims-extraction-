import os
import json

# ==============================
# HARDCODED PATHS
# ==============================

ORGANISED_FOLDER = "/home/nalabotalaadvait/Documents/DATA/Finetuning files/MatchedFiles"  # Folder containing subfolders (one per transcript)
OUTPUT_FILE      = "/home/nalabotalaadvait/Documents/DATA/Finetuning files/llama_finetune_dataset.json"  # Output dataset file

# ==============================
# SYSTEM PROMPT (instruction only, no transcript placeholder)
# ==============================

SYSTEM_PROMPT = """You are a data extraction assistant. Your task is to extract **structured medical patient-related claim information** from a given conversation transcript and - Strictly do not extract provider-only information.
. The transcript may contain **details for multiple claims**, possibly involving different providers or patients. Your focus is strictly on **patient-related claim information**.

### Your Responsibilities:
- Extract **only patient-related claim information** from the transcript. Ignore provider-only or administrative details not directly related to patient claims.
- Identify and separate **individual claim contexts** within the transcript. A new claim usually corresponds to a change in patient, member ID, or date of service.
- Group claims by **Tax ID**. If the Tax ID is unavailable, use `"tax_id": null`.
- Extract all relevant fields **exactly as spoken in the transcript** — do NOT infer, assume, or generate any information not explicitly present in the transcript.
- Include the **complete transcription snippet** corresponding to each claim.
- Respond strictly in **valid JSON format only** — no explanations or additional commentary.
- Be careful not to confuse different ID types: do NOT misclassify Tax ID, Member ID, or NPI.

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
  - `"member_id"`: an Alphanumerical Identifier consisting of numbers and/or letters that may include letters spelled out as words; Use NATO phonetic alphabet to spell alphabets accurately. Do NOT extract or include invalid IDs or misclassify similar-looking numbers.
- If any ambiguity arises, assign `null` rather than guessing.

### Field Pattern Hints:
- Tax ID: 9-digit numeric, no letters.
- NPI ID: 10-digit numeric.
- Member ID: an identifier consisting of numbers and letters (Alphanumerical Value), Use NATO phonetic alphabet to spell alphabets accurately *MAKE SURE TO INCLUDE ALPHABETS ALONG WITH NUMBERS*.
- patient_first_name and patient_last_name: Extract exactly as spoken in context, do not assume or guess names. More information regarding names can be written in the form of phonetic alphabets.
- Dates: Use ISO format `MM-DD-YYYY`.
- Amounts: Extract exactly as spoken.
- Names: Extract exactly as spoken. Do not Merge 2 names together at any cost.

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

---

### Output Format:
[
  {
    "tax_id": "string or null",
    "claims": [
      {
        "npi_id": "string or null",
        "patient_first_name": "string or null",
        "patient_last_name": "string or null",
        "date_of_birth": "MM-DD-YYYY or null",
        "billed_amount": "string or null",
        "date_of_service": "MM-DD-YYYY or null",
        "member_id": "string or null"
      }
    ]
  }
]

Respond ONLY with the JSON array."""

# ==============================
# HELPERS
# ==============================

def setup_folder(path: str):
    if not os.path.exists(path):
        os.makedirs(path)

def read_transcript(filepath: str) -> str | None:
    """Read transcript text from a .txt file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"  [!] Could not read transcript '{os.path.basename(filepath)}': {e}")
        return None

def load_json_file(filepath: str) -> dict | None:
    """Load and parse a JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [!] Could not parse JSON '{os.path.basename(filepath)}': {e}")
        return None

def extract_claims_output(json_data: dict) -> list:
    """
    Extract claims from the JSON and group them by provider_tax_id.
    """
    grouped = {}

    try:
        claims_list = json_data.get("ARRecordingDetails", {}).get("ClaimsList", [])
        for claim in claims_list:
            tax_id = claim.get("provider_tax_id", None)
            key    = tax_id if tax_id else "null"

            if key not in grouped:
                grouped[key] = {
                    "tax_id": tax_id,
                    "claims": []
                }

            grouped[key]["claims"].append({
                "npi_id":             claim.get("provider_npi_id", None),
                "patient_first_name": claim.get("patient_first_name", None),
                "patient_last_name":  claim.get("patient_last_name", None),
                "date_of_birth":      claim.get("date_of_birth", None),
                "billed_amount":      claim.get("billed_amount", None),
                "date_of_service":    claim.get("date_of_service", None),
                "member_id":          claim.get("member_id", None),
            })
    except Exception as e:
        print(f"  [!] Error extracting claims: {e}")

    return list(grouped.values())

# ==============================
# DATASET BUILDER
# ==============================

def build_dataset() -> list:
    """
    Walk through each subfolder in ORGANISED_FOLDER.
    Each subfolder = one transcript + one or more JSON files.
    Produces one ShareGPT sample per subfolder with 3 turns:
      - system : extraction instructions
      - human  : raw transcript text
      - gpt    : expected JSON output
    """
    dataset = []

    subfolders = sorted([
        d for d in os.listdir(ORGANISED_FOLDER)
        if os.path.isdir(os.path.join(ORGANISED_FOLDER, d))
    ])

    if not subfolders:
        print(f"No subfolders found in '{ORGANISED_FOLDER}'.")
        return dataset

    print(f"Found {len(subfolders)} subfolder(s) to process.\n")

    skipped = 0

    for folder_name in subfolders:
        folder_path = os.path.join(ORGANISED_FOLDER, folder_name)

        # ── Find the transcript file ──────────────────────────────────────────
        txt_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".txt")]
        if not txt_files:
            print(f"  [!] No transcript found in '{folder_name}', skipping.")
            skipped += 1
            continue

        transcript_filename = txt_files[0]
        transcript_text     = read_transcript(os.path.join(folder_path, transcript_filename))
        if not transcript_text:
            print(f"  [!] Transcript is empty in '{folder_name}', skipping.")
            skipped += 1
            continue

        # ── Find all JSON files ───────────────────────────────────────────────
        json_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".json")]
        if not json_files:
            print(f"  [!] No JSON files found in '{folder_name}', skipping.")
            skipped += 1
            continue

        # ── Merge all claims from all JSON files grouped by tax_id ────────────
        merged_output = {}

        for json_filename in sorted(json_files):
            json_data = load_json_file(os.path.join(folder_path, json_filename))
            if not json_data:
                continue

            for group in extract_claims_output(json_data):
                key = group["tax_id"] if group["tax_id"] else "null"
                if key not in merged_output:
                    merged_output[key] = {"tax_id": group["tax_id"], "claims": []}
                merged_output[key]["claims"].extend(group["claims"])

        if not merged_output:
            print(f"  [!] No claims extracted in '{folder_name}', skipping.")
            skipped += 1
            continue

        final_output = list(merged_output.values())

        # ── Build ShareGPT sample ─────────────────────────────────────────────
        # 3 turns: system (instructions) → human (transcript) → gpt (JSON output)
        sample = {
            "conversations": [
                {
                    "from":  "system",
                    "value": SYSTEM_PROMPT
                },
                {
                    "from":  "human",
                    "value": transcript_text
                },
                {
                    "from":  "gpt",
                    "value": json.dumps(final_output, indent=2, ensure_ascii=False)
                }
            ]
        }

        dataset.append(sample)
        print(f"  [✓] {folder_name}  ({len(json_files)} JSON file(s), {len(final_output)} tax group(s))")

    print(f"\n── Build Complete ───────────────────────────────────────────────")
    print(f"  Samples created : {len(dataset)}")
    print(f"  Skipped         : {skipped}")

    return dataset

# ==============================
# MAIN
# ==============================

def main():
    if not os.path.exists(ORGANISED_FOLDER):
        raise FileNotFoundError(f"Organised folder not found: '{ORGANISED_FOLDER}'")

    setup_folder(os.path.dirname(OUTPUT_FILE))

    print(f"Building LlamaFactory ShareGPT dataset...\n")
    print(f"  Source : {ORGANISED_FOLDER}")
    print(f"  Output : {OUTPUT_FILE}\n")

    dataset = build_dataset()

    if not dataset:
        print("No samples generated. Dataset file not created.")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)

    print(f"\n  Dataset saved → {OUTPUT_FILE}")
    print(f"  Total samples : {len(dataset)}")
    print("\n── All done ──")


if __name__ == "__main__":
    main()