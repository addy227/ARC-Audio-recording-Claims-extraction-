#!/usr/bin/env python3
"""
Average Audio File Length Calculator
Scans a directory for MP3 files and calculates average duration.
Requires: mutagen  ->  pip install mutagen
"""

import os
import sys
import argparse
from pathlib import Path

def get_mp3_duration(filepath):
    """Returns duration in seconds for a given MP3 file, or None on failure."""
    try:
        from mutagen.mp3 import MP3
        audio = MP3(str(filepath))
        return audio.info.length
    except Exception as e:
        return None


def format_duration(seconds):
    """Converts seconds to a human-readable HH:MM:SS or MM:SS string."""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def scan_directory(directory, recursive=False):
    """Scans a directory for MP3 files and returns duration stats."""
    dir_path = Path(directory)

    if not dir_path.exists():
        print(f"Error: Directory '{directory}' does not exist.")
        sys.exit(1)

    if not dir_path.is_dir():
        print(f"Error: '{directory}' is not a directory.")
        sys.exit(1)

    # Collect MP3 files
    if recursive:
        mp3_files = list(dir_path.rglob("*.mp3")) + list(dir_path.rglob("*.MP3"))
    else:
        mp3_files = list(dir_path.glob("*.mp3")) + list(dir_path.glob("*.MP3"))

    total_files = len(mp3_files)

    if total_files == 0:
        print(f"No MP3 files found in '{directory}'.")
        sys.exit(0)

    print(f"\nFound {total_files} MP3 file(s). Scanning...")
    print("-" * 50)

    durations = []
    failed = []

    for i, filepath in enumerate(mp3_files, 1):
        # Progress indicator every 100 files
        if i % 100 == 0 or i == total_files:
            print(f"  Progress: {i}/{total_files} files processed...", end="\r")

        duration = get_mp3_duration(filepath)
        if duration is not None:
            durations.append(duration)
        else:
            failed.append(filepath.name)

    print()  # newline after progress

    return durations, failed


def print_report(durations, failed, directory):
    """Prints a summary report of the scan results."""
    total = len(durations) + len(failed)
    successful = len(durations)

    print("\n" + "=" * 50)
    print("        AUDIO FILE LENGTH REPORT")
    print("=" * 50)
    print(f"  Directory     : {directory}")
    print(f"  Total files   : {total}")
    print(f"  Readable files: {successful}")
    print(f"  Failed/skipped: {len(failed)}")
    print("-" * 50)

    if durations:
        avg = sum(durations) / len(durations)
        total_dur = sum(durations)
        min_dur = min(durations)
        max_dur = max(durations)

        print(f"  Average length: {format_duration(avg)}  ({avg:.2f}s)")
        print(f"  Shortest file : {format_duration(min_dur)}  ({min_dur:.2f}s)")
        print(f"  Longest file  : {format_duration(max_dur)}  ({max_dur:.2f}s)")
        print(f"  Total duration: {format_duration(total_dur)}")
    else:
        print("  No valid durations found.")

    if failed:
        print("\n  Files that could not be read (first 10):")
        for name in failed[:10]:
            print(f"    - {name}")
        if len(failed) > 10:
            print(f"    ... and {len(failed) - 10} more.")

    print("=" * 50 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Calculate average MP3 audio file length in a directory."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Path to the directory containing MP3 files (default: current directory)"
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="Scan subdirectories recursively"
    )

    args = parser.parse_args()

    # Check mutagen is installed
    try:
        import mutagen
    except ImportError:
        print("Error: 'mutagen' library is not installed.")
        print("Install it with:  pip install mutagen")
        sys.exit(1)

    durations, failed = scan_directory(args.directory, recursive=args.recursive)
    print_report(durations, failed, args.directory)


if __name__ == "__main__":
    main()