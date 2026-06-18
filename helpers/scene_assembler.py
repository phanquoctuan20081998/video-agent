#!/usr/bin/env python3
"""
Scene assembler: renders each scene (Remotion/stock/AI) then assembles with voiceover.

Usage:
    python helpers/scene_assembler.py storyboard.json --voiceover voiceover.mp3 --output final.mp4
"""

import argparse
import json
import os
import random
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


def align_scenes_to_voiceover(scenes: list[dict], voiceover_path: Path) -> list[dict]:
    """Align scene durations to actual voiceover word timestamps.
    
    Uses the voiceover transcript (word-level JSON) to find where each scene's
    narration text starts/ends in the audio, then sets scene duration accordingly.
    Also uses audio-first cut detection to prefer natural silence gaps.
    """
    # Find transcript JSON for voiceover
    transcript_dir = voiceover_path.parent / "transcripts"
    transcript_path = transcript_dir / f"{voiceover_path.stem}.json"
    
    if not transcript_path.exists():
        # Try to transcribe on-the-fly
        try:
            from helpers.transcribe import transcribe_file
            api_key = os.getenv("ELEVENLABS_API_KEY", "")
            if api_key:
                transcript_path = transcribe_file(
                    voiceover_path, transcript_dir, api_key, speakers=1
                )
        except Exception as e:
            print(f"[assembler] voiceover transcript unavailable: {e}", file=sys.stderr)
            return scenes
    
    if not transcript_path.exists():
        print("[assembler] no voiceover transcript for timing alignment", file=sys.stderr)
        return scenes
    
    try:
        transcript = json.loads(transcript_path.read_text())
    except (json.JSONDecodeError, OSError):
        return scenes
    
    # Audio-first: detect natural cut points from silence gaps
    try:
        from helpers.cut_detector import detect_cuts
        cut_candidates = detect_cuts(transcript_path)
        # Build set of "safe cut times" (confidence ≥ 0.6)
        safe_cuts = {round(c.time, 2) for c in cut_candidates if c.confidence >= 0.6}
        print(f"[assembler] {len(safe_cuts)} safe audio cut points detected", file=sys.stderr)
    except Exception as e:
        print(f"[assembler] cut_detector unavailable: {e}", file=sys.stderr)
        safe_cuts = set()
    
    # Build word list with timestamps
    words = []
    for w in transcript.get("words", []):
        words.append({
            "text": w.get("text", "").strip(),
            "start": w.get("start", 0.0),
            "end": w.get("end", 0.0),
        })
    
    if not words:
        return scenes
    
    # For each scene, find matching word span in transcript
    word_idx = 0
    total_words = len(words)
    aligned_scenes = []
    
    for scene in scenes:
        narration = scene.get("narration", "").strip()
        if not narration or word_idx >= total_words:
            aligned_scenes.append(scene)
            continue
        
        # Find the start of this narration in the word list
        narration_words = narration.split()
        if not narration_words:
            aligned_scenes.append(scene)
            continue
        
        # Match first word of narration to find start position
        scene_start_idx = word_idx
        first_word_clean = narration_words[0].strip(".,!?;:\"'").lower()
        
        # Search forward for first matching word (fuzzy: ignore punctuation)
        for j in range(word_idx, min(word_idx + 20, total_words)):
            w_clean = words[j]["text"].strip(".,!?;:\"'").lower()
            if w_clean == first_word_clean or first_word_clean in w_clean or w_clean in first_word_clean:
                scene_start_idx = j
                break
        
        # Estimate end position based on narration word count
        scene_end_idx = min(scene_start_idx + len(narration_words) - 1, total_words - 1)
        
        # Verify by checking last word
        last_word_clean = narration_words[-1].strip(".,!?;:\"'").lower()
        for j in range(scene_end_idx, min(scene_end_idx + 10, total_words)):
            w_clean = words[j]["text"].strip(".,!?;:\"'").lower()
            if w_clean == last_word_clean or last_word_clean in w_clean:
                scene_end_idx = j
                break
        
        # Set duration from word timestamps
        start_time = words[scene_start_idx]["start"]
        end_time = words[scene_end_idx]["end"]
        duration = end_time - start_time
        
        # Audio-first: snap end_time to nearest safe cut point (within ±300ms)
        if safe_cuts:
            best_snap = None
            best_dist = 0.3  # max snap distance 300ms
            for sc in safe_cuts:
                dist = abs(sc - end_time)
                if dist < best_dist:
                    best_dist = dist
                    best_snap = sc
            if best_snap is not None:
                end_time = best_snap
                duration = end_time - start_time
        
        if duration > 0.5:  # sanity check
            # Add 50ms padding before, 80ms after (Hard Rule 7: 30-200ms)
            padded_start = max(0, start_time - 0.05)
            padded_end = end_time + 0.08
            padded_duration = padded_end - padded_start
            scene = {**scene, "duration_s": round(padded_duration, 2), "_vo_start": padded_start, "_vo_end": padded_end}
        
        word_idx = scene_end_idx + 1
        aligned_scenes.append(scene)
    
    print(f"[assembler] aligned {len(aligned_scenes)} scenes to voiceover timestamps", file=sys.stderr)
    return aligned_scenes


