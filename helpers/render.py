#!/usr/bin/env python3
"""
EDL JSON → final.mp4 via FFmpeg.

Pipeline per Hard Rules:
  1. Per-segment extract with lossless -c copy
  2. Grade each segment (grade.py)
  3. Concat graded segments
  4. Add voiceover/audio
  5. 30ms audio fades at every boundary
  6. Overlay animations (PTS-shifted)
  7. Burn subtitles LAST (Rule 1)

Usage:
    python helpers/render.py outputs/edit/edl.json --output outputs/edit/preview.mp4
    python helpers/render.py outputs/edit/edl.json --output outputs/edit/final.mp4 --res 1920x1080
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

GENERATED_MUSIC_VOLUME = float(os.getenv("GENERATED_MUSIC_VOLUME", "0.75"))
BACKGROUND_MUSIC_VOLUME = float(os.getenv("BACKGROUND_MUSIC_VOLUME", "0.22"))


def run(cmd: list[str], label: str = "") -> subprocess.CompletedProcess:
    print(f"[render] {label or ' '.join(cmd[:4])}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[render] ERROR: {result.stderr[-500:]}", file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed: {label}")
    return result


def get_duration(path: str) -> float:
    """Get video/audio duration via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def has_audio_stream(path: Path) -> bool:
    """Return True if a media file has at least one audio stream."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError:
        return False
    return any(s.get("codec_type") == "audio" for s in streams)


def find_music_asset() -> Path | None:
    music_dir = Path("assets/music")
    if not music_dir.exists():
        return None
    for pattern in ("bg_music.mp3", "*.mp3", "*.m4a", "*.wav", "*.aac"):
        matches = sorted(music_dir.glob(pattern))
        for match in matches:
            if match.exists() and match.stat().st_size > 10000:
                return match
    return None


def generate_music_bed(duration: float, output_path: Path) -> Path:
    """Generate a fallback music bed when no licensed music file exists."""
    duration = max(1.0, duration)
    run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i",
        (
            "aevalsrc="
            "'0.18*sin(2*PI*110*t)+0.10*sin(2*PI*165*t)+0.08*sin(2*PI*220*t)':"
            "s=44100"
        ),
        "-t", f"{duration:.3f}",
        "-af", (
            "afade=t=in:st=0:d=2,"
            f"afade=t=out:st={max(0, duration - 3):.3f}:d=3,"
            f"lowpass=f=1800,volume={GENERATED_MUSIC_VOLUME}"
        ),
        "-c:a", "aac",
        "-b:a", "128k",
        str(output_path),
    ], "generate fallback music bed")
    return output_path


def mix_background_music(video_path: Path, output_path: Path) -> Path:
    """Mix background music under an already-voiced edit."""
    duration = get_duration(str(video_path))
    if duration <= 0 or not has_audio_stream(video_path):
        return video_path

    music_path = find_music_asset()
    work_dir = output_path.parent
    if not music_path:
        music_path = generate_music_bed(duration, work_dir / "fallback_music.m4a")

    run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", "-1",
        "-i", str(music_path),
        "-filter_complex",
        (
            f"[1:a]atrim=0:{duration:.3f},"
            "afade=t=in:st=0:d=2,"
            f"afade=t=out:st={max(0, duration - 3):.3f}:d=3,"
            f"volume={BACKGROUND_MUSIC_VOLUME}[music];"
            "[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        ),
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path),
    ], "mix background music")
    return output_path


def match_video_duration(video_path: Path, target_duration: float, out_path: Path) -> Path:
    """Loop or trim the visual timeline so it matches the voiceover duration."""
    current_duration = get_duration(str(video_path))
    if target_duration <= 0 or current_duration <= 0:
        return video_path
    if abs(current_duration - target_duration) < 0.25:
        return video_path

    print(
        f"[render] match video duration {current_duration:.2f}s -> {target_duration:.2f}s",
        file=sys.stderr,
    )
    run([
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(video_path),
        "-t", f"{target_duration:.3f}",
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        str(out_path),
    ], "match video duration to voiceover")
    return out_path


def extract_segment(
    source_path: str,
    start: float,
    end: float,
    out_path: Path,
    pad_ms: int = 30,
) -> Path:
    """Lossless segment extract with padding (Rule 2, 7).
    Clamps timestamps to source duration to handle wrong EDL offsets."""
    src_dur = get_duration(source_path)
    if src_dur > 0:
        # Clamp: if start beyond source, use proportional position
        if start >= src_dur:
            ratio = start / max(end, start + 1)
            start = src_dur * ratio * 0.5
            end = min(src_dur, start + (end - start))
            print(f"[render] clamped timestamps to [{start:.2f}-{end:.2f}] (src={src_dur:.1f}s)", file=sys.stderr)
        end = min(end, src_dur)

    padded_start = max(0.0, start - pad_ms / 1000)
    duration = max(0.5, (end + pad_ms / 1000) - padded_start)

    run([
        "ffmpeg", "-y",
        "-ss", str(padded_start),
        "-i", source_path,
        "-t", str(duration),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        str(out_path),
    ], f"extract {Path(source_path).name} [{start:.2f}-{end:.2f}]")
    return out_path


def add_audio_fades(segment_path: Path, out_path: Path, fade_ms: int = 30) -> Path:
    """Add 30ms audio fades at start/end of segment (Rule 3)."""
    fade_s = fade_ms / 1000
    # Get duration
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
         str(segment_path)],
        capture_output=True, text=True
    )
    info = json.loads(probe.stdout)
    audio_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio_streams:
        return segment_path  # no audio track

    duration = float(info["streams"][0].get("duration", 0)) or float(
        next((s for s in info["streams"] if s.get("duration")), {}).get("duration", 10)
    )
    fade_out_start = max(0, duration - fade_s)

    run([
        "ffmpeg", "-y", "-i", str(segment_path),
        "-af", f"afade=t=in:st=0:d={fade_s},afade=t=out:st={fade_out_start:.3f}:d={fade_s}",
        "-c:v", "copy",
        str(out_path),
    ], f"audio fades {segment_path.name}")
    return out_path


def concat_segments(segment_paths: list[Path], out_path: Path) -> Path:
    """Concat using FFmpeg concat demuxer (lossless, Rule 2)."""
    list_file = out_path.parent / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in segment_paths)
    )
    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path),
    ], "concat segments")
    list_file.unlink(missing_ok=True)
    return out_path


def add_overlay(
    base_path: Path,
    overlay_file: str,
    start_in_output: float,
    duration: float,
    out_path: Path,
) -> Path:
    """Screen-blend overlay (Rule 4). Black pixels in overlay = transparent.
    Works for motion graphic effects (light sweeps, frames, glows).
    PTS-shifted so overlay starts at start_in_output seconds (Rule 4)."""
    overlay_path = Path(overlay_file)
    if not overlay_path.is_absolute():
        overlay_path = Path.cwd() / overlay_file

    end_t = start_in_output + duration
    run([
        "ffmpeg", "-y",
        "-i", str(base_path),
        "-i", str(overlay_path),
        "-filter_complex",
        (
            # Shift overlay PTS to start_in_output (Rule 4)
            f"[1:v]setpts=PTS-STARTPTS+{start_in_output}/TB,format=rgb24[ov];"
            # Screen blend: black=transparent, bright=additive.
            # NOTE: named "all_mode=screen:all_opacity=..." corrupts colors
            # (verified: produces a pink/magenta tint even at opacity=1.0 —
            # an ffmpeg blend-filter bug in this param path). The bare
            # "screen" shorthand is the verified-clean equivalent.
            f"[0:v][ov]blend=screen:"
            f"enable='between(t,{start_in_output},{end_t})'[vout]"
        ),
        "-map", "[vout]", "-map", "0:a?",
        "-c:a", "copy",
        str(out_path),
    ], f"overlay {Path(overlay_file).name} at {start_in_output:.1f}s")
    return out_path


def burn_subtitles(video_path: Path, srt_path: str, out_path: Path) -> Path:
    """Burn subtitles LAST in filter chain (Rule 1)."""
    srt_abs = str(Path(srt_path).resolve())
    # Escape colons in path for ffmpeg filter
    srt_escaped = srt_abs.replace(":", "\\:")

    run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", f"subtitles={srt_escaped}",
        "-c:a", "copy",
        str(out_path),
    ], "burn subtitles (last step, Rule 1)")
    return out_path


def resize_video(video_path: Path, resolution: str, out_path: Path) -> Path:
    w, h = resolution.split("x")
    run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        "-c:a", "copy",
        str(out_path),
    ], f"resize to {resolution}")
    return out_path


def download_source(url: str, dest: Path) -> Path:
    """Download remote URL to local file. Skip if already exists."""
    if dest.exists() and dest.stat().st_size > 10000:
        print(f"[render] download cached: {dest.name}", file=sys.stderr)
        return dest
    if not url.startswith("http"):
        return Path(url)  # already local path

    dest.parent.mkdir(parents=True, exist_ok=True)

    if "youtube.com/watch" in url or "youtu.be/" in url:
        # Watch-page URL has no direct media stream; needs yt-dlp extraction.
        print(f"[render] downloading via yt-dlp: {url[:80]}...", file=sys.stderr)
        import subprocess
        try:
            subprocess.run(
                ["yt-dlp", "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
                 "-o", str(dest), url],
                check=True, capture_output=True, text=True, timeout=180,
            )
            print(f"[render] downloaded: {dest.name} ({dest.stat().st_size // 1024}KB)", file=sys.stderr)
        except subprocess.CalledProcessError as e:
            print(f"[render] yt-dlp download failed: {e.stderr[-500:]}", file=sys.stderr)
        return dest

    print(f"[render] downloading: {url[:80]}...", file=sys.stderr)
    import urllib.request
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        print(f"[render] downloaded: {dest.name} ({dest.stat().st_size // 1024}KB)", file=sys.stderr)
    except Exception as e:
        print(f"[render] download failed: {e}", file=sys.stderr)
    return dest


def render_edl(edl: dict, output_path: Path, resolution: str = "1920x1080") -> Path:
    sources = edl.get("sources", {})
    ranges = edl.get("ranges", [])
    grade_preset = edl.get("grade", "none")
    overlays = edl.get("overlays", [])
    subtitles = edl.get("subtitles")

    if not ranges:
        raise ValueError("EDL has no ranges")

    work_dir = output_path.parent / "render_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir = output_path.parent / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = output_path.parent / "clips_graded"
    clips_dir.mkdir(parents=True, exist_ok=True)

    # Download remote sources to local files first
    local_sources = {}
    for name, url in sources.items():
        if url.startswith("http"):
            if "youtube.com/watch" in url or "youtu.be/" in url:
                ext = "mp4"
            else:
                stem = url.split("?")[0].rsplit(".", 1)[-1][:4]
                ext = stem if stem.isalnum() else "mp4"
            dest = downloads_dir / f"{name}.{ext}"
            local_sources[name] = str(download_source(url, dest))
        else:
            local_sources[name] = url
    sources = local_sources

    # Step 1: Extract + fade each segment
    segment_paths = []
    for i, r in enumerate(ranges):
        source_name = r["source"]
        source_file = sources.get(source_name)
        if not source_file:
            print(f"[render] skip range {i}: source '{source_name}' missing", file=sys.stderr)
            continue

        raw_clip = work_dir / f"clip_{i:03d}_raw.mp4"
        faded_clip = clips_dir / f"clip_{i:03d}.mp4"

        try:
            extract_segment(source_file, r["start"], r["end"], raw_clip)
        except RuntimeError as e:
            print(f"[render] extract failed for range {i} ({source_name}): {e}", file=sys.stderr)
            continue

        if not raw_clip.exists() or raw_clip.stat().st_size < 1000:
            print(f"[render] clip_{i:03d} empty/missing after extract, skip", file=sys.stderr)
            continue

        try:
            faded_clip = add_audio_fades(raw_clip, faded_clip)
        except Exception:
            faded_clip = raw_clip  # use without fades if fails
        segment_paths.append(faded_clip)

    if not segment_paths:
        raise ValueError("No valid segments extracted — check EDL sources are accessible")

    # Step 2: Grade + normalize each segment (fps/res must match before concat)
    from helpers.grade import grade_segment
    graded_paths = []
    for i, clip in enumerate(segment_paths):
        graded = clips_dir / f"clip_{i:03d}_graded.mp4"
        try:
            grade_segment(clip, grade_preset or "none", graded, resolution=resolution)
            graded_paths.append(graded)
        except Exception as e:
            print(f"[render] grade failed for {clip.name}: {e}, using ungraded", file=sys.stderr)
            graded_paths.append(clip)
    segment_paths = graded_paths

    # Step 3: Concat
    # Debug: verify segments exist and have size
    valid_segments = []
    for p in segment_paths:
        size = p.stat().st_size if p.exists() else 0
        print(f"[render] segment {p.name}: {size} bytes", file=sys.stderr)
        if size > 1000:
            valid_segments.append(p)
        else:
            print(f"[render] skip {p.name}: too small ({size} bytes)", file=sys.stderr)
    if not valid_segments:
        raise ValueError("No valid segments to concat — stock video URLs may be inaccessible")
    segment_paths = valid_segments
    concat_out = work_dir / "concat.mp4"
    concat_segments(segment_paths, concat_out)
    current = concat_out

    # Step 4: Overlays (Rule 4 — PTS shifted)
    for j, overlay in enumerate(overlays):
        overlay_out = work_dir / f"overlay_{j}.mp4"
        current = add_overlay(
            current,
            overlay["file"],
            overlay["start_in_output"],
            overlay["duration"],
            overlay_out,
        )

    # Step 5: Mix voiceover audio (if present in EDL or auto-detected)
    voiceover = edl.get("voiceover")
    if not voiceover:
        # Auto-detect voiceover next to edl.json
        auto_vo = output_path.parent / "voiceover.mp3"
        if auto_vo.exists():
            voiceover = str(auto_vo)
    if voiceover and Path(voiceover).exists():
        voiceover_duration = get_duration(str(voiceover))
        matched = work_dir / "matched_to_voiceover.mp4"
        current = match_video_duration(current, voiceover_duration, matched)

        voiced = work_dir / "with_voiceover.mp4"
        has_audio = has_audio_stream(current)
        if has_audio:
            filter_complex = "[0:a]volume=0.1[bg];[1:a]volume=1.0[vo];[bg][vo]amix=inputs=2:duration=shortest[a]"
            audio_map = "[a]"
        else:
            filter_complex = "[1:a]volume=1.0[a]"
            audio_map = "[a]"
        run([
            "ffmpeg", "-y",
            "-i", str(current),
            "-i", str(voiceover),
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", audio_map,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(voiced),
        ], "mix voiceover")
        current = voiced

    # Step 5b: Background music under voiceover
    music_out = work_dir / "with_music.mp4"
    try:
        mixed_music = mix_background_music(current, music_out)
        if mixed_music.exists() and mixed_music.stat().st_size > 10000:
            current = mixed_music
    except Exception as e:
        print(f"[render] music mix skipped: {e}", file=sys.stderr)

    # Step 6: Burn subtitles LAST (Rule 1)
    if subtitles and Path(subtitles).exists():
        subbed = work_dir / "with_subs.mp4"
        burn_subtitles(current, subtitles, subbed)
        current = subbed

    # Copy to final output
    import shutil
    shutil.copy2(str(current), str(output_path))
    print(f"[render] done: {output_path}", file=sys.stderr)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="EDL JSON → video via FFmpeg")
    parser.add_argument("edl", help="Path to edl.json")
    parser.add_argument("--output", required=True, help="Output video path")
    parser.add_argument("--res", default="1920x1080", help="Output resolution (default: 1920x1080)")
    args = parser.parse_args()

    edl_path = Path(args.edl)
    if not edl_path.exists():
        print(f"ERROR: {edl_path} not found", file=sys.stderr)
        sys.exit(1)

    edl = json.loads(edl_path.read_text())
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    render_edl(edl, output_path, args.res)


if __name__ == "__main__":
    main()
