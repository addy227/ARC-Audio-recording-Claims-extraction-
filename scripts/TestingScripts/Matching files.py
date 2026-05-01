import os
import json
import shutil

# ==============================
# HARDCODED PATHS
# ==============================

TRANSCRIPT_FOLDER = "/home/nalabotalaadvait/Documents/DATA/Finetuning files/TranscriptFiles"  # Folder containing .txt transcript files
JSON_FOLDER       = "/home/nalabotalaadvait/Documents/DATA/Finetuning files/ClaimJsonFiles"         # Folder containing .json files
OUTPUT_FOLDER     = "/home/nalabotalaadvait/Documents/DATA/Finetuning files/MatchedFiles"          # Folder where organised subfolders will be created

# ==============================
# HELPERS
# ==============================

def setup_folder(path: str):
    if not os.path.exists(path):
        os.makedirs(path)

def get_files(folder: str, extension: str) -> list:
    """Return all files in a folder with the given extension."""
    return [
        f for f in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, f)) and f.lower().endswith(extension)
    ]

def load_json_file(filepath: str) -> dict | None:
    """Load and parse a JSON file. Returns None on failure."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [!] Could not parse JSON '{os.path.basename(filepath)}': {e}")
        return None

def extract_transcript_names_from_json(data: dict) -> list:
    """
    Extract all claim_transcript_file_name values from the ClaimsList
    in an ARRecordingDetails JSON structure.
    """
    names = []
    try:
        claims = data.get("ARRecordingDetails", {}).get("ClaimsList", [])
        for claim in claims:
            name = claim.get("claim_transcript_file_name")
            if name:
                # Normalise: ensure .txt extension for comparison
                if not name.lower().endswith(".txt"):
                    name += ".txt"
                names.append(name)
    except Exception:
        pass
    return names

# ==============================
# CORE LOGIC
# ==============================

def build_transcript_to_json_map(json_files: list) -> dict:
    """
    Build a mapping of:
        transcript_filename → [list of json filenames that reference it]
    """
    mapping = {}

    for json_filename in json_files:
        json_path = os.path.join(JSON_FOLDER, json_filename)
        data      = load_json_file(json_path)
        if not data:
            continue

        transcript_names = extract_transcript_names_from_json(data)
        for transcript_name in transcript_names:
            if transcript_name not in mapping:
                mapping[transcript_name] = []
            if json_filename not in mapping[transcript_name]:
                mapping[transcript_name].append(json_filename)

    return mapping


def organise_files(transcript_files: list, transcript_to_json: dict):
    """
    For each transcript file:
      - Create a subfolder named after the transcript file (without extension)
      - Copy the transcript file into it
      - Copy all matching JSON files into it
    """
    setup_folder(OUTPUT_FOLDER)

    total_folders_created = 0
    total_json_copied     = 0
    total_unmatched       = 0

    for transcript_filename in transcript_files:
        transcript_path = os.path.join(TRANSCRIPT_FOLDER, transcript_filename)

        # Folder name = transcript filename without extension
        folder_name   = os.path.splitext(transcript_filename)[0]
        target_folder = os.path.join(OUTPUT_FOLDER, folder_name)

        matched_jsons = transcript_to_json.get(transcript_filename, [])

        if not matched_jsons:
            print(f"  [!] No matching JSON found for: {transcript_filename}")
            total_unmatched += 1
            continue

        setup_folder(target_folder)
        total_folders_created += 1

        # ── Copy transcript file ──────────────────────────────────────────────
        shutil.copy2(transcript_path, os.path.join(target_folder, transcript_filename))
        print(f"\n[→] {transcript_filename}")
        print(f"    Folder   : {target_folder}")
        print(f"    Transcript copied.")

        # ── Copy matched JSON files ───────────────────────────────────────────
        for json_filename in matched_jsons:
            json_src = os.path.join(JSON_FOLDER, json_filename)
            json_dst = os.path.join(target_folder, json_filename)

            if not os.path.exists(json_src):
                print(f"    [!] JSON file not found on disk: {json_filename}")
                continue

            shutil.copy2(json_src, json_dst)
            total_json_copied += 1
            print(f"    [✓] JSON copied : {json_filename}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n── Summary ──────────────────────────────────────────────────────")
    print(f"  Transcript files processed : {len(transcript_files)}")
    print(f"  Folders created            : {total_folders_created}")
    print(f"  JSON files copied          : {total_json_copied}")
    print(f"  Unmatched transcripts      : {total_unmatched}")

# ==============================
# MAIN
# ==============================

def main():
    for folder in [TRANSCRIPT_FOLDER, JSON_FOLDER]:
        if not os.path.exists(folder):
            raise FileNotFoundError(f"Folder not found: '{folder}'")

    transcript_files = get_files(TRANSCRIPT_FOLDER, ".txt")
    json_files       = get_files(JSON_FOLDER, ".json")

    if not transcript_files:
        print(f"No transcript (.txt) files found in '{TRANSCRIPT_FOLDER}'.")
        return
    if not json_files:
        print(f"No JSON files found in '{JSON_FOLDER}'.")
        return

    print(f"Found {len(transcript_files)} transcript file(s) and {len(json_files)} JSON file(s).\n")
    print("Building transcript → JSON mapping...\n")

    transcript_to_json = build_transcript_to_json_map(json_files)

    print(f"Organising files into '{OUTPUT_FOLDER}'...\n")
    organise_files(transcript_files, transcript_to_json)

    print("\n── All done ──")


if __name__ == "__main__":
    main()