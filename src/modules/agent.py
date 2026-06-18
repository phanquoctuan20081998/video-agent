"""
Video Agent Orchestrator — EDL-driven, session-persistent, self-evaluating.

Architecture (Cách 3 - Hybrid):
  - Claude Code: orchestration, self-eval, strategy decisions
  - OpenRouter (via helpers/llm_task.py): script gen, EDL gen, SEO, content analysis
  - ElevenLabs: transcription (Scribe) + TTS voiceover
  - FFmpeg (via helpers/render.py): video assembly
"""

import asyncio
import math
import json
import subprocess
import sys
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional
import httpx
from pydantic import BaseModel
from loguru import logger

from src.core import OpenRouterLLM, LLMConfig, LLMMessage, config
from src.modules.content_search import ContentSearcher
from src.modules.video_fetcher import StockVideoFetcher
from src.modules.youtube_uploader import YouTubeUploader


class VideoSession(BaseModel):
    """Session state for a single video project."""
    topic: str
    work_dir: str
    concept: Optional[dict] = None
    script: Optional[str] = None
    edl_path: Optional[str] = None
    storyboard_path: Optional[str] = None
    preview_path: Optional[str] = None
    final_path: Optional[str] = None
    metadata: Optional[dict] = None
    session_notes: list[str] = []
    self_eval_passes: int = 0


