#!/usr/bin/env python3
"""
Scene assembler: renders each scene (Remotion/stock/AI) then assembles with voiceover.

Usage:
    python helpers/scene_assembler.py storyboard.json --voiceover voiceover.mp3 --output final.mp4
"""

import argparse
import json
import os
import subprocess
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

# Per-video cost budget (configurable via env)
VIDEO_BUDGET_USD = float(os.getenv("VIDEO_BUDGET_USD", "1.00"))

# Seedance: ~$0.10/s. Reserve max 30% of budget, cap at 2 clips
SEEDANCE_COST_PER_S = float(os.getenv("SEEDANCE_COST_PER_S", "0.10"))
SEEDANCE_MAX_CLIPS = int(os.getenv("SEEDANCE_MAX_CLIPS", "2"))
SEEDANCE_MAX_DURATION_S = int(os.getenv("SEEDANCE_MAX_DURATION_S", "5"))  # per clip

# AI image: Together Flux Free = $0; Gemini fallback = $0.04
AI_IMAGE_COST = float(os.getenv("AI_IMAGE_COST", "0.00"))  # assume free tier


def run(cmd: list[str], label: str = "") -> subprocess.CompletedProcess:
    print(f"[assembler] {label or cmd[0]}", file=sys.stderr)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[assembler] ERROR: {r.stderr[-500:]}", file=sys.stderr)
        raise RuntimeError(f"failed: {label}")
    return r


def load_json_lenient(path: Path) -> dict:
    text = Path(path).read_text().strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text.startswith("{"):
        start = text.find("{")
        if start >= 0:
            text = text[start:]
    if not text.rstrip().endswith("}"):
        end = text.rfind("}")
        if end >= 0:
            text = text[:end + 1]
    return json.loads(text)


def get_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def render_remotion_scene(scene: dict, output: Path) -> Path:
    from helpers.remotion_runner import render_scene
    return render_scene(scene, output)


def fetch_stock_clip(
    scene: dict,
    output: Path,
    downloads_dir: Path,
    used_video_ids: set | None = None,
) -> Path:
    """Fetch stock video for a broll_stock scene. Skips already-used video IDs."""
    if used_video_ids is None:
        used_video_ids = set()

    props = scene.get("props", {})
    source_url = props.get("source_url") or props.get("url")
    if source_url:
        source_path = download_direct_source(str(source_url), downloads_dir)
        return trim_to_duration(source_path, duration_s=scene.get("duration_s", 6.0), output=output)

    source = props.get("source")
    sources = scene.get("_sources", {})
    if source and source in sources:
        source_path = download_direct_source(str(sources[source]), downloads_dir)
        return trim_to_duration(source_path, duration_s=scene.get("duration_s", 6.0), output=output)

    query = props.get("query", props.get("caption", "nature"))
    duration_s = scene.get("duration_s", 6.0)

    api_key = os.getenv("PEXELS_API_KEY", "")
    if api_key:
        try:
            import httpx, urllib.request
            # Fetch more results so we can skip already-used ones
            r = httpx.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": api_key},
                params={"query": query, "per_page": 15, "size": "medium"},
                timeout=15,
            )
            r.raise_for_status()
            videos = r.json().get("videos", [])
            landscape = [v for v in videos if v.get("width", 0) >= v.get("height", 1)]

            # Pick first video whose ID hasn't been used yet
            chosen = None
            for v in landscape:
                vid_id = v.get("id")
                if vid_id not in used_video_ids:
                    chosen = v
                    break

            # All results exhausted — try page 2
            if not chosen:
                print(f"[assembler] all page-1 results used for '{query}', fetching page 2", file=sys.stderr)
                r2 = httpx.get(
                    "https://api.pexels.com/videos/search",
                    headers={"Authorization": api_key},
                    params={"query": query, "per_page": 15, "size": "medium", "page": 2},
                    timeout=15,
                )
                if r2.status_code == 200:
                    p2 = [v for v in r2.json().get("videos", []) if v.get("width", 0) >= v.get("height", 1)]
                    chosen = next((v for v in p2 if v.get("id") not in used_video_ids), None)

            if chosen:
                vid_id = chosen.get("id")
                files = chosen.get("video_files", [])
                files_hd = [f for f in files if f.get("width", 0) >= 1280]
                dl_url = (files_hd or files)[0].get("link", "")
                if dl_url:
                    # Cache by video ID so same clip reused only if explicitly requested
                    cached = downloads_dir / f"stock_{vid_id}.mp4"
                    if not (cached.exists() and cached.stat().st_size > 10000):
                        downloads_dir.mkdir(parents=True, exist_ok=True)
                        req = urllib.request.Request(dl_url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=60) as resp, open(cached, "wb") as f:
                            f.write(resp.read())
                        print(f"[assembler] downloaded stock id={vid_id}: {cached.name}", file=sys.stderr)
                    else:
                        print(f"[assembler] stock cache hit id={vid_id}", file=sys.stderr)
                    used_video_ids.add(vid_id)
                    return trim_to_duration(cached, duration_s, output)
        except Exception as e:
            print(f"[assembler] pexels fetch failed: {e}", file=sys.stderr)

    # Fallback: black clip
    print(f"[assembler] generating black fallback for: {query}", file=sys.stderr)
    run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=black:s=1920x1080:r=30:d={duration_s}",
        "-t", str(duration_s), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(output),
    ], "black fallback clip")
    return output


