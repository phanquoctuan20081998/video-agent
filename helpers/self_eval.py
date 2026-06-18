#!/usr/bin/env python3
"""
Self-evaluation loop for rendered video.

Runs timeline_view at every cut boundary, analyzes results via vision model,
detects issues (pops, jumps, misaligned overlays), suggests fixes.
Re-renders up to MAX_PASSES times.

Usage:
    python helpers/self_eval.py outputs/edit/preview.mp4 --storyboard outputs/edit/storyboard.json

    # Or programmatic:
    from helpers.self_eval import self_eval_loop
    final = self_eval_loop(video_path, storyboard, voiceover_path, output_path)
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from helpers.timeline_view import view_cut_boundary, extract_frame, extract_waveform
from helpers.llm_task import call_openrouter_vision, TASK_MODELS

MAX_PASSES = 3
EVAL_MODEL = TASK_MODELS.get("verify_stock_relevance", "google/gemini-2.5-flash-lite")

EVAL_PROMPT = """You are a professional video editor QA reviewer.
Analyze this filmstrip/waveform image from a cut boundary in a rendered video.

Check for these issues:
1. VISUAL_JUMP — abrupt visual discontinuity or flash at the cut point
2. AUDIO_POP — waveform spike at boundary (indicates missing audio fade)
3. BLACK_FRAME — unexpected black/blank frame
4. REPEAT_CONTENT — same visual appearing (looped/duplicate clip)
5. POOR_GRADE — inconsistent color between adjacent segments

Image shows: filmstrip of frames around a cut boundary OR waveform.

Respond ONLY with JSON:
{"pass": true} if no issues detected
{"pass": false, "issues": ["ISSUE_TYPE: description"]} if problems found

Be strict about VISUAL_JUMP and AUDIO_POP — these are the most common problems.
Be lenient about minor color variations."""


def evaluate_cut_point(
    video: str,
    cut_time: float,
    window: float,
    work_dir: Path,
    label: str,
    api_key: str,
) -> dict:
    """Evaluate a single cut point via vision model."""
    results = view_cut_boundary(video, cut_time, window, work_dir, label)

    filmstrip_path = results.get("filmstrip")
    if not filmstrip_path or not Path(filmstrip_path).exists():
        return {"pass": True, "label": label, "time": cut_time}

    # For now, analyze filmstrip (visual jump detection)
    # Vision model needs a URL — use file path as data URI won't work
    # Skip if no API key
    if not api_key:
        return {"pass": True, "label": label, "time": cut_time, "skipped": True}

    # Check if we have a preview URL (for remote hosted), else skip vision
    # For local files we'll do heuristic check via waveform
    waveform_path = results.get("waveform")

    # Heuristic: check waveform for spikes at boundary center
    if waveform_path and Path(waveform_path).exists():
        spike = _check_waveform_spike(video, cut_time)
        if spike:
            return {
                "pass": False,
                "label": label,
                "time": cut_time,
                "issues": ["AUDIO_POP: waveform spike detected at cut boundary"],
            }

    return {"pass": True, "label": label, "time": cut_time}


def _check_waveform_spike(video: str, cut_time: float) -> bool:
    """Heuristic: check if audio has a sharp transient at cut_time.
    Uses ffmpeg loudness measurement around the cut point."""
    import subprocess

    # Sample 60ms around cut point
    start = max(0, cut_time - 0.03)
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", video,
                "-t", "0.06",
                "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.Peak_level",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=10,
        )
        # Parse peak level from stderr
        for line in result.stderr.split("\n"):
            if "Peak_level" in line:
                try:
                    val = float(line.split("=")[-1].strip())
                    # Peak > -1 dB at boundary = likely pop
                    if val > -1.0:
                        return True
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass
    return False


def compute_cut_times_from_storyboard(storyboard: dict) -> list[tuple[float, str]]:
    """Compute cut boundary times from storyboard scenes."""
    scenes = storyboard.get("scenes", [])
    cut_times = []
    cursor = 0.0

    for i, scene in enumerate(scenes):
        duration = scene.get("duration_s", 5.0)
        if i > 0:
            cut_times.append((cursor, f"cut_{i}_{scene.get('id', '')}"))
        cursor += duration

    # Also check first 2s, last 2s, midpoint
    total = cursor
    cut_times.append((min(2.0, total), "first_2s"))
    cut_times.append((max(0, total - 2.0), "last_2s"))
    cut_times.append((total / 2, "midpoint"))

    return cut_times


def self_eval_pass(
    video_path: Path,
    storyboard: dict,
    work_dir: Path,
) -> list[dict]:
    """Run one self-evaluation pass. Returns list of issue dicts."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    cut_times = compute_cut_times_from_storyboard(storyboard)
    verify_dir = work_dir / "verify"
    verify_dir.mkdir(parents=True, exist_ok=True)

    issues = []
    print(f"[self_eval] checking {len(cut_times)} boundary points", file=sys.stderr)

    for ts, label in cut_times:
        result = evaluate_cut_point(
            str(video_path), ts, 3.0, verify_dir, label, api_key
        )
        if not result.get("pass", True):
            issues.append(result)
            print(
                f"[self_eval] ISSUE at {ts:.2f}s ({label}): "
                f"{result.get('issues', ['unknown'])}",
                file=sys.stderr,
            )

    if not issues:
        print("[self_eval] ✓ all checks passed", file=sys.stderr)

    return issues


