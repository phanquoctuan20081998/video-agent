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
    "audit_script": "google/gemini-2.5-flash-lite",
    "generate_geo_topics": "google/gemini-2.5-flash-lite",
    "generate_edl": "deepseek/deepseek-r1",
    "generate_storyboard": "deepseek/deepseek-r1",
    "generate_hybrid_storyboard": "deepseek/deepseek-r1",
    "analyze_content": "google/gemini-2.5-flash-lite",
    "generate_seo": "meta-llama/llama-3.1-8b-instruct",
    "generate_concept": "meta-llama/llama-3.3-70b-instruct",
    "generate_overlays": "google/gemini-2.5-flash-lite",
    "verify_stock_relevance": "google/gemini-2.5-flash-lite",
    "generate_search_terms": "google/gemini-2.5-flash-lite",
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

Write the title/hook in the same language as the topic (do not force Vietnamese unless the
topic itself is in Vietnamese).

If the topic is geography/explainer/listicle style (country comparisons, "why does X happen",
geographic facts), use fast-paced structure, surprising statistics, map-friendly beats designed
for map/country-highlight overlays, and thumbnail ideas with satellite maps, highlighted country
shapes, bold text.

Respond ONLY with valid JSON.""",

    "generate_script": """You are a professional YouTube scriptwriter.
Given a video concept, write a voiceover script optimized for {duration}s.

Rules:
- Natural spoken language (not written text)
- Each paragraph = one breath / scene cut
- Hook in first 5 seconds
- No filler intros like "Hey guys welcome back"
- Match the concept language. If Vietnamese, write natural Vietnamese narration.
- Vietnamese output must use only Vietnamese Quốc ngữ text; do not include Chinese characters.
- For geography/listicle concepts, use short punchy facts, vivid comparisons, and map-friendly visual cues.
- Write like a narrator telling a curious little story, not like a school essay or news report.
- Keep the mood bright, playful, and approachable. Make it witty and lightly funny without turning into stand-up comedy.
- Use charming everyday comparisons, small surprises, and conversational pivots like "nghe hơi lạ đúng không", "nhưng khoan", "và đây mới vui".
- Avoid fearmongering, over-dramatic doom language, corporate wording, and stiff textbook phrasing.
- If writing Vietnamese, make it sound like a warm, mischievous Vietnamese storyteller: natural, lively, and easy to speak out loud.
- End with only a soft, short CTA asking viewers to like and subscribe. Do not over-sell it.
- Good Vietnamese ending style: "Nếu thấy câu chuyện này thú vị, nhớ like và đăng ký kênh nhé."

Concept:
{input}

Return ONLY the script text the narrator will speak. No JSON. No metadata. No labels.
No "Estimated duration", no "Scene count", no section headers. Just the spoken words.""",

    "audit_script": """You are a senior YouTube narration editor and script doctor.
Review and correct this generated voiceover script for a {duration}s video.

Your job:
- Preserve the original meaning, structure, language, and approximate length.
- Correct awkward phrasing, grammar, repetition, stiff wording, and unnatural spoken rhythm.
- Make it sound like a warm narrator telling a curious little story.
- Keep the mood bright, playful, witty, and lightly funny without becoming silly or noisy.
- Strengthen the first 5 seconds as a clean hook if needed.
- Keep paragraphs short: each paragraph should feel like one breath / scene cut.
- For Vietnamese, use natural Quốc ngữ only. No Chinese characters. Avoid textbook phrasing.
- For geography/fact scripts, keep claims cautious and do not invent new statistics or facts.
- Remove over-dramatic fearmongering, corporate wording, and generic YouTube filler.
- Ensure the ending has only a short soft CTA to like and subscribe.
- Good Vietnamese ending style: "Nếu thấy câu chuyện này thú vị, nhớ like và đăng ký kênh nhé."

Concept/context:
{context_section}

Draft script:
{input}