def download_direct_source(source: str, downloads_dir: Path) -> Path:
    """Download/cache a direct remote media source or return local source path."""
    source_path = Path(source)
    if source_path.exists():
        return source_path
    if not source.startswith("http"):
        return source_path

    import hashlib
    import urllib.request

    downloads_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".mp4"
    clean = source.split("?", 1)[0]
    for candidate in (".mp4", ".mov", ".mkv", ".webm"):
        if clean.lower().endswith(candidate):
            suffix = candidate
            break
    dest = downloads_dir / f"direct_{hashlib.sha1(source.encode()).hexdigest()[:12]}{suffix}"
    if dest.exists() and dest.stat().st_size > 10000:
        print(f"[assembler] direct source cache hit: {dest.name}", file=sys.stderr)
        return dest

    req = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    print(f"[assembler] downloaded direct source: {dest.name}", file=sys.stderr)
    return dest


def trim_to_duration(source: Path, duration_s: float, output: Path) -> Path:
    src_dur = get_duration(str(source))
    if src_dur > 0 and abs(src_dur - duration_s) < 0.25:
        shutil.copy2(str(source), str(output))
        return output
    run([
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(source),
        "-t", str(duration_s),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-an",
        str(output),
    ], f"trim/loop to {duration_s}s")
    return output


def add_caption_overlay(clip_path: Path, caption: str, output: Path) -> Path:
    """Overlay caption text bar on a stock clip."""
    safe_caption = caption.replace("'", "\\'").replace(":", "\\:")
    run([
        "ffmpeg", "-y", "-i", str(clip_path),
        "-vf",
        (
            "drawbox=x=0:y=ih-120:w=iw:h=120:color=black@0.75:t=fill,"
            f"drawtext=fontsize=44:fontcolor=white:fontfile=/System/Library/Fonts/Helvetica.ttc:"
            f"text='{safe_caption}':x=(w-tw)/2:y=h-80"
        ),
        "-c:a", "copy",
        str(output),
    ], f"caption overlay: {caption[:30]}")
    return output


def normalize_clip(clip_path: Path, output: Path) -> Path:
    """Force all clips to 1920x1080 @ 30fps h264 yuv420p before concat."""
    run([
        "ffmpeg", "-y", "-i", str(clip_path),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-an",
        str(output),
    ], f"normalize {clip_path.name}")
    return output


def concat_clips(clip_paths: list[Path], output: Path) -> Path:
    list_file = output.parent / "concat_list.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in clip_paths))
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output),
    ], "concat scenes")
    list_file.unlink(missing_ok=True)
    return output


def mix_voiceover(video: Path, voiceover: Path, output: Path) -> Path:
    vo_dur = get_duration(str(voiceover))
    vid_dur = get_duration(str(video))

    # Loop video if shorter than voiceover
    if vid_dur < vo_dur - 0.5:
        looped = video.parent / "looped_video.mp4"
        run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(video),
            "-t", str(vo_dur), "-c", "copy", str(looped),
        ], "loop video to match voiceover")
        video = looped

    run([
        "ffmpeg", "-y",
        "-i", str(video), "-i", str(voiceover),
        "-filter_complex", "[1:a]volume=1.0[vo];[vo]aformat=fltp:44100:stereo[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output),
    ], "mix voiceover")
    return output


