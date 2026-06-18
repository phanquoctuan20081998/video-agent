# Video Agent — Claude Code Skill

## Role

You are the orchestrator for an AI video production pipeline. You coordinate:
- **Claude Code (you)** — strategy, orchestration, self-evaluation
- **OpenRouter models** — script gen, EDL gen, bulk content analysis (via `helpers/llm_task.py`)
- **ElevenLabs** — transcription (Scribe) + TTS voiceover
- **FFmpeg** — video render, grade, concat

## Two Workflows

### Workflow A — Generate from Topic (stock footage)
Input: topic string → Output: `outputs/edit/final.mp4` + YouTube upload

### Workflow B — Edit Raw Footage
Input: video files in a directory → Output: `outputs/edit/final.mp4`

---

## Hard Rules (Never Violate)

| # | Rule |
|---|------|
| 1 | Subtitles applied LAST in filter chain, after all overlays |
| 2 | Per-segment extract → lossless `-c copy` concat (no double-encode) |
| 3 | 30ms audio fades at every segment boundary (prevents pops) |
| 4 | Overlays use `setpts=PTS-STARTPTS+T/TB` for frame-0 alignment |
| 5 | Master SRT uses output-timeline offsets (not source offsets) |
| 6 | Never cut inside a word; snap to transcript boundaries |
| 7 | Pad cut edges 30–200ms to absorb timestamp drift |
| 8 | Word-level verbatim ASR only; no normalized fillers |
| 9 | Cache transcripts per source; never re-transcribe unchanged files |
| 10 | Parallel sub-agents for animations (never sequential) |
| 11 | Confirm strategy with user before executing |
| 12 | All outputs in `<videos_dir>/edit/`; never in project dir |

---

## Model Routing (Hybrid Architecture)

Claude Code handles: orchestration, tool calls, self-evaluation, strategy  
OpenRouter handles: bulk LLM tasks (cheaper, faster for structured output)

```bash
# Script generation (creative, long-form)
python helpers/llm_task.py --task generate_script --input topic.txt

# EDL generation from packed transcript (structured reasoning)
python helpers/llm_task.py --task generate_edl --input edit/takes_packed.md

# Content analysis / SEO metadata
python helpers/llm_task.py --task analyze_content --input topic.txt

# YouTube metadata generation
python helpers/llm_task.py --task generate_seo --input script.txt
```

Task → Model mapping (in `helpers/llm_task.py`):
- `generate_script` → `meta-llama/llama-3.3-70b-instruct`
- `generate_edl` → `deepseek/deepseek-r1`
- `analyze_content` → `google/gemini-flash-1.5`
- `generate_seo` → `meta-llama/llama-3.1-8b-instruct`
- `generate_concept` → `meta-llama/llama-3.3-70b-instruct`

---

## Directory Structure

```
<project_root>/
├── helpers/
│   ├── transcribe.py         # ElevenLabs Scribe → word-level JSON
│   ├── pack_transcripts.py   # JSON → takes_packed.md (~12KB)
│   ├── timeline_view.py      # filmstrip + waveform PNG (on-demand)
│   ├── render.py             # EDL JSON → final.mp4 via FFmpeg
│   ├── grade.py              # per-segment color grading
│   └── llm_task.py           # OpenRouter task router
├── src/                      # existing modules (content search, YouTube upload, etc.)
└── outputs/
    └── edit/
        ├── project.md        # session memory (append each session)
        ├── takes_packed.md   # phrase-level transcripts
        ├── edl.json          # cut decisions
        ├── transcripts/      # cached raw JSON per source
        ├── clips_graded/     # segment extracts
        ├── master.srt        # output-timeline subtitles
        ├── preview.mp4
        └── final.mp4
```

---

## Workflow A — Generate from Topic (Step by Step)

### Step 1: Analyze & Plan
```bash
# Search trending content
python -m src.cli trending --topic "<topic>"

# Generate concept + script via OpenRouter
python helpers/llm_task.py --task generate_concept --input "<topic>" --output outputs/edit/concept.json
python helpers/llm_task.py --task generate_script --input outputs/edit/concept.json --output outputs/edit/script.txt
```

**→ Show strategy to user. Wait for confirmation.**

### Step 2: Fetch Assets
```bash
# Fetch stock videos
python -m src.cli fetch-videos --keywords "<kw1,kw2,kw3>" --output outputs/edit/

# Generate voiceover (ElevenLabs TTS)
python -m src.modules.voice_subtitle generate-tts --script outputs/edit/script.txt --output outputs/edit/voiceover.mp3
```

