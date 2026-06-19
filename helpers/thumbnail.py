#!/usr/bin/env python3
"""
AI thumbnail generator with a fixed channel template.

The model creates the background image only. The final YouTube thumbnail layout
is rendered locally so every video keeps the same recognizable format.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

STYLE_VERSION = "geo_thumbnail_template_v1"
WIDTH = 1280
HEIGHT = 720

BRAND_LABEL = os.getenv("THUMBNAIL_BRAND_LABEL", "DIA LY 60S")
ACCENT_HEX = os.getenv("THUMBNAIL_ACCENT_COLOR", "#FFD23F")
SERIES_LABEL = os.getenv("THUMBNAIL_SERIES_LABEL", "GEO EXPLAINER")


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    if not re.match(r"^#[0-9a-fA-F]{6}$", value or ""):
        value = "#FFD23F"
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    preferred = candidates[0] if bold else candidates[1]
    for path in [preferred, *candidates]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_size(draw, text: str, font) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _wrap_headline(draw, text: str, font, max_width: int, max_lines: int = 3) -> list[str]:
    clean = re.sub(r"\s+", " ", text.strip())
    if not clean:
        return ["BAN DO", "BAT NGO"]

    words = clean.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if _text_size(draw, candidate, font)[0] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
        lines[-1] = lines[-1].rstrip(" .,!?:;") + "..."
    return lines


def _thumbnail_text(metadata: dict, concept: dict) -> str:
    value = (
        metadata.get("thumbnail_text")
        or concept.get("thumbnail_text")
        or concept.get("title")
        or metadata.get("title")
        or "Ban do bat ngo"
    )
    value = re.sub(r"[\"'`]", "", str(value)).strip()
    return value.upper()


def _image_prompt(metadata: dict, concept: dict, topic: str) -> str:
    title = metadata.get("title") or concept.get("title") or topic
    thumbnail_concept = concept.get("thumbnail_concept") or ""
    hook = concept.get("hook") or ""
    keywords = ", ".join((concept.get("keywords") or [])[:6])
    return (
        "Create a YouTube thumbnail background image for a fast Vietnamese geography "
        "explainer channel. Use the same house style every time: cinematic "
        "photoreal satellite-map collage, one clear central geographic subject, "
        "deep teal shadows, warm golden highlights, high contrast, clean negative "
        "space on the left for large text, sharp editorial lighting, premium "
        "documentary feel. No text, no letters, no logos, no watermark, no UI, "
        "no captions. "
        f"Video title: {title}. Hook: {hook}. Thumbnail idea: {thumbnail_concept}. "
        f"Keywords: {keywords}."
    )


def _fallback_background(output_path: Path) -> Path:
    from PIL import Image, ImageDraw

    accent = _hex_to_rgb(ACCENT_HEX)
    image = Image.new("RGB", (WIDTH, HEIGHT), (7, 24, 34))
    px = image.load()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            t = x / WIDTH
            u = y / HEIGHT
            r = int(7 + 18 * t + accent[0] * 0.08 * (1 - u))
            g = int(24 + 40 * t + accent[1] * 0.06 * (1 - u))
            b = int(34 + 35 * t)
            px[x, y] = (min(r, 255), min(g, 255), min(b, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    for i in range(9):
        x = 720 + i * 52
        draw.ellipse((x, 110 + i * 8, x + 320, 430 + i * 8), outline=(*accent, 45), width=5)
    image.save(output_path)
    return output_path


def _generate_background(prompt: str, work_dir: Path) -> Path:
    from helpers.image_gen import generate_image

    raw_path = work_dir / "thumbnail_ai_background.png"
    result = generate_image(prompt, raw_path, width=WIDTH, height=HEIGHT)
    if not result.exists() or result.stat().st_size < 1000:
        return _fallback_background(raw_path)
    return result


def _compose_thumbnail(background_path: Path, output_path: Path, metadata: dict, concept: dict) -> Path:
    from PIL import Image, ImageDraw, ImageFilter

    accent = _hex_to_rgb(ACCENT_HEX)
    bg = Image.open(background_path).convert("RGB")
    bg = bg.resize((WIDTH, HEIGHT))

    image = bg.convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")

    # Fixed readability treatment: left-side dark panel, right-side subject remains visible.
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    opx = overlay.load()
    for x in range(WIDTH):
        alpha = int(max(0, 235 * (1 - x / 760)))
        for y in range(HEIGHT):
            opx[x, y] = (4, 10, 16, alpha)
    image.alpha_composite(overlay)

    # Consistent frame language.
    draw.rectangle((0, 0, WIDTH, 12), fill=(*accent, 255))
    draw.rectangle((0, HEIGHT - 18, WIDTH, HEIGHT), fill=(*accent, 255))
    draw.rectangle((46, 38, 285, 88), fill=(*accent, 255))
    draw.text((66, 49), BRAND_LABEL, font=_font(25, bold=True), fill=(7, 17, 24, 255))
    draw.rounded_rectangle((1015, 38, 1234, 88), radius=5, fill=(7, 17, 24, 220), outline=(*accent, 255), width=3)
    draw.text((1035, 50), SERIES_LABEL, font=_font(23, bold=True), fill=(245, 249, 252, 255))

    headline = _thumbnail_text(metadata, concept)
    font_size = 88
    while font_size >= 54:
        font = _font(font_size, bold=True)
        lines = _wrap_headline(draw, headline, font, max_width=610, max_lines=3)
        total_h = len(lines) * int(font_size * 1.05)
        if total_h <= 320 and all(_text_size(draw, line, font)[0] <= 640 for line in lines):
            break
        font_size -= 4

    y = 210
    for line in lines:
        draw.text(
            (58, y),
            line,
            font=font,
            fill=(250, 252, 255, 255),
            stroke_width=5,
            stroke_fill=(0, 0, 0, 210),
        )
        y += int(font_size * 1.05)

    # Small recurring visual signature under the headline.
    draw.rounded_rectangle((58, 575, 430, 622), radius=4, fill=(7, 17, 24, 220))
    draw.text((78, 586), "BAN DO  |  SU THAT  |  60S", font=_font(24, bold=True), fill=(*accent, 255))

    # Gentle final sharpening for social feed compression.
    image = image.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, quality=92, optimize=True)
    return output_path


def _fingerprint(metadata: dict, concept: dict, topic: str) -> str:
    payload = {
        "style": STYLE_VERSION,
        "metadata": metadata,
        "concept": concept,
        "topic": topic,
        "brand": BRAND_LABEL,
        "series": SERIES_LABEL,
        "accent": ACCENT_HEX,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_thumbnail(
    metadata: dict,
    concept: dict | None,
    work_dir: Path,
    topic: str = "",
    output_path: Path | None = None,
) -> Path:
    work_dir = Path(work_dir)
    concept = concept or {}
    output_path = Path(output_path or work_dir / "thumbnail.jpg")
    meta_path = output_path.with_suffix(output_path.suffix + ".meta.json")
    fingerprint = _fingerprint(metadata, concept, topic)

    if output_path.exists() and meta_path.exists():
        try:
            if json.loads(meta_path.read_text()).get("fingerprint") == fingerprint:
                return output_path
        except json.JSONDecodeError:
            pass

    prompt = _image_prompt(metadata, concept, topic)
    try:
        bg_path = _generate_background(prompt, work_dir)
    except Exception as e:
        print(f"[thumbnail] image model failed, using template fallback: {e}", file=sys.stderr)
        bg_path = _fallback_background(work_dir / "thumbnail_ai_background.png")

    result = _compose_thumbnail(bg_path, output_path, metadata, concept)
    meta_path.write_text(json.dumps({
        "fingerprint": fingerprint,
        "style_version": STYLE_VERSION,
        "prompt": prompt,
        "background": str(bg_path),
    }, indent=2, ensure_ascii=False))
    print(f"[thumbnail] generated: {result}", file=sys.stderr)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--concept")
    parser.add_argument("--topic", default="")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    metadata = json.loads(Path(args.metadata).read_text())
    concept = json.loads(Path(args.concept).read_text()) if args.concept else {}
    result = generate_thumbnail(
        metadata=metadata,
        concept=concept,
        work_dir=Path(args.work_dir),
        topic=args.topic,
        output_path=Path(args.output) if args.output else None,
    )
    print(str(result))


if __name__ == "__main__":
    main()