Return ONLY the corrected script the narrator will speak. No JSON. No labels. No review notes.""",

    "generate_geo_topics": """You are a YouTube geography channel strategist.
Pick video topics for a Vietnamese-language geography explainer channel.

Goal:
- Find ideas that can become bright, curious, story-driven geography videos.
- Prefer "why/how" topics with a clear map/geography reason.
- Avoid generic trivia, war gore, hateful framing, political propaganda, or doom-only angles.
- Avoid repeating topics already used.
- Each topic must be specific enough for a 60-90 second video.
- Titles/topic ideas should be in Vietnamese.

Trend and competitor context:
{input}

Previously used topics:
{context_section}

Return ONLY valid JSON:
[
  {{
    "topic": "Vì sao ...?",
    "angle": "one sentence explaining the story angle",
    "keywords": ["english stock search phrase", "english stock search phrase"],
    "score": 0.0,
    "reason": "why this has viral potential"
  }}
]

Return 8-12 candidates sorted by score descending.""",

    "generate_edl": """You are a professional video editor AI.
Given a packed transcript of video takes, generate an EDL (Edit Decision List) as JSON.

Rules:
- Never cut inside a word (snap to transcript boundaries)
- Pad cut edges 30-200ms
- Prefer silence gaps ≥400ms for cuts
- Label each range with a beat (HOOK, PROBLEM, SOLUTION, BENEFIT, EXAMPLE, CTA)
- Include quote (verbatim words) and reason for each cut
- Cap every range at ~8 seconds, even if the underlying narration runs longer uninterrupted —
  split a longer stretch into multiple consecutive ranges (alternating sources) so the footage
  cuts every few seconds. A single range covering 20+ seconds of narration is wrong: viewers
  expect new footage every 3-8s in this fast-paced explainer style. Most ranges should land in
  the 3-6s sweet spot.
- Spread ranges evenly across ALL available sources before reusing any of them.
- Assign sources in round-robin order (stock_0 for range 1, stock_1 for range 2, etc.)
- If there are more ranges than sources, looping back is fine — but never let two consecutive
  ranges share the same source (viewers notice back-to-back repeats, not spaced-out repeats).