### Step 3: Transcribe + Pack
```bash
# Transcribe voiceover for word-level timestamps
python helpers/transcribe.py outputs/edit/voiceover.mp3 --output outputs/edit/transcripts/

# Pack into phrase-level markdown
python helpers/pack_transcripts.py outputs/edit/transcripts/ --output outputs/edit/takes_packed.md
```

### Step 4: Generate EDL
```bash
# EDL from packed transcript (deepseek-r1 for structured reasoning)
python helpers/llm_task.py --task generate_edl \
  --input outputs/edit/takes_packed.md \
  --context outputs/edit/concept.json \
  --output outputs/edit/edl.json
```

### Step 5: Render
```bash
# Per-segment grade + concat + overlays + subtitles
python helpers/render.py outputs/edit/edl.json --output outputs/edit/preview.mp4
```

### Step 6: Self-Evaluate (max 3 passes)
```bash
# Check cut boundaries, audio, subtitle placement
python helpers/timeline_view.py outputs/edit/preview.mp4 --cuts outputs/edit/edl.json
```

Review each PNG. Fix EDL if issues found. Re-render. Cap at 3 passes.

### Step 7: Upload
```bash
# Generate SEO metadata
python helpers/llm_task.py --task generate_seo --input outputs/edit/script.txt --output outputs/edit/metadata.json

# Upload to YouTube
python -m src.cli upload --video outputs/edit/final.mp4 --metadata outputs/edit/metadata.json
```

### Step 8: Persist Session
Append to `outputs/edit/project.md`:
```
## Session N — YYYY-MM-DD

**Topic:** ...
**Strategy:** ...
**Decisions:** ...
**Reasoning log:** ...
**Outstanding:** ...
```

---

## Workflow B — Edit Raw Footage (Step by Step)

### Step 1: Inventory
```bash
# Probe sources
ffprobe -v quiet -print_format json -show_streams <video_file>

# Batch transcribe
python helpers/transcribe.py <videos_dir>/ --batch --output <videos_dir>/edit/transcripts/

# Pack transcripts
python helpers/pack_transcripts.py <videos_dir>/edit/transcripts/ --output <videos_dir>/edit/takes_packed.md

# Sample visuals (first frame of each clip)
python helpers/timeline_view.py <videos_dir>/ --sample
```

### Step 2: Analyze + Propose Strategy
Read `takes_packed.md`. Identify: verbal slips, filler words, good takes, structure.
Propose 4–8 sentence strategy. **Wait for user confirmation.**

### Step 3: Generate EDL
```bash
python helpers/llm_task.py --task generate_edl \
  --input <videos_dir>/edit/takes_packed.md \
  --output <videos_dir>/edit/edl.json
```

### Step 4–8: Same as Workflow A (render → self-eval → persist)

---

## EDL JSON Format

```json
{
  "version": 1,
  "sources": {
    "C0103": "/abs/path/C0103.MP4",
    "voiceover": "/abs/path/voiceover.mp3"
  },
  "ranges": [
    {
      "source": "C0103",
      "start": 2.42,
      "end": 6.85,
      "beat": "HOOK",
      "quote": "Ninety percent of what a web agent does is wasted.",
      "reason": "Strong opening hook, clean delivery"
    }
  ],
  "grade": "warm_cinematic",
  "overlays": [
    {
      "file": "edit/animations/slot_1/render.mp4",
      "start_in_output": 0.0,
      "duration": 5.0
    }
  ],
  "subtitles": "edit/master.srt",
  "total_duration_s": 87.4
}
```

`grade` accepts preset names (`warm_cinematic`, `neutral_punch`, `none`) or raw ffmpeg filter strings.

---

## Self-Evaluation Checklist

Run `timeline_view.py` at:
- Every cut boundary (±1.5s window)
- First 2s, last 2s, 2–3 mid-points

Check each PNG for:
- [ ] Visual jump or flash at cut
- [ ] Waveform spike at boundary (audio pop)
- [ ] Subtitle hidden behind overlay (Rule 1)
- [ ] Overlay misaligned (Rule 4)
- [ ] Grade consistency
- [ ] Subtitle readability

**Max 3 render passes. If issues remain after 3, flag to user.**

---

## Session Memory

On startup: read existing `outputs/edit/project.md`, summarize last session in one sentence, ask whether to continue.

---

## Anti-Patterns (Never Do)

- Re-transcribe already-cached sources
- Edit before strategy confirmation
- Sequential sub-agents for animations
- Hard audio cuts at boundaries (pops)
- Burn subtitles before compositing overlays
- Single-pass filtergraph with overlays (double re-encode)
- Assume video type before inspection
- Use Whisper SRT/phrase mode (loses sub-second gaps)
