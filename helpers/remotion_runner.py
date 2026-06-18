#!/usr/bin/env python3
"""
Renders individual Remotion compositions to MP4 clips.

Usage:
    python helpers/remotion_runner.py --composition KineticText --props '{"text":"Hello","duration_s":5}' --output clip.mp4
    python helpers/remotion_runner.py --scene scene.json --output clip.mp4
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REMOTION_DIR = Path(__file__).parent.parent / "remotion"
ENTRY = REMOTION_DIR / "src" / "index.ts"

COMPOSITION_MAP = {
    "kinetic_text": "KineticText",
    "title_card": "TitleCard",
    "definition_card": "DefinitionCard",
    "stat_card": "StatCard",
    "quote_card": "QuoteCard",
    "timeline": "Timeline",
    "list_reveal": "ListReveal",
    "split_comparison": "SplitComparison",
    "caption_bar": "CaptionBar",
    "kinetic_typography": "KineticTypography",
    "quick_zoom": "QuickZoom",
    "map_highlight": "MapHighlight",
    "fact_counter": "FactCounter",
}


def render_composition(
    composition_id: str,
    props: dict,
    output_path: Path,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    crf: int = 18,
) -> Path:
    """Render a single Remotion composition to MP4."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Normalize composition ID (snake_case → PascalCase)
    comp_id = COMPOSITION_MAP.get(composition_id, composition_id)

    # Write props to temp file (avoids shell escaping issues with long JSON)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(props, f)
        props_file = f.name

    try:
        cmd = [
            "npx", "remotion", "render",
            str(ENTRY),
            comp_id,
            str(output_path.resolve()),
            f"--props={props_file}",
            f"--codec=h264",
            f"--crf={crf}",
            f"--pixel-format=yuv420p",
        ]

        print(f"[remotion] render {comp_id} → {output_path.name}", file=sys.stderr)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(REMOTION_DIR),
        )

        if result.returncode != 0:
            print(f"[remotion] STDERR: {result.stderr[-600:]}", file=sys.stderr)
            raise RuntimeError(f"Remotion render failed: {comp_id}")

        print(f"[remotion] done: {output_path.name} ({output_path.stat().st_size // 1024}KB)", file=sys.stderr)
        return output_path

    finally:
        os.unlink(props_file)


def render_scene(scene: dict, output_path: Path) -> Path:
    """Render a storyboard scene dict to MP4."""
    template = scene.get("template", scene.get("type", ""))
    props = scene.get("props", {})
    props.setdefault("duration_s", scene.get("duration_s", 5.0))
    return render_composition(template, props, output_path)


def main():
    parser = argparse.ArgumentParser(description="Render Remotion composition to MP4")
    parser.add_argument("--composition", help="Composition ID (e.g. KineticText)")
    parser.add_argument("--scene", help="Scene JSON file")
    parser.add_argument("--props", help="JSON props string")
    parser.add_argument("--props-file", help="JSON props file path")
    parser.add_argument("--output", required=True, help="Output MP4 path")
    parser.add_argument("--crf", type=int, default=18)
    args = parser.parse_args()

    output = Path(args.output)

    if args.scene:
        scene = json.loads(Path(args.scene).read_text())
        render_scene(scene, output)
    elif args.composition:
        props = {}
        if args.props:
            props = json.loads(args.props)
        elif args.props_file:
            props = json.loads(Path(args.props_file).read_text())
        render_composition(args.composition, props, output, crf=args.crf)
    else:
        parser.error("Provide --composition or --scene")


if __name__ == "__main__":
    main()
