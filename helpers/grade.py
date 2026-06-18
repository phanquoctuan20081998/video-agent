#!/usr/bin/env python3
"""
Per-segment color grading via FFmpeg (ASC CDL model).
Apply per-segment during extraction, never post-concat.

Mental model: out = (in * slope + offset) ** power, then global saturation.

Usage:
    python helpers/grade.py clip.mp4 warm_cinematic --output graded.mp4
    python helpers/grade.py clip.mp4 "curves=r='0/0 0.5/0.6 1/1':b='0/0 0.5/0.45 1/0.9'" --output graded.mp4
    python helpers/grade.py --list-presets
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Preset ffmpeg filter chains
GRADE_PRESETS: dict[str, str] = {
    "warm_cinematic": (
        "curves=r='0/0 0.5/0.55 1/0.95':"  # slight red lift
        "b='0/0 0.5/0.45 1/0.85',"          # teal shadows
        "colorchannelmixer=rr=1.02:gg=0.98:bb=0.96,"  # warm cast
        "eq=saturation=0.85:contrast=1.05"   # desaturate slightly
    ),
    "neutral_punch": (
        "curves='0/0 0.25/0.22 0.75/0.78 1/1',"  # S-curve contrast
        "eq=saturation=1.05:contrast=1.08"
    ),
    "cool_dramatic": (
        "curves=r='0/0 0.5/0.45 1/0.9':"
        "b='0/0 0.5/0.55 1/1.05',"
        "eq=saturation=0.9:contrast=1.1"
    ),
    "vibrant": (
        "eq=saturation=1.3:contrast=1.05:brightness=0.02"
    ),
    "none": "",  # passthrough
}


def grade_segment(
    input_path: Path,
    preset_or_filter: str,
    output_path: Path,
    fps: int = 30,
    resolution: str = "1920x1080",
) -> Path:
    """Apply color grade to a video segment, normalizing fps and resolution."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve preset name to filter string
    if preset_or_filter in GRADE_PRESETS:
        grade_filter = GRADE_PRESETS[preset_or_filter]
    else:
        grade_filter = preset_or_filter  # treat as raw ffmpeg filter

    # Always normalize: consistent fps + resolution prevents black frames at concat
    w, h = resolution.split("x")
    norm = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps},"
        f"setsar=1"
    )
    filter_str = f"{norm},{grade_filter}" if grade_filter else norm

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", filter_str,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(output_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[grade] ERROR: {result.stderr[-300:]}", file=sys.stderr)
        raise RuntimeError(f"grade failed: {input_path.name}")

    print(f"[grade] {input_path.name} → {output_path.name} ({preset_or_filter})", file=sys.stderr)
    return output_path


def grade_directory(
    input_dir: Path,
    preset_or_filter: str,
    output_dir: Path,
) -> list[Path]:
    """Grade all .mp4 files in directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    clips = sorted(input_dir.glob("*.mp4"))
    results = []
    for clip in clips:
        out = output_dir / clip.name
        grade_segment(clip, preset_or_filter, out)
        results.append(out)
    return results


def main():
    parser = argparse.ArgumentParser(description="Per-segment color grading via FFmpeg")
    parser.add_argument("input", nargs="?", help="Input video file or directory")
    parser.add_argument("grade", nargs="?", help="Preset name or raw ffmpeg filter string")
    parser.add_argument("--output", help="Output file or directory")
    parser.add_argument("--list-presets", action="store_true", help="List available presets")
    args = parser.parse_args()

    if args.list_presets:
        print("Available presets:")
        for name, filt in GRADE_PRESETS.items():
            preview = filt[:60] + "..." if len(filt) > 60 else filt or "(passthrough)"
            print(f"  {name:20s}  {preview}")
        return

    if not args.input or not args.grade:
        parser.error("input and grade required (or --list-presets)")

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else None

    if input_path.is_dir():
        if not output_path:
            output_path = input_path.parent / f"{input_path.name}_graded"
        grade_directory(input_path, args.grade, output_path)
    else:
        if not output_path:
            output_path = input_path.parent / f"{input_path.stem}_graded{input_path.suffix}"
        grade_segment(input_path, args.grade, output_path)
        print(str(output_path))


if __name__ == "__main__":
    main()
