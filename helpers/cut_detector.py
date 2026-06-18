#!/usr/bin/env python3
"""
Audio-first cut intelligence.

Analyzes voiceover/audio transcript to find optimal cut points:
- Silence gaps ≥400ms = primary cut candidates
- Preserve laughs, peaks, emphasis beats
- Never cut inside a word (snap to word boundaries)

Usage:
    python helpers/cut_detector.py outputs/edit/transcripts/voiceover.json --output cuts.json

    # Programmatic:
    from helpers.cut_detector import detect_cuts, CutCandidate
    candidates = detect_cuts(transcript_path)
"""

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# Thresholds
SILENCE_CUT_MIN = 0.4       # seconds — silence ≥ this is a cut candidate
SILENCE_CUT_IDEAL = 0.6     # silence ≥ this is ideal cut point
PHRASE_BOUNDARY_MIN = 0.15   # silence ≥ this is usable but needs visual check
UNSAFE_GAP = 0.15           # < this = mid-phrase, unsafe to cut

# Audio events that should be preserved (never cut through)
PRESERVE_EVENTS = {"(laughter)", "(laughs)", "(applause)", "(music)", "(cheering)"}

# Events that signal emphasis / peak moments — extend past them
PEAK_EVENTS = {"(laughter)", "(laughs)", "(applause)", "(cheering)", "(wow)"}


@dataclass
class CutCandidate:
    """A potential cut point in the audio timeline."""
    time: float          # timestamp in seconds
    type: str            # 'silence', 'speaker_change', 'phrase_end'
    confidence: float    # 0.0-1.0 — how safe this cut is
    gap_duration: float  # duration of the silence gap
    context_before: str  # last few words before cut
    context_after: str   # first few words after cut
    preserve_until: float | None = None  # if near a peak, don't cut before this


def detect_cuts(transcript_path: Path) -> list[CutCandidate]:
    """Analyze transcript and return ranked cut candidates.
    
    Algorithm:
    1. Find all inter-word gaps
    2. Classify by duration: ideal / usable / unsafe
    3. Check for audio events that must be preserved
    4. Rank by confidence
    """
    data = json.loads(transcript_path.read_text())
    words = data.get("words", [])

    if not words:
        return []

    # Filter to actual word tokens + audio events
    tokens = []
    for w in words:
        token_type = w.get("type", "word")
        text = w.get("text", "").strip()
        if text:
            tokens.append({
                "text": text,
                "start": w.get("start", 0.0),
                "end": w.get("end", 0.0),
                "type": token_type,
                "speaker": w.get("speaker_id", "S0"),
            })

    if len(tokens) < 2:
        return []

    candidates = []
    preserve_zones = []

    # First pass: identify audio events that create preserve zones
    for i, tok in enumerate(tokens):
        if tok["text"].lower() in PEAK_EVENTS:
            # Preserve zone: don't cut within 0.5s after this event
            preserve_end = tok["end"] + 0.5
            preserve_zones.append((tok["start"], preserve_end))

    # Second pass: find gaps between tokens
    for i in range(1, len(tokens)):
        prev = tokens[i - 1]
        curr = tokens[i]

        gap_start = prev["end"]
        gap_end = curr["start"]
        gap_duration = gap_end - gap_start

        if gap_duration < UNSAFE_GAP:
            continue  # mid-phrase, skip

        # Determine cut confidence
        cut_time = gap_start + gap_duration / 2  # center of gap

        # Check if in preserve zone
        in_preserve = False
        preserve_until = None
        for pstart, pend in preserve_zones:
            if pstart <= cut_time <= pend:
                in_preserve = True
                preserve_until = pend
                break

        # Speaker change = strong cut signal
        speaker_change = prev["speaker"] != curr["speaker"]

        # Classify
        if gap_duration >= SILENCE_CUT_IDEAL:
            confidence = 0.95
            cut_type = "silence_ideal"
        elif gap_duration >= SILENCE_CUT_MIN:
            confidence = 0.8
            cut_type = "silence"
        elif gap_duration >= PHRASE_BOUNDARY_MIN:
            confidence = 0.5
            cut_type = "phrase_boundary"
        else:
            confidence = 0.3
            cut_type = "tight_gap"

        # Boost for speaker change
        if speaker_change:
            confidence = min(1.0, confidence + 0.15)
            cut_type = "speaker_change"

        # Penalize if in preserve zone
        if in_preserve:
            confidence *= 0.3

        # Context
        context_before = " ".join(
            t["text"] for t in tokens[max(0, i - 4):i]
            if t["type"] == "word"
        )
        context_after = " ".join(
            t["text"] for t in tokens[i:min(len(tokens), i + 4)]
            if t["type"] == "word"
        )

        candidates.append(CutCandidate(
            time=round(cut_time, 3),
            type=cut_type,
            confidence=round(confidence, 2),
            gap_duration=round(gap_duration, 3),
            context_before=context_before,
            context_after=context_after,
            preserve_until=preserve_until,
        ))

    # Sort by confidence (highest first), then by time
    candidates.sort(key=lambda c: (-c.confidence, c.time))

    return candidates


