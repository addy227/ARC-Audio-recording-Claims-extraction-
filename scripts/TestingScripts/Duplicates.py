import os
import re
from collections import defaultdict

# ==============================
# HARDCODED PATH
# ==============================

TARGET_FOLDER = "/home/nalabotalaadvait/Documents/DATA/Finetuning files/TranscriptFiles"  # Folder to scan for duplicates

# ==============================
# HELPERS
# ==============================

def scan_files(folder: str) -> list:
    """Return a list of (filepath, filename, size) for all files in the folder."""
    files = []
    for f in os.listdir(folder):
        filepath = os.path.join(folder, f)
        if os.path.isfile(filepath):
            size = os.path.getsize(filepath)
            files.append((filepath, f, size))
    return files


def is_duplicate(filename: str) -> bool:
    """
    Returns True if the filename ends with _N.txt pattern.
    e.g. report_1.txt, file_3.txt, transcript_12.txt → True
         report.txt, file.txt                         → False
    """
    return bool(re.search(r"_\d{1,3}\.txt$", filename, re.IGNORECASE))


def format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def find_duplicates(files: list) -> list:
    """
    Return all files whose filename matches the _N.txt duplicate pattern.
    """
    return [(filepath, filename, size) for filepath, filename, size in files if is_duplicate(filename)]


def print_statistics(files: list, duplicates: list):
    """Print overall and per-file duplicate statistics."""

    total_files     = len(files)
    total_dup_files = len(duplicates)
    total_originals = total_files - total_dup_files
    total_wasted    = sum(size for _, _, size in duplicates)

    print("── Overall Statistics ───────────────────────────────────────────")
    print(f"  Total files scanned      : {total_files}")
    print(f"  Unique original files    : {total_originals}")
    print(f"  Total duplicate files    : {total_dup_files}")
    print(f"  Total wasted space       : {format_size(total_wasted)}")

    print("\n── Duplicate Files Found ────────────────────────────────────────")
    for filepath, filename, size in duplicates:
        print(f"  [dupe] {filename}  ({format_size(size)})")

    print(f"\n  Total wasted space : {format_size(total_wasted)}")

# ==============================
# CORE LOGIC
# ==============================

def delete_duplicates(duplicates: list) -> tuple:
    """
    Delete all files identified as duplicates (_N.txt pattern).
    Returns (deleted_count, freed_bytes).
    """
    deleted_count = 0
    freed_bytes   = 0

    for filepath, filename, size in duplicates:
        try:
            os.remove(filepath)
            freed_bytes   += size
            deleted_count += 1
            print(f"  [✓] Deleted → {filename}")
        except Exception as e:
            print(f"  [✗] Failed to delete '{filename}': {str(e)}")

    return deleted_count, freed_bytes

# ==============================
# MAIN
# ==============================

def main():
    if not os.path.exists(TARGET_FOLDER):
        raise FileNotFoundError(f"Target folder not found: '{TARGET_FOLDER}'")

    print(f"Scanning: {TARGET_FOLDER}\n")

    files      = scan_files(TARGET_FOLDER)
    duplicates = find_duplicates(files)

    if not duplicates:
        print(f"Total files scanned : {len(files)}")
        print("\nNo duplicates found. No files ending with _N.txt detected.")
        return

    # ── Statistics & preview ─────────────────────────────────────────────────
    print_statistics(files, duplicates)

    # ── Confirm before deleting ───────────────────────────────────────────────
    print("\n─────────────────────────────────────────────────────────────────")
    confirm = input("Delete all duplicate files (those ending in _N.txt)? (yes/no): ").strip().lower()

    if confirm != "yes":
        print("Aborted. No files were deleted.")
        return

    # ── Delete ────────────────────────────────────────────────────────────────
    print("\n── Deleting Duplicates ──────────────────────────────────────────")
    deleted_count, freed_bytes = delete_duplicates(duplicates)

    print(f"\n── Final Summary ────────────────────────────────────────────────")
    print(f"  Files deleted : {deleted_count}")
    print(f"  Space freed   : {format_size(freed_bytes)}")
    print("\n── All done ──")


if __name__ == "__main__":
    main()