def render_remotion_scene(scene: dict, output: Path) -> Path:
    from helpers.remotion_runner import render_scene
    return render_scene(scene, output)


def render_pil_scene(scene: dict, output: Path) -> Path:
    """Render an animation using PIL (text cards, counters, simple overlays).
    
    Generates PNG frames → encodes to MP4 via ffmpeg.
    Fast, free, any aesthetic.
    """
    props = scene.get("props", {})
    duration_s = scene.get("duration_s", 5.0)
    fps = 30
    total_frames = int(duration_s * fps)
    width, height = 1920, 1080

    # Style
    bg_color = props.get("bg_color", (10, 10, 10))
    text_color = props.get("text_color", (255, 255, 255))
    accent_color = props.get("accent_color", (255, 90, 0))
    text = props.get("text", props.get("headline", scene.get("narration", "")[:40]))
    subtext = props.get("subtext", props.get("detail", ""))

    if isinstance(bg_color, str):
        bg_color = tuple(int(bg_color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    if isinstance(accent_color, str):
        accent_color = tuple(int(accent_color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[assembler] PIL not available, falling back to black clip", file=sys.stderr)
        run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r={fps}:d={duration_s}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output),
        ], "pil fallback")
        return output

    frames_dir = output.parent / f"_pil_frames_{scene.get('id', 'x')}"
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Try to find a font
    font_path = None
    for candidate in [
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if Path(candidate).exists():
            font_path = candidate
            break

    def ease_out_cubic(t):
        return 1 - (1 - t) ** 3

    for frame_idx in range(total_frames):
        t = frame_idx / max(total_frames - 1, 1)
        img = Image.new("RGB", (width, height), bg_color)
        draw = ImageDraw.Draw(img)

        # Animate text reveal (ease out)
        reveal_progress = ease_out_cubic(min(t * 3, 1.0))  # reveal in first 1/3

        # Main text
        font_size = props.get("font_size", 72)
        try:
            font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
            sub_font = ImageFont.truetype(font_path, 36) if font_path else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
            sub_font = font

        # Calculate visible characters based on reveal
        visible_chars = int(len(text) * reveal_progress)
        visible_text = text[:visible_chars]

        # Center text (based on full text width for stable anchoring)
        bbox = draw.textbbox((0, 0), text, font=font)
        full_w = bbox[2] - bbox[0]
        text_x = (width - full_w) // 2
        text_y = height // 2 - 50

        # Draw with accent on first word
        draw.text((text_x, text_y), visible_text, fill=text_color, font=font)

        # Subtext (fades in after main)
        if subtext and t > 0.4:
            sub_progress = ease_out_cubic(min((t - 0.4) * 3, 1.0))
            sub_visible = subtext[:int(len(subtext) * sub_progress)]
            sub_bbox = draw.textbbox((0, 0), subtext, font=sub_font)
            sub_x = (width - (sub_bbox[2] - sub_bbox[0])) // 2
            draw.text((sub_x, text_y + 100), sub_visible, fill=accent_color, font=sub_font)

        # Accent bar (bottom)
        bar_width = int(width * 0.3 * reveal_progress)
        bar_y = height - 60
        draw.rectangle(
            [(width // 2 - bar_width // 2, bar_y), (width // 2 + bar_width // 2, bar_y + 4)],
            fill=accent_color,
        )

        img.save(frames_dir / f"frame_{frame_idx:05d}.png")

    # Encode frames to video
    run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%05d.png"),
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-t", str(duration_s),
        str(output),
    ], "pil encode frames")

    # Cleanup frames
    import shutil
    shutil.rmtree(frames_dir, ignore_errors=True)

    return output


def render_hyperframes_scene(scene: dict, output: Path, work_dir: Path) -> Path:
    """Render animation via HyperFrames (HTML/CSS/GSAP compositions).
    
    Requires Node.js 22+ and npx. Falls back to PIL if unavailable.
    """
    props = scene.get("props", {})
    duration_s = scene.get("duration_s", 5.0)
    scene_id = scene.get("id", "hf_scene")

    slot_dir = work_dir / "animations" / f"slot_{scene_id}"
    slot_dir.mkdir(parents=True, exist_ok=True)

    # Check if npx/hyperframes available
    check = subprocess.run(
        ["npx", "--yes", "hyperframes", "--version"],
        capture_output=True, text=True, timeout=30,
    )
    if check.returncode != 0:
        print("[assembler] HyperFrames not available, falling back to PIL", file=sys.stderr)
        return render_pil_scene(scene, output)

    # Generate HTML composition
    html_content = props.get("html", _generate_hf_html(props, duration_s))
    html_path = slot_dir / "index.html"
    html_path.write_text(html_content, encoding="utf-8")

    # Render via HyperFrames
    try:
        run([
            "npx", "--yes", "hyperframes", "render", str(slot_dir),
            "-o", str(output),
            "--width", "1920", "--height", "1080",
            "--fps", "30",
            "--duration", str(duration_s),
        ], f"hyperframes render {scene_id}")
    except RuntimeError:
        print(f"[assembler] HyperFrames render failed for {scene_id}, falling back to PIL", file=sys.stderr)
        return render_pil_scene(scene, output)

    if not output.exists() or output.stat().st_size < 1000:
        return render_pil_scene(scene, output)

    return output


def _generate_hf_html(props: dict, duration_s: float) -> str:
    """Generate a basic HyperFrames HTML composition from props."""
    text = props.get("text", props.get("headline", ""))
    subtext = props.get("subtext", props.get("detail", ""))
    bg_color = props.get("bg_color", "#0A0A0A")
    accent_color = props.get("accent_color", "#FF5A00")

    if isinstance(bg_color, (tuple, list)):
        bg_color = "#{:02x}{:02x}{:02x}".format(*bg_color)
    if isinstance(accent_color, (tuple, list)):
        accent_color = "#{:02x}{:02x}{:02x}".format(*accent_color)

    return f"""<!DOCTYPE html>
<html>
<head>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1920px; height: 1080px;
    background: {bg_color};
    display: flex; align-items: center; justify-content: center;
    flex-direction: column; font-family: 'Inter', 'Helvetica', sans-serif;
    overflow: hidden;
  }}
  .main-text {{
    font-size: 72px; font-weight: 900; color: white;
    opacity: 0; transform: translateY(40px);
    animation: revealUp 0.8s ease-out 0.2s forwards;
  }}
  .sub-text {{
    font-size: 36px; color: {accent_color}; margin-top: 24px;
    opacity: 0; transform: translateY(20px);
    animation: revealUp 0.6s ease-out 0.8s forwards;
  }}
  .accent-bar {{
    width: 0; height: 4px; background: {accent_color};
    margin-top: 40px;
    animation: expandBar 1s ease-out 0.4s forwards;
  }}
  @keyframes revealUp {{
    to {{ opacity: 1; transform: translateY(0); }}
  }}
  @keyframes expandBar {{
    to {{ width: 300px; }}
  }}
</style>
</head>
<body>
  <div class="main-text">{text}</div>
  <div class="sub-text">{subtext}</div>
  <div class="accent-bar"></div>
</body>
</html>"""


def _ensure_english_query(query: str, narration: str = "") -> str:
    """Translate non-English stock search queries to detailed English.
    
    Stock APIs (Pexels, Pixabay) only understand English.
    Also enriches vague queries with more descriptive terms.
    """
    # Check if query contains non-ASCII (Vietnamese, Chinese, etc.)
    has_non_ascii = any(ord(c) > 127 for c in query)
    is_too_short = len(query.split()) < 3
    
    if not has_non_ascii and not is_too_short:
        return query
    
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        # No API key — do basic cleanup: strip non-ASCII, keep what we can
        if has_non_ascii:
            ascii_words = [w for w in query.split() if all(ord(c) < 128 for c in w)]
            return " ".join(ascii_words) if ascii_words else "cinematic landscape"
        return query
    
    try:
        import httpx as _httpx
        context = f"\nScene narration: {narration}" if narration else ""
        resp = _httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "google/gemini-2.5-flash-lite",
                "messages": [{"role": "user", "content": (
                    f"Translate this stock video search query to English. "
                    f"Make it specific and descriptive (4-6 words) for finding "
                    f"the exact visual on Pexels/Pixabay. "
                    f"Reply with ONLY the English search query, nothing else.\n\n"
                    f"Query: {query}{context}"
                )}],
                "temperature": 0.3,
                "max_tokens": 30,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            result = resp.json()["choices"][0]["message"]["content"].strip().strip('"\'')
            if result and len(result) > 2:
                print(f"[assembler] query translated: '{query}' → '{result}'", file=sys.stderr)
                return result
    except Exception as e:
        print(f"[assembler] query translation failed: {e}", file=sys.stderr)
    
    return query


def fetch_stock_clip(
    scene: dict,
    output: Path,
    downloads_dir: Path,
    used_video_ids: set | None = None,
    used_queries: list | None = None,
) -> Path:
    """Fetch stock video for a broll_stock scene. Skips already-used video IDs.
    
    Diversity strategy:
    - Tracks used queries to vary search terms
    - Appends narration keywords to query for variety
    - Uses random seek offset when trimming (not always from 0:00)
    """
    if used_video_ids is None:
        used_video_ids = set()
    if used_queries is None:
        used_queries = []

    props = scene.get("props", {})
    source_url = props.get("source_url") or props.get("url")
    if source_url:
        source_path = download_direct_source(str(source_url), downloads_dir)
        return trim_to_duration(source_path, duration_s=scene.get("duration_s", 6.0), output=output, random_seek=True)

    source = props.get("source")
    sources = scene.get("_sources", {})
    if source and source in sources:
        source_path = download_direct_source(str(sources[source]), downloads_dir)
        return trim_to_duration(source_path, duration_s=scene.get("duration_s", 6.0), output=output, random_seek=True)

    query = props.get("query", props.get("caption", "nature"))
    duration_s = scene.get("duration_s", 6.0)
    narration = scene.get("narration", "")

    # Ensure query is in English and descriptive — stock APIs only understand English
    query = _ensure_english_query(query, narration)
    
    # Diversity: if same query used before, augment with narration keywords
    if query in used_queries and narration:
        # Extract 1-2 unique keywords from narration to diversify
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "of", "in", "to", "and", "for", "on", "with", "that", "this", "it", "from", "by", "as", "at", "or", "but", "not", "be", "have", "has", "had", "do", "does", "did", "will", "would", "can", "could", "should", "may", "might", "shall", "must", "các", "một", "của", "và", "là", "cho", "với", "này", "đó", "được", "có", "từ", "trong", "những", "đã", "sẽ", "để", "không"}
        narr_words = [w.strip(".,!?;:\"'()[]") for w in narration.split() if len(w) > 3]
        unique_kw = [w for w in narr_words if w.lower() not in stop_words and w.lower() not in query.lower()]
        if unique_kw:
            extra = " ".join(random.sample(unique_kw, min(2, len(unique_kw))))
            query = f"{query} {extra}"
            print(f"[assembler] diversified query: '{query}'", file=sys.stderr)
    
    used_queries.append(props.get("query", query))

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
                    return trim_to_duration(cached, duration_s, output, random_seek=True)
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


def trim_to_duration(source: Path, duration_s: float, output: Path, random_seek: bool = False) -> Path:
    src_dur = get_duration(str(source))
    if src_dur > 0 and abs(src_dur - duration_s) < 0.25:
        shutil.copy2(str(source), str(output))
        return output
    
    # Random seek: pick a random start offset if source is longer than needed
    # This prevents all clips from showing only the first N seconds (boring)
    seek_offset = 0.0
    if random_seek and src_dur > duration_s + 1.0:
        max_offset = src_dur - duration_s - 0.5
        if max_offset > 0.5:
            seek_offset = round(random.uniform(0.3, max_offset), 2)
    
    cmd = ["ffmpeg", "-y"]
    if seek_offset > 0:
        cmd += ["-ss", str(seek_offset)]
    cmd += [
        "-stream_loop", "-1", "-i", str(source),
        "-t", str(duration_s),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-an",
        str(output),
    ]
    run(cmd, f"trim/loop to {duration_s}s (seek={seek_offset}s)")
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

    # If video is shorter than voiceover, re-encode with stream_loop to fill
    # the entire voiceover duration (not just freeze on last frame)
    if vid_dur < vo_dur - 0.5:
        looped = video.parent / "looped_video.mp4"
        run([
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(video),
            "-t", str(vo_dur),
            "-vf", "fps=30",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an",
            str(looped),
        ], f"loop video {vid_dur:.1f}s → {vo_dur:.1f}s to match voiceover")
        video = looped

    run([
        "ffmpeg", "-y",
        "-i", str(video), "-i", str(voiceover),
        "-filter_complex", "[1:a]volume=1.0[vo];[vo]aformat=fltp:44100:stereo[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
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

    # Align scene durations to voiceover word timestamps (voice-driven timing)
    if voiceover_path and voiceover_path.exists():
        scenes = align_scenes_to_voiceover(scenes, voiceover_path)

    work_dir = output_path.parent / "scene_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir = output_path.parent / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    # Budget tracker
    budget_remaining = VIDEO_BUDGET_USD
    seedance_clips_used = 0
    cost_log: list[dict] = []
    used_video_ids: set = set()  # prevent same Pexels clip appearing twice
    used_queries: list = []  # track queries for diversity
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
            if scene_type in ("remotion", "pil", "pil_animation", "text_card", "hyperframes", "hf"):
                # Degrade text/animation scenes to stock footage
                print(f"[assembler] degrading {scene_id} [{scene_type}] → broll_stock", file=sys.stderr)
                scene["props"] = scene.get("props", {})
                if not scene["props"].get("query"):
                    scene["props"]["query"] = scene.get("narration", "cinematic landscape")[:60]
                fetch_stock_clip(scene, raw_clip, downloads_dir, used_video_ids, used_queries)
            elif scene_type in ("broll_stock", "stock"):
                fetch_stock_clip(scene, raw_clip, downloads_dir, used_video_ids, used_queries)
            elif scene_type in ("broll_ai_image", "ai_image"):
                # Together Flux Free = $0; Gemini fallback = ~$0.04
                img_cost = AI_IMAGE_COST
                if not spend(scene_id, img_cost):
                    # Budget exceeded — degrade to stock video
                    print(f"[assembler] degrading {scene_id} to stock (budget)", file=sys.stderr)
                    scene["props"] = scene.get("props", {})
                    scene["props"]["query"] = scene.get("narration", "technology")[:40]
                    fetch_stock_clip(scene, raw_clip, downloads_dir, used_video_ids, used_queries)
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

    # Self-evaluation loop (max 3 passes)
    skip_eval = os.getenv("SKIP_SELF_EVAL", "").lower() in ("1", "true")
    if not skip_eval:
        try:
            from helpers.self_eval import self_eval_loop
            output_path = self_eval_loop(
                video_path=output_path,
                storyboard=storyboard,
                voiceover_path=voiceover_path,
                output_path=output_path,
                render_fn=None,  # report-only mode (no auto re-render in first integration)
            )
        except Exception as e:
            print(f"[assembler] self-eval failed (non-fatal): {e}", file=sys.stderr)

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