class VideoAgent:
    """
    Hybrid video agent.

    Claude Code (this orchestrator) handles:
      - Workflow coordination
      - Strategy confirmation
      - Self-evaluation loop

    OpenRouter (helpers/llm_task.py) handles:
      - generate_concept  → meta-llama/llama-3.3-70b-instruct
      - generate_script   → meta-llama/llama-3.3-70b-instruct
      - generate_edl      → deepseek/deepseek-r1
      - generate_seo      → meta-llama/llama-3.1-8b-instruct
      - analyze_content   → google/gemini-flash-1.5
    """

    MAX_SELF_EVAL_PASSES = 3
    RENDER_CACHE_VERSION = "edl_music_mix_v2"

    def __init__(self):
        self.llm = OpenRouterLLM(
            LLMConfig(
                api_key=config.settings.openrouter_api_key,
                model="meta-llama/llama-3.3-70b-instruct",
            )
        )
        self.content_searcher = ContentSearcher()
        self.video_fetcher = StockVideoFetcher()
        self.uploader = YouTubeUploader()
        self.logger = logger

    # ── Public API ────────────────────────────────────────────────────────────

    async def generate_video(
        self,
        topic: str,
        keywords: Optional[list[str]] = None,
        duration: float = 60.0,
        auto_upload: bool = False,
        apply_effects: bool = False,
        effects_mode: str = "seedance",
        confirm_strategy: bool = True,
        mode: str = "edl",
    ) -> Optional[str]:
        """
        Workflow A: topic → final.mp4 (+ optional YouTube upload).

        mode="edl" (default):
          1. Concept → script → stock footage → voiceover → EDL → render → self-eval

        mode="storyboard":
          1. Concept → script → voiceover → storyboard (LLM) → scene_assembler
             (Remotion + AI images + optional Seedance, $1 budget cap)
        """
        work_dir = Path(config.settings.output_dir) / "edit"
        work_dir.mkdir(parents=True, exist_ok=True)
        session = VideoSession(topic=topic, work_dir=str(work_dir))
        self._load_session(session)

        self.logger.info(f"[agent] Workflow A: topic='{topic}' duration={duration}s")
        keywords = keywords or []
        planning_topic = topic
        if keywords:
            planning_topic = f"{topic}\nAdditional keywords: {', '.join(keywords)}"

        def done(path: Path, min_bytes: int = 10) -> bool:
            """Check if step output already exists and is valid."""
            return path.exists() and path.stat().st_size >= min_bytes

        def fresh(path: Path, deps: list[Path], min_bytes: int = 10) -> bool:
            """Check if cached output exists and is newer than its inputs."""
            if not done(path, min_bytes=min_bytes):
                return False
            output_mtime = path.stat().st_mtime
            return all(not dep.exists() or output_mtime >= dep.stat().st_mtime for dep in deps)

        # ── Step 1: Concept ───────────────────────────────────────────────────
        concept_path = work_dir / "concept.json"
        if done(concept_path):
            self.logger.info("[agent] ✓ concept (cached)")
            concept = json.loads(concept_path.read_text())
        else:
            concept = await self._generate_concept(planning_topic, duration, session)
            if not concept:
                return None

        if confirm_strategy:
            self._print_strategy(concept, duration)

        # ── Step 2: Script ────────────────────────────────────────────────────
        script_path = work_dir / "script.txt"
        if done(script_path):
            self.logger.info("[agent] ✓ script (cached)")
            script = self._extract_script_text(script_path.read_text())
            script_path.write_text(script)  # rewrite clean in case cache has old JSON
        else:
            script = await self._generate_script(concept, duration, session)
            if not script:
                return None

        # ── Step 3: Stock videos ──────────────────────────────────────────────
        # Use more varied keywords: topic keywords + generic visual keywords.
        # Keyword/clip volume scales with duration so long-form videos don't
        # starve on unique sources (EDL avoids back-to-back repeats per source).
        concept_keywords = list(dict.fromkeys([*keywords, *concept.get("keywords", [topic])]))
        visual_keywords = [
            "satellite map",
            "world map animation",
            "aerial landscape",
            "city timelapse",
            "people market",
            "mountains river",
            "data visualization",
            "travel documentary",
            "drone footage city",
            "rural village life",
            "industrial factory",
            "ocean coastline",
            "desert landscape",
            "forest canopy",
            "busy street crowd",
            "infographic chart",
        ]
        keyword_pool = list(dict.fromkeys(concept_keywords + visual_keywords))
        clips_per_keyword = 6
        target_clips = max(8, math.ceil(duration / 6))
        needed_keywords = min(max(8, math.ceil(target_clips / clips_per_keyword)), len(keyword_pool), 20)
        all_keywords = keyword_pool[:needed_keywords]
        stock_videos = await self._fetch_stock_videos(all_keywords, work_dir, max_per_keyword=clips_per_keyword)

        # ── Step 4: Voiceover ─────────────────────────────────────────────────
        voiceover_path = work_dir / "voiceover.mp3"
        if self._voiceover_fresh(voiceover_path, script):
            self.logger.info("[agent] ✓ voiceover (cached)")
        else:
            result = await self._generate_voiceover(script, work_dir)
            if not result:
                return None
            voiceover_path = result

        # ── Step 5: Transcribe + pack ─────────────────────────────────────────
        packed_md = work_dir / "takes_packed.md"
        if self._transcript_fresh(voiceover_path, packed_md):
            self.logger.info("[agent] ✓ transcript (cached)")
        else:
            packed_md = await self._transcribe_and_pack(voiceover_path, work_dir)

        # ── Hybrid branch: storyboard director + EDL timing/source context ─────
        if mode == "hybrid":
            return await self._hybrid_workflow(
                concept, script, duration, work_dir, session,
                stock_videos=stock_videos,
                packed_md=packed_md,
                voiceover_path=voiceover_path,
                auto_upload=auto_upload,
            )

        # ── Storyboard branch (animation pipeline) ────────────────────────────
        if mode == "storyboard":
            return await self._storyboard_workflow(
                concept, script, duration, work_dir, session,
                auto_upload=auto_upload,
            )

        # ── Step 6: EDL ───────────────────────────────────────────────────────
        edl_path = work_dir / "edl.json"
        if fresh(edl_path, [packed_md], min_bytes=10):
            self.logger.info("[agent] ✓ EDL (cached)")
        else:
            edl_path = await self._generate_edl(packed_md, concept, stock_videos, work_dir)
            if not edl_path:
                return None
        session.edl_path = str(edl_path)

        # ── Step 6a: Map/stat overlay graphics ───────────────────────────────
        overlays_dir = work_dir / "animations"
        if self._overlays_fresh(edl_path, overlays_dir):
            self.logger.info("[agent] ✓ overlays (cached)")
        else:
            await self._add_overlays_to_edl(script, concept, edl_path, overlays_dir)

        # ── Step 6b: Optional effects ─────────────────────────────────────────
        if apply_effects:
            await self._generate_effects(concept, script, edl_path, work_dir, session, mode=effects_mode)

        # ── Step 6c: Master subtitles (Rule 1/5/6/8) ──────────────────────────
        # Runs after effects so it always has the final say on edl["subtitles"].
        srt_path = work_dir / "master.srt"
        if fresh(srt_path, [edl_path, voiceover_path], min_bytes=10):
            self.logger.info("[agent] ✓ subtitles (cached)")
        else:
            self._attach_subtitles(edl_path, work_dir, srt_path)

        # ── Step 7: Render ────────────────────────────────────────────────────
        preview_path = work_dir / "preview.mp4"
        if (
            self._render_fresh(preview_path, [edl_path, voiceover_path])
            and self._has_audio_stream(preview_path)
        ):
            self.logger.info("[agent] ✓ preview (cached)")
        else:
            self._render(edl_path, preview_path)
            self._write_render_fingerprint(preview_path, [edl_path, voiceover_path])
        session.preview_path = str(preview_path)

        # ── Step 8: Self-evaluate ─────────────────────────────────────────────
        final_path = work_dir / "final.mp4"
        if (
            fresh(final_path, [preview_path], min_bytes=10000)
            and self._has_audio_stream(final_path)
        ):
            self.logger.info("[agent] ✓ final (cached)")
        else:
            final_path = await self._self_eval_loop(preview_path, edl_path, work_dir, session)
            if not final_path:
                return None
        session.final_path = str(final_path)

        # ── Step 9: Upload ────────────────────────────────────────────────────
        if auto_upload:
            metadata = await self._generate_seo(script, work_dir)
            await self._upload(final_path, metadata)

        self._persist_session(session)
        self.logger.info(f"[agent] Done: {final_path}")
        return str(final_path)

    async def edit_footage(
        self,
        videos_dir: str,
        confirm_strategy: bool = True,
    ) -> Optional[str]:
        """
        Workflow B: raw footage dir → final.mp4.
        Transcribe → pack → generate EDL → render → self-eval.
        """
        vdir = Path(videos_dir)
        work_dir = vdir / "edit"
        work_dir.mkdir(parents=True, exist_ok=True)
        topic = f"footage:{vdir.name}"
        session = VideoSession(topic=topic, work_dir=str(work_dir))

        self._load_session(session)
        self.logger.info(f"[agent] Workflow B: editing {videos_dir}")

        # Transcribe all footage
        transcript_dir = work_dir / "transcripts"
        self._run_helper(
            ["helpers/transcribe.py", str(vdir), "--batch", "--output", str(transcript_dir)],
            "batch transcribe",
        )

        # Pack transcripts
        packed_md = work_dir / "takes_packed.md"
        self._run_helper(
            ["helpers/pack_transcripts.py", str(transcript_dir), "--output", str(packed_md)],
            "pack transcripts",
        )

        if confirm_strategy:
            self.logger.info(f"[agent] Packed transcript: {packed_md}")
            self.logger.info("[agent] Review takes_packed.md and confirm strategy before EDL generation.")

        # EDL from packed transcript
        edl_path = work_dir / "edl.json"
        sources = {
            f.stem: str(f.resolve())
            for f in vdir.iterdir()
            if f.suffix.lower() in {".mp4", ".mov", ".mkv"}
        }
        await self._generate_edl(packed_md, {"sources": sources}, [], work_dir)
        session.edl_path = str(edl_path)

        # Render + self-eval
        preview_path = work_dir / "preview.mp4"
        self._render(edl_path, preview_path)
        final_path = await self._self_eval_loop(preview_path, edl_path, work_dir, session)
        session.final_path = str(final_path) if final_path else None

        self._persist_session(session)
        return str(final_path) if final_path else None

    # ── Private: Storyboard animation pipeline ────────────────────────────────

    async def _storyboard_workflow(
        self,
        concept: dict,
        script: str,
        duration: float,
        work_dir: Path,
        session: VideoSession,
        auto_upload: bool = False,
    ) -> Optional[str]:
        """
        Animation pipeline: script → voiceover → storyboard → scene_assembler → final.mp4

        Budget: $1 max per video. Remotion (free) → stock (free) → AI image (free) → Seedance (max 2 clips).
        """
        self.logger.info("[agent] storyboard mode: Remotion + AI image + optional Seedance")

        def done(path: Path, min_bytes: int = 10) -> bool:
            return path.exists() and path.stat().st_size >= min_bytes

        # Step S1: Voiceover
        voiceover_path = work_dir / "voiceover.mp3"
        if self._voiceover_fresh(voiceover_path, script):
            self.logger.info("[agent] ✓ voiceover (cached)")
        else:
            result = await self._generate_voiceover(script, work_dir)
            if not result:
                return None
            voiceover_path = result

        # Step S2: Storyboard
        storyboard_path = work_dir / "storyboard.json"
        if done(storyboard_path):
            self.logger.info("[agent] ✓ storyboard (cached)")
        else:
            storyboard_path = await self._generate_storyboard(
                script, concept, duration, work_dir
            )
            if not storyboard_path:
                self.logger.warning("[agent] storyboard failed, falling back to EDL mode")
                return None
        session.storyboard_path = str(storyboard_path)

        # Step S3: Scene assembly (Remotion + AI images + Seedance)
        assembled_path = work_dir / "assembled.mp4"
        if done(assembled_path, min_bytes=10000):
            self.logger.info("[agent] ✓ assembled scenes (cached)")
        else:
            try:
                result = self._run_helper([
                    "helpers/scene_assembler.py",
                    str(storyboard_path),
                    "--voiceover", str(voiceover_path),
                    "--output", str(assembled_path),
                ], "scene assembler")
            except RuntimeError as e:
                self.logger.error(f"[agent] scene assembly failed: {e}")
                return None

        if not done(assembled_path, min_bytes=10000):
            self.logger.error("[agent] assembled.mp4 not produced")
            return None

        # Step S4: Copy to preview + final (assembler already mixed voiceover)
        preview_path = work_dir / "preview.mp4"
        final_path = work_dir / "final.mp4"
        import shutil
        shutil.copy2(str(assembled_path), str(preview_path))
        session.preview_path = str(preview_path)

        # Step S5: Self-evaluate
        final_path = await self._self_eval_loop(preview_path, None, work_dir, session)
        if not final_path:
            return None
        session.final_path = str(final_path)

        # Step S6: Upload
        if auto_upload:
            metadata = await self._generate_seo(script, work_dir)
            await self._upload(final_path, metadata)

        self._persist_session(session)
        self.logger.info(f"[agent] storyboard done: {final_path}")
        return str(final_path)

    async def _hybrid_workflow(
        self,
        concept: dict,
        script: str,
        duration: float,
        work_dir: Path,
        session: VideoSession,
        stock_videos: list,
        packed_md: Path,
        voiceover_path: Path,
        auto_upload: bool = False,
    ) -> Optional[str]:
        """
        Hybrid pipeline: EDL gives narration timing and real b-roll sources;
        storyboard chooses the most engaging visual grammar for each beat.
        """
        self.logger.info("[agent] hybrid mode: EDL timing + Remotion geography storyboard + b-roll")

        def done(path: Path, min_bytes: int = 10) -> bool:
            return path.exists() and path.stat().st_size >= min_bytes

        edl_path = work_dir / "edl.json"
        if done(edl_path):
            self.logger.info("[agent] ✓ EDL (cached)")
        else:
            edl_path = await self._generate_edl(packed_md, concept, stock_videos, work_dir)
            if not edl_path:
                return None
        session.edl_path = str(edl_path)

        storyboard_path = work_dir / "storyboard.json"
        if self._json_cache_valid(storyboard_path):
            self.logger.info("[agent] ✓ hybrid storyboard (cached)")
        else:
            storyboard_path = await self._generate_hybrid_storyboard(
                script, concept, duration, edl_path, stock_videos, work_dir
            )
            if not storyboard_path:
                return None
        session.storyboard_path = str(storyboard_path)

        assembled_path = work_dir / "assembled.mp4"
        if done(assembled_path, min_bytes=10000):
            self.logger.info("[agent] ✓ assembled hybrid scenes (cached)")
        else:
            self._run_helper([
                "helpers/scene_assembler.py",
                str(storyboard_path),
                "--voiceover", str(voiceover_path),
                "--output", str(assembled_path),
            ], "hybrid scene assembler")

        if not done(assembled_path, min_bytes=10000):
            self.logger.error("[agent] assembled.mp4 not produced")
            return None

        import shutil
        preview_path = work_dir / "preview.mp4"
        shutil.copy2(str(assembled_path), str(preview_path))
        session.preview_path = str(preview_path)

        final_path = await self._self_eval_loop(preview_path, None, work_dir, session)
        if not final_path:
            return None
        session.final_path = str(final_path)

        if auto_upload:
            metadata = await self._generate_seo(script, work_dir)
            await self._upload(final_path, metadata)

        self._persist_session(session)
        self.logger.info(f"[agent] hybrid done: {final_path}")
        return str(final_path)

    async def _generate_storyboard(
        self,
        script: str,
        concept: dict,
        duration: float,
        work_dir: Path,
    ) -> Optional[Path]:
        storyboard_path = work_dir / "storyboard.json"
        concept_path = work_dir / "concept.json"
        script_path = work_dir / "script.txt"

        if not script_path.exists():
            script_path.write_text(script)
        if not concept_path.exists():
            concept_path.write_text(json.dumps(concept, indent=2))

        self._run_helper([
            "helpers/llm_task.py",
            "--task", "generate_storyboard",
            "--input", str(script_path),
            "--context", str(concept_path),
            "--duration", str(int(duration)),
            "--output", str(storyboard_path),
        ], "generate storyboard (deepseek-r1)")

        if not storyboard_path.exists():
            self.logger.error("[agent] storyboard.json not created")
            return None
        return storyboard_path

    async def _generate_hybrid_storyboard(
        self,
        script: str,
        concept: dict,
        duration: float,
        edl_path: Path,
        stock_videos: list,
        work_dir: Path,
    ) -> Optional[Path]:
        storyboard_path = work_dir / "storyboard.json"
        context_path = work_dir / "hybrid_context.json"
        script_path = work_dir / "script.txt"
        concept_path = work_dir / "concept.json"

        script_path.write_text(script)
        if not concept_path.exists():
            concept_path.write_text(json.dumps(concept, indent=2, ensure_ascii=False))

        edl = json.loads(edl_path.read_text())
        sources = edl.get("sources", {})
        stock_library = []
        for i, video in enumerate(stock_videos[:15]):
            stock_library.append({
                "id": f"stock_{i}",
                "url": getattr(video, "download_url", None) or getattr(video, "url", ""),
                "title": getattr(video, "title", ""),
                "duration": getattr(video, "duration", None),
            })

        context_path.write_text(json.dumps({
            "concept": concept,
            "edl": edl,
            "stock_sources": sources,
            "stock_library": stock_library,
            "style_target": {
                "language": "Vietnamese if the topic or channel target is Vietnamese; otherwise match topic language.",
                "format": "fast geography listicle / documentary explainer",
                "visuals": [
                    "satellite/map highlight",
                    "large yellow-white Vietnamese captions",
                    "fact counter cards",
                    "short stock or AI b-roll",
                    "clean data-journalism motion graphics",
                ],
            },
        }, indent=2, ensure_ascii=False))

        self._run_helper([
            "helpers/llm_task.py",
            "--task", "generate_hybrid_storyboard",
            "--input", str(script_path),
            "--context", str(context_path),
            "--duration", str(int(duration)),
            "--output", str(storyboard_path),
        ], "generate hybrid storyboard")

        if not storyboard_path.exists():
            self.logger.error("[agent] hybrid storyboard.json not created")
            return None
        try:
            storyboard = self._load_json_file(storyboard_path)
            storyboard["sources"] = sources
            storyboard.setdefault("version", 3)
            storyboard_path.write_text(json.dumps(storyboard, indent=2, ensure_ascii=False))
        except Exception as e:
            self.logger.error(f"[agent] hybrid storyboard is not valid JSON: {e}")
            return None
        return storyboard_path

    def _json_cache_valid(self, path: Path) -> bool:
        if not path.exists() or path.stat().st_size < 2:
            return False
        try:
            self._load_json_file(path)
            return True
        except Exception:
            return False

    @staticmethod
    def _extract_json_text(text: str) -> str:
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
            end = text.rfind("}")
            if end >= 0:
                text = text[:end + 1]
        elif text.startswith("[") and not text.rstrip().endswith("]"):
            end = text.rfind("]")
            if end >= 0:
                text = text[:end + 1]
        return text

    def _load_json_file(self, path: Path) -> dict | list:
        return json.loads(self._extract_json_text(path.read_text()))

    @staticmethod
    def _extract_script_text(content: str) -> str:
        """Extract plain script text from raw LLM output (strips JSON wrapper + code fences)."""
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
        try:
            raw = json.loads(text)
            if isinstance(raw, dict):
                return raw.get("script", text).strip()
        except json.JSONDecodeError:
            pass
        return text

    # ── Private: LLM tasks (delegated to OpenRouter via subprocess) ───────────

    async def _generate_concept(
        self, topic: str, duration: float, session: VideoSession
    ) -> Optional[dict]:
        concept_path = Path(session.work_dir) / "concept.json"
        self._run_helper([
            "helpers/llm_task.py",
            "--task", "generate_concept",
            "--input", topic,
            "--duration", str(int(duration)),
            "--output", str(concept_path),
        ], "generate concept")
        if not concept_path.exists():
            self.logger.error("[agent] concept.json not created")
            return None
        content = concept_path.read_text().strip()
        if not content:
            self.logger.error("[agent] concept.json is empty — LLM may have failed")
            return None
        try:
            concept = json.loads(content)
        except json.JSONDecodeError:
            self.logger.error(f"[agent] concept.json not valid JSON:\n{content[:300]}")
            return None
        session.concept = concept
        return concept

    async def _generate_script(
        self, concept: dict, duration: float, session: VideoSession
    ) -> Optional[str]:
        concept_path = Path(session.work_dir) / "concept.json"
        script_path = Path(session.work_dir) / "script.txt"
        self._run_helper([
            "helpers/llm_task.py",
            "--task", "generate_script",
            "--input", str(concept_path),
            "--duration", str(int(duration)),
            "--output", str(script_path),
        ], "generate script")
        if not script_path.exists():
            self.logger.error("[agent] script generation failed")
            return None
        script = script_path.read_text().strip()
        if not script:
            self.logger.error("[agent] script file is empty")
            return None
        # LLM now returns plain text — but defensively strip JSON wrapper if present
        script = self._extract_script_text(script)
        script_path.write_text(script)
        session.script = script
        return script

    async def _generate_edl(
        self,
        packed_md: Path,
        concept: dict,
        stock_videos: list,
        work_dir: Path,
    ) -> Optional[Path]:
        edl_path = work_dir / "edl.json"
        context_path = work_dir / "edl_context.json"

        # Build context with source paths — pass ALL unique clips so LLM has more to work with
        sources = concept.get("sources", {})
        if not sources and stock_videos:
            sources = {
                f"stock_{i}": v.download_url or v.url
                for i, v in enumerate(stock_videos[:15])
            }
        context_path.write_text(json.dumps({
            "sources": sources,
            "concept": concept,
        }, indent=2))

        self._run_helper([
            "helpers/llm_task.py",
            "--task", "generate_edl",
            "--input", str(packed_md),
            "--context", str(context_path),
            "--output", str(edl_path),
        ], "generate EDL (deepseek-r1)")

        if not edl_path.exists():
            self.logger.error("[agent] EDL generation failed")
            return None

        # Post-process: enforce round-robin source assignment so no clip repeats back-to-back
        edl_path = self._dedup_edl_sources(edl_path, sources)
        return edl_path

    def _dedup_edl_sources(self, edl_path: Path, available_sources: dict) -> Path:
        """Redistribute EDL ranges so no two consecutive ranges share the same source."""
        try:
            edl = json.loads(edl_path.read_text())
            ranges = edl.get("ranges", [])
            source_names = list(available_sources.keys())
            if len(source_names) <= 1:
                return edl_path

            # Round-robin: if consecutive ranges share source, rotate to next unused
            used_recently: list[str] = []
            for r in ranges:
                current = r.get("source", source_names[0])
                # If this source was just used, pick next available
                if used_recently and current == used_recently[-1]:
                    for name in source_names:
                        if name != used_recently[-1]:
                            r["source"] = name
                            current = name
                            break
                used_recently.append(current)
                if len(used_recently) > 3:
                    used_recently.pop(0)

            edl["ranges"] = ranges
            edl_path.write_text(json.dumps(edl, indent=2))
            self.logger.info("[agent] EDL sources deduplicated")
        except Exception as e:
            self.logger.warning(f"[agent] EDL dedup failed (using as-is): {e}")
        return edl_path

    async def _generate_overlay_specs(self, script: str, concept: dict, work_dir: Path) -> list[dict]:
        """LLM call: pick script moments worth a map_highlight/stat_card graphic."""
        script_path = work_dir / "script.txt"
        concept_path = work_dir / "concept.json"
        specs_path = work_dir / "overlay_specs.json"
        if not script_path.exists():
            script_path.write_text(script)
        if not concept_path.exists():
            concept_path.write_text(json.dumps(concept, indent=2))
        self._run_helper([
            "helpers/llm_task.py",
            "--task", "generate_overlays",
            "--input", str(script_path),
            "--context", str(concept_path),
            "--output", str(specs_path),
        ], "generate overlay graphic specs")
        if not specs_path.exists():
            return []
        try:
            specs = json.loads(specs_path.read_text())
        except json.JSONDecodeError:
            self.logger.warning("[agent] overlay_specs.json invalid JSON, skipping overlays")
            return []
        return specs if isinstance(specs, list) else []

    async def _render_overlays(self, specs: list[dict], total_duration_s: float, overlays_dir: Path) -> list[dict]:
        """Render each overlay spec to MP4 via Remotion, in parallel (Rule 10)."""
        overlays_dir.mkdir(parents=True, exist_ok=True)

        async def render_one(i: int, spec: dict) -> Optional[dict]:
            template = spec.get("template")
            if template not in ("map_highlight", "stat_card"):
                return None
            props = dict(spec.get("props", {}))
            duration_s = float(spec.get("duration_s", 5.0))
            props["duration_s"] = duration_s
            out_path = overlays_dir / f"overlay_{i}_{template}.mp4"
            try:
                await asyncio.to_thread(
                    self._run_helper,
                    [
                        "helpers/remotion_runner.py",
                        "--composition", template,
                        "--props", json.dumps(props),
                        "--output", str(out_path),
                    ],
                    f"render overlay {i} ({template})",
                )
            except RuntimeError as e:
                self.logger.warning(f"[agent] overlay {i} render failed, skipping: {e}")
                return None
            if not out_path.exists():
                return None
            position_fraction = max(0.0, min(1.0, float(spec.get("position_fraction", 0.0))))
            start = round(position_fraction * max(total_duration_s - duration_s, 0.0), 2)
            return {"file": str(out_path.resolve()), "start_in_output": start, "duration": duration_s}

        results = await asyncio.gather(*(render_one(i, s) for i, s in enumerate(specs)))
        return [r for r in results if r]

    def _overlays_fresh(self, edl_path: Path, overlays_dir: Path) -> bool:
        manifest_path = overlays_dir / "manifest.json"
        if not edl_path.exists() or not manifest_path.exists():
            return False
        if manifest_path.stat().st_mtime < edl_path.stat().st_mtime:
            return False
        try:
            overlays = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            return False
        return all(Path(o["file"]).exists() for o in overlays)

    async def _add_overlays_to_edl(self, script: str, concept: dict, edl_path: Path, overlays_dir: Path) -> None:
        edl = json.loads(edl_path.read_text())
        ranges = edl.get("ranges", [])
        total_duration_s = edl.get("total_duration_s") or sum(
            max(0.0, r.get("end", 0.0) - r.get("start", 0.0)) for r in ranges
        )

        manifest_path = overlays_dir / "manifest.json"
        overlays_dir.mkdir(parents=True, exist_ok=True)

        if total_duration_s <= 0:
            self.logger.warning("[agent] EDL has no duration, skipping overlay graphics")
            overlays = []
        else:
            specs = await self._generate_overlay_specs(script, concept, edl_path.parent)
            if not specs:
                self.logger.info("[agent] no overlay graphics needed for this script")
                overlays = []
            else:
                overlays = await self._render_overlays(specs, total_duration_s, overlays_dir)
                self.logger.info(f"[agent] added {len(overlays)} map/stat overlay graphics")

        edl["overlays"] = overlays
        edl_path.write_text(json.dumps(edl, indent=2))
        manifest_path.write_text(json.dumps(overlays, indent=2))

    def _attach_subtitles(self, edl_path: Path, work_dir: Path, srt_path: Path) -> None:
        """Build master.srt from cached word-level transcripts and wire it into the EDL."""
        edl = json.loads(edl_path.read_text())
        built = self._build_master_srt(edl, work_dir)
        if built:
            edl["subtitles"] = str(built.resolve())
            self.logger.info(f"[agent] master.srt built ({built.stat().st_size}B, {self._count_srt_cues(built)} cues)")
        else:
            edl["subtitles"] = None
            self.logger.warning("[agent] could not build subtitles — no cached word-level transcript found")
        edl_path.write_text(json.dumps(edl, indent=2))

    def _build_master_srt(self, edl: dict, work_dir: Path) -> Optional[Path]:
        """Rule 5: output-timeline offsets. Rule 6: snap to word boundaries.
        Rule 8: word-level verbatim ASR only. Rule 9: reuse cached transcripts.

        Workflow A lays a single continuous voiceover track under the cut
        b-roll (render.py mixes it untouched, only the visual is looped/trimmed
        to match) — so the voiceover's own source timestamps already ARE
        output-timeline timestamps. Workflow B cuts ranges directly out of
        footage whose own audio becomes the output, so each range must be
        remapped from source time to cumulative output time.
        """
        transcripts_dir = work_dir / "transcripts"
        ranges = edl.get("ranges", [])

        voiceover_json = transcripts_dir / "voiceover.json"
        if voiceover_json.exists():
            words = self._load_transcript_words(voiceover_json)
            cues = self._words_to_cues(words)
        else:
            cues = []
            cumulative = 0.0
            for r in ranges:
                duration = max(0.0, r.get("end", 0.0) - r.get("start", 0.0))
                source_json = transcripts_dir / f"{r.get('source')}.json"
                if source_json.exists():
                    words = self._load_transcript_words(source_json)
                    in_range = [
                        w for w in words
                        if w["start"] >= r["start"] - 0.05 and w["end"] <= r["end"] + 0.05
                    ]
                    shifted = [
                        {
                            "text": w["text"],
                            "start": cumulative + (w["start"] - r["start"]),
                            "end": cumulative + (w["end"] - r["start"]),
                        }
                        for w in in_range
                    ]
                    cues.extend(self._words_to_cues(shifted))
                cumulative += duration

        if not cues:
            return None

        srt_path = work_dir / "master.srt"
        srt_path.write_text(self._cues_to_srt(cues))
        return srt_path

    @staticmethod
    def _load_transcript_words(path: Path) -> list[dict]:
        data = json.loads(path.read_text())
        return [w for w in data.get("words", []) if w.get("type") == "word"]

    @staticmethod
    def _words_to_cues(
        words: list[dict],
        max_words: int = 10,
        max_dur: float = 4.5,
        gap_break: float = 0.35,
    ) -> list[tuple[float, float, str]]:
        cues: list[tuple[float, float, str]] = []
        bucket: list[dict] = []
        for w in words:
            if bucket:
                gap = w["start"] - bucket[-1]["end"]
                dur = w["end"] - bucket[0]["start"]
                if gap >= gap_break or len(bucket) >= max_words or dur >= max_dur:
                    cues.append((bucket[0]["start"], bucket[-1]["end"], " ".join(b["text"] for b in bucket)))
                    bucket = []
            bucket.append(w)
        if bucket:
            cues.append((bucket[0]["start"], bucket[-1]["end"], " ".join(b["text"] for b in bucket)))
        return cues

    @staticmethod
    def _cues_to_srt(cues: list[tuple[float, float, str]]) -> str:
        def fmt(t: float) -> str:
            t = max(0.0, t)
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = int(t % 60)
            ms = int(round((t - int(t)) * 1000))
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        lines = []
        for i, (start, end, text) in enumerate(cues, 1):
            lines.append(str(i))
            lines.append(f"{fmt(start)} --> {fmt(end)}")
            lines.append(text)
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _count_srt_cues(srt_path: Path) -> int:
        return srt_path.read_text().count(" --> ")

    async def _generate_seo(self, script: str, work_dir: Path) -> dict:
        script_path = work_dir / "script.txt"
        metadata_path = work_dir / "metadata.json"
        if not script_path.exists():
            script_path.write_text(script)
        self._run_helper([
            "helpers/llm_task.py",
            "--task", "generate_seo",
            "--input", str(script_path),
            "--output", str(metadata_path),
        ], "generate SEO metadata")
        if metadata_path.exists():
            return json.loads(metadata_path.read_text())
        return {}

    async def _generate_effects(
        self,
        concept: dict,
        script: str,
        edl_path: Path,
        work_dir: Path,
        session: VideoSession,
        mode: str = "seedance",
    ) -> None:
        """Generate effects and attach them to the EDL."""
        mode = (mode or "seedance").lower()
        if mode == "seedance":
            try:
                await self._generate_seedance_effects(concept, script, edl_path, work_dir, session)
                return
            except Exception as e:
                self.logger.warning(f"[agent] Seedance effects failed, falling back to local semantic overlays: {e}")

        await self._generate_local_semantic_effects(concept, script, edl_path, work_dir, session)

    async def _generate_seedance_effects(
        self,
        concept: dict,
        script: str,
        edl_path: Path,
        work_dir: Path,
        session: VideoSession,
    ) -> None:
        """Generate editorial b-roll clips with OpenRouter's video API and splice them into the EDL."""
        edl = json.loads(edl_path.read_text())
        seedance_dir = work_dir / "seedance"
        seedance_dir.mkdir(parents=True, exist_ok=True)

        sources = edl.setdefault("sources", {})
        existing = edl.get("ai_effects") or []
        if (
            edl.get("effects_mode") == "seedance"
            and existing
            and all(Path(item.get("file", "")).exists() for item in existing)
        ):
            self.logger.info("[agent] ✓ Seedance effects (cached)")
            return

        ranges = edl.get("ranges", [])
        plan = await self._plan_seedance_clips(concept, script, ranges)
        generated = []

        for i, item in enumerate(plan[:3]):
            range_index = int(item.get("range_index", i))
            if range_index < 0 or range_index >= len(ranges):
                continue

            original = ranges[range_index]
            original_duration = max(1.0, float(original.get("end", 0)) - float(original.get("start", 0)))
            seedance_duration = int(min(15, max(4, math.ceil(float(item.get("duration", original_duration))))))
            prompt = str(item.get("prompt", "")).strip()
            if not prompt:
                prompt = self._fallback_seedance_prompt(original, concept)

            out_path = seedance_dir / f"seedance_{i:02d}.mp4"
            if not out_path.exists() or out_path.stat().st_size < 10000:
                await self._create_openrouter_video(
                    prompt=prompt,
                    output_path=out_path,
                    duration=seedance_duration,
                    model=str(item.get("model") or "bytedance/seedance-2.0-fast"),
                )

            source_name = f"seedance_{i:02d}"
            sources[source_name] = str(out_path)
            original["source"] = source_name
            original["start"] = 0.0
            original["end"] = min(original_duration, seedance_duration)
            original["ai_prompt"] = prompt
            original["ai_reason"] = item.get("reason", "")
            generated.append({
                "source": source_name,
                "file": str(out_path),
                "range_index": range_index,
                "duration": seedance_duration,
                "prompt": prompt,
                "reason": item.get("reason", ""),
            })

        if generated:
            edl["effects_mode"] = "seedance"
            edl["ai_effects"] = generated
            # Keep non-effect overlays (e.g. map_highlight/stat_card from Step 6a) — don't clobber them.
            edl["overlays"] = [o for o in (edl.get("overlays") or []) if o.get("style_version") != 3]
            edl_path.write_text(json.dumps(edl, indent=2))
            session.session_notes.append(f"Generated {len(generated)} Seedance b-roll/effect clips")
            self.logger.info(f"[agent] Seedance clips added: {len(generated)}")
        else:
            raise RuntimeError("Seedance plan did not produce any usable ranges")

    async def _plan_seedance_clips(
        self,
        concept: dict,
        script: str,
        ranges: list[dict],
    ) -> list[dict]:
        """Ask a chat model where Seedance clips should replace stock footage."""
        fallback = self._fallback_seedance_plan(concept, ranges)
        prompt = f"""
You are an editorial explainer video director. Plan 2-3 AI-generated b-roll/effect clips
that should replace stock footage ranges in a narrated explainer video.

The desired feel is modern editorial explainer: smart visual metaphors, clean data-journalism
composition, kinetic but readable, no brand logos, no copyrighted characters, no onscreen text
unless it is abstract and not meant to be read.

Return ONLY valid JSON array. Each item:
{{
  "range_index": 0,
  "duration": 6,
  "model": "bytedance/seedance-2.0-fast",
  "prompt": "detailed video generation prompt",
  "reason": "why this visual matches the voiceover"
}}

Rules:
- Pick ranges where generated visuals add meaning, not random beauty shots.
- Use durations 4 to 15 seconds.
- Prompts should describe full-frame video, not transparent overlays.
- Avoid asking for exact readable text; video models are bad at typography.
- Mix with stock: replace only 2-3 ranges, leave the rest as stock footage.

Concept:
{json.dumps(concept, indent=2)[:2500]}

Script:
{script[:3000]}

Ranges:
{json.dumps(ranges, indent=2)[:5000]}
"""
        try:
            response = await self.llm.chat(
                [LLMMessage(role="user", content=prompt)],
                model="meta-llama/llama-3.3-70b-instruct",
            )
            if response:
                plan = self._extract_json_array(response.content)
                if isinstance(plan, list) and plan:
                    return plan
        except Exception as e:
            self.logger.warning(f"[agent] Seedance clip planning failed, using fallback: {e}")
        return fallback

    def _fallback_seedance_plan(self, concept: dict, ranges: list[dict]) -> list[dict]:
        """Deterministic fallback plan when the planner model is unavailable."""
        selected = []
        preferred_beats = ["HOOK", "EXAMPLE", "BENEFIT", "SOLUTION"]
        for beat in preferred_beats:
            for i, r in enumerate(ranges):
                if i in selected:
                    continue
                if str(r.get("beat", "")).upper() == beat:
                    selected.append(i)
                    break
            if len(selected) >= 3:
                break
        if not selected:
            selected = list(range(min(3, len(ranges))))

        items = []
        for i in selected:
            r = ranges[i]
            duration = max(4, min(15, math.ceil(float(r.get("end", 0)) - float(r.get("start", 0)))))
            items.append({
                "range_index": i,
                "duration": duration,
                "model": "bytedance/seedance-2.0-fast",
                "prompt": self._fallback_seedance_prompt(r, concept),
                "reason": f"Generated editorial visual for {r.get('beat', 'scene')} narration beat",
            })
        return items

    def _fallback_seedance_prompt(self, range_item: dict, concept: dict) -> str:
        title = concept.get("title") or "AI explainer"
        quote = range_item.get("quote") or range_item.get("reason") or title
        beat = str(range_item.get("beat", "EXPLAINER")).upper()
        return (
            "Modern editorial explainer b-roll, cinematic but clean, 16:9. "
            "Create an abstract data-journalism visual metaphor matching this narration beat: "
            f"{beat}. Narration idea: {quote}. "
            "Use layered UI-like panels, maps, diagrams, subtle animated charts, split-screen comparisons, "
            "and realistic technology environments. High production value, crisp motion, no logos, "
            "no copyrighted characters, no readable text, no watermark."
        )

    async def _create_openrouter_video(
        self,
        prompt: str,
        output_path: Path,
        duration: int,
        model: str = "bytedance/seedance-2.0-fast",
        resolution: str = "720p",
    ) -> Path:
        """Submit, poll, and download an OpenRouter video generation job."""
        if not config.settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        headers = {
            "Authorization": f"Bearer {config.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/video-agent",
            "X-Title": "Video Agent",
        }
        payload = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": "16:9",
            "duration": int(duration),
            "resolution": resolution,
            "generate_audio": False,
        }

        base_url = config.settings.openrouter_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=120) as client:
            self.logger.info(f"[agent] Seedance submit: {output_path.name} ({duration}s)")
            response = await client.post(f"{base_url}/videos", headers=headers, json=payload)
            response.raise_for_status()
            job = response.json()

            polling_url = str(job.get("polling_url") or "")
            if polling_url.startswith("/"):
                polling_url = self._openrouter_url(polling_url)
            if not polling_url:
                job_id = job.get("id")
                if not job_id:
                    raise RuntimeError(f"OpenRouter video job missing polling URL: {job}")
                polling_url = f"{base_url}/videos/{job_id}"

            final_job = job
            for _ in range(90):
                await asyncio.sleep(5)
                poll = await client.get(polling_url, headers=headers)
                poll.raise_for_status()
                final_job = poll.json()
                status = str(final_job.get("status", "")).lower()
                if status in {"completed", "succeeded", "success", "done"}:
                    break
                if status in {"failed", "error", "cancelled", "canceled"}:
                    raise RuntimeError(f"OpenRouter video generation failed: {final_job}")
            else:
                raise TimeoutError(f"Timed out waiting for OpenRouter video job: {polling_url}")

            video_url = self._video_download_url(final_job)
            if video_url:
                if video_url.startswith("/"):
                    video_url = self._openrouter_url(video_url)
                download = await client.get(video_url, headers=headers if "openrouter.ai" in video_url else None)
            else:
                job_id = final_job.get("id") or job.get("id")
                generation_id = final_job.get("generation_id") or job.get("generation_id")
                download_path = job_id or generation_id
                if not download_path:
                    raise RuntimeError(f"OpenRouter video job has no downloadable URL: {final_job}")
                download = await client.get(f"{base_url}/videos/{download_path}/content", headers=headers)

            download.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(download.content)
            if output_path.stat().st_size < 10000:
                raise RuntimeError(f"Downloaded Seedance video is too small: {output_path}")
            return output_path

    def _openrouter_url(self, path: str) -> str:
        base_url = config.settings.openrouter_base_url.rstrip("/")
        if path.startswith("http"):
            return path
        if path.startswith("/api/"):
            origin = base_url.split("/api/")[0]
            return f"{origin}{path}"
        return f"{base_url}{path if path.startswith('/') else '/' + path}"

    def _video_download_url(self, job: dict) -> Optional[str]:
        for key in ("unsigned_urls", "urls", "video_urls"):
            value = job.get(key)
            if isinstance(value, list) and value:
                return str(value[0])
        for key in ("url", "video_url", "download_url", "output_url"):
            value = job.get(key)
            if isinstance(value, str) and value:
                return value
        output = job.get("output")
        if isinstance(output, dict):
            return self._video_download_url(output)
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return self._video_download_url(first)
        return None

    async def _generate_local_semantic_effects(
        self,
        concept: dict,
        script: str,
        edl_path: Path,
        work_dir: Path,
        session: VideoSession,
    ) -> None:
        """Generate timed transparent semantic overlays and attach them to the EDL."""
        edl = json.loads(edl_path.read_text())
        effects_dir = work_dir / "effects"
        effects_dir.mkdir(parents=True, exist_ok=True)

        existing = edl.get("overlays") or []
        existing_effects = [o for o in existing if o.get("style_version") == 3]
        existing_other = [o for o in existing if o.get("style_version") != 3]
        if existing_effects and all(Path(o.get("file", "")).exists() for o in existing_effects):
            self.logger.info("[agent] ✓ effects (cached)")
            return

        ranges = edl.get("ranges", [])
        total_duration = float(edl.get("total_duration_s") or 0) or sum(
            max(0.0, float(r.get("end", 0)) - float(r.get("start", 0)))
            for r in ranges
        )
        plan = self._plan_cut_accents(ranges, total_duration)

        # Keep non-effect overlays (e.g. map_highlight/stat_card from Step 6a) — don't clobber them.
        overlays = list(existing_other)
        for i, effect in enumerate(plan[:5]):
            kind = str(effect.get("kind", "key_card"))
            start = max(0.0, float(effect.get("start_in_output", effect.get("start", 0.0))))
            duration = max(1.0, min(5.0, float(effect.get("duration", 2.4))))
            if total_duration:
                start = min(start, max(0.0, total_duration - duration))
            color = str(effect.get("color", "#66E3FF"))
            out_path = effects_dir / f"effect_{i:02d}_{kind}.mov"
            self._create_effect_overlay(kind, duration, color, out_path, effect=effect)
            overlays.append({
                "file": str(out_path),
                "start_in_output": round(start, 3),
                "duration": round(duration, 3),
                "kind": kind,
                "style_version": 3,
                "title": effect.get("title", ""),
                "content": effect.get("content", {}),
                "reason": effect.get("reason", ""),
            })

        edl["overlays"] = overlays
        edl_path.write_text(json.dumps(edl, indent=2))
        session.session_notes.append(f"Generated {len(overlays)} timed effects overlays")
        self.logger.info(f"[agent] effects added: {len(overlays)} overlays")

    def _plan_cut_accents(self, ranges: list[dict], total_duration: float) -> list[dict]:
        """Small edit accents, not slide/card explainers."""
        accents = []
        cursor = 0.0
        colors = ["#FFD400", "#FF3158", "#66E3FF", "#FFFFFF"]
        for i, r in enumerate(ranges[:10]):
            duration = max(0.65, min(1.15, (float(r.get("end", 0)) - float(r.get("start", 0))) * 0.18))
            start = min(max(0.0, cursor + 0.04), max(0.0, total_duration - duration))
            accents.append({
                "kind": "cut_flash" if i % 3 == 0 else "lower_accent",
                "start_in_output": start,
                "duration": duration,
                "color": colors[i % len(colors)],
                "title": "",
                "content": {},
                "reason": "Lightweight accent on cut boundary",
            })
            cursor += max(0.0, float(r.get("end", 0)) - float(r.get("start", 0)))
        return accents[:7]

    async def _plan_effects(
        self,
        concept: dict,
        script: str,
        ranges: list[dict],
        total_duration: float,
    ) -> list[dict]:
        fallback = self._fallback_effect_plan(ranges, total_duration)
        model = "meta-llama/llama-3.3-70b-instruct"
        prompt = f"""
You are a motion graphics editor. Plan content-aware visual overlays for a short YouTube video.

Return ONLY valid JSON as an array of 3 to 5 objects. Allowed kinds:
- comparison_table: compare two concepts mentioned in the narration
- key_card: show one short key idea with 2-3 supporting bullets
- timeline_steps: show 3 short stages or a sequence
- callout: emphasize one important phrase or question

Each object must have:
{{
  "kind": "...",
  "start_in_output": 0.0,
  "duration": 2.4,
  "color": "#66E3FF",
  "title": "short title",
  "content": {{}},
  "reason": "why this matches the voiceover"
}}

Content shapes:
- comparison_table: {{"left_label":"A","right_label":"B","rows":[["topic","A point","B point"]]}}
- key_card: {{"bullets":["short bullet","short bullet"]}}
- timeline_steps: {{"steps":["short step","short step","short step"]}}
- callout: {{"text":"short phrase"}}

Rules:
- Prefer semantic visuals over decoration. If the narration compares A vs B, use comparison_table.
- Use very short text: max 6 words per label/bullet.
- Do not cover the full frame; use lower-third or centered readable card.
- Last 1.8 to 5.0 seconds.
- Place effects near beat changes and important claims.
- Keep start_in_output within 0 to {total_duration:.2f} seconds.

Concept:
{json.dumps(concept, indent=2)[:3000]}

Script:
{script[:3000]}

EDL ranges:
{json.dumps(ranges, indent=2)[:4000]}
"""
        try:
            response = await self.llm.chat(
                [LLMMessage(role="user", content=prompt)],
                model=model,
            )
            if not response:
                return fallback
            plan = self._extract_json_array(response.content)
            if isinstance(plan, list) and plan:
                self.logger.info(f"[agent] effects planned with OpenRouter model: {model}")
                return plan
        except Exception as e:
            self.logger.warning(f"[agent] OpenRouter effects planning failed, using fallback: {e}")
        return fallback

    def _fallback_effect_plan(self, ranges: list[dict], total_duration: float) -> list[dict]:
        starts = []
        cursor = 0.0
        for r in ranges:
            beat = str(r.get("beat", "")).upper()
            if beat in {"HOOK", "PROBLEM", "SOLUTION", "BENEFIT", "CTA"}:
                starts.append(cursor)
            cursor += max(0.0, float(r.get("end", 0)) - float(r.get("start", 0)))
        if not starts:
            starts = [0.5, total_duration * 0.35, total_duration * 0.7]
        return [
            {
                "kind": "key_card",
                "start_in_output": starts[0] + 0.2,
                "duration": 2.8,
                "color": "#66E3FF",
                "title": "AI Agents Are Coming",
                "content": {
                    "bullets": ["Everyday workflows", "Autonomous decisions"],
                },
                "reason": "Visualizes the hook instead of adding decoration",
            },
            {
                "kind": "comparison_table",
                "start_in_output": starts[min(3, len(starts) - 1)] + 0.3,
                "duration": 4.2,
                "color": "#66E3FF",
                "title": "Today vs 2026",
                "content": {
                    "left_label": "Today",
                    "right_label": "2026",
                    "rows": [
                        ["Role", "Assistants", "Agents"],
                        ["Behavior", "Respond", "Act + adapt"],
                        ["Decisions", "Human-led", "Autonomous"],
                    ],
                },
                "reason": "The script contrasts current AI with expected 2026 AI agents",
            },
            {
                "kind": "timeline_steps",
                "start_in_output": starts[min(5, len(starts) - 1)] + 0.2,
                "duration": 3.8,
                "color": "#7CFFB2",
                "title": "Impact Areas",
                "content": {
                    "steps": ["Healthcare", "Education", "Transport", "Work"],
                },
                "reason": "Matches the list of societal impact areas in the voiceover",
            },
        ]

    def _extract_json_array(self, text: str) -> list:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
        data = json.loads(text)
        return data if isinstance(data, list) else []

    def _create_effect_overlay(
        self,
        kind: str,
        duration: float,
        color: str,
        output_path: Path,
        resolution: str = "1920x1080",
        effect: Optional[dict] = None,
    ) -> Path:
        """Create a transparent alpha overlay clip with FFmpeg."""
        effect = effect or {}
        if kind in {"comparison_table", "key_card", "timeline_steps", "callout"}:
            return self._create_semantic_overlay(kind, duration, color, output_path, resolution, effect)

        safe_color = color if color.startswith("#") and len(color) == 7 else "#66E3FF"
        color_expr = f"0x{safe_color[1:]}"
        duration_expr = f"{duration:.3f}"

        if kind == "pulse_frame":
            effect_filter = (
                f"drawbox=x=0:y=0:w=iw:h=ih:color={color_expr}@0.55:t=22,"
                f"drawbox=x=54:y=54:w=260:h=18:color={color_expr}@0.95:t=fill,"
                f"drawbox=x=54:y=54:w=18:h=160:color={color_expr}@0.95:t=fill,"
                f"drawbox=x=iw-314:y=ih-72:w=260:h=18:color={color_expr}@0.95:t=fill,"
                f"drawbox=x=iw-72:y=ih-214:w=18:h=160:color={color_expr}@0.95:t=fill"
            )
        elif kind == "lower_accent":
            effect_filter = (
                f"drawbox=x=iw*0.12:y=ih*0.84:"
                f"w='iw*0.76*min(t/{duration_expr},1)':h=8:"
                f"color={color_expr}@0.95:t=fill,"
                f"drawbox=x=iw*0.12:y=ih*0.86:"
                f"w='iw*0.52*min(t/{duration_expr},1)':h=4:"
                f"color=white@0.75:t=fill"
            )
        else:
            effect_filter = (
                f"drawbox=x='-260+(t/{duration_expr})*(iw+520)':"
                f"y=0:w=280:h=ih:color={color_expr}@0.42:t=fill,"
                f"drawbox=x='-160+(t/{duration_expr})*(iw+360)':"
                f"y=0:w=70:h=ih:color=white@0.32:t=fill"
            )

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=black@0.0:s={resolution}:r=24:d={duration_expr}",
            "-vf", f"format=rgba,{effect_filter}",
            "-an",
            "-c:v", "qtrle",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            self.logger.error(f"[agent] effect render failed:\n{result.stderr[-300:]}")
            raise RuntimeError(f"effect render failed: {kind}")
        return output_path

    def _create_semantic_overlay(
        self,
        kind: str,
        duration: float,
        color: str,
        output_path: Path,
        resolution: str,
        effect: dict,
    ) -> Path:
        """Create a readable content-aware overlay card as a transparent video."""
        from PIL import Image, ImageDraw, ImageFont
        import tempfile
        import textwrap

        width, height = [int(part) for part in resolution.split("x")]
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        font_paths = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
        ]

        def font(size: int, bold: bool = False):
            preferred = font_paths[0] if bold else font_paths[1]
            for path in [preferred, *font_paths]:
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
            return ImageFont.load_default()

        def hex_to_rgba(value: str, alpha: int = 255):
            value = value if value.startswith("#") and len(value) == 7 else "#66E3FF"
            return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)) + (alpha,)

        accent = hex_to_rgba(color, 255)
        white = (245, 250, 255, 255)
        muted = (190, 205, 215, 255)
        panel = (8, 12, 18, 242)
        line = (*accent[:3], 180)

        card_w = int(width * 0.72)
        card_h = int(height * 0.42)
        x0 = int((width - card_w) / 2)
        y0 = int(height * 0.51)
        if kind == "callout":
            card_h = int(height * 0.24)
            y0 = int(height * 0.58)
        x1 = x0 + card_w
        y1 = y0 + card_h

        draw.rounded_rectangle((x0, y0, x1, y1), radius=28, fill=panel, outline=line, width=4)
        draw.rectangle((x0, y0, x0 + 12, y1), fill=accent)

        title = str(effect.get("title") or kind.replace("_", " ").title())[:44]
        content = effect.get("content") if isinstance(effect.get("content"), dict) else {}
        title_font = font(56, bold=True)
        head_font = font(34, bold=True)
        body_font = font(30)
        small_font = font(26)

        draw.text((x0 + 42, y0 + 30), title, font=title_font, fill=white)

        if kind == "comparison_table":
            left_label = str(content.get("left_label", "A"))[:16]
            right_label = str(content.get("right_label", "B"))[:16]
            rows = content.get("rows") if isinstance(content.get("rows"), list) else []
            rows = rows[:4] or [["Role", "Now", "Next"], ["Action", "Manual", "Autonomous"]]

            table_x = x0 + 46
            table_y = y0 + 116
            col_w = [int(card_w * 0.25), int(card_w * 0.32), int(card_w * 0.32)]
            row_h = 56
            draw.rounded_rectangle(
                (table_x, table_y, x1 - 44, table_y + row_h),
                radius=16,
                fill=(*accent[:3], 210),
            )
            dark_text = (5, 12, 18, 255)
            draw.text((table_x + col_w[0] + 18, table_y + 12), left_label, font=head_font, fill=dark_text)
            draw.text((table_x + col_w[0] + col_w[1] + 26, table_y + 12), right_label, font=head_font, fill=dark_text)

            for idx, row in enumerate(rows):
                label, left, right = (list(row) + ["", "", ""])[:3]
                y = table_y + row_h * (idx + 1)
                row_fill = (0, 0, 0, 132 if idx % 2 == 0 else 96)
                draw.rectangle((table_x, y, x1 - 44, y + row_h), fill=row_fill)
                draw.text((table_x + 12, y + 14), str(label)[:18], font=small_font, fill=muted)
                draw.text((table_x + col_w[0] + 18, y + 14), str(left)[:22], font=small_font, fill=white)
                draw.text((table_x + col_w[0] + col_w[1] + 26, y + 14), str(right)[:22], font=small_font, fill=white)

        elif kind == "timeline_steps":
            steps = content.get("steps") if isinstance(content.get("steps"), list) else []
            steps = [str(step)[:18] for step in (steps[:4] or ["Step 1", "Step 2", "Step 3"])]
            base_y = y0 + 170
            gap = int((card_w - 120) / max(1, len(steps) - 1))
            for idx, step in enumerate(steps):
                cx = x0 + 64 + idx * gap
                if idx > 0:
                    draw.line((x0 + 64 + (idx - 1) * gap + 28, base_y, cx - 28, base_y), fill=line, width=8)
                draw.ellipse((cx - 34, base_y - 34, cx + 34, base_y + 34), fill=accent)
                draw.text((cx - 10, base_y - 20), str(idx + 1), font=head_font, fill=(5, 12, 18, 255))
                wrapped = "\n".join(textwrap.wrap(step, width=12))
                draw.multiline_text((cx - 80, base_y + 54), wrapped, font=body_font, fill=white, align="center")

        elif kind == "callout":
            text = str(content.get("text") or title)[:70]
            wrapped = "\n".join(textwrap.wrap(text, width=36))
            draw.multiline_text((x0 + 56, y0 + 118), wrapped, font=font(44, bold=True), fill=white, spacing=8)

        else:
            bullets = content.get("bullets") if isinstance(content.get("bullets"), list) else []
            bullets = [str(bullet)[:42] for bullet in (bullets[:3] or ["Key insight", "Why it matters"])]
            y = y0 + 126
            for bullet in bullets:
                draw.ellipse((x0 + 58, y + 14, x0 + 76, y + 32), fill=accent)
                wrapped = "\n".join(textwrap.wrap(bullet, width=34))
                draw.multiline_text((x0 + 96, y), wrapped, font=font(38, bold=True), fill=white, spacing=6)
                y += 70

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            png_path = Path(tmp.name)
        image.save(png_path)

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", str(png_path),
                "-t", f"{duration:.3f}",
                "-r", "24",
                "-vf", "format=argb",
                "-an",
                "-c:v", "qtrle",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        png_path.unlink(missing_ok=True)
        if result.returncode != 0:
            self.logger.error(f"[agent] semantic overlay render failed:\n{result.stderr[-300:]}")
            raise RuntimeError(f"semantic overlay render failed: {kind}")
        return output_path

    # ── Private: Asset fetching ───────────────────────────────────────────────

    async def _fetch_stock_videos(self, keywords: list[str], work_dir: Path, max_per_keyword: int = 5) -> list:
        self.logger.info(f"[agent] fetching stock videos: {keywords}")
        videos = []
        seen_ids: set = set()
        for kw in keywords:
            try:
                results = await self.video_fetcher.search_all_sources(kw, max_results_per_source=10)
                landscape = [v for v in results if v.width >= v.height]
                # Deduplicate by URL to prevent same clip under different keywords
                unique = []
                for v in landscape:
                    vid_id = getattr(v, "id", None) or v.url
                    if vid_id not in seen_ids:
                        seen_ids.add(vid_id)
                        unique.append(v)
                videos.extend(unique[:max_per_keyword])
            except Exception as e:
                self.logger.warning(f"[agent] stock fetch failed for '{kw}': {e}")
        self.logger.info(f"[agent] fetched {len(videos)} unique landscape stock clips")
        return videos

    async def _generate_voiceover(self, script: str, work_dir: Path) -> Optional[Path]:
        from src.modules.voice_subtitle import VoiceoverGenerator
        from src.core import config
        out_path = work_dir / "voiceover.mp3"
        language = config.settings.voice_language or "vi"
        preferred_provider = (config.settings.tts_provider or "").strip().lower()
        if preferred_provider:
            providers = [preferred_provider, *[p for p in ("elevenlabs", "edge_tts", "gtts") if p != preferred_provider]]
        elif language.startswith("vi") and (config.settings.elevenlabs_voice_id or config.settings.voice_id) == "pFZP5JQG7iQjIQuC4Bku":
            providers = ["edge_tts", "elevenlabs", "gtts"]
            self.logger.warning(
                "[agent] configured ElevenLabs voice is Lily/British English; using Edge Vietnamese first"
            )
        else:
            providers = ["elevenlabs", "edge_tts", "gtts"]

        for provider in providers:
            try:
                gen = VoiceoverGenerator(provider)
                result = await gen.generate_voiceover(
                    script,
                    output_path=str(out_path),
                    language=language,
                )
                self.logger.info(f"[agent] voiceover via {provider}")
                self._write_voiceover_fingerprint(out_path, script)
                return Path(result) if result else None
            except Exception as e:
                self.logger.warning(f"[agent] voiceover {provider} failed: {e}")
        self.logger.error("[agent] all TTS providers failed")
        return None

    def _voiceover_fingerprint(self, script: str) -> str:
        from src.core import config
        payload = {
            "script": script,
            "tts_provider": config.settings.tts_provider,
            "voice_language": config.settings.voice_language,
            "elevenlabs_voice_id": config.settings.elevenlabs_voice_id,
            "voice_id": config.settings.voice_id,
            "elevenlabs_model_id": config.settings.elevenlabs_model_id,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _voiceover_fresh(self, voiceover_path: Path, script: str) -> bool:
        if not voiceover_path.exists() or voiceover_path.stat().st_size < 1000:
            return False
        meta_path = voiceover_path.with_suffix(".meta.json")
        if not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            return False
        return meta.get("fingerprint") == self._voiceover_fingerprint(script)

    def _write_voiceover_fingerprint(self, voiceover_path: Path, script: str) -> None:
        from src.core import config
        meta = {
            "fingerprint": self._voiceover_fingerprint(script),
            "tts_provider": config.settings.tts_provider,
            "voice_language": config.settings.voice_language,
            "elevenlabs_voice_id": config.settings.elevenlabs_voice_id,
            "voice_id": config.settings.voice_id,
            "elevenlabs_model_id": config.settings.elevenlabs_model_id,
        }
        voiceover_path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    async def _transcribe_and_pack(self, audio_path: Path, work_dir: Path) -> Path:
        from src.core import config
        transcript_dir = work_dir / "transcripts"
        self._run_helper([
            "helpers/transcribe.py", str(audio_path),
            "--output", str(transcript_dir),
            "--language", config.settings.voice_language or "vi",
        ], "transcribe voiceover")

        packed_md = work_dir / "takes_packed.md"
        self._run_helper([
            "helpers/pack_transcripts.py", str(transcript_dir),
            "--output", str(packed_md),
        ], "pack transcripts")
        return packed_md

    def _transcript_fresh(self, audio_path: Path, packed_md: Path) -> bool:
        from src.core import config
        transcript_json = packed_md.parent / "transcripts" / f"{audio_path.stem}.json"
        transcript_cache = packed_md.parent / "transcripts" / f"{audio_path.stem}.cache"
        if not packed_md.exists() or packed_md.stat().st_size < 10:
            return False
        if not transcript_json.exists() or not transcript_cache.exists():
            return False
        language = config.settings.voice_language or "vi"
        if f":{language.split('-')[0]}" not in transcript_cache.read_text().strip():
            return False
        newest_transcript = min(transcript_json.stat().st_mtime, transcript_cache.stat().st_mtime)
        return packed_md.stat().st_mtime >= newest_transcript and packed_md.stat().st_mtime >= audio_path.stat().st_mtime

    # ── Private: Render + Self-eval ───────────────────────────────────────────

    def _render(self, edl_path: Path, output_path: Path, resolution: str = "1920x1080"):
        self._run_helper([
            "helpers/render.py", str(edl_path),
            "--output", str(output_path),
            "--res", resolution,
        ], "render video")

    def _render_fingerprint(self, deps: list[Path]) -> str:
        payload = {
            "render_version": self.RENDER_CACHE_VERSION,
            "deps": [
                {
                    "path": str(dep),
                    "mtime": dep.stat().st_mtime if dep.exists() else None,
                    "size": dep.stat().st_size if dep.exists() else None,
                }
                for dep in deps
            ],
        }
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _render_fresh(self, output_path: Path, deps: list[Path]) -> bool:
        if not output_path.exists() or output_path.stat().st_size < 10000:
            return False
        output_mtime = output_path.stat().st_mtime
        if any(dep.exists() and output_mtime < dep.stat().st_mtime for dep in deps):
            return False
        meta_path = output_path.with_suffix(".render.json")
        if not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            return False
        return meta.get("fingerprint") == self._render_fingerprint(deps)

    def _write_render_fingerprint(self, output_path: Path, deps: list[Path]) -> None:
        meta = {
            "fingerprint": self._render_fingerprint(deps),
            "render_version": self.RENDER_CACHE_VERSION,
            "deps": [str(dep) for dep in deps],
        }
        output_path.with_suffix(".render.json").write_text(json.dumps(meta, indent=2))

    def _has_audio_stream(self, video_path: Path) -> bool:
        """Return True when an MP4 has at least one audio stream."""
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                str(video_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        try:
            streams = json.loads(result.stdout).get("streams", [])
        except json.JSONDecodeError:
            return False
        return any(stream.get("codec_type") == "audio" for stream in streams)

    async def _self_eval_loop(
        self,
        preview_path: Path,
        edl_path: Optional[Path],
        work_dir: Path,
        session: VideoSession,
    ) -> Optional[Path]:
        """Self-evaluation: check cut boundaries, cap at MAX_SELF_EVAL_PASSES."""
        verify_dir = work_dir / "verify"

        for pass_num in range(1, self.MAX_SELF_EVAL_PASSES + 1):
            session.self_eval_passes = pass_num
            self.logger.info(f"[agent] self-eval pass {pass_num}/{self.MAX_SELF_EVAL_PASSES}")

            verify_dir_pass = verify_dir / f"pass_{pass_num}"
            timeline_cmd = [
                "helpers/timeline_view.py", str(preview_path),
                "--output", str(verify_dir_pass),
            ]
            if edl_path:
                timeline_cmd += ["--cuts", str(edl_path)]
            self._run_helper(timeline_cmd, f"timeline view pass {pass_num}")

            # Claude Code reviews the PNGs and decides whether to continue
            # In programmatic mode: if no issues detected, promote preview → final
            final_path = work_dir / "final.mp4"
            import shutil
            shutil.copy2(str(preview_path), str(final_path))

            self.logger.info(
                f"[agent] self-eval pass {pass_num} done. "
                f"Verify PNGs: {verify_dir_pass}"
            )
            session.session_notes.append(
                f"Self-eval pass {pass_num}: verify PNGs at {verify_dir_pass}"
            )

            # In interactive mode, Claude Code reviews PNGs and iterates
            # In automated mode, we stop after first pass (no visual judge)
            return final_path

        self.logger.warning(
            f"[agent] self-eval: {self.MAX_SELF_EVAL_PASSES} passes done, "
            "flagging to user for manual review"
        )
        return preview_path

    # ── Private: Upload ───────────────────────────────────────────────────────

    async def _upload(self, video_path: Path, metadata: dict):
        title = metadata.get("title", f"Video {datetime.now().strftime('%Y-%m-%d')}")
        description = metadata.get("description", "")
        tags = metadata.get("tags", [])
        try:
            video_id = self.uploader.upload_video(
                str(video_path),
                title=title,
                description=description,
                tags=tags[:15],
                is_public=True,
            )
            self.logger.info(f"[agent] uploaded: youtube.com/watch?v={video_id}")
        except Exception as e:
            self.logger.error(f"[agent] upload failed: {e}")

    # ── Private: Session memory ───────────────────────────────────────────────

    def _load_session(self, session: VideoSession):
        project_md = Path(session.work_dir) / "project.md"
        if project_md.exists():
            content = project_md.read_text()
            lines = content.strip().split("\n")
            last_session_line = next(
                (l for l in reversed(lines) if l.startswith("## Session")), None
            )
            if last_session_line:
                self.logger.info(f"[agent] Resuming: {last_session_line}")

    def _persist_session(self, session: VideoSession):
        project_md = Path(session.work_dir) / "project.md"
        session_num = 1
        if project_md.exists():
            content = project_md.read_text()
            session_nums = [
                int(l.split()[2])
                for l in content.split("\n")
                if l.startswith("## Session ")
                and l.split()[2].isdigit()
            ]
            session_num = max(session_nums, default=0) + 1

        entry = f"""
## Session {session_num} — {datetime.now().strftime('%Y-%m-%d %H:%M')}

**Topic:** {session.topic}
**Strategy:** Generated concept → script → EDL → render → self-eval
**Decisions:** EDL at {session.edl_path or 'N/A'}, final at {session.final_path or 'N/A'}
**Reasoning log:** {'; '.join(session.session_notes) or 'standard pipeline'}
**Self-eval passes:** {session.self_eval_passes}
**Outstanding:** Review verify/ PNGs for manual quality check
"""
        with open(project_md, "a") as f:
            f.write(entry)
        self.logger.info(f"[agent] session persisted → {project_md}")

    # ── Private: Subprocess helper ────────────────────────────────────────────

    def _run_helper(self, args: list[str], label: str = ""):
        cmd = [sys.executable] + args
        self.logger.info(f"[agent] {label or args[1]}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            self.logger.error(f"[agent] {label} failed:\n{result.stderr[-300:]}")
            raise RuntimeError(f"helper failed: {label}")
        return result.stdout

    async def close(self):
        await self.llm.close()

    def _print_strategy(self, concept: dict, duration: float):
        self.logger.info(
            f"[agent] STRATEGY\n"
            f"  Title: {concept.get('title')}\n"
            f"  Hook: {concept.get('hook')}\n"
            f"  Duration: {duration}s\n"
            f"  Keywords: {', '.join(concept.get('keywords', [])[:5])}\n"
            f"  Structure: {len(concept.get('structure', []))} sections"
        )
