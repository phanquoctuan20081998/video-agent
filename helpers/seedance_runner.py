#!/usr/bin/env python3
"""
Seedance video generation — hard cap at 10s per clip.
Uses ByteDance Volcengine API (or VolcEngine proxy).

Usage:
    python helpers/seedance_runner.py --prompt "cinematic shot of..." --duration 6 --output clip.mp4
"""

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

MAX_DURATION_S = 10  # Hard cap — prevents runaway cost


def generate_clip(
    prompt: str,
    output_path: Path,
    duration_s: float = 5.0,
    aspect_ratio: str = "16:9",
    motion_level: str = "medium",
) -> Path:
    """Generate a video clip with Seedance. Caps duration at 10s."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Hard cap
    if duration_s > MAX_DURATION_S:
        print(f"[seedance] capping duration {duration_s}s → {MAX_DURATION_S}s", file=sys.stderr)
        duration_s = MAX_DURATION_S

    api_key = os.getenv("SEEDANCE_API_KEY") or os.getenv("VOLCENGINE_API_KEY", "")
    if not api_key:
        raise ValueError("SEEDANCE_API_KEY or VOLCENGINE_API_KEY not set in .env")

    for attempt in (_try_volcengine, _try_openrouter_seedance):
        try:
            return attempt(prompt, output_path, duration_s, aspect_ratio, api_key)
        except Exception as e:
            print(f"[seedance] {attempt.__name__} failed: {e}", file=sys.stderr)

    raise RuntimeError("All Seedance providers failed")


def _try_volcengine(prompt: str, output_path: Path, duration_s: float, aspect_ratio: str, api_key: str) -> Path:
    """ByteDance Volcengine API — primary Seedance endpoint."""
    import httpx

    # Volcengine video generation API
    # Docs: https://www.volcengine.com/docs/82379
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Submit generation job
    resp = httpx.post(
        "https://api.volcengine.com/visual/v2/video_generation/submit",
        headers=headers,
        json={
            "model_id": "seedance-1-lite",  # or seedance-1-pro
            "prompt": prompt,
            "duration": int(duration_s),
            "aspect_ratio": aspect_ratio,
            "motion_level": motion_level if "motion_level" in dir() else "medium",
        },
        timeout=30,
    )
    resp.raise_for_status()
    task_id = resp.json().get("data", {}).get("task_id", "")
    if not task_id:
        raise ValueError(f"No task_id returned: {resp.text[:200]}")

    print(f"[seedance] task submitted: {task_id}", file=sys.stderr)

    # Poll for result (max 5 min)
    for poll in range(60):
        time.sleep(5)
        poll_resp = httpx.get(
            "https://api.volcengine.com/visual/v2/video_generation/result",
            headers=headers,
            params={"task_id": task_id},
            timeout=30,
        )
        poll_resp.raise_for_status()
        data = poll_resp.json().get("data", {})
        status = data.get("status", "")

        if status == "succeeded":
            video_url = data.get("video_url", "")
            if not video_url:
                raise ValueError("succeeded but no video_url")
            _download(video_url, output_path)
            print(f"[seedance] done: {output_path.name}", file=sys.stderr)
            return output_path
        elif status == "failed":
            raise RuntimeError(f"Seedance task failed: {data.get('message', 'unknown')}")
        else:
            print(f"[seedance] poll {poll+1}/60: {status}", file=sys.stderr)

    raise TimeoutError("Seedance generation timed out after 5 minutes")


def _try_openrouter_seedance(prompt: str, output_path: Path, duration_s: float, aspect_ratio: str, api_key: str) -> Path:
    """OpenRouter proxy for Seedance (if available)."""
    import httpx

    or_key = os.getenv("OPENROUTER_API_KEY", "")
    if not or_key:
        raise ValueError("OPENROUTER_API_KEY not set")

    resp = httpx.post(
        "https://openrouter.ai/api/v1/video/generate",
        headers={
            "Authorization": f"Bearer {or_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "bytedance/seedance-2.0",
            "prompt": prompt,
            "duration": int(duration_s),
            "aspect_ratio": aspect_ratio,
        },
        timeout=300,
    )
    resp.raise_for_status()
    video_url = resp.json().get("url", "")
    if not video_url:
        raise ValueError("No URL in response")
    _download(video_url, output_path)
    return output_path


def _download(url: str, dest: Path) -> None:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())
    print(f"[seedance] downloaded: {dest.name} ({dest.stat().st_size // 1024}KB)", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Seedance video generation (max 10s)")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=float, default=5.0, help=f"Seconds (max {MAX_DURATION_S})")
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--motion", default="medium", choices=["low", "medium", "high"])
    args = parser.parse_args()

    result = generate_clip(args.prompt, Path(args.output), args.duration, args.aspect_ratio)
    print(str(result))


if __name__ == "__main__":
    main()