def render_ai_image_scene(scene: dict, output: Path, work_dir: Path) -> Path:
    """Generate image with AI, then apply Ken Burns QuickZoom via Remotion."""
    from helpers.image_gen import generate_image

    props = scene.get("props", {})
    prompt = props.get("prompt", props.get("image_prompt", scene.get("narration", "")))
    # Enrich prompt for cinematic quality
    cinematic_prompt = f"cinematic, photorealistic, 4K, professional photography, {prompt}"
    duration_s = scene.get("duration_s", 6.0)

    img_path = work_dir / f"{scene.get('id','scene')}_ai_image.png"
    generate_image(cinematic_prompt, img_path)

    if not img_path.exists() or img_path.stat().st_size < 1000:
        raise RuntimeError(f"Image generation produced no output for: {prompt[:60]}")

    # Serve image as static file via Remotion QuickZoom
    # Remotion needs a URL or staticFile — use file:// path
    zoom_props = {
        "image_url": img_path.resolve().as_uri(),
        "caption": props.get("caption", ""),
        "zoom_start": props.get("zoom_start", 1.02),
        "zoom_end": props.get("zoom_end", 1.18),
        "pan_x": props.get("pan_x", 2),
        "pan_y": props.get("pan_y", 1),
        "duration_s": duration_s,
    }

    from helpers.remotion_runner import render_composition
    render_composition("QuickZoom", zoom_props, output)
    return output


def render_seedance_scene(scene: dict, output: Path, duration_cap_s: float = 5.0) -> Path:
    """Generate video clip with Seedance. Duration capped by budget policy."""
    from helpers.seedance_runner import generate_clip

    props = scene.get("props", {})
    prompt = props.get("prompt", props.get("broll_prompt", scene.get("narration", "")))
    duration_s = min(scene.get("duration_s", 5.0), duration_cap_s)

    print(f"[assembler] seedance: {prompt[:60]}... ({duration_s}s)", file=sys.stderr)
    return generate_clip(prompt, output, duration_s)


