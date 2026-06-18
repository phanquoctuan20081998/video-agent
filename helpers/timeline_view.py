#!/usr/bin/env python3
"""
Generate filmstrip + waveform + label PNGs for self-evaluation.
On-demand visual verification at cut boundaries.

Usage:
    # Check all cut boundaries from EDL
    python helpers/timeline_view.py outputs/edit/preview.mp4 --cuts outputs/edit/edl.json

    # Sample first frame of each source file in directory
    python helpers/timeline_view.py videos/ --sample --output outputs/edit/verify/

    # Single window around a timestamp
    python helpers/timeline_view.py video.mp4 --at 12.5 --window 1.5 --output outputs/edit/verify/
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def extract_frame(video: str, timestamp: float, out_path: Path) -> Path:
    """Extract single frame as PNG."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = run([
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", video,
        "-frames:v", "1",
        "-q:v", "2",
        str(out_path),
    ])
    if result.returncode != 0:
        print(f"[timeline] frame extract failed at {timestamp:.2f}s", file=sys.stderr)
    return out_path


def extract_filmstrip(
    video: str,
    start: float,
    end: float,
    out_path: Path,
    n_frames: int = 8,
) -> Path:
    """Extract n evenly-spaced frames and tile horizontally as filmstrip PNG."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = end - start
    step = duration / max(n_frames - 1, 1)

    frame_paths = []
    tmp_dir = out_path.parent / "_tmp_frames"
    tmp_dir.mkdir(exist_ok=True)

    for i in range(n_frames):
        ts = start + i * step
        fp = tmp_dir / f"frame_{i:03d}.png"
        extract_frame(video, ts, fp)
        if fp.exists():
            frame_paths.append(fp)

    if not frame_paths:
        print(f"[timeline] no frames extracted for {start:.2f}-{end:.2f}", file=sys.stderr)
        return out_path

    # Tile frames horizontally using ffmpeg
    inputs = []
    for fp in frame_paths:
        inputs += ["-i", str(fp)]

    filter_str = "".join(f"[{i}:v]" for i in range(len(frame_paths)))
    filter_str += f"hstack=inputs={len(frame_paths)}[out]"

    run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_str,
        "-map", "[out]",
        str(out_path),
    ])

    # Cleanup temp frames
    for fp in frame_paths:
        fp.unlink(missing_ok=True)
    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    return out_path


def extract_waveform(
    video: str,
    start: float,
    end: float,
    out_path: Path,
    width: int = 1200,
    height: int = 120,
) -> Path:
    """Extract waveform image for audio analysis."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = end - start

    run([
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video,
        "-t", str(duration),
        "-filter_complex",
        f"showwavespic=s={width}x{height}:colors=0x00ff88",
        "-frames:v", "1",
        str(out_path),
    ])
    return out_path


def view_cut_boundary(
    video: str,
    cut_time: float,
    window: float,
    out_dir: Path,
    label: str = "",
) -> dict[str, Path]:
    """Generate filmstrip + waveform centered on a cut point."""
    safe_label = label.replace(" ", "_").replace("/", "-")[:30]
    ts_str = f"{cut_time:.2f}".replace(".", "_")
    prefix = f"cut_{ts_str}_{safe_label}" if safe_label else f"cut_{ts_str}"

    half = window / 2
    start = max(0.0, cut_time - half)
    end = cut_time + half

    filmstrip = extract_filmstrip(video, start, end, out_dir / f"{prefix}_film.png")
    waveform = extract_waveform(video, start, end, out_dir / f"{prefix}_wave.png")

    return {"filmstrip": filmstrip, "waveform": waveform}


def view_from_edl(video: str, edl_path: str, out_dir: Path, window: float = 3.0):
    """Check all cut boundaries from EDL file."""
    edl = json.loads(Path(edl_path).read_text())
    ranges = edl.get("ranges", [])

    # Build list of cut times in output timeline
    cut_times = []
    cursor = 0.0
    for r in ranges:
        duration = r["end"] - r["start"]
        # Start of segment in output
        cut_times.append((cursor, f"{r.get('beat', '')}_{r.get('source', '')}"))
        # End of segment in output
        cursor += duration
        cut_times.append((cursor, f"end_{r.get('beat', '')}"))

    # Also check first 2s, last 2s, 2-3 mid-points
    total = cursor
    checkpoints = [
        (2.0, "first_2s"),
        (max(0, total - 2.0), "last_2s"),
        (total / 2, "midpoint"),
    ]
    all_checks = cut_times + checkpoints

    print(f"[timeline] checking {len(all_checks)} points in {video}", file=sys.stderr)
    for ts, label in all_checks:
        if ts < 0 or ts > total:
            continue
        view_cut_boundary(video, ts, window, out_dir, label)
        print(f"[timeline] ✓ {label} @ {ts:.2f}s", file=sys.stderr)

    print(f"[timeline] PNGs written to {out_dir}", file=sys.stderr)


def sample_sources(directory: str, out_dir: Path):
    """Sample first frame from each video file in directory."""
    video_exts = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    videos = [
        f for f in sorted(Path(directory).iterdir())
        if f.suffix.lower() in video_exts
    ]
    for v in videos:
        out_path = out_dir / f"sample_{v.stem}.png"
        extract_frame(str(v), 1.0, out_path)
        print(f"[timeline] sample: {v.name} → {out_path.name}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Timeline view for self-evaluation")
    parser.add_argument("input", help="Video file or directory (with --sample)")
    parser.add_argument("--cuts", help="EDL JSON — check all cut boundaries")
    parser.add_argument("--at", type=float, help="Single timestamp to inspect")
    parser.add_argument("--window", type=float, default=3.0,
                        help="Window around cut point in seconds (default: 3.0)")
    parser.add_argument("--sample", action="store_true",
                        help="Sample first frame from each video in directory")
    parser.add_argument("--output", default="outputs/edit/verify",
                        help="Output directory for PNGs")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.sample:
        sample_sources(args.input, out_dir)
    elif args.cuts:
        view_from_edl(args.input, args.cuts, out_dir, args.window)
    elif args.at is not None:
        view_cut_boundary(args.input, args.at, args.window, out_dir)
        print(f"[timeline] PNGs written to {out_dir}", file=sys.stderr)
    else:
        # Default: sample video at start/mid/end
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-print_format", "json", args.input],
            capture_output=True, text=True
        )
        info = json.loads(probe.stdout)
        duration = float(info.get("format", {}).get("duration", 0))

        for ts, label in [(2.0, "start"), (duration / 2, "mid"), (duration - 2, "end")]:
            extract_frame(args.input, ts, out_dir / f"{label}.png")
        print(f"[timeline] sampled {args.input} → {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
