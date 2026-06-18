#!/usr/bin/env python3
"""
Convert raw ElevenLabs Scribe JSON transcripts to phrase-level markdown.
Breaks on speaker change or silence ≥0.5s. Output ~12KB vs 300KB raw JSON.

Usage:
    python helpers/pack_transcripts.py outputs/edit/transcripts/ --output outputs/edit/takes_packed.md
    python helpers/pack_transcripts.py single_transcript.json --output takes_packed.md
"""

import argparse
import json
import sys
from pathlib import Path


SILENCE_THRESHOLD = 0.5  # seconds — break phrase on gap ≥ this


def format_ts(seconds: float) -> str:
    return f"{seconds:06.2f}"


def pack_transcript(name: str, data: dict) -> str:
    """Convert single Scribe JSON to packed phrase markdown."""
    words = data.get("words", [])
    if not words:
        return f"## {name}  (no words)\n\n"

    # Filter to actual word tokens (not punctuation-only or audio events)
    word_tokens = [
        w for w in words
        if w.get("type") == "word" and w.get("text", "").strip()
    ]

    if not word_tokens:
        return f"## {name}  (no word tokens)\n\n"

    # Calculate duration from first/last word
    duration = word_tokens[-1].get("end", 0) - word_tokens[0].get("start", 0)

    # Group into phrases: break on speaker change or silence ≥ threshold
    phrases = []
    current_phrase = []
    current_speaker = word_tokens[0].get("speaker_id", "S0")

    for i, word in enumerate(word_tokens):
        speaker = word.get("speaker_id", "S0")
        prev_end = word_tokens[i - 1].get("end", 0) if i > 0 else 0
        gap = word.get("start", 0) - prev_end if i > 0 else 0

        speaker_changed = speaker != current_speaker
        long_silence = gap >= SILENCE_THRESHOLD

        if current_phrase and (speaker_changed or long_silence):
            phrases.append({
                "speaker": current_speaker,
                "words": current_phrase[:],
                "start": current_phrase[0]["start"],
                "end": current_phrase[-1]["end"],
            })
            current_phrase = []
            current_speaker = speaker

        current_phrase.append(word)

    if current_phrase:
        phrases.append({
            "speaker": current_speaker,
            "words": current_phrase,
            "start": current_phrase[0]["start"],
            "end": current_phrase[-1]["end"],
        })

    # Build markdown
    lines = [f"## {name}  (duration: {duration:.1f}s, {len(phrases)} phrases)"]
    for phrase in phrases:
        text = " ".join(w["text"] for w in phrase["words"])
        ts = f"[{format_ts(phrase['start'])}-{format_ts(phrase['end'])}]"
        speaker = phrase["speaker"] or "S0"
        lines.append(f"  {ts} {speaker} {text}")

    lines.append("")
    return "\n".join(lines) + "\n"


def pack_directory(transcript_dir: Path) -> str:
    """Pack all JSON files in directory, sorted by filename."""
    json_files = sorted(transcript_dir.glob("*.json"))
    if not json_files:
        print(f"[pack] no JSON files in {transcript_dir}", file=sys.stderr)
        return ""

    parts = []
    for path in json_files:
        # Skip cache metadata files
        if path.stem.endswith(".cache"):
            continue
        try:
            data = json.loads(path.read_text())
            packed = pack_transcript(path.stem, data)
            parts.append(packed)
            print(f"[pack] packed: {path.name}", file=sys.stderr)
        except Exception as e:
            print(f"[pack] ERROR {path.name}: {e}", file=sys.stderr)

    return "\n".join(parts)


def pack_single(json_path: Path) -> str:
    data = json.loads(json_path.read_text())
    return pack_transcript(json_path.stem, data)


def main():
    global SILENCE_THRESHOLD
    parser = argparse.ArgumentParser(description="Pack Scribe transcripts to phrase markdown")
    parser.add_argument("input", help="JSON file or directory of JSON files")
    parser.add_argument("--output", required=True, help="Output .md file path")
    parser.add_argument(
        "--silence-threshold", type=float, default=SILENCE_THRESHOLD,
        help=f"Silence gap to break phrase (default: {SILENCE_THRESHOLD}s)"
    )
    args = parser.parse_args()
    SILENCE_THRESHOLD = args.silence_threshold

    input_path = Path(args.input)

    if input_path.is_dir():
        packed = pack_directory(input_path)
    elif input_path.is_file() and input_path.suffix == ".json":
        packed = pack_single(input_path)
    else:
        print(f"ERROR: {input_path} must be a .json file or directory", file=sys.stderr)
        sys.exit(1)

    if not packed.strip():
        print("[pack] WARNING: empty output", file=sys.stderr)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(packed)
    size_kb = len(packed.encode()) / 1024
    print(f"[pack] written {out_path} ({size_kb:.1f} KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