- If Additional context includes stock_library metadata, choose the source whose title/tags/query
  best match the range quote and reason. Never assign a source just because it is next in order
  when another source is visibly more relevant to the quote.

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

    "generate_storyboard": """You are a video storyboard director for cinematic explainer videos.
Given a script and concept, break the narration into scenes. Use ONLY real footage (stock video or AI-generated visuals). No text overlays, no motion graphics, no animated cards.

Available scene types (ONLY these three):
- broll_stock: Real stock video footage. FREE. Props: query (English, 4-6 descriptive words), caption
- broll_ai_image: AI-generated image with Ken Burns zoom. CHEAP ($0.01). Props: prompt (detailed cinematic image description), caption, zoom_start, zoom_end
- broll_ai_video: AI-generated video clip. EXPENSIVE. MAX 5s per clip, MAX 2 per video. Only for scenes needing real motion (fire, water, crowds). Props: prompt (cinematic video description with motion), caption

DO NOT use: kinetic_text, title_card, stat_card, definition_card, quote_card, timeline, list_reveal, split_comparison, fact_counter, map_highlight, or any Remotion template. These look cheap and amateurish.

BUDGET:
- broll_stock: FREE. Use for 70%+ of scenes.
- broll_ai_image: $0.01 each. Use when stock can't match the exact visual needed.
- broll_ai_video: MAX 2 clips total. Only when motion is essential.

Priority: broll_stock → broll_ai_image → broll_ai_video (last resort, max 2)

Stock search queries:
- ALL query values MUST be in ENGLISH regardless of script language
- Be SPECIFIC and DESCRIPTIVE (4-6 words): "busy tokyo crosswalk night rain" NOT "city street"
- Describe the EXACT visual: subject + setting + action + mood
- BAD: "technology", "nature", "city" (too vague)
- GOOD: "scientist examining microscope laboratory", "misty mountain sunrise timelapse"
- If script is non-English, TRANSLATE the concept to English for query, keep caption in original language

Rules:
- Each scene 3-8 seconds
- Total duration matches script (~{duration}s)
- Assign narration text to each scene so they add up to the full script
- Set style.music_mood to one of: cinematic, upbeat, minimal, dramatic
- Every scene must have real visuals (stock or AI), never plain text on screen

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
      "type": "broll_stock",
      "duration_s": 5.0,
      "narration": "exact narration text for this scene",
      "props": {{"query": "aerial city skyline golden hour", "caption": ""}}
    }},
    {{
      "id": "s002",
      "type": "broll_ai_image",
      "duration_s": 6.0,
      "narration": "narration for this scene",
      "props": {{"prompt": "cinematic detailed image description", "caption": "", "zoom_start": 1.02, "zoom_end": 1.18}}
    }},
    {{
      "id": "s003",
      "type": "broll_ai_video",
      "duration_s": 5.0,
      "narration": "narration for motion scene",
      "props": {{"prompt": "cinematic video with motion", "caption": ""}}
    }}
  ]
}}

Respond ONLY with valid JSON.""",

    "generate_hybrid_storyboard": """You are a senior YouTube geography-video director.
Build the most engaging hybrid storyboard from a script, using EDL timing and stock sources when useful.

Goal:
- Make a cinematic, information-dense geography/listicle explainer using ONLY real footage.
- Use stock video clips, AI-generated images, and AI-generated video. NO text overlays, NO motion graphics, NO animated cards.
- Every scene must show real visuals — never plain text on screen.

Available scene types (ONLY these three):
- broll_stock: Real stock video or EDL source. FREE. Props: query (MUST be English, 4-6 descriptive words), caption, source_url OR source
- broll_ai_image: AI image + Ken Burns zoom. CHEAP. Props: prompt (detailed cinematic description), caption, zoom_start, zoom_end
- broll_ai_video: AI-generated video, expensive. MAX 2 clips, max 5s each. Props: prompt, caption

DO NOT use any Remotion templates: no kinetic_text, title_card, map_highlight, fact_counter, stat_card, definition_card, quote_card, timeline, list_reveal, split_comparison. These look cheap and amateurish.

Stock search queries:
- ALL query values MUST be in ENGLISH even when the script is in Vietnamese or another language
- Be SPECIFIC: "crowded delhi street market spices" NOT "India market"
- Describe the EXACT visual: subject + setting + action + detail
- BAD: "landscape", "people", "building" (returns unrelated generic footage)
- GOOD: "himalayan snow peaks aerial sunrise", "vietnamese floating market mekong river", "ancient temple angkor wat moss"

Hard rules:
- Each scene should be 3-7 seconds.
- Use broll_stock for 70%+ of scenes. Use broll_ai_image when stock can't match the visual.
- When using EDL stock, set props.source to an existing key from context.stock_sources OR props.source_url to a direct URL.
- Use broll_ai_video only for motion that cannot be faked by image+zoom, maximum 2 scenes.
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
      "type": "broll_stock",
      "duration_s": 5.0,
      "narration": "exact narration",
      "props": {{"query": "india aerial landscape diverse terrain", "caption": ""}}
    }},
    {{
      "id": "s002",
      "type": "broll_ai_image",
      "duration_s": 4.0,
      "narration": "exact narration",
      "props": {{"prompt": "satellite view of Indian subcontinent showing climate zones from desert to snow mountains", "caption": "", "zoom_start": 1.0, "zoom_end": 1.15}}
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

    "generate_overlays": """You are a motion graphics director for a YouTube geography/explainer video.
Given a script and its concept, pick 4-10 moments that deserve an on-screen graphic overlay
on top of the stock-footage edit.

