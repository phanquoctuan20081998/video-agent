#!/usr/bin/env python3
"""
ElevenLabs Scribe transcription — word-level timestamps, speaker ID, audio events.
Caches results per source file (never re-transcribes unchanged files).

Usage:
    # Single file
    python helpers/transcribe.py video.mp4 --output outputs/edit/transcripts/

    # Batch directory (4 parallel workers)
    python helpers/transcribe.py videos/ --batch --output outputs/edit/transcripts/

    # With speaker count hint
    python helpers/transcribe.py video.mp4 --speakers 2 --output outputs/edit/transcripts/
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"
AUDIO_EXTS = {".mp3", ".mp4", ".mov", ".m4a", ".wav", ".aac", ".mkv", ".webm"}


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.stat().st_mtime_ns.to_bytes(8, "little"))
    h.update(str(path.stat().st_size).encode())
    return h.hexdigest()[:16]


def cache_key(path: Path, language: str) -> str:
    return f"{file_hash(path)}:{language or 'auto'}"


def extract_audio(video_path: Path, tmp_dir: str) -> Path:
    """Extract audio track from video for transcription."""
    import subprocess
    audio_path = Path(tmp_dir) / f"{video_path.stem}_audio.mp3"
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-q:a", "0", "-map", "a",
         "-ac", "1", "-ar", "16000", str(audio_path)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg extract failed: {result.stderr}")
    return audio_path


def transcribe_file(
    file_path: Path,
    output_dir: Path,
    api_key: str,
    speakers: int = 1,
    language: str = "",
) -> Path:
    """Transcribe single file. Returns path to JSON output. Uses cache."""
    output_dir.mkdir(parents=True, exist_ok=True)
    current_cache_key = cache_key(file_path, language)
    out_path = output_dir / f"{file_path.stem}.json"

    # Check cache: if output exists and source unchanged, skip
    cache_meta = output_dir / f"{file_path.stem}.cache"
    if out_path.exists() and cache_meta.exists():
        if cache_meta.read_text().strip() == current_cache_key:
            print(f"[transcribe] cache hit: {file_path.name}", file=sys.stderr)
            return out_path

    print(f"[transcribe] transcribing: {file_path.name}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        # Extract audio if video file
        if file_path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}:
            audio_path = extract_audio(file_path, tmp)
        else:
            audio_path = file_path

        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/mpeg")}
            data = {
                "model_id": "scribe_v1",
                "timestamps_granularity": "word",
                "diarize": "true" if speakers > 1 else "false",
                "num_speakers": str(speakers),
                "tag_audio_events": "true",
            }
            if language:
                data["language_code"] = language.split("-")[0]
            headers = {"xi-api-key": api_key}

            resp = httpx.post(
                SCRIBE_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=300,
            )
            resp.raise_for_status()

    result = resp.json()
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    cache_meta.write_text(current_cache_key)
    print(f"[transcribe] done: {out_path}", file=sys.stderr)
    return out_path


def batch_transcribe(
    directory: Path,
    output_dir: Path,
    api_key: str,
    speakers: int = 1,
    workers: int = 4,
    language: str = "",
) -> list[Path]:
    files = [f for f in sorted(directory.iterdir())
             if f.suffix.lower() in AUDIO_EXTS and not f.name.startswith(".")]

    if not files:
        print(f"[transcribe] no audio/video files in {directory}", file=sys.stderr)
        return []

    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(transcribe_file, f, output_dir, api_key, speakers, language): f
            for f in files
        }
        for future in as_completed(futures):
            src = futures[future]
            try:
                results.append(future.result())
            except Exception as e:
                print(f"[transcribe] ERROR {src.name}: {e}", file=sys.stderr)

    return results


def main():
    parser = argparse.ArgumentParser(description="ElevenLabs Scribe transcription")
    parser.add_argument("input", help="Video/audio file or directory (with --batch)")
    parser.add_argument("--output", required=True, help="Output directory for JSON files")
    parser.add_argument("--batch", action="store_true", help="Transcribe all files in directory")
    parser.add_argument("--speakers", type=int, default=1, help="Expected speaker count (for diarization)")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (batch mode)")
    parser.add_argument("--language", default="", help="Language code hint, e.g. vi")
    args = parser.parse_args()

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.input)
    output_dir = Path(args.output)

    if args.batch:
        if not input_path.is_dir():
            print(f"ERROR: {input_path} is not a directory", file=sys.stderr)
            sys.exit(1)
        results = batch_transcribe(input_path, output_dir, api_key, args.speakers, args.workers, args.language)
        print(f"[transcribe] batch complete: {len(results)} files", file=sys.stderr)
    else:
        if not input_path.exists():
            print(f"ERROR: {input_path} not found", file=sys.stderr)
            sys.exit(1)
        result = transcribe_file(input_path, output_dir, api_key, args.speakers, args.language)
        print(str(result))


if __name__ == "__main__":
    main()
