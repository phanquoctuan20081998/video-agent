#!/usr/bin/env python3
"""
AI image generation for video B-roll.
Provider priority: OpenRouter Gemini 2.5 Flash Image → Together AI (Flux) → OpenAI DALL-E → black fallback.

Cheapest: OpenRouter google/gemini-2.5-flash-image (~$0.04/image, uses existing OPENROUTER_API_KEY).

Usage:
    python helpers/image_gen.py --prompt "cinematic shot of..." --output frame.png
"""

import argparse
import base64
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()


def generate_image(prompt: str, output_path: Path, width: int = 1920, height: int = 1080) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for provider in (_try_together_flux, _try_openrouter_gemini, _try_openai_dalle):
        try:
            result = provider(prompt, output_path, width, height)
            if result and result.exists() and result.stat().st_size > 1000:
                size_kb = result.stat().st_size // 1024
                print(f"[image_gen] {provider.__name__}: {output_path.name} ({size_kb}KB)", file=sys.stderr)
                return result
        except Exception as e:
            print(f"[image_gen] {provider.__name__} failed: {e}", file=sys.stderr)

    _black_fallback(output_path, width, height)
    print(f"[image_gen] fallback black frame: {output_path.name}", file=sys.stderr)
    return output_path


def _try_openrouter_gemini(prompt: str, output_path: Path, width: int, height: int) -> Path:
    """OpenRouter + Gemini 2.5 Flash Image — uses existing OPENROUTER_API_KEY (~$0.04/image)."""
    import httpx

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")

    resp = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/video-agent",
        },
        json={
            "model": "google/gemini-2.5-flash-image",
            "messages": [{"role": "user", "content": f"Generate an image of: {prompt}. Output only the image, no text."}],
            "max_tokens": 4096,
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()

    msg = data["choices"][0]["message"]
    images = msg.get("images", [])
    if not images:
        raise ValueError("No images in response (model returned text only)")

    url = images[0]["image_url"]["url"]  # data:image/png;base64,...
    header, b64 = url.split(",", 1)
    ext = header.split("/")[1].split(";")[0]  # png or jpeg

    out = output_path.with_suffix(f".{ext}")
    out.write_bytes(base64.b64decode(b64))
    return out


def _try_together_flux(prompt: str, output_path: Path, width: int, height: int) -> Path:
    """Together AI FLUX.1-schnell-Free — FREE tier, rate-limited. No cost."""
    import httpx

    api_key = os.getenv("TOGETHER_API_KEY", "")
    if not api_key:
        raise ValueError("TOGETHER_API_KEY not set")

    resp = httpx.post(
        "https://api.together.xyz/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "black-forest-labs/FLUX.1-schnell-Free",
            "prompt": prompt,
            "width": 1280,
            "height": 720,
            "steps": 4,
            "n": 1,
            "response_format": "b64_json",
        },
        timeout=60,
    )
    resp.raise_for_status()
    b64 = resp.json()["data"][0]["b64_json"]
    output_path.write_bytes(base64.b64decode(b64))
    return output_path


def _try_openai_dalle(prompt: str, output_path: Path, width: int, height: int) -> Path:
    """OpenAI DALL-E 3 (~$0.04/image)."""
    import httpx

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set")

    resp = httpx.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "dall-e-3",
            "prompt": prompt,
            "size": "1792x1024",
            "quality": "standard",
            "n": 1,
            "response_format": "b64_json",
        },
        timeout=60,
    )
    resp.raise_for_status()
    b64 = resp.json()["data"][0]["b64_json"]
    output_path.write_bytes(base64.b64decode(b64))
    return output_path


def _black_fallback(output_path: Path, width: int, height: int) -> None:
    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=black:s={width}x{height}",
        "-vframes", "1", str(output_path.with_suffix(".png")),
    ], capture_output=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args()

    result = generate_image(args.prompt, Path(args.output), args.width, args.height)
    print(str(result))


if __name__ == "__main__":
    main()
