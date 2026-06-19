#!/usr/bin/env python3
"""
EDL JSON → final.mp4 via FFmpeg.

Pipeline per Hard Rules (optimized: collapsed encode passes):
  1. Per-segment extract + grade + normalize in ONE encode pass
     (was 3 separate passes: extract → grade → normalize)
  2. Audio fades only for Workflow B (native audio kept); skipped in Workflow A
  3. Concat graded segments (lossless -c copy, all same format now)
  4. Overlay animations (PTS-shifted)
  5. Attach voiceover (Workflow A) / keep native audio (Workflow B)
  6. Background music
  7. Burn subtitles LAST (Rule 1) — only remaining re-encode

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

    from helpers.audio_mixer import fetch_music
    music_path = fetch_music(duration_s=duration)
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
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ], "match video duration to voiceover")
    return out_path


def extract_segment(
    source_path: str,
    start: float,
    end: float,
    out_path: Path,
    pad_ms: int = 30,
    grade_filter: str = "",
    resolution: str = "1920x1080",
) -> Path:
    """Extract + grade + normalize in a SINGLE encode pass.

    Combines what was previously 3 separate FFmpeg invocations (extract → grade → normalize)
    into one. Applies: seek/trim, CFR re-encode, scale/pad, grade filter, fps lock.

    Rule 7: padding at cut edges.
    Rule 2: CFR re-encode at extract time (VFR/sparse-keyframe stock sources need it).
    """
    src_dur = get_duration(source_path)
    if src_dur > 0:
        # Clamp: if start beyond source, use proportional position
        if start >= src_dur:
            span = end - start  # preserve requested duration before reassigning start
            ratio = start / max(end, start + 1)
            start = src_dur * ratio * 0.5
            end = min(src_dur, start + span)
            print(f"[render] clamped timestamps to [{start:.2f}-{end:.2f}] (src={src_dur:.1f}s)", file=sys.stderr)
        end = min(end, src_dur)

    padded_start = max(0.0, start - pad_ms / 1000)
    duration = max(0.5, (end + pad_ms / 1000) - padded_start)

    # Build unified video filter: normalize (scale+pad+fps) + grade in one pass
    w, h = resolution.split("x")
    vf_parts = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
        "fps=30",
        "setsar=1",
    ]
    if grade_filter:
        vf_parts.append(grade_filter)
    vf = ",".join(vf_parts)

    run([
        "ffmpeg", "-y",
        "-ss", str(padded_start),
        "-i", source_path,
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-avoid_negative_ts", "make_zero",
        str(out_path),
    ], f"extract+grade {Path(source_path).name} [{start:.2f}-{end:.2f}]")
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
    mode: str = "replace",
) -> Path:
    """Composite an overlay clip onto the base (Rule 4), PTS-shifted so it starts at
    start_in_output seconds.
    mode="replace": full-frame cutaway — the overlay (e.g. MapHighlight) is opaque and
      same dimensions as the base, fully replacing the frame for its duration, like a
      standalone inserted scene.
    mode="screen": screen-blend — black pixels in the overlay are transparent, bright
      pixels are additive. For semantically-transparent motion graphics (light sweeps,
      frame accents) that should sit on top of the footage, not replace it."""
    overlay_path = Path(overlay_file)
    if not overlay_path.is_absolute():
        overlay_path = Path.cwd() / overlay_file

    end_t = start_in_output + duration
    if mode == "screen":
        filter_complex = (
            f"[1:v]setpts=PTS-STARTPTS+{start_in_output}/TB,format=rgb24[ov];"
            f"[0:v][ov]blend=screen:enable='between(t,{start_in_output},{end_t})'[vout]"
        )
    else:
        filter_complex = (
            f"[1:v]setpts=PTS-STARTPTS+{start_in_output}/TB,format=yuv420p[ov];"
            f"[0:v][ov]overlay=enable='between(t,{start_in_output},{end_t})'[vout]"
        )

    run([
        "ffmpeg", "-y",
        "-i", str(base_path),
        "-i", str(overlay_path),
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "0:a?",
        "-c:a", "copy",
        str(out_path),
    ], f"overlay {Path(overlay_file).name} at {start_in_output:.1f}s")
    return out_path


def add_end_screen(video_path: Path, out_path: Path, duration_s: float = 8.0) -> Path:
    """Add a subscribe/end-card overlay in the last N seconds of the video.

    YouTube's algorithm rewards sessions (viewer watches another video after yours).
    The end screen shows two placeholder boxes where YouTube can place interactive
    end screen elements (configured in YouTube Studio after upload), plus a subscribe
    reminder. This is purely visual — the actual clickable end screen is added via
    YouTube Studio, but the visual guides viewers' eyes to the right spots.
    """
    total_dur = get_duration(str(video_path))
    if total_dur <= 0 or total_dur < duration_s + 5:
        return video_path  # video too short for end screen

    start_time = total_dur - duration_s
    brand_label = os.getenv("THUMBNAIL_BRAND_LABEL", "DIA LY 60S")

    # Draw end screen overlay using FFmpeg drawbox + drawtext
    # Two rounded suggestion boxes (where YouTube end screen elements will go)
    # + subscribe text
    end_filter = (
        # Dim background slightly in end screen zone
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.35:t=fill:enable='gte(t,{start_time:.2f})',"
        # Left suggestion box (for "next video")
        f"drawbox=x=iw*0.08:y=ih*0.25:w=iw*0.38:h=ih*0.45:color=white@0.12:t=fill:enable='gte(t,{start_time:.2f})',"
        f"drawbox=x=iw*0.08:y=ih*0.25:w=iw*0.38:h=ih*0.45:color=white@0.5:t=3:enable='gte(t,{start_time:.2f})',"
        # Right suggestion box (for "best for viewer")
        f"drawbox=x=iw*0.54:y=ih*0.25:w=iw*0.38:h=ih*0.45:color=white@0.12:t=fill:enable='gte(t,{start_time:.2f})',"
        f"drawbox=x=iw*0.54:y=ih*0.25:w=iw*0.38:h=ih*0.45:color=white@0.5:t=3:enable='gte(t,{start_time:.2f})',"
        # Subscribe CTA at bottom
        f"drawtext=text='SUBSCRIBE {brand_label}':fontsize=36:fontcolor=white:"
        f"borderw=3:bordercolor=black@0.8:"
        f"x=(w-tw)/2:y=h*0.82:enable='gte(t,{start_time:.2f})'"
    )

    run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", end_filter,
        "-c:a", "copy",
        str(out_path),
    ], f"add end screen (last {duration_s}s)")
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
        # moov atom at the front (not the tail) — without this, players that start
        # reading before the full file/index is available can stutter or drop audio
        # partway through (observed: audio cuts ~10s in, briefly resumes on seek).
        "-movflags", "+faststart",
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

    # Rule 6: validate EDL cuts against transcript word boundaries before rendering
    try:
        from helpers.edl_validator import validate_edl
        # Look for transcript alongside output
        transcript_dir = output_path.parent / "transcripts"
        vo_transcript = transcript_dir / "voiceover.json"
        if vo_transcript.exists():
            edl = validate_edl(edl, vo_transcript)
            ranges = edl.get("ranges", [])
    except Exception as e:
        print(f"[render] edl_validator skipped: {e}", file=sys.stderr)

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

    # Determine if this is Workflow A (voiceover replaces all segment audio).
    # If so, skip per-segment audio fades — they're wasted work since voiceover
    # becomes the sole audio track (Rule 3 only matters for Workflow B native audio).
    voiceover = edl.get("voiceover")
    if not voiceover:
        auto_vo = output_path.parent / "voiceover.mp3"
        if auto_vo.exists():
            voiceover = str(auto_vo)
    is_workflow_a = bool(voiceover and Path(voiceover).exists())

    # Resolve grade filter once (used in single-pass extract+grade)
    from helpers.grade import GRADE_PRESETS
    if grade_preset in GRADE_PRESETS:
        grade_filter = GRADE_PRESETS[grade_preset]
    else:
        grade_filter = grade_preset if grade_preset and grade_preset != "none" else ""

    # Step 1: Extract + grade + normalize in ONE pass per segment
    segment_paths = []
    for i, r in enumerate(ranges):
        source_name = r["source"]
        source_file = sources.get(source_name)
        if not source_file:
            print(f"[render] skip range {i}: source '{source_name}' missing", file=sys.stderr)
            continue

        graded_clip = clips_dir / f"clip_{i:03d}.mp4"

        try:
            extract_segment(
                source_file, r["start"], r["end"], graded_clip,
                pad_ms=r.get("pad_ms", 30),
                grade_filter=grade_filter,
                resolution=resolution,
            )
        except RuntimeError as e:
            print(f"[render] extract failed for range {i} ({source_name}): {e}", file=sys.stderr)
            continue

        if not graded_clip.exists() or graded_clip.stat().st_size < 1000:
            print(f"[render] clip_{i:03d} empty/missing after extract, skip", file=sys.stderr)
            continue

        # Audio fades only for Workflow B (native camera audio kept)
        if not is_workflow_a:
            faded_clip = clips_dir / f"clip_{i:03d}_faded.mp4"
            try:
                graded_clip = add_audio_fades(graded_clip, faded_clip)
            except Exception:
                pass  # use without fades if fails

        segment_paths.append(graded_clip)

    if not segment_paths:
        raise ValueError("No valid segments extracted — check EDL sources are accessible")

    # Step 2: Concat
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

    # Step 3: Overlays (Rule 4 — PTS shifted)
    for j, overlay in enumerate(overlays):
        overlay_out = work_dir / f"overlay_{j}.mp4"
        # style_version 3 = local semantic effects (cut_flash/lower_accent): transparent
        # motion graphics meant to screen-blend on top. Everything else (map_highlight)
        # is an opaque full-frame cutaway.
        mode = "screen" if overlay.get("style_version") == 3 else "replace"
        current = add_overlay(
            current,
            overlay["file"],
            overlay["start_in_output"],
            overlay["duration"],
            overlay_out,
            mode=mode,
        )

    # Step 4: Attach voiceover (Workflow A) — already resolved above
    if is_workflow_a:
        voiceover_duration = get_duration(str(voiceover))
        matched = work_dir / "matched_to_voiceover.mp4"
        current = match_video_duration(current, voiceover_duration, matched)

        voiced = work_dir / "with_voiceover.mp4"
        # Attach the voiceover as the sole audio track instead of mixing in whatever
        # native audio survived concat. The b-roll segments come from many different
        # stock sources with inconsistent sample rates/channel layouts; carrying that
        # audio through 18 stream-copy splices + a stream_loop re-encode is what was
        # producing corrupted/cut-out audio mid-playback. Voiceover is one clean
        # continuous file — just attach it directly.
        run([
            "ffmpeg", "-y",
            "-i", str(current),
            "-i", str(voiceover),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(voiced),
        ], "attach voiceover")
        current = voiced

    # Step 5: Background music under voiceover
    music_out = work_dir / "with_music.mp4"
    try:
        mixed_music = mix_background_music(current, music_out)
        if mixed_music.exists() and mixed_music.stat().st_size > 10000:
            current = mixed_music
    except Exception as e:
        print(f"[render] music mix skipped: {e}", file=sys.stderr)

    # Step 6: End screen overlay (before subtitles — Rule 1 still last)
    skip_end_screen = os.getenv("SKIP_END_SCREEN", "").lower() in ("1", "true")
    if not skip_end_screen:
        end_screen_out = work_dir / "with_endscreen.mp4"
        try:
            result = add_end_screen(current, end_screen_out)
            if result.exists() and result.stat().st_size > 10000:
                current = result
        except Exception as e:
            print(f"[render] end screen skipped: {e}", file=sys.stderr)

    # Step 7: Burn subtitles LAST (Rule 1) — only re-encode left in chain
    if subtitles and Path(subtitles).exists():
        subbed = work_dir / "with_subs.mp4"
        burn_subtitles(current, subtitles, subbed)
        current = subbed

    # Final output: remux with moov-at-front regardless of which branch above ran
    # (burn_subtitles already does this, but this guarantees it when there are no
    # subtitles too — stream copy only, no re-encode).
    run([
        "ffmpeg", "-y", "-i", str(current),
        "-c", "copy", "-movflags", "+faststart",
        str(output_path),
    ], "finalize output (faststart)")
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
