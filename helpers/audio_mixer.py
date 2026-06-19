#!/usr/bin/env python3
"""
Audio mixing: background music + per-scene sound effects.

Background music:
  - assets/music/bg_music.mp3 (user drop-in) takes priority
  - Else searches Jamendo API for a mood-matched Creative Commons track (JAMENDO_CLIENT_ID)
  - Loops to video duration, mixed at -22dB under voiceover

Sound effects:
  - Maps scene types → SFX files in assets/sfx/
  - Mixed at scene start timestamps
  - Default SFX generated via ffmpeg tones if no file found

Drop custom files:
  assets/music/bg_music.mp3   — overrides auto-fetch
  assets/sfx/whoosh.mp3       — used for kinetic_text, definition_card
  assets/sfx/impact.mp3       — used for title_card
  assets/sfx/ding.mp3         — used for stat_card
  assets/sfx/tick.mp3         — used for timeline, list_reveal
  assets/sfx/soft_appear.mp3  — used for quote_card

Usage:
    python helpers/audio_mixer.py video.mp4 --storyboard storyboard.json --output out.mp4
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

ASSETS_DIR = Path(__file__).parent.parent / "assets"
SFX_DIR = ASSETS_DIR / "sfx"
MUSIC_DIR = ASSETS_DIR / "music"

MUSIC_VOLUME_DB = float(os.getenv("MUSIC_VOLUME_DB", "-22"))
SFX_VOLUME_DB = float(os.getenv("SFX_VOLUME_DB", "-12"))

# Scene type → SFX filename
SCENE_SFX = {
    "title_card": "impact.mp3",
    "kinetic_text": "whoosh.mp3",
    "kinetic_typography": "whoosh.mp3",
    "definition_card": "whoosh.mp3",
    "stat_card": "ding.mp3",
    "quote_card": "soft_appear.mp3",
    "timeline": "tick.mp3",
    "list_reveal": "tick.mp3",
    "split_comparison": "whoosh.mp3",
    # B-roll: no SFX
    "broll_stock": None,
    "broll_ai_image": None,
    "broll_ai_video": None,
}


def run(cmd: list[str], label: str = "") -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[audio_mixer] ERROR {label}: {r.stderr[-400:]}", file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed: {label}")
    return r


def get_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ── Background music ──────────────────────────────────────────────────────────

def fetch_music(mood: str = "cinematic", duration_s: float = 60.0) -> Path | None:
    """
    Find/download background music:
    1. assets/music/bg_music.mp3 (user drop-in)
    2. Any *.mp3 in assets/music/
    3. Jamendo API (Creative Commons tracks, uses JAMENDO_CLIENT_ID)
    """
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    # User drop-in
    preferred = MUSIC_DIR / "bg_music.mp3"
    if preferred.exists() and preferred.stat().st_size > 10000:
        print(f"[audio_mixer] using user music: {preferred.name}", file=sys.stderr)
        return preferred

    # Any existing file
    existing = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.m4a")) + list(MUSIC_DIR.glob("*.wav"))
    if existing:
        print(f"[audio_mixer] using cached music: {existing[0].name}", file=sys.stderr)
        return existing[0]

    # Jamendo API (Creative Commons music, free for non-commercial use)
    client_id = os.getenv("JAMENDO_CLIENT_ID", "")
    if client_id:
        try:
            import httpx
            tags = {
                "cinematic": "cinematic",
                "upbeat": "happy",
                "minimal": "ambient",
                "dramatic": "epic",
            }.get(mood, mood)

            dur_min = max(20, int(duration_s * 0.5))
            dur_max = max(dur_min + 60, int(duration_s * 3))

            r = httpx.get(
                "https://api.jamendo.com/v3.0/tracks/",
                params={
                    "client_id": client_id,
                    "format": "json",
                    "limit": 5,
                    "fuzzytags": tags,
                    "durationbetween": f"{dur_min}_{dur_max}",
                    "audioformat": "mp32",
                    "order": "popularity_total",
                },
                timeout=15,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            for track in results:
                music_url = track.get("audiodownload") if track.get("audiodownload_allowed") else track.get("audio")
                if not music_url:
                    continue
                import urllib.request
                dest = MUSIC_DIR / f"jamendo_{track.get('id', 'track')}.mp3"
                req = urllib.request.Request(music_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
                    f.write(resp.read())
                print(f"[audio_mixer] downloaded music: {dest.name} ({track.get('name')} by {track.get('artist_name')})", file=sys.stderr)
                return dest
        except Exception as e:
            print(f"[audio_mixer] music fetch failed: {e}", file=sys.stderr)

    print("[audio_mixer] no background music available (drop mp3 into assets/music/)", file=sys.stderr)
    return None


def mix_music(video_path: Path, music_path: Path, output_path: Path) -> Path:
    """Loop music to video duration, mix at MUSIC_VOLUME_DB under existing audio."""
    vid_dur = get_duration(str(video_path))
    music_dur = get_duration(str(music_path))

    # How many loops needed
    loops = max(1, int(vid_dur / music_dur) + 2)

    run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", str(loops), "-i", str(music_path),
        "-filter_complex",
        (
            f"[1:a]atrim=0:{vid_dur:.3f},"
            f"afade=t=in:st=0:d=2,"
            f"afade=t=out:st={max(0, vid_dur - 3):.3f}:d=3,"
            f"volume={MUSIC_VOLUME_DB}dB[music];"
            f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        ),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(output_path),
    ], "mix background music")
    return output_path


# ── Sound effects ─────────────────────────────────────────────────────────────

def _generate_sfx(sfx_name: str, output: Path) -> Path | None:
    """Generate a synthetic SFX via ffmpeg if no file exists."""
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    generators = {
        "whoosh.mp3": (
            "aevalsrc=sin(2*PI*t*800)*exp(-t*8):s=44100:d=0.4,"
            "afade=t=out:st=0.2:d=0.2,"
            f"volume={SFX_VOLUME_DB}dB"
        ),
        "impact.mp3": (
            "aevalsrc=sin(2*PI*t*200)*exp(-t*5)+sin(2*PI*t*400)*exp(-t*10):s=44100:d=0.5,"
            f"volume={SFX_VOLUME_DB}dB"
        ),
        "ding.mp3": (
            "aevalsrc=sin(2*PI*t*1200)*exp(-t*4):s=44100:d=0.6,"
            f"volume={SFX_VOLUME_DB}dB"
        ),
        "tick.mp3": (
            "aevalsrc=sin(2*PI*t*2000)*exp(-t*30):s=44100:d=0.1,"
            f"volume={SFX_VOLUME_DB}dB"
        ),
        "soft_appear.mp3": (
            "aevalsrc=sin(2*PI*t*900)*exp(-t*6):s=44100:d=0.4,"
            "afade=t=in:st=0:d=0.1,"
            f"volume={SFX_VOLUME_DB}dB"
        ),
    }
    gen = generators.get(sfx_name)
    if not gen:
        return None
    try:
        run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", gen,
            str(output),
        ], f"generate SFX {sfx_name}")
        return output
    except Exception:
        return None


def get_sfx_path(sfx_name: str) -> Path | None:
    """Return path to SFX file, generating synthetic one if missing."""
    if not sfx_name:
        return None
    path = SFX_DIR / sfx_name
    if path.exists() and path.stat().st_size > 100:
        return path
    return _generate_sfx(sfx_name, path)


def mix_sfx(video_path: Path, scenes: list[dict], output_path: Path) -> Path:
    """
    Mix sound effects into video at scene start timestamps.
    Each scene type maps to an SFX file. Remotion/text scenes get SFX; B-roll doesn't.
    """
    # Build per-scene timestamps
    sfx_events: list[tuple[float, Path]] = []
    cursor = 0.0
    for scene in scenes:
        scene_type = scene.get("type", "remotion")
        template = scene.get("template", scene_type)
        duration_s = float(scene.get("duration_s", 5.0))

        # Resolve SFX
        sfx_key = SCENE_SFX.get(template) or SCENE_SFX.get(scene_type)
        if sfx_key:
            sfx_path = get_sfx_path(sfx_key)
            if sfx_path:
                sfx_events.append((cursor, sfx_path))

        cursor += duration_s

    if not sfx_events:
        print("[audio_mixer] no SFX events, skipping", file=sys.stderr)
        import shutil
        shutil.copy2(str(video_path), str(output_path))
        return output_path

    # Build ffmpeg filter: delay each SFX to its timestamp, then amix all
    inputs = ["-i", str(video_path)]
    for _, sfx_path in sfx_events:
        inputs += ["-i", str(sfx_path)]

    # Each SFX input gets delayed to scene start
    filter_parts = []
    mix_labels = ["[0:a]"]
    for idx, (start_s, _) in enumerate(sfx_events):
        delay_ms = int(start_s * 1000)
        label = f"[sfx{idx}]"
        filter_parts.append(
            f"[{idx + 1}:a]adelay={delay_ms}|{delay_ms},volume={SFX_VOLUME_DB}dB{label}"
        )
        mix_labels.append(label)

    n_inputs = len(mix_labels)
    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={n_inputs}:duration=first:dropout_transition=0[aout]"
    )

    run(
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex", ";".join(filter_parts),
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ],
        "mix sound effects",
    )
    return output_path


# ── Combined entry point ──────────────────────────────────────────────────────

def add_audio(
    video_path: Path,
    output_path: Path,
    scenes: list[dict] | None = None,
    mood: str = "cinematic",
    skip_music: bool = False,
    skip_sfx: bool = False,
) -> Path:
    """
    Full audio post-processing:
    1. Mix sound effects at scene boundaries (if scenes provided)
    2. Mix background music under everything
    """
    video_path = Path(video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    vid_dur = get_duration(str(video_path))
    current = video_path

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # Step 1: SFX
        if not skip_sfx and scenes:
            sfx_out = tmp / "with_sfx.mp4"
            try:
                mix_sfx(current, scenes, sfx_out)
                if sfx_out.exists() and sfx_out.stat().st_size > 10000:
                    current = sfx_out
                    print(f"[audio_mixer] SFX mixed: {len(scenes)} scenes", file=sys.stderr)
            except Exception as e:
                print(f"[audio_mixer] SFX failed (skipping): {e}", file=sys.stderr)

        # Step 2: Background music
        if not skip_music:
            music_path = fetch_music(mood=mood, duration_s=vid_dur)
            if music_path:
                music_out = tmp / "with_music.mp4"
                try:
                    mix_music(current, music_path, music_out)
                    if music_out.exists() and music_out.stat().st_size > 10000:
                        current = music_out
                        print(f"[audio_mixer] music mixed: {music_path.name}", file=sys.stderr)
                except Exception as e:
                    print(f"[audio_mixer] music mix failed (skipping): {e}", file=sys.stderr)

        import shutil
        shutil.copy2(str(current), str(output_path))

    print(f"[audio_mixer] done: {output_path}", file=sys.stderr)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Mix background music + SFX into video")
    parser.add_argument("video", help="Input video path")
    parser.add_argument("--storyboard", help="Storyboard JSON for SFX timing")
    parser.add_argument("--output", required=True, help="Output video path")
    parser.add_argument("--mood", default="cinematic", help="Music mood: cinematic/upbeat/minimal/dramatic")
    parser.add_argument("--no-music", action="store_true")
    parser.add_argument("--no-sfx", action="store_true")
    args = parser.parse_args()

    scenes = None
    if args.storyboard:
        data = json.loads(Path(args.storyboard).read_text())
        scenes = data.get("scenes", [])

    add_audio(
        Path(args.video),
        Path(args.output),
        scenes=scenes,
        mood=args.mood,
        skip_music=args.no_music,
        skip_sfx=args.no_sfx,
    )


if __name__ == "__main__":
    main()
