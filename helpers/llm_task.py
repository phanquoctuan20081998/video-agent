#!/usr/bin/env python3
"""
OpenRouter task router — delegates specific tasks to optimal cheap models.
Claude Code orchestrates; this handles bulk LLM work.

Usage:
    python helpers/llm_task.py --task generate_script --input topic.txt --output script.txt
    python helpers/llm_task.py --task generate_edl --input takes_packed.md --context concept.json --output edl.json
    python helpers/llm_task.py --task analyze_content --input topic.txt
    python helpers/llm_task.py --task generate_seo --input script.txt --output metadata.json
    python helpers/llm_task.py --task generate_concept --input topic.txt --output concept.json
"""

import argparse
import json
import os
import sys
import httpx
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

TASK_MODELS = {
    "generate_script": "meta-llama/llama-3.3-70b-instruct",
    "generate_edl": "deepseek/deepseek-r1",
    "generate_storyboard": "deepseek/deepseek-r1",
    "generate_hybrid_storyboard": "deepseek/deepseek-r1",
    "analyze_content": "google/gemini-flash-1.5",
    "generate_seo": "meta-llama/llama-3.1-8b-instruct",
    "generate_concept": "meta-llama/llama-3.3-70b-instruct",
}

TASK_PROMPTS = {
    "generate_concept": """You are a YouTube content strategist.
Given a topic, generate a video concept as JSON with keys:
- title: catchy YouTube title (max 70 chars)
- hook: opening 5 seconds description
- structure: list of sections with labels and descriptions
- target_duration_s: suggested duration in seconds
- keywords: list of 10 SEO keywords
- thumbnail_concept: one-sentence thumbnail description

Topic: {input}

If the topic is Vietnamese or asks for a geography channel/style, prefer Vietnamese titles,
fast geography/listicle structure, surprising facts, map-friendly beats, and thumbnail ideas
with satellite maps, highlighted country shapes, big yellow-white text.

Respond ONLY with valid JSON.""",

    "generate_script": """You are a professional YouTube scriptwriter.
Given a video concept, write a voiceover script optimized for {duration}s.

Rules:
- Natural spoken language (not written text)
- Each paragraph = one breath / scene cut
- Hook in first 5 seconds
- No filler intros like "Hey guys welcome back"
- End with clear CTA
- Match the concept language. If Vietnamese, write natural Vietnamese narration.
- Vietnamese output must use only Vietnamese Quốc ngữ text; do not include Chinese characters.
- For geography/listicle concepts, use short punchy facts, vivid comparisons, and map-friendly visual cues.

Concept:
{input}

Return ONLY the script text the narrator will speak. No JSON. No metadata. No labels.
No "Estimated duration", no "Scene count", no section headers. Just the spoken words.""",

    "generate_edl": """You are a professional video editor AI.
Given a packed transcript of video takes, generate an EDL (Edit Decision List) as JSON.

Rules:
- Never cut inside a word (snap to transcript boundaries)
- Pad cut edges 30-200ms
- Prefer silence gaps ≥400ms for cuts
- Label each range with a beat (HOOK, PROBLEM, SOLUTION, BENEFIT, EXAMPLE, CTA)
- Include quote (verbatim words) and reason for each cut
- CRITICAL: Each source may appear at most ONCE in ranges. Spread ranges evenly across ALL available sources.
- Do NOT reuse the same source for multiple ranges — viewers will notice repeated footage.
- Assign sources in round-robin order (stock_0 for range 1, stock_1 for range 2, etc.)
- If more ranges than sources, loop sources but ensure no two consecutive ranges share a source.

Packed transcript:
{input}

{context_section}

Output JSON matching this schema:
{{
  "version": 1,
  "sources": {{"<name>": "<abs_path>"}},
  "ranges": [
    {{
      "source": "<name>",
      "start": 0.0,
      "end": 0.0,
      "beat": "<label>",
      "quote": "<verbatim>",
      "reason": "<why>"
    }}
  ],
  "grade": "warm_cinematic",
  "overlays": [],
  "subtitles": null,
  "total_duration_s": 0.0
}}

Respond ONLY with valid JSON.""",

    "analyze_content": """Analyze this video topic and return JSON with:
- target_audience: description
- content_angle: unique angle to approach this topic
- competitor_gaps: what existing videos miss
- viral_hooks: list of 5 potential hook angles
- recommended_duration_s: optimal video length

Topic: {input}

Respond ONLY with valid JSON.""",

    "generate_storyboard": """You are a video storyboard director for Vox-style explainer videos.
Given a script and concept, break the narration into scenes and assign each scene a visual template.

Available templates:
- kinetic_text: Large animated words flying in. Props: text, accent_words[], font_size
- title_card: Big title + subtitle slide. Props: title, subtitle
- definition_card: Word definition reveal. Props: term, definition, etymology
- stat_card: Animated number/stat. Props: value, label, context, prefix, suffix
- quote_card: Styled quote with attribution. Props: quote, attribution, year
- timeline: Horizontal timeline. Props: title, events[{{year,label,highlight}}], active_index
- list_reveal: Items appear one by one. Props: title, items[], style(bullet|numbered|check)
- split_comparison: Left vs right. Props: title, left_label, left_items[], right_label, right_items[]
- quick_zoom: Ken Burns zoom on image. Props: image_url, caption, zoom_start, zoom_end
- broll_stock: Stock video clip. Props: query (for video search), caption
- broll_ai_image: AI image → Ken Burns zoom. CHEAP ($0.01). Props: prompt (image description), caption, zoom_start, zoom_end
- broll_ai_video: Seedance video. VERY EXPENSIVE. MAX 5s per clip. MAX 2 clips per video. Only for fire, explosions, crowds, flowing water — motion impossible to fake with image+zoom. Props: prompt (cinematic video description), caption

HARD BUDGET RULE — $1 total per video:
- broll_ai_video: MAX 2 clips × 5s = $1.00. Do not exceed. If not essential, use broll_ai_image instead.
- broll_ai_image: FREE (Together Flux). Use freely for any static or slow scene.
- Remotion templates: FREE. Prefer these for 60%+ of scenes.
- broll_stock: FREE. Use for generic B-roll (city, nature, people working).

Priority order: remotion → broll_stock → broll_ai_image → broll_ai_video (last resort, max 2)

Other rules:
- Each scene 3-8 seconds
- Total duration matches script (~{duration}s)
- First scene: kinetic_text or title_card (hook)
- Last scene: kinetic_text or list_reveal (CTA)
- Assign narration text to each scene so they add up to the full script
- Set style.music_mood to one of: cinematic, upbeat, minimal, dramatic

Script:
{input}

Concept context:
{context_section}

Return JSON:
{{
  "version": 2,
  "style": {{
    "accent_color": "#FFCC00",
    "bg_color": "#0A0A0A",
    "font": "Inter",
    "music_mood": "cinematic"
  }},
  "scenes": [
    {{
      "id": "s001",
      "type": "remotion",
      "template": "kinetic_text",
      "duration_s": 5.0,
      "narration": "exact narration text for this scene",
      "props": {{"text": "...", "accent_words": []}}
    }},
    {{
      "id": "s002",
      "type": "broll_ai_image",
      "duration_s": 6.0,
      "narration": "narration for this scene",
      "props": {{"prompt": "cinematic image description", "caption": "overlay text", "zoom_start": 1.02, "zoom_end": 1.18}}
    }},
    {{
      "id": "s003",
      "type": "broll_ai_video",
      "duration_s": 8.0,
      "narration": "narration for motion scene (MUST need real video motion)",
      "props": {{"prompt": "cinematic video description with motion", "caption": "overlay text"}}
    }}
  ]
}}

Respond ONLY with valid JSON.""",

    "generate_hybrid_storyboard": """You are a senior YouTube geography-video director.
Build the most engaging hybrid storyboard from a script, using EDL timing and stock sources when useful.

Goal:
- Make a fast, information-dense geography/listicle explainer.
- If the script/topic is Vietnamese or about a Vietnamese reference channel, write onscreen text in Vietnamese.
- Mix motion graphics, map scenes, fact cards, stock clips, and AI images.
- Do NOT make every scene stock footage. Use stock for realism, Remotion for clarity, AI images for hard-to-source geography visuals.

Available Remotion templates:
- kinetic_text: Large animated hook words. Props: text, accent_words[], font_size
- title_card: Big title + subtitle. Props: title, subtitle
- map_highlight: Satellite/map-style country callout. Props: region, headline, subline, callouts[], marker_label
- fact_counter: Numbered fact card. Props: fact_number, headline, detail, tag
- definition_card: Props: term, definition, etymology
- stat_card: Props: value, label, context, prefix, suffix
- timeline: Props: title, events[{{year,label,highlight}}], active_index
- list_reveal: Props: title, items[], style
- split_comparison: Props: title, left_label, left_items[], right_label, right_items[]
- quick_zoom: Props: image_url, caption, zoom_start, zoom_end

Other scene types:
- broll_stock: Use real stock/EDL source. Props: query, caption, source_url OR source
- broll_ai_image: AI image + Ken Burns. Props: prompt, caption, zoom_start, zoom_end
- broll_ai_video: Seedance video, expensive. MAX 2 clips, max 5s each. Props: prompt, caption

Hard rules:
- Each scene should be 3-7 seconds.
- First 5 seconds must hook with kinetic_text, map_highlight, or title_card.
- Use map_highlight or fact_counter at least 35% of scenes for geography/listicle topics.
- Use broll_stock for scenes where the EDL/stock source clearly matches the narration.
- When using EDL stock, set props.source to an existing key from context.stock_sources OR props.source_url to a direct URL.
- Use broll_ai_video only for motion that cannot be faked by image+zoom, maximum 2 scenes.
- Keep text punchy: headline max 9 words, detail max 16 words.
- Avoid unverifiable claims unless they are in the script.
- Scene narration texts must concatenate to the full script in order.
- Total duration should be close to {duration}s, but voiceover will be the source of truth.

Script:
{input}

Context with concept, EDL ranges, and stock sources:
{context_section}

Return ONLY valid JSON:
{{
  "version": 3,
  "style": {{
    "accent_color": "#FFD400",
    "bg_color": "#080808",
    "font": "Inter",
    "music_mood": "cinematic"
  }},
  "scenes": [
    {{
      "id": "s001",
      "type": "remotion",
      "template": "map_highlight",
      "duration_s": 5.0,
      "narration": "exact narration",
      "props": {{"region":"Ấn Độ","headline":"Không chỉ là một quốc gia","subline":"Mà như một hành tinh riêng","callouts":["1.4 tỷ dân","sa mạc tới Himalaya"],"marker_label":"INDIA"}}
    }},
    {{
      "id": "s002",
      "type": "remotion",
      "template": "fact_counter",
      "duration_s": 4.0,
      "narration": "exact narration",
      "props": {{"fact_number":"01","headline":"Một lục địa thu nhỏ","detail":"Khí hậu thay đổi cực mạnh chỉ trong một quốc gia","tag":"ĐỊA LÝ"}}
    }},
    {{
      "id": "s003",
      "type": "broll_stock",
      "duration_s": 5.0,
      "narration": "exact narration",
      "props": {{"source":"stock_0","query":"India aerial landscape","caption":"Ấn Độ nhìn từ trên cao"}}
    }}
  ]
}}""",

    "generate_seo": """You are a YouTube SEO expert.
Given a video script, generate optimized metadata as JSON:
- title: SEO-optimized title (max 70 chars, include main keyword)
- description: full description (300-500 words, first 125 chars are crucial)
- tags: list of 15 tags (mix broad + specific)
- category: YouTube category name
- thumbnail_text: 3-5 words for thumbnail overlay

Script:
{input}

Respond ONLY with valid JSON.""",
}