Use these two overlay types only:
- map_highlight: script names a specific country/region/place. Props: region, headline, subline (optional), callouts (list of <=3 short strings, optional), marker_label (optional)
- stat_card: script states a specific number/statistic/comparison. Props: value, label, context (optional), prefix (optional), suffix (optional)

Rules:
- Order the list by where each moment occurs in the script (earliest first).
- position_fraction estimates how far through the script's runtime this moment occurs (0.0 = start, 1.0 = end).
- duration_s between 4 and 6.
- Don't invent statistics or facts not present in the script.
- For map_highlight, choose the MOST SPECIFIC mappable place named in that script moment:
  country > city/landmark/river/desert/region > continent. Do NOT use a continent when
  the sentence names a country or place inside that continent. Examples:
  "Ai Cập" → region "Ai Cập" or "Egypt", NOT "Châu Phi";
  "Cairo" → region "Ai Cập" or "Egypt", NOT "Châu Phi";
  "Sahara" → region "Sahara" only if supported, otherwise the nearest named country/region,
  NOT generic "Châu Phi" unless the narration is explicitly about the whole continent.
- Use continent regions like "Châu Phi" only when the narration is truly about the entire
  continent as a whole, not when it is discussing a specific country/place within it.
- Do not create multiple map_highlight overlays for the same country/region/place. A repeated
  map of the same region looks identical; use at most one map_highlight per unique region.
- Skip this entirely (return []) if the script has no clear place names or stats to highlight.

Concept context:
{context_section}

Script:
{input}

Return ONLY a valid JSON array:
[
  {{
    "template": "map_highlight",
    "position_fraction": 0.08,
    "duration_s": 5,
    "props": {{"region": "...", "headline": "...", "subline": "...", "callouts": ["...", "..."], "marker_label": "..."}}
  }},
  {{
    "template": "stat_card",
    "position_fraction": 0.34,
    "duration_s": 5,
    "props": {{"value": "60", "label": "percent of trade routes", "context": "...", "suffix": "%"}}
  }}
]""",

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

    "generate_search_terms": """You are a stock footage research specialist.
Given a video script, generate UNIQUE and DIVERSE search terms for each sentence/segment.
Each search term should find visually DIFFERENT footage — avoid generic repetition.

Rules:
- ALL search terms MUST be in ENGLISH regardless of script language. Translate concepts to English.
- One search term per script sentence (or logical segment if sentences are very short)
- Each term must be VISUALLY DISTINCT from all others (different subject, location, action)
- Use 4-6 DESCRIPTIVE words per search term — be specific about the exact visual
- Include specific details: country names, landmarks, actions, objects, lighting, camera angle
- BAD (too vague): "technology", "India", "nature", "people working"
- GOOD (specific): "silicon valley office glass building", "rajasthan desert camel caravan sunset", "dense amazon rainforest canopy aerial", "japanese chef preparing sushi closeup"
- Mix wide shots (aerial, landscape) with close-ups (hands, faces, objects)
- For geography topics: alternate between map/satellite, street-level, people, nature, architecture
- NEVER repeat the same base concept with just a different angle (e.g. "Africa aerial" and "Africa drone" are too similar)

Topic/concept context:
{context_section}

Script:
{input}

Return ONLY a valid JSON array of objects:
[
  {{"sentence": "first sentence of script...", "search_term": "sahara sand dunes"}},
  {{"sentence": "second sentence...", "search_term": "cairo bustling market"}},
  {{"sentence": "third sentence...", "search_term": "nile river boat"}}
]""",
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


def call_openrouter_vision(model: str, prompt: str, image_url: str, api_key: str) -> str:
    """Call OpenRouter with a vision model, sending an image URL for analysis."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/video-agent",
        "X-Title": "Video Agent",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "temperature": 0.0,
        "max_tokens": 200,
    }
    resp = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=30,
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

    json_tasks = {"generate_concept", "generate_geo_topics", "generate_edl", "generate_storyboard", "generate_hybrid_storyboard", "generate_overlays", "analyze_content", "generate_seo", "generate_search_terms"}
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