def assemble(storyboard: dict, voiceover_path: Path, output_path: Path) -> Path:
    scenes = storyboard.get("scenes", [])
    if not scenes:
        raise ValueError("Storyboard has no scenes")

    work_dir = output_path.parent / "scene_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir = output_path.parent / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    # Budget tracker
    budget_remaining = VIDEO_BUDGET_USD
    seedance_clips_used = 0
    cost_log: list[dict] = []
    used_video_ids: set = set()  # prevent same Pexels clip appearing twice
    storyboard_sources = storyboard.get("sources", {}) or storyboard.get("stock_sources", {})

    def spend(label: str, cost: float) -> bool:
        nonlocal budget_remaining
        if cost > budget_remaining:
            print(f"[assembler] BUDGET EXCEEDED: {label} costs ${cost:.3f}, only ${budget_remaining:.3f} left", file=sys.stderr)
            return False
        budget_remaining -= cost
        cost_log.append({"scene": label, "cost": cost, "remaining": budget_remaining})
        print(f"[assembler] cost ${cost:.3f} for {label} | budget left: ${budget_remaining:.3f}", file=sys.stderr)
        return True

    rendered_clips: list[Path] = []

    for i, scene in enumerate(scenes):
        scene_id = scene.get("id", f"s{i:03d}")
        scene_type = scene.get("type", "remotion")
        if storyboard_sources:
            scene.setdefault("_sources", storyboard_sources)
        raw_clip = work_dir / f"{scene_id}_raw.mp4"
        norm_clip = work_dir / f"{scene_id}_norm.mp4"

        print(f"[assembler] scene {i+1}/{len(scenes)} [{scene_type}] {scene_id}", file=sys.stderr)

        try:
            if scene_type == "remotion":
                render_remotion_scene(scene, raw_clip)
            elif scene_type in ("broll_stock", "stock"):
                fetch_stock_clip(scene, raw_clip, downloads_dir, used_video_ids)
            elif scene_type in ("broll_ai_image", "ai_image"):
                # Together Flux Free = $0; Gemini fallback = ~$0.04
                img_cost = AI_IMAGE_COST
                if not spend(scene_id, img_cost):
                    # Budget exceeded — degrade to stock video
                    print(f"[assembler] degrading {scene_id} to stock (budget)", file=sys.stderr)
                    scene["props"] = scene.get("props", {})
                    scene["props"]["query"] = scene.get("narration", "technology")[:40]
                    fetch_stock_clip(scene, raw_clip, downloads_dir, used_video_ids)
                else:
                    render_ai_image_scene(scene, raw_clip, work_dir)

            elif scene_type in ("broll_ai_video", "ai_video", "broll_ai", "ai"):
                # Seedance: hard clip + count limit
                duration_s = min(scene.get("duration_s", 5.0), SEEDANCE_MAX_DURATION_S)
                clip_cost = duration_s * SEEDANCE_COST_PER_S

                if seedance_clips_used >= SEEDANCE_MAX_CLIPS:
                    print(f"[assembler] Seedance clip limit ({SEEDANCE_MAX_CLIPS}) reached — downgrade to ai_image", file=sys.stderr)
                    scene["type"] = "broll_ai_image"
                    render_ai_image_scene(scene, raw_clip, work_dir)
                    spend(scene_id, AI_IMAGE_COST)
                elif not spend(scene_id, clip_cost):
                    print(f"[assembler] Seedance over budget — downgrade to ai_image", file=sys.stderr)
                    render_ai_image_scene(scene, raw_clip, work_dir)
                    spend(scene_id, AI_IMAGE_COST)
                else:
                    render_seedance_scene(scene, raw_clip, duration_s)
                    seedance_clips_used += 1

            else:
                print(f"[assembler] unknown scene type: {scene_type}, skipping", file=sys.stderr)
                continue

            if not raw_clip.exists() or raw_clip.stat().st_size < 1000:
                print(f"[assembler] scene {scene_id} produced no output, skipping", file=sys.stderr)
                continue

            # Caption overlay for B-roll clips
            caption = scene.get("props", {}).get("caption")
            if scene_type in ("broll_stock", "stock", "broll_ai", "ai", "broll_ai_video") and caption:
                captioned = work_dir / f"{scene_id}_captioned.mp4"
                add_caption_overlay(raw_clip, caption, captioned)
                raw_clip = captioned

            normalize_clip(raw_clip, norm_clip)
            rendered_clips.append(norm_clip)

        except Exception as e:
            print(f"[assembler] scene {scene_id} failed: {e}, skipping", file=sys.stderr)
            continue

    if not rendered_clips:
        raise ValueError("No scenes rendered successfully")

    # Print cost summary
    total_spent = VIDEO_BUDGET_USD - budget_remaining
    print(f"[assembler] cost summary: ${total_spent:.3f} / ${VIDEO_BUDGET_USD:.2f} budget", file=sys.stderr)
    print(f"[assembler] seedance clips used: {seedance_clips_used}/{SEEDANCE_MAX_CLIPS}", file=sys.stderr)

    print(f"[assembler] concatenating {len(rendered_clips)} scenes", file=sys.stderr)
    concat_out = work_dir / "concat.mp4"
    concat_clips(rendered_clips, concat_out)

    if voiceover_path and voiceover_path.exists():
        print(f"[assembler] mixing voiceover", file=sys.stderr)
        vo_out = work_dir / "with_voiceover.mp4"
        mix_voiceover(concat_out, voiceover_path, vo_out)
    else:
        vo_out = concat_out

    # Background music + SFX
    mood = storyboard.get("style", {}).get("music_mood", "cinematic")
    skip_music = os.getenv("SKIP_MUSIC", "").lower() in ("1", "true")
    skip_sfx = os.getenv("SKIP_SFX", "").lower() in ("1", "true")
    try:
        from helpers.audio_mixer import add_audio
        add_audio(
            vo_out,
            output_path,
            scenes=scenes,
            mood=mood,
            skip_music=skip_music,
            skip_sfx=skip_sfx,
        )
    except Exception as e:
        print(f"[assembler] audio post-processing failed (skipping): {e}", file=sys.stderr)
        shutil.copy2(str(vo_out), str(output_path))

    print(f"[assembler] done: {output_path} ({output_path.stat().st_size // 1024 // 1024}MB)", file=sys.stderr)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Assemble storyboard scenes into final video")
    parser.add_argument("storyboard", help="Path to storyboard.json")
    parser.add_argument("--voiceover", help="Voiceover MP3 path")
    parser.add_argument("--output", required=True, help="Output MP4 path")
    args = parser.parse_args()

    storyboard = load_json_lenient(Path(args.storyboard))
    voiceover = Path(args.voiceover) if args.voiceover else None
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    assemble(storyboard, voiceover, output)


if __name__ == "__main__":
    main()