def suggest_scene_splits(
    transcript_path: Path,
    target_scene_duration: float = 5.0,
    min_scene_duration: float = 3.0,
    max_scene_duration: float = 8.0,
) -> list[dict]:
    """Suggest where to split narration into scenes based on audio analysis.
    
    Returns list of scene boundaries with timestamps, respecting:
    - Natural silence gaps as split points
    - Min/max scene duration constraints
    - Never splitting inside words
    """
    candidates = detect_cuts(transcript_path)
    data = json.loads(transcript_path.read_text())
    words = [w for w in data.get("words", []) if w.get("type") == "word"]

    if not words:
        return []

    total_duration = words[-1]["end"] - words[0]["start"]
    start_time = words[0]["start"]

    # Filter to usable candidates (confidence ≥ 0.5)
    usable = [c for c in candidates if c.confidence >= 0.5]
    # Sort by time for sequential processing
    usable.sort(key=lambda c: c.time)

    # Greedy: pick cuts that respect min/max duration
    splits = []
    last_split = start_time

    for c in usable:
        elapsed = c.time - last_split

        if elapsed < min_scene_duration:
            continue  # too soon

        if elapsed >= max_scene_duration:
            # Overdue — take this cut even if not ideal
            splits.append({
                "time": c.time,
                "type": c.type,
                "confidence": c.confidence,
                "gap_ms": round(c.gap_duration * 1000),
            })
            last_split = c.time
        elif elapsed >= target_scene_duration:
            # Good range — take if it's a strong candidate
            if c.confidence >= 0.7:
                splits.append({
                    "time": c.time,
                    "type": c.type,
                    "confidence": c.confidence,
                    "gap_ms": round(c.gap_duration * 1000),
                })
                last_split = c.time

    return splits


def get_narration_segments(
    transcript_path: Path,
    splits: list[dict],
) -> list[dict]:
    """Given split points, return narration text segments with timing."""
    data = json.loads(transcript_path.read_text())
    words = [w for w in data.get("words", []) if w.get("type") == "word" and w.get("text", "").strip()]

    if not words:
        return []

    segments = []
    split_times = [s["time"] for s in splits]
    split_times = [words[0]["start"]] + split_times + [words[-1]["end"]]

    for i in range(len(split_times) - 1):
        seg_start = split_times[i]
        seg_end = split_times[i + 1]

        # Collect words in this segment
        seg_words = [
            w for w in words
            if w["start"] >= seg_start - 0.05 and w["end"] <= seg_end + 0.05
        ]

        if seg_words:
            segments.append({
                "start": seg_words[0]["start"],
                "end": seg_words[-1]["end"],
                "duration": seg_words[-1]["end"] - seg_words[0]["start"],
                "text": " ".join(w["text"] for w in seg_words),
                "word_count": len(seg_words),
            })

    return segments


def main():
    parser = argparse.ArgumentParser(description="Audio-first cut detection")
    parser.add_argument("transcript", help="Transcript JSON path")
    parser.add_argument("--output", help="Output JSON path for cut candidates")
    parser.add_argument("--splits", action="store_true",
                        help="Output scene split suggestions instead of raw candidates")
    parser.add_argument("--target-duration", type=float, default=5.0,
                        help="Target scene duration in seconds")
    args = parser.parse_args()

    transcript_path = Path(args.transcript)

    if args.splits:
        splits = suggest_scene_splits(
            transcript_path,
            target_scene_duration=args.target_duration,
        )
        segments = get_narration_segments(transcript_path, splits)
        result = {"splits": splits, "segments": segments}
    else:
        candidates = detect_cuts(transcript_path)
        result = [asdict(c) for c in candidates]

    output = json.dumps(result, indent=2, ensure_ascii=False)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output)
        print(f"[cut_detector] written to {args.output}", file=sys.stderr)
    else:
        print(output)

    # Summary
    if args.splits:
        print(f"[cut_detector] {len(splits)} splits → {len(segments)} segments", file=sys.stderr)
        for seg in segments:
            print(
                f"  [{seg['start']:.2f}-{seg['end']:.2f}] "
                f"({seg['duration']:.1f}s, {seg['word_count']}w) "
                f"{seg['text'][:60]}...",
                file=sys.stderr,
            )
    else:
        top = [c for c in candidates if c.confidence >= 0.7]
        print(
            f"[cut_detector] {len(candidates)} total candidates, "
            f"{len(top)} high-confidence (≥0.7)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
