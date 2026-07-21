#!/usr/bin/env python3
"""
Flattens OpenIV's per-file-folder audio export into one flat folder.

OpenIV's "Export to WAVE" (and OpenFormats/OAC export) often creates a
separate subfolder for every single audio clip, e.g.:

    RADIO_02_POP/
        song_01/
            song_01.wav
        song_02/
            song_02.wav
        ...

This script walks a source folder recursively, finds every audio file
no matter how deeply nested, and copies (or moves) them all into one
flat destination folder -- exactly the shape this radio app's songs/,
dj/, and ads/ folders expect.

Usage:
    python flatten_audio.py <source_folder> <destination_folder>
    python flatten_audio.py <source_folder> <destination_folder> --move

Example, going straight into the app's folder structure:
    python flatten_audio.py "OpenIV Export/RADIO_02_POP" stations/non_stop_pop/songs

By default files are copied (originals kept). Pass --move to move them
instead -- faster, and it cleans up the now-empty nested folders
afterward.
"""

import argparse
import shutil
import sys
from pathlib import Path

AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac"}


def unique_destination(dest_folder: Path, filename: str) -> Path:
    """Return a non-colliding path in dest_folder for filename."""
    target = dest_folder / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 1
    while True:
        candidate = dest_folder / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def flatten(source: Path, dest: Path, move: bool):
    dest.mkdir(parents=True, exist_ok=True)

    audio_files = [p for p in source.rglob("*") if p.suffix.lower() in AUDIO_EXTS]

    if not audio_files:
        print(f"No audio files found under {source}")
        return

    count = 0
    for f in audio_files:
        target = unique_destination(dest, f.name)
        if move:
            shutil.move(str(f), str(target))
        else:
            shutil.copy2(str(f), str(target))
        count += 1

    print(f"{'Moved' if move else 'Copied'} {count} audio file(s) into {dest}")

    if move:
        # Clean up now-empty subfolders left behind, deepest first.
        removed = 0
        for folder in sorted(source.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if folder.is_dir() and folder != dest and not any(folder.iterdir()):
                folder.rmdir()
                removed += 1
        if removed:
            print(f"Cleaned up {removed} now-empty folder(s)")


def main():
    parser = argparse.ArgumentParser(
        description="Flatten OpenIV's per-file-folder audio export into one folder."
    )
    parser.add_argument("source", type=Path,
                         help="Folder OpenIV exported into (searched recursively)")
    parser.add_argument("destination", type=Path,
                         help="Flat folder to collect all audio files into")
    parser.add_argument("--move", action="store_true",
                         help="Move files instead of copying them, then clean up empty folders")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Source folder not found: {args.source}", file=sys.stderr)
        sys.exit(1)

    flatten(args.source, args.destination, args.move)


if __name__ == "__main__":
    main()