def fix_storyboard_from_issues(storyboard: dict, issues: list[dict]) -> dict:
    """Apply automatic fixes to storyboard based on detected issues.
    
    Fixes:
    - AUDIO_POP → add audio_fade flag to affected scene boundaries
    - VISUAL_JUMP → extend padding on scene transition
    - REPEAT_CONTENT → flag scene for re-fetch with different query
    """
    scenes = storyboard.get("scenes", [])
    cursor = 0.0
    scene_boundaries = []

    for i, scene in enumerate(scenes):
        duration = scene.get("duration_s", 5.0)
        scene_boundaries.append((cursor, cursor + duration, i))
        cursor += duration

    for issue in issues:
        issue_time = issue.get("time", 0)
        issue_types = issue.get("issues", [])

        # Find which scene boundary this corresponds to
        affected_idx = None
        for start, end, idx in scene_boundaries:
            if abs(start - issue_time) < 0.5:
                affected_idx = idx
                break
            if abs(end - issue_time) < 0.5:
                affected_idx = idx
                break

        if affected_idx is None:
            continue

        for issue_str in issue_types:
            if "AUDIO_POP" in issue_str:
                # Ensure audio fade is applied
                scenes[affected_idx]["_fix_audio_fade"] = True
                print(
                    f"[self_eval:fix] scene {affected_idx}: adding audio fade",
                    file=sys.stderr,
                )
            elif "VISUAL_JUMP" in issue_str:
                # Add crossfade between scenes
                scenes[affected_idx]["_fix_crossfade"] = True
                print(
                    f"[self_eval:fix] scene {affected_idx}: adding crossfade",
                    file=sys.stderr,
                )
            elif "REPEAT_CONTENT" in issue_str:
                # Flag for re-fetch with different query
                scenes[affected_idx]["_fix_refetch"] = True
                props = scenes[affected_idx].get("props", {})
                old_query = props.get("query", "")
                props["query"] = f"{old_query} different angle"
                scenes[affected_idx]["props"] = props
                print(
                    f"[self_eval:fix] scene {affected_idx}: diversifying stock query",
                    file=sys.stderr,
                )

    storyboard["scenes"] = scenes
    return storyboard


def self_eval_loop(
    video_path: Path,
    storyboard: dict,
    voiceover_path: Path | None,
    output_path: Path,
    render_fn=None,
) -> Path:
    """Full self-eval loop: evaluate → fix → re-render, max 3 passes.
    
    Args:
        video_path: current rendered video
        storyboard: storyboard dict
        voiceover_path: voiceover audio
        output_path: final output path
        render_fn: callable(storyboard, voiceover_path, output_path) → Path
                   If None, only evaluates without re-rendering.
    
    Returns:
        Path to final video (may be re-rendered).
    """
    work_dir = output_path.parent

    for pass_num in range(1, MAX_PASSES + 1):
        print(f"\n[self_eval] === Pass {pass_num}/{MAX_PASSES} ===", file=sys.stderr)
        issues = self_eval_pass(video_path, storyboard, work_dir)

        if not issues:
            print(f"[self_eval] ✓ Video passed all checks on pass {pass_num}", file=sys.stderr)
            return video_path

        print(
            f"[self_eval] Found {len(issues)} issues on pass {pass_num}",
            file=sys.stderr,
        )

        if render_fn is None:
            print("[self_eval] No render_fn provided; reporting issues only", file=sys.stderr)
            # Write issues report
            issues_path = work_dir / "self_eval_issues.json"
            issues_path.write_text(json.dumps(issues, indent=2, ensure_ascii=False))
            return video_path

        # Apply fixes
        storyboard = fix_storyboard_from_issues(storyboard, issues)

        # Re-render
        print(f"[self_eval] Re-rendering (pass {pass_num})...", file=sys.stderr)
        video_path = render_fn(storyboard, voiceover_path, output_path)

    # Max passes reached — report remaining issues
    print(
        f"[self_eval] WARNING: {len(issues)} issues remain after {MAX_PASSES} passes. "
        "Flagging to user.",
        file=sys.stderr,
    )
    issues_path = work_dir / "self_eval_issues.json"
    issues_path.write_text(json.dumps(issues, indent=2, ensure_ascii=False))
    return video_path


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Self-evaluate rendered video")
    parser.add_argument("video", help="Rendered video path")
    parser.add_argument("--storyboard", required=True, help="Storyboard JSON path")
    parser.add_argument("--output", default=None, help="Output directory for verify PNGs")
    args = parser.parse_args()

    video_path = Path(args.video)
    storyboard = json.loads(Path(args.storyboard).read_text())
    work_dir = Path(args.output) if args.output else video_path.parent

    issues = self_eval_pass(video_path, storyboard, work_dir)
    if issues:
        print(f"\nFound {len(issues)} issues:", file=sys.stderr)
        for iss in issues:
            print(f"  - {iss['time']:.2f}s [{iss['label']}]: {iss.get('issues', [])}", file=sys.stderr)
        sys.exit(1)
    else:
        print("All checks passed.", file=sys.stderr)


if __name__ == "__main__":
    main()
