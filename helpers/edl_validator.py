#!/usr/bin/env python3
"""
Post-LLM EDL validator: snaps cut times to transcript word boundaries (Rule 6).

After the LLM generates an EDL, this validator checks every start/end time against
the word-level transcript and snaps any timestamp that falls mid-word to the nearest
word boundary (gap between words).

Usage:
    python helpers/edl_validator.py edl.json --transcript transcripts/voiceover.json --output edl_validated.json

    # Programmatic:
    from helpers.edl_validator import validate_edl
    edl = validate_edl(edl_dict, transcript_path)
"""

import argparse
import json
import sys
from pathlib import Path


def _load_words(transcript_path: Path) -> list[dict]:
    """Load word-level timestamps from transcript JSON."""
    data = json.loads(transcript_path.read_text())
    words = []
    for w in data.get("words", []):
        text = w.get("text", "").strip()
        if text and w.get("start") is not None and w.get("end") is not None:
            words.append({
                "text": text,
                "start": float(w["start"]),
                "end": float(w["end"]),
            })
    return words


def _find_word_boundaries(words: list[dict]) -> list[tuple[float, float]]:
    """Return list of (gap_start, gap_end) between consecutive words.

    These are safe cut zones — timestamps within a gap don't split a word.
    """
    boundaries = []
    for i in range(len(words) - 1):
        gap_start = words[i]["end"]
        gap_end = words[i + 1]["start"]
        if gap_end > gap_start:
            boundaries.append((gap_start, gap_end))
    return boundaries


def _is_inside_word(t: float, words: list[dict]) -> dict | None:
    """Return the word dict if timestamp t falls inside a word, else None."""
    for w in words:
        if w["start"] < t < w["end"]:
            return w
    return None


def _snap_to_boundary(t: float, words: list[dict], boundaries: list[tuple[float, float]], max_snap: float = 0.2) -> float:
    """Snap timestamp to nearest word boundary (inter-word gap midpoint).

    Returns original timestamp if it's already in a gap or no boundary is close enough.
    max_snap: maximum distance (seconds) we're willing to move the timestamp.
    """
    # Check if already safe (inside a gap)
    for gap_start, gap_end in boundaries:
        if gap_start <= t <= gap_end:
            return t  # already between words

    # Not inside a word either (before first word or after last)
    if not _is_inside_word(t, words):
        return t

    # Find nearest boundary midpoint
    best_t = t
    best_dist = max_snap
    for gap_start, gap_end in boundaries:
        midpoint = (gap_start + gap_end) / 2
        dist = abs(t - midpoint)
        if dist < best_dist:
            best_dist = dist
            best_t = midpoint

    return best_t


def validate_edl(edl: dict, transcript_path: Path, max_snap: float = 0.2) -> dict:
    """Validate and fix EDL cut times against transcript word boundaries.

    Args:
        edl: EDL dictionary with "ranges" list.
        transcript_path: Path to word-level transcript JSON.
        max_snap: Maximum seconds to shift a cut point (Rule 7: 30-200ms).

    Returns:
        Modified EDL with snapped timestamps. Adds "_snapped" flag to modified ranges.
    """
    if not transcript_path.exists():
        print(f"[edl_validator] transcript not found: {transcript_path}, skipping validation", file=sys.stderr)
        return edl

    words = _load_words(transcript_path)
    if not words:
        print("[edl_validator] no words in transcript, skipping validation", file=sys.stderr)
        return edl

    boundaries = _find_word_boundaries(words)
    if not boundaries:
        print("[edl_validator] no word boundaries found, skipping validation", file=sys.stderr)
        return edl

    ranges = edl.get("ranges", [])
    fixes = 0

    for r in ranges:
        start = r.get("start", 0.0)
        end = r.get("end", 0.0)

        new_start = _snap_to_boundary(start, words, boundaries, max_snap)
        new_end = _snap_to_boundary(end, words, boundaries, max_snap)

        if new_start != start or new_end != end:
            r["start"] = round(new_start, 3)
            r["end"] = round(new_end, 3)
            r["_snapped"] = True
            fixes += 1
            print(
                f"[edl_validator] snapped range '{r.get('beat', '?')}': "
                f"[{start:.3f}-{end:.3f}] → [{new_start:.3f}-{new_end:.3f}]",
                file=sys.stderr,
            )

    if fixes:
        print(f"[edl_validator] fixed {fixes}/{len(ranges)} ranges (snapped to word boundaries)", file=sys.stderr)
    else:
        print(f"[edl_validator] all {len(ranges)} ranges already on word boundaries", file=sys.stderr)

    return edl


def main():
    parser = argparse.ArgumentParser(description="Validate EDL cuts against transcript word boundaries")
    parser.add_argument("edl", help="Path to edl.json")
    parser.add_argument("--transcript", required=True, help="Path to word-level transcript JSON")
    parser.add_argument("--output", help="Output path (default: overwrite input)")
    parser.add_argument("--max-snap", type=float, default=0.2, help="Max snap distance in seconds (default: 0.2)")
    args = parser.parse_args()

    edl_path = Path(args.edl)
    edl = json.loads(edl_path.read_text())

    edl = validate_edl(edl, Path(args.transcript), args.max_snap)

    out_path = Path(args.output) if args.output else edl_path
    out_path.write_text(json.dumps(edl, indent=2, ensure_ascii=False))
    print(f"[edl_validator] wrote: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