def call_openrouter(model: str, prompt: str, api_key: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/video-agent",
        "X-Title": "Video Agent",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 8000,
    }
    resp = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def load_input(path_or_str: str) -> str:
    p = Path(path_or_str)
    if p.exists():
        return p.read_text()
    return path_or_str


def extract_json(text: str) -> dict | list:
    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text.startswith(("{", "[")):
        first_obj = text.find("{")
        first_arr = text.find("[")
        starts = [i for i in (first_obj, first_arr) if i >= 0]
        if starts:
            text = text[min(starts):]
    if text.startswith("{") and not text.rstrip().endswith("}"):
        text = text[:text.rfind("}") + 1]
    elif text.startswith("[") and not text.rstrip().endswith("]"):
        text = text[:text.rfind("]") + 1]
    return json.loads(text)


def main():
    parser = argparse.ArgumentParser(description="OpenRouter task router for Video Agent")
    parser.add_argument("--task", required=True, choices=list(TASK_MODELS.keys()))
    parser.add_argument("--input", required=True, help="Input text or file path")
    parser.add_argument("--context", help="Optional context file (for EDL generation)")
    parser.add_argument("--output", help="Output file path (default: stdout)")
    parser.add_argument("--duration", type=int, default=60, help="Target duration in seconds")
    parser.add_argument("--model", help="Override model (default: task-specific)")
    args = parser.parse_args()

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    model = args.model or TASK_MODELS[args.task]
    input_text = load_input(args.input)

    context_section = ""
    if args.context:
        ctx = load_input(args.context)
        context_section = f"Additional context:\n{ctx}"

    prompt_template = TASK_PROMPTS[args.task]
    prompt = prompt_template.format(
        input=input_text,
        duration=args.duration,
        context_section=context_section,
    )

    print(f"[llm_task] task={args.task} model={model}", file=sys.stderr)

    json_tasks = {"generate_concept", "generate_edl", "generate_storyboard", "analyze_content", "generate_seo"}
    result = ""
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        result = call_openrouter(model, prompt, api_key)
        if args.task in json_tasks:
            try:
                parsed = extract_json(result)
                result = json.dumps(parsed, indent=2, ensure_ascii=False)
                break  # valid JSON — done
            except json.JSONDecodeError:
                print(f"[llm_task] attempt {attempt}/{max_retries}: invalid JSON, retrying...", file=sys.stderr)
                if attempt == max_retries:
                    print(f"[llm_task] ERROR: JSON still invalid after {max_retries} attempts", file=sys.stderr)
                    sys.exit(1)
        else:
            break

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(result)
        print(f"[llm_task] written to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
