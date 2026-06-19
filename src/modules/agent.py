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
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
import httpx
from pydantic import BaseModel
from loguru import logger

from src.core import OpenRouterLLM, LLMConfig, LLMMessage, config
from src.modules.content_search import ContentSearcher
from src.modules.video_fetcher import StockVideo, StockVideoFetcher
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
    EDL_CACHE_VERSION = "edl_stock_visual_match_v3"
    OVERLAY_CACHE_VERSION = "map_label_dedupe_v2"
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
        stop_at: Optional[str] = None,
        batch_count: int = 1,
    ) -> Optional[str]:
        """
        Workflow A: topic → final.mp4 (+ optional YouTube upload).

        mode="edl" (default):
          1. Concept → script → stock footage → voiceover → EDL → render → self-eval

        mode="storyboard":
          1. Concept → script → voiceover → storyboard (LLM) → scene_assembler
             (Remotion + AI images + optional Seedance, $1 budget cap)

        stop_at: stop pipeline at a specific step and return intermediate result.
          Options: "concept", "script", "terms", "audio", "materials", "storyboard", "render", None (full)
        
        batch_count: generate N versions of the video, return the best one (by file size heuristic).
        """
        if batch_count > 1:
            return await self._batch_generate(
                topic=topic, keywords=keywords, duration=duration,
                auto_upload=auto_upload, apply_effects=apply_effects,
                effects_mode=effects_mode, confirm_strategy=confirm_strategy,
                mode=mode, stop_at=stop_at, batch_count=batch_count,
            )
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
        concept_fingerprint = self._artifact_fingerprint({
            "step": "concept",
            "topic": planning_topic,
            "duration": duration,
        })
        if done(concept_path) and self._artifact_cache_fresh(concept_path, concept_fingerprint):
            self.logger.info("[agent] ✓ concept (cached)")
            concept = json.loads(concept_path.read_text())
        else:
            concept = await self._generate_concept(planning_topic, duration, session)
            if not concept:
                return None
            self._write_artifact_cache(concept_path, concept_fingerprint)

        if stop_at == "concept":
            self.logger.info(f"[agent] stop_at=concept → {concept_path}")
            return str(concept_path)

        if confirm_strategy:
            self._print_strategy(concept, duration)

        # ── Step 1b: Deep Research (web search → fact brief) ──────────────────
        research_path = work_dir / "research_brief.json"
        research_fingerprint = self._artifact_fingerprint({
            "step": "research",
            "concept": concept,
        })
        if done(research_path) and self._artifact_cache_fresh(research_path, research_fingerprint):
            self.logger.info("[agent] ✓ research brief (cached)")
        else:
            await self._run_deep_research(concept, work_dir, session)
            if research_path.exists():
                self._write_artifact_cache(research_path, research_fingerprint)

        # ── Step 2: Script ────────────────────────────────────────────────────
        script_path = work_dir / "script.txt"
        script_fingerprint = self._artifact_fingerprint({
            "step": "script",
            "concept": concept,
            "duration": duration,
            "has_research": research_path.exists(),
        })
        if done(script_path) and self._artifact_cache_fresh(script_path, script_fingerprint):
            self.logger.info("[agent] ✓ script (cached)")
            script = self._extract_script_text(script_path.read_text())
            script_path.write_text(script)  # rewrite clean in case cache has old JSON
            self._write_artifact_cache(script_path, script_fingerprint)
        else:
            script = await self._generate_script(concept, duration, session)
            if not script:
                return None
            self._write_artifact_cache(script_path, script_fingerprint)

        if stop_at == "script":
            self.logger.info(f"[agent] stop_at=script → {script_path}")
            return str(script_path)

        # Storyboard mode has its own voiceover/assembly path and does not use
        # EDL stock search or transcript packing. Branch early to avoid wasted API work.
        if mode == "storyboard":
            return await self._storyboard_workflow(
                concept, script, duration, work_dir, session,
                auto_upload=auto_upload,
            )

        # ── Step 2b: Per-sentence search terms (audio-matched stock diversity) ──
        search_terms_path = work_dir / "search_terms.json"
        search_terms_fingerprint = self._artifact_fingerprint({
            "step": "search_terms",
            "script": script,
            "concept": concept,
        })
        if done(search_terms_path) and self._artifact_cache_fresh(search_terms_path, search_terms_fingerprint):
            self.logger.info("[agent] ✓ search terms (cached)")
            per_sentence_terms = json.loads(search_terms_path.read_text())
        else:
            per_sentence_terms = await self._generate_search_terms(script, concept, work_dir)
            if search_terms_path.exists():
                self._write_artifact_cache(search_terms_path, search_terms_fingerprint)

        if stop_at == "terms":
            self.logger.info(f"[agent] stop_at=terms → {search_terms_path}")
            return str(search_terms_path)

        # ── Step 3: Stock videos (per-sentence keywords for diversity) ────────
        # Use per-sentence terms as PRIMARY keywords (each unique)
        # Fallback to concept keywords + generic visual keywords if per-sentence failed
        if per_sentence_terms:
            # Extract unique search terms from per-sentence analysis
            sentence_keywords = list(dict.fromkeys(
                item.get("search_term", "") for item in per_sentence_terms if item.get("search_term")
            ))
            # concept["keywords"] are SEO terms for YouTube metadata (often in the topic's own
            # language, e.g. Vietnamese) — wrong tool for stock search, which needs concrete
            # English visual phrases. Don't mix them into the stock-search pool.
            all_keywords = list(dict.fromkeys(sentence_keywords + list(keywords)))[:25]
        else:
            visual_keywords = [
                "satellite map", "world map animation", "aerial landscape",
                "city timelapse", "people market", "mountains river",
                "data visualization", "travel documentary", "drone footage city",
                "rural village life", "industrial factory", "ocean coastline",
                "desert landscape", "forest canopy", "busy street crowd",
            ]
            all_keywords = list(dict.fromkeys(list(keywords) + visual_keywords))[:20]

        clips_per_keyword = 4
        stock_videos = await self._fetch_stock_videos(
            all_keywords, work_dir, max_per_keyword=clips_per_keyword,
            topic_context=topic,
        )

        if stop_at == "materials":
            self.logger.info(f"[agent] stop_at=materials → {len(stock_videos)} clips fetched")
            return str(work_dir / "materials_fetched")

        # ── Step 4: Voiceover ─────────────────────────────────────────────────
        voiceover_path = work_dir / "voiceover.mp3"
        if self._voiceover_fresh(voiceover_path, script):
            self.logger.info("[agent] ✓ voiceover (cached)")
        else:
            result = await self._generate_voiceover(script, work_dir)
            if not result:
                return None
            voiceover_path = result

        if stop_at == "audio":
            self.logger.info(f"[agent] stop_at=audio → {voiceover_path}")
            return str(voiceover_path)

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

        # ── Step 6: EDL ───────────────────────────────────────────────────────
        edl_path = work_dir / "edl.json"
        stock_cache_path = work_dir / "stock_videos.json"
        edl_deps = [packed_md, stock_cache_path]
        if self._edl_fresh(edl_path, edl_deps):
            self.logger.info("[agent] ✓ EDL (cached)")
        else:
            edl_path = await self._generate_edl(packed_md, concept, stock_videos, work_dir)
            if not edl_path:
                return None
        session.edl_path = str(edl_path)

        # ── Step 6a: Map overlay graphics (map_highlight only — stat_card still looks
        # cheap as a flat full-screen card, kept disabled until it gets a real redesign)
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

        if stop_at == "render":
            self.logger.info(f"[agent] stop_at=render → {preview_path}")
            return str(preview_path)

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
            await self._upload(final_path, metadata, concept=concept, topic=topic)

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

        def fresh(path: Path, deps: list[Path], min_bytes: int = 10) -> bool:
            if not done(path, min_bytes=min_bytes):
                return False
            output_mtime = path.stat().st_mtime
            return all(not dep.exists() or output_mtime >= dep.stat().st_mtime for dep in deps)

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
        storyboard_fingerprint = self._artifact_fingerprint({
            "step": "storyboard",
            "script": script,
            "concept": concept,
            "duration": duration,
        })
        if done(storyboard_path) and self._artifact_cache_fresh(storyboard_path, storyboard_fingerprint):
            self.logger.info("[agent] ✓ storyboard (cached)")
        else:
            storyboard_path = await self._generate_storyboard(
                script, concept, duration, work_dir
            )
            if not storyboard_path:
                self.logger.warning("[agent] storyboard failed, falling back to EDL mode")
                return None
            self._write_artifact_cache(storyboard_path, storyboard_fingerprint)
        session.storyboard_path = str(storyboard_path)

        # Step S3: Scene assembly (Remotion + AI images + Seedance)
        assembled_path = work_dir / "assembled.mp4"
        if fresh(assembled_path, [storyboard_path, voiceover_path], min_bytes=10000):
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
            await self._upload(final_path, metadata, concept=concept, topic=session.topic)

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

        def fresh(path: Path, deps: list[Path], min_bytes: int = 10) -> bool:
            if not done(path, min_bytes=min_bytes):
                return False
            output_mtime = path.stat().st_mtime
            return all(not dep.exists() or output_mtime >= dep.stat().st_mtime for dep in deps)

        edl_path = work_dir / "edl.json"
        stock_cache_path = work_dir / "stock_videos.json"
        if self._edl_fresh(edl_path, [packed_md, stock_cache_path]):
            self.logger.info("[agent] ✓ EDL (cached)")
        else:
            edl_path = await self._generate_edl(packed_md, concept, stock_videos, work_dir)
            if not edl_path:
                return None
        session.edl_path = str(edl_path)

        storyboard_path = work_dir / "storyboard.json"
        hybrid_storyboard_fingerprint = self._artifact_fingerprint({
            "step": "hybrid_storyboard",
            "script": script,
            "concept": concept,
            "duration": duration,
            "edl": edl_path.read_text() if edl_path.exists() else "",
        })
        if (
            self._json_cache_valid(storyboard_path)
            and self._artifact_cache_fresh(storyboard_path, hybrid_storyboard_fingerprint)
        ):
            self.logger.info("[agent] ✓ hybrid storyboard (cached)")
        else:
            storyboard_path = await self._generate_hybrid_storyboard(
                script, concept, duration, edl_path, stock_videos, work_dir
            )
            if not storyboard_path:
                return None
            self._write_artifact_cache(storyboard_path, hybrid_storyboard_fingerprint)
        session.storyboard_path = str(storyboard_path)

        assembled_path = work_dir / "assembled.mp4"
        if fresh(assembled_path, [storyboard_path, voiceover_path], min_bytes=10000):
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
            await self._upload(final_path, metadata, concept=concept, topic=session.topic)

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
    def _artifact_fingerprint(payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _artifact_meta_path(path: Path) -> Path:
        return path.with_name(f"{path.name}.meta.json")

    def _artifact_cache_fresh(self, path: Path, fingerprint: str) -> bool:
        meta_path = self._artifact_meta_path(path)
        if not path.exists() or not meta_path.exists():
            return False
        if meta_path.stat().st_mtime < path.stat().st_mtime:
            return False
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            return False
        return meta.get("fingerprint") == fingerprint

    def _write_artifact_cache(self, path: Path, fingerprint: str) -> None:
        self._artifact_meta_path(path).write_text(json.dumps({
            "fingerprint": fingerprint,
        }, indent=2))

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

    async def _run_deep_research(
        self, concept: dict, work_dir: Path, session: VideoSession
    ) -> Optional[dict]:
        """Step 1b: Web search → deep_research LLM → structured fact brief."""
        research_raw_path = work_dir / "_research_raw.json"
        research_path = work_dir / "research_brief.json"

        # Build search queries from concept
        topic = concept.get("title", session.topic)
        research_questions = concept.get("research_questions", [])
        keywords = concept.get("keywords", [])

        # Combine: topic + concept research questions + keyword-derived queries
        queries = [topic]
        for q in research_questions:
            queries.append(q)
        # Add keyword combos for breadth
        if keywords:
            queries.append(f"{topic} {keywords[0]} statistics data")
            if len(keywords) > 2:
                queries.append(f"{topic} {keywords[2]} facts")

        queries_str = ",".join(queries[:12])  # cap at 12 queries

        # Step 1: Web search
        self.logger.info(f"[agent] running web research: {len(queries)} queries")
        try:
            self._run_helper([
                "helpers/web_research.py",
                "--topic", topic,
                "--queries", queries_str,
                "--output", str(research_raw_path),
            ], "web research")
        except RuntimeError as e:
            self.logger.warning(f"[agent] web research failed: {e}")
            return None

        if not research_raw_path.exists():
            self.logger.warning("[agent] web research produced no output")
            return None

        # Step 2: LLM distills raw search results → structured fact brief
        self.logger.info("[agent] distilling research into fact brief")
        try:
            self._run_helper([
                "helpers/llm_task.py",
                "--task", "deep_research",
                "--input", session.topic,
                "--context", str(research_raw_path),
                "--output", str(research_path),
            ], "deep research distillation")
        except RuntimeError as e:
            self.logger.warning(f"[agent] research distillation failed: {e}")
            return None

        if not research_path.exists():
            return None

        try:
            brief = json.loads(research_path.read_text())
            n_facts = len(brief.get("key_findings", []))
            n_stats = len(brief.get("statistics", []))
            n_quotes = len(brief.get("notable_quotes", []))
            self.logger.info(
                f"[agent] ✓ research brief: {n_facts} findings, {n_stats} stats, {n_quotes} quotes"
            )
            session.session_notes.append(
                f"Research: {n_facts} findings, {n_stats} stats, {n_quotes} quotes"
            )
            return brief
        except json.JSONDecodeError:
            self.logger.warning("[agent] research_brief.json invalid JSON")
            return None

    async def _generate_script(
        self, concept: dict, duration: float, session: VideoSession
    ) -> Optional[str]:
        concept_path = Path(session.work_dir) / "concept.json"
        script_path = Path(session.work_dir) / "script.txt"
        draft_path = Path(session.work_dir) / "script.draft.txt"
        research_path = Path(session.work_dir) / "research_brief.json"

        # Build context: concept + research brief if available
        context_path = concept_path
        if research_path.exists():
            # Merge concept + research into a single context file for the LLM
            merged_context_path = Path(session.work_dir) / "_script_context.json"
            merged = {
                "concept": concept,
                "research_brief": json.loads(research_path.read_text()),
            }
            merged_context_path.write_text(
                json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            context_path = merged_context_path
            self.logger.info("[agent] script generation will use research brief")

        self._run_helper([
            "helpers/llm_task.py",
            "--task", "generate_script",
            "--input", str(concept_path),
            "--context", str(context_path),
            "--duration", str(int(duration)),
            "--output", str(draft_path),
        ], "generate script")
        if not draft_path.exists():
            self.logger.error("[agent] script generation failed")
            return None
        script = draft_path.read_text().strip()
        if not script:
            self.logger.error("[agent] script file is empty")
            return None
        # LLM now returns plain text — but defensively strip JSON wrapper if present
        script = self._extract_script_text(script)
        draft_path.write_text(script)

        try:
            self._run_helper([
                "helpers/llm_task.py",
                "--task", "audit_script",
                "--input", str(draft_path),
                "--context", str(concept_path),
                "--duration", str(int(duration)),
                "--output", str(script_path),
            ], "audit and correct script")
            audited = self._extract_script_text(script_path.read_text().strip())
            if audited:
                script = audited
        except Exception as e:
            self.logger.warning(f"[agent] script audit failed; using generated draft: {e}")

        script_path.write_text(script)
        session.script = script
        return script

    async def _generate_search_terms(
        self, script: str, concept: dict, work_dir: Path
    ) -> list[dict]:
        """Generate per-sentence search terms for diverse stock footage."""
        script_path = work_dir / "script.txt"
        concept_path = work_dir / "concept.json"
        terms_path = work_dir / "search_terms.json"

        if not script_path.exists():
            script_path.write_text(script)
        if not concept_path.exists():
            concept_path.write_text(json.dumps(concept, indent=2, ensure_ascii=False))

        try:
            self._run_helper([
                "helpers/llm_task.py",
                "--task", "generate_search_terms",
                "--input", str(script_path),
                "--context", str(concept_path),
                "--output", str(terms_path),
            ], "generate per-sentence search terms")
        except RuntimeError as e:
            self.logger.warning(f"[agent] search terms generation failed: {e}")
            return []

        if not terms_path.exists():
            return []

        try:
            terms = json.loads(terms_path.read_text())
            if isinstance(terms, list):
                self.logger.info(f"[agent] generated {len(terms)} per-sentence search terms")
                return terms
        except json.JSONDecodeError:
            self.logger.warning("[agent] search_terms.json invalid")
        return []

    async def _batch_generate(
        self,
        topic: str,
        keywords: Optional[list[str]],
        duration: float,
        auto_upload: bool,
        apply_effects: bool,
        effects_mode: str,
        confirm_strategy: bool,
        mode: str,
        stop_at: Optional[str],
        batch_count: int,
    ) -> Optional[str]:
        """Generate N versions of the video, pick the best by file size + duration match."""
        import shutil

        self.logger.info(f"[agent] batch mode: generating {batch_count} versions")
        base_work_dir = Path(config.settings.output_dir) / "edit"
        results: list[tuple[str, int]] = []  # (path, score)

        for i in range(batch_count):
            batch_dir = base_work_dir / f"batch_{i}"
            batch_dir.mkdir(parents=True, exist_ok=True)

            # Temporarily override output dir for this batch
            original_output = config.settings.output_dir
            config.settings.output_dir = str(batch_dir.parent.parent)

            # Clear caches that should differ between batches
            # (stock videos, storyboard — keep concept and script shared)
            for cache_file in ["search_terms.json", "storyboard.json", "edl.json"]:
                (batch_dir / cache_file).unlink(missing_ok=True)

            # Copy shared artifacts (concept, script, voiceover) to save LLM/TTS calls
            for shared in ["concept.json", "script.txt", "voiceover.mp3"]:
                src = base_work_dir / shared
                dst = batch_dir / shared
                if src.exists() and not dst.exists():
                    shutil.copy2(str(src), str(dst))

            try:
                # Generate with batch_count=1 to avoid recursion
                result = await self.generate_video(
                    topic=topic, keywords=keywords, duration=duration,
                    auto_upload=False, apply_effects=apply_effects,
                    effects_mode=effects_mode, confirm_strategy=(confirm_strategy and i == 0),
                    mode=mode, stop_at=stop_at, batch_count=1,
                )
                if result:
                    path = Path(result)
                    if path.exists():
                        # Score: prefer larger files (more content) close to target duration
                        size_score = path.stat().st_size
                        results.append((str(path), size_score))
                        self.logger.info(f"[agent] batch {i+1}/{batch_count}: {path.name} ({size_score // 1024}KB)")
            except Exception as e:
                self.logger.warning(f"[agent] batch {i+1} failed: {e}")
            finally:
                config.settings.output_dir = original_output

        if not results:
            self.logger.error("[agent] all batch attempts failed")
            return None

        # Pick best (highest score = largest file with most content)
        results.sort(key=lambda x: x[1], reverse=True)
        best_path = results[0][0]
        self.logger.info(
            f"[agent] batch winner: {Path(best_path).name} "
            f"({results[0][1] // 1024}KB, best of {len(results)})"
        )

        # Copy winner to final location
        final_path = base_work_dir / "final.mp4"
        shutil.copy2(best_path, str(final_path))

        if auto_upload:
            script_path = base_work_dir / "script.txt"
            script = script_path.read_text() if script_path.exists() else topic
            metadata = await self._generate_seo(script, base_work_dir)
            concept_path = base_work_dir / "concept.json"
            concept = json.loads(concept_path.read_text()) if concept_path.exists() else {}
            await self._upload(final_path, metadata, concept=concept, topic=topic)

        return str(final_path)

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
        stock_library = self._build_stock_library(stock_videos)
        if not sources and stock_videos:
            sources = {
                f"stock_{i}": v.download_url or v.url
                for i, v in enumerate(stock_videos[:15])
            }
        context_path.write_text(json.dumps({
            "sources": sources,
            "stock_library": stock_library[:15],
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
        edl_path = await self._repair_edl_stock_matches(edl_path, concept, stock_videos)
        try:
            edl = json.loads(edl_path.read_text())
            edl["_cache_version"] = self.EDL_CACHE_VERSION
            edl_path.write_text(json.dumps(edl, indent=2, ensure_ascii=False))
        except Exception as e:
            self.logger.warning(f"[agent] could not stamp EDL cache version: {e}")
        return edl_path

    def _edl_fresh(self, edl_path: Path, deps: list[Path]) -> bool:
        if not edl_path.exists() or edl_path.stat().st_size < 10:
            return False
        output_mtime = edl_path.stat().st_mtime
        if any(dep.exists() and output_mtime < dep.stat().st_mtime for dep in deps):
            return False
        try:
            edl = json.loads(edl_path.read_text())
        except json.JSONDecodeError:
            return False
        return edl.get("_cache_version") == self.EDL_CACHE_VERSION

    def _build_stock_library(self, stock_videos: list) -> list[dict]:
        """Small metadata index used to match EDL quotes to stock clips."""
        library = []
        for i, video in enumerate(stock_videos):
            library.append({
                "id": f"stock_{i}",
                "title": getattr(video, "title", ""),
                "tags": getattr(video, "tags", []) or [],
                "source": getattr(video, "source", ""),
                "url": getattr(video, "download_url", None) or getattr(video, "url", ""),
                "preview_url": getattr(video, "preview_url", ""),
                "duration": getattr(video, "duration", 0),
                "width": getattr(video, "width", 0),
                "height": getattr(video, "height", 0),
            })
        return library

    def _stock_match_prompt(
        self,
        range_item: dict,
        concept: dict,
        stock_library: list[dict],
    ) -> str:
        compact_library = [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "tags": item.get("tags", [])[:8],
                "source": item.get("source"),
            }
            for item in stock_library[:30]
        ]
        return (
            "You are a strict stock-footage relevance evaluator for an EDL.\n"
            "Choose the stock clip whose metadata best matches the narration quote.\n"
            "The quote may be Vietnamese; reason semantically and generate English stock search terms.\n\n"
            "Return ONLY valid JSON with this schema:\n"
            "{\n"
            '  "best_source": "stock_0 or null",\n'
            '  "score": 0.0,\n'
            '  "verdict": "MATCH or MISMATCH",\n'
            '  "reason": "short reason",\n'
            '  "search_query": "english 4-6 word stock search query"\n'
            "}\n\n"
            "Scoring:\n"
            "- 0.80-1.00: direct visual match for the quote\n"
            "- 0.55-0.79: acceptable generic visual match\n"
            "- below 0.55: mismatch; search again\n\n"
            f"Video concept: {json.dumps(concept, ensure_ascii=False)[:2000]}\n\n"
            f"EDL range: {json.dumps(range_item, ensure_ascii=False)}\n\n"
            f"Stock library: {json.dumps(compact_library, ensure_ascii=False)}"
        )

    def _fallback_stock_match(self, range_item: dict, stock_library: list[dict]) -> dict:
        quote = str(range_item.get("quote") or range_item.get("reason") or "").lower()
        quote_words = {
            w for w in re.findall(r"[a-zA-Z][a-zA-Z]{2,}", quote)
            if w not in {"the", "and", "for", "with", "this", "that", "from"}
        }
        best_id = None
        best_score = 0.0
        for item in stock_library:
            haystack = " ".join([
                str(item.get("title") or ""),
                " ".join(str(t) for t in item.get("tags", [])),
                str(item.get("source") or ""),
            ]).lower()
            overlap = sum(1 for word in quote_words if word in haystack)
            score = min(0.75, overlap / max(4, len(quote_words))) if quote_words else 0.35
            if score > best_score:
                best_id = item.get("id")
                best_score = score
        search_query = " ".join(list(quote_words)[:6]) or str(range_item.get("beat") or "documentary b roll")
        return {
            "best_source": best_id,
            "score": best_score,
            "verdict": "MATCH" if best_score >= 0.55 else "MISMATCH",
            "reason": "fallback lexical match",
            "search_query": search_query,
        }

    async def _evaluate_stock_match(
        self,
        range_item: dict,
        concept: dict,
        stock_library: list[dict],
    ) -> dict:
        api_key = config.settings.openrouter_api_key
        if not api_key or not stock_library:
            return self._fallback_stock_match(range_item, stock_library)

        from helpers.llm_task import TASK_MODELS, call_openrouter, extract_json

        prompt = self._stock_match_prompt(range_item, concept, stock_library)
        model = TASK_MODELS.get("verify_stock_relevance", "google/gemini-2.5-flash-lite")
        try:
            raw = await asyncio.to_thread(call_openrouter, model, prompt, api_key)
            parsed = extract_json(raw)
            if isinstance(parsed, dict):
                parsed.setdefault("score", 0.0)
                parsed.setdefault("verdict", "MISMATCH")
                return parsed
        except Exception as e:
            self.logger.warning(f"[agent] EDL stock-match eval failed; using fallback: {e}")
        return self._fallback_stock_match(range_item, stock_library)

    async def _evaluate_stock_visual_match(
        self,
        range_item: dict,
        concept: dict,
        stock_item: dict,
    ) -> dict:
        """Use the stock thumbnail as the final judge for quote/source relevance."""
        preview_url = str(stock_item.get("preview_url") or "").strip()
        if not preview_url:
            return {"accepted": True, "reason": "no preview thumbnail available"}

        api_key = config.settings.openrouter_api_key
        if not api_key:
            return {"accepted": True, "reason": "OPENROUTER_API_KEY unavailable; fail-open"}

        from helpers.llm_task import TASK_MODELS, call_openrouter_vision

        prompt = (
            "You are a strict visual editor checking whether a stock video thumbnail fits a narration beat.\n"
            "The thumbnail is a proxy for the video. Reject generic or unrelated footage.\n\n"
            f"Video concept: {json.dumps(concept, ensure_ascii=False)[:1600]}\n"
            f"Narration quote: {range_item.get('quote', '')}\n"
            f"Beat/reason: {range_item.get('beat', '')} / {range_item.get('reason', '')}\n"
            f"Candidate stock metadata: {json.dumps(stock_item, ensure_ascii=False)[:1200]}\n\n"
            "ACCEPT only if the image visibly supports the quote, or is a strong contextual b-roll "
            "for an abstract transition in the same topic.\n"
            "REJECT if it shows the wrong geography/culture, unrelated objects, random nature/city shots, "
            "or a generic visual that does not help this narration beat.\n\n"
            "Respond with ONLY one word: ACCEPT or REJECT"
        )
        model = TASK_MODELS.get("verify_stock_relevance", "google/gemini-2.5-flash-lite")
        try:
            raw = await asyncio.to_thread(call_openrouter_vision, model, prompt, preview_url, api_key)
            accepted = "ACCEPT" in raw.upper() and "REJECT" not in raw.upper()
            return {"accepted": accepted, "reason": raw.strip()[:200]}
        except Exception as e:
            self.logger.warning(f"[agent] visual stock-match eval failed; fail-open: {e}")
            return {"accepted": True, "reason": f"vision eval failed: {e}"}

    async def _repair_edl_stock_matches(self, edl_path: Path, concept: dict, stock_videos: list) -> Path:
        """Ensure every EDL quote uses matching stock; search again when current library is weak."""
        try:
            edl = json.loads(edl_path.read_text())
            ranges = edl.get("ranges", [])
            if not ranges:
                return edl_path

            sources = edl.setdefault("sources", {})
            stock_library = self._build_stock_library(stock_videos)
            source_ids = {item["id"] for item in stock_library}
            report: list[dict] = []
            changed = False
            topic_context = " ".join(
                str(concept.get(k, "")) for k in ("title", "hook", "thumbnail_concept")
            ).strip()

            for index, range_item in enumerate(ranges):
                current_source = range_item.get("source")
                evaluation = await self._evaluate_stock_match(range_item, concept, stock_library)
                best_source = evaluation.get("best_source")
                score = float(evaluation.get("score") or 0.0)
                verdict = str(evaluation.get("verdict") or "").upper()
                action = "kept"
                visual_evaluation: dict = {}

                if best_source in source_ids and (best_source != current_source or score >= 0.55):
                    if best_source not in sources:
                        best_item = next((item for item in stock_library if item.get("id") == best_source), None)
                        if best_item and best_item.get("url"):
                            sources[best_source] = best_item["url"]
                            changed = True
                    if best_source != current_source:
                        self.logger.info(
                            f"[agent] EDL range {index}: source {current_source} -> {best_source} "
                            f"(score={score:.2f})"
                        )
                        range_item["source"] = best_source
                        changed = True
                        action = "switched_existing"
                selected_item = next(
                    (item for item in stock_library if item.get("id") == range_item.get("source")),
                    None,
                )
                if selected_item:
                    visual_evaluation = await self._evaluate_stock_visual_match(range_item, concept, selected_item)
                    if not visual_evaluation.get("accepted", True):
                        verdict = "MISMATCH"
                        score = min(score, 0.30)
                        self.logger.info(
                            f"[agent] EDL range {index}: visual thumbnail rejected "
                            f"{range_item.get('source')} ({visual_evaluation.get('reason', '')})"
                        )
                if score < 0.55 or verdict == "MISMATCH" or range_item.get("source") not in sources:
                    query = str(evaluation.get("search_query") or "").strip()
                    if not query:
                        query = str(range_item.get("quote") or range_item.get("beat") or "documentary b roll")
                    self.logger.info(f"[agent] EDL range {index}: stock mismatch, searching again: {query}")
                    range_context = " ".join([
                        topic_context,
                        str(range_item.get("quote") or ""),
                        str(range_item.get("reason") or ""),
                    ]).strip()
                    replacements = await self.video_fetcher.search_all_sources(
                        query,
                        max_results_per_source=6,
                        topic_context=range_context or None,
                    )
                    replacements = [v for v in replacements if v.width >= v.height]
                    if replacements:
                        replacement = replacements[0]
                        new_id = f"stock_{len(sources)}"
                        sources[new_id] = replacement.download_url or replacement.url
                        stock_videos.append(replacement)
                        new_item = self._build_stock_library([replacement])[0]
                        new_item["id"] = new_id
                        stock_library.append(new_item)
                        source_ids.add(new_id)
                        range_item["source"] = new_id
                        changed = True
                        action = "searched_replacement"
                        self.logger.info(f"[agent] EDL range {index}: replaced with freshly searched {new_id}")
                        visual_evaluation = {"accepted": True, "reason": "replacement passed search visual filter"}

                report.append({
                    "range_index": index,
                    "quote": range_item.get("quote", ""),
                    "source": range_item.get("source"),
                    "score": score,
                    "verdict": verdict or ("MATCH" if score >= 0.55 else "MISMATCH"),
                    "reason": evaluation.get("reason", ""),
                    "search_query": evaluation.get("search_query", ""),
                    "action": action,
                    "visual_accepted": visual_evaluation.get("accepted"),
                    "visual_reason": visual_evaluation.get("reason", ""),
                })

            if changed:
                edl["ranges"] = ranges
                edl["sources"] = sources
                edl_path.write_text(json.dumps(edl, indent=2, ensure_ascii=False))
            report_path = edl_path.parent / "edl_match_report.json"
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
            bad = [r for r in report if r["score"] < 0.55]
            self.logger.info(
                f"[agent] EDL stock-match validation complete: {len(report) - len(bad)}/{len(report)} matched"
            )
        except Exception as e:
            self.logger.warning(f"[agent] EDL stock-match repair failed (using as-is): {e}")
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
            if template == "map_highlight":
                # Remotion's render CLI shallow-merges missing keys from the Composition's
                # Studio-preview defaultProps (Root.tsx) — explicitly fill optional fields so
                # an LLM spec that omits them never leaks unrelated example data (e.g. "INDIA").
                props.setdefault("subline", "")
                props.setdefault("callouts", [])
                props.setdefault("marker_label", "")
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

    @staticmethod
    def _dedupe_overlay_specs(specs: list[dict]) -> list[dict]:
        """Drop duplicate map highlights that would render the same region-only animation."""
        deduped: list[dict] = []
        seen_map_regions: set[str] = set()
        for spec in specs:
            if spec.get("template") != "map_highlight":
                deduped.append(spec)
                continue
            props = spec.get("props") if isinstance(spec.get("props"), dict) else {}
            region_key = str(props.get("region") or "").strip().casefold()
            if region_key and region_key in seen_map_regions:
                continue
            if region_key:
                seen_map_regions.add(region_key)
            deduped.append(spec)
        return deduped

    @staticmethod
    def _prefer_specific_map_regions(specs: list[dict]) -> list[dict]:
        """Correct broad continent regions when the spec text names a specific country."""
        country_aliases = {
            "ai cập": "Ai Cập",
            "egypt": "Egypt",
            "cairo": "Ai Cập",
            "nam phi": "Nam Phi",
            "south africa": "South Africa",
            "ma-rốc": "Ma-rốc",
            "maroc": "Ma-rốc",
            "morocco": "Morocco",
            "nigeria": "Nigeria",
            "kenya": "Kenya",
            "ethiopia": "Ethiopia",
            "ethiopie": "Ethiopia",
            "algeria": "Algeria",
            "algérie": "Algeria",
            "tunisia": "Tunisia",
            "ghana": "Ghana",
            "sudan": "Sudan",
            "libya": "Libya",
        }
        continent_regions = {"châu phi", "africa", "châu á", "asia", "châu âu", "europe"}

        fixed: list[dict] = []
        for spec in specs:
            if spec.get("template") != "map_highlight":
                fixed.append(spec)
                continue
            props = spec.get("props") if isinstance(spec.get("props"), dict) else {}
            region = str(props.get("region") or "").strip()
            if region.casefold() not in continent_regions:
                fixed.append(spec)
                continue

            haystack_parts = [region, str(props.get("headline") or ""), str(props.get("subline") or "")]
            callouts = props.get("callouts")
            if isinstance(callouts, list):
                haystack_parts.extend(str(item) for item in callouts)
            haystack = " ".join(haystack_parts).casefold()

            replacement = next((country for alias, country in country_aliases.items() if alias in haystack), None)
            if replacement:
                new_spec = dict(spec)
                new_props = dict(props)
                new_props["region"] = replacement
                new_spec["props"] = new_props
                fixed.append(new_spec)
            else:
                fixed.append(spec)
        return fixed

    def _overlays_fresh(self, edl_path: Path, overlays_dir: Path) -> bool:
        manifest_path = overlays_dir / "manifest.json"
        if not edl_path.exists() or not manifest_path.exists():
            return False
        if manifest_path.stat().st_mtime < edl_path.stat().st_mtime:
            return False
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            return False
        if not isinstance(manifest, dict):
            return False
        if manifest.get("version") != self.OVERLAY_CACHE_VERSION:
            return False
        overlays = manifest.get("overlays")
        if not isinstance(overlays, list):
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
            # stat_card still renders as a flat full-screen card (looks cheap) — only
            # map_highlight has the real-map treatment, so drop stat_card specs for now.
            specs = [s for s in specs if s.get("template") == "map_highlight"]
            specs = self._prefer_specific_map_regions(specs)
            specs = self._dedupe_overlay_specs(specs)
            if not specs:
                self.logger.info("[agent] no overlay graphics needed for this script")
                overlays = []
            else:
                for old_overlay in overlays_dir.glob("overlay_*_map_highlight.mp4"):
                    old_overlay.unlink(missing_ok=True)
                overlays = await self._render_overlays(specs, total_duration_s, overlays_dir)
                self.logger.info(f"[agent] added {len(overlays)} map/stat overlay graphics")

        edl["overlays"] = overlays
        edl_path.write_text(json.dumps(edl, indent=2, ensure_ascii=False))
        manifest_path.write_text(json.dumps({
            "version": self.OVERLAY_CACHE_VERSION,
            "overlays": overlays,
        }, indent=2, ensure_ascii=False))

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
        Rule 9: reuse cached transcripts.

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
            words = self._apply_script_text_to_word_timings(words, work_dir)
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

    def _apply_script_text_to_word_timings(self, words: list[dict], work_dir: Path) -> list[dict]:
        """Use ASR word timings but preserve the approved script spelling/diacritics.

        ElevenLabs Scribe/Whisper can hear Vietnamese correctly enough for timing
        while still dropping or changing accents. The script is the source of truth
        for subtitle text; ASR is only the clock.
        """
        script_path = work_dir / "script.txt"
        if not words or not script_path.exists():
            return words

        script_tokens = re.findall(r"\S+", script_path.read_text().strip(), flags=re.UNICODE)
        if not script_tokens:
            return words

        ratio = len(script_tokens) / max(1, len(words))
        if ratio < 0.65 or ratio > 1.45:
            self.logger.warning(
                "[agent] script/transcript word count differs too much "
                f"({len(script_tokens)} script vs {len(words)} ASR); using ASR subtitle text"
            )
            return words

        aligned: list[dict] = []
        limit = min(len(words), len(script_tokens))
        for i in range(limit):
            item = dict(words[i])
            item["text"] = script_tokens[i]
            aligned.append(item)

        if len(script_tokens) > limit and aligned:
            remainder = " ".join(script_tokens[limit:])
            aligned[-1]["text"] = f"{aligned[-1]['text']} {remainder}".strip()
        elif len(words) > limit:
            aligned.extend(words[limit:])

        self.logger.info(
            f"[agent] subtitle text normalized from script.txt "
            f"({len(script_tokens)} script tokens, {len(words)} ASR words)"
        )
        return aligned

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
        if not metadata_path.exists():
            return {}
        metadata = json.loads(metadata_path.read_text())

        # CC-BY (and similar) tracks require attribution — append it to the
        # description so it ships even on auto-upload. assets/music/CREDIT.txt is a
        # generic sidecar: drop a new licensed track + its credit text, no code change.
        credit_path = Path("assets/music/CREDIT.txt")
        if credit_path.exists():
            credit = credit_path.read_text().strip()
            if credit and credit not in metadata.get("description", ""):
                metadata["description"] = f"{metadata.get('description', '').rstrip()}\n\n{credit}"
                metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))

        return metadata

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

        duration_expr = f"{duration:.3f}"

        if kind == "pulse_frame":
            effect_filter = "colorchannelmixer=aa=0"
        elif kind == "lower_accent":
            effect_filter = "colorchannelmixer=aa=0"
        else:
            effect_filter = "colorchannelmixer=aa=0"

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

    async def _fetch_stock_videos(self, keywords: list[str], work_dir: Path, max_per_keyword: int = 5, topic_context: Optional[str] = None) -> list:
        cache_path = work_dir / "stock_videos.json"
        fingerprint = self._stock_cache_fingerprint(keywords, max_per_keyword, topic_context)
        cached = self._load_stock_cache(cache_path, fingerprint)
        if cached is not None:
            self.logger.info(f"[agent] ✓ stock videos (cached): {len(cached)} clips")
            return cached

        self.logger.info(f"[agent] fetching stock videos: {len(keywords)} keywords")
        videos = []
        seen_ids: set = set()
        for kw in keywords:
            try:
                results = await self.video_fetcher.search_all_sources(
                    kw, max_results_per_source=10, topic_context=topic_context
                )
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
        by_source = {}
        for video in videos:
            source = getattr(video, "source", "unknown")
            by_source[source] = by_source.get(source, 0) + 1
        self.logger.info(f"[agent] fetched {len(videos)} unique landscape stock clips by source: {by_source}")
        self._write_stock_cache(cache_path, fingerprint, videos)
        return videos

    def _stock_cache_fingerprint(
        self,
        keywords: list[str],
        max_per_keyword: int,
        topic_context: Optional[str],
    ) -> str:
        payload = {
            "version": "stock_search_v2",
            "keywords": keywords,
            "max_per_keyword": max_per_keyword,
            "topic_context": topic_context,
            "stock_video_sources": config.settings.stock_video_sources,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_stock_cache(self, cache_path: Path, fingerprint: str) -> Optional[list[StockVideo]]:
        if not cache_path.exists() or cache_path.stat().st_size < 10:
            return None
        try:
            data = json.loads(cache_path.read_text())
            if data.get("fingerprint") != fingerprint:
                return None
            videos = data.get("videos")
            if not isinstance(videos, list):
                return None
            return [StockVideo(**item) for item in videos]
        except Exception as e:
            self.logger.warning(f"[agent] stock cache invalid, refetching: {e}")
            return None

    def _write_stock_cache(self, cache_path: Path, fingerprint: str, videos: list) -> None:
        def dump_video(video):
            if hasattr(video, "model_dump"):
                return video.model_dump()
            if hasattr(video, "dict"):
                return video.dict()
            return dict(video)

        cache_path.write_text(json.dumps({
            "fingerprint": fingerprint,
            "videos": [dump_video(video) for video in videos],
        }, indent=2, ensure_ascii=False))

    async def _generate_voiceover(self, script: str, work_dir: Path) -> Optional[Path]:
        from src.modules.voice_subtitle import VoiceoverGenerator
        from src.core import config
        out_path = work_dir / "voiceover.mp3"
        language = config.settings.voice_language or "vi"
        preferred_provider = (config.settings.tts_provider or "").strip().lower()
        if preferred_provider:
            providers = [preferred_provider, *[p for p in ("elevenlabs", "edge_tts", "gtts") if p != preferred_provider]]
        elif language.startswith("vi"):
            providers = ["edge_tts", "elevenlabs", "gtts"]
            self.logger.info("[agent] Vietnamese voiceover: using Edge TTS first unless TTS_PROVIDER overrides it")
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
                self._write_voiceover_fingerprint(out_path, script, provider)
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
            "elevenlabs_language_code": config.settings.elevenlabs_language_code,
            "voiceover_policy_version": 2,
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

    def _write_voiceover_fingerprint(self, voiceover_path: Path, script: str, provider_used: str) -> None:
        from src.core import config
        meta = {
            "fingerprint": self._voiceover_fingerprint(script),
            "tts_provider": config.settings.tts_provider,
            "tts_provider_used": provider_used,
            "voice_language": config.settings.voice_language,
            "elevenlabs_voice_id": config.settings.elevenlabs_voice_id,
            "voice_id": config.settings.voice_id,
            "elevenlabs_model_id": config.settings.elevenlabs_model_id,
            "elevenlabs_language_code": config.settings.elevenlabs_language_code,
            "voiceover_policy_version": 2,
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
        self._normalize_voiceover_transcript_text(transcript_dir / f"{audio_path.stem}.json", work_dir)

        packed_md = work_dir / "takes_packed.md"
        self._run_helper([
            "helpers/pack_transcripts.py", str(transcript_dir),
            "--output", str(packed_md),
        ], "pack transcripts")
        return packed_md

    def _normalize_voiceover_transcript_text(self, transcript_path: Path, work_dir: Path) -> None:
        if not transcript_path.exists():
            return
        try:
            data = json.loads(transcript_path.read_text())
            words = data.get("words", [])
            word_indexes = [
                i for i, word in enumerate(words)
                if word.get("type") == "word" and word.get("text", "").strip()
            ]
            aligned = self._apply_script_text_to_word_timings(
                [words[i] for i in word_indexes],
                work_dir,
            )
            if len(aligned) != len(word_indexes):
                return
            for source_index, aligned_word in zip(word_indexes, aligned):
                words[source_index]["text"] = aligned_word.get("text", words[source_index].get("text", ""))
            data["words"] = words
            data["_script_text_normalized"] = 2
            transcript_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            self.logger.info(f"[agent] normalized transcript text from script: {transcript_path}")
        except Exception as e:
            self.logger.warning(f"[agent] could not normalize transcript text from script: {e}")

    def _transcript_fresh(self, audio_path: Path, packed_md: Path) -> bool:
        from src.core import config
        transcript_json = packed_md.parent / "transcripts" / f"{audio_path.stem}.json"
        transcript_cache = packed_md.parent / "transcripts" / f"{audio_path.stem}.cache"
        if not packed_md.exists() or packed_md.stat().st_size < 10:
            return False
        if not transcript_json.exists() or not transcript_cache.exists():
            return False
        try:
            transcript_data = json.loads(transcript_json.read_text())
            if transcript_data.get("_script_text_normalized") != 2:
                return False
        except json.JSONDecodeError:
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
        """Self-evaluation: vision-model + audio-pop check at every cut boundary
        (helpers/self_eval.py). Auto-fixes (pad escalation / source swap) and
        re-renders, up to MAX_SELF_EVAL_PASSES, before flagging for manual review."""
        verify_dir = work_dir / "verify"
        final_path = work_dir / "final.mp4"
        import shutil

        if not edl_path:
            # Storyboard/hybrid workflows don't carry an EDL here — just dump verify
            # PNGs for manual review and promote preview → final.
            self._run_helper([
                "helpers/timeline_view.py", str(preview_path),
                "--output", str(verify_dir / "pass_1"),
            ], "timeline view pass 1")
            shutil.copy2(str(preview_path), str(final_path))
            return final_path

        current_edl = edl_path
        for pass_num in range(1, self.MAX_SELF_EVAL_PASSES + 1):
            session.self_eval_passes = pass_num
            self.logger.info(f"[agent] self-eval pass {pass_num}/{self.MAX_SELF_EVAL_PASSES}")

            verify_dir_pass = verify_dir / f"pass_{pass_num}"
            issues_path = verify_dir_pass / "issues.json"
            fixed_edl_path = work_dir / f"edl_fixed_pass{pass_num}.json"

            self._run_helper([
                "helpers/self_eval.py", str(preview_path),
                "--edl", str(current_edl),
                "--output", str(verify_dir_pass),
                "--report", str(issues_path),
                "--fix-output", str(fixed_edl_path),
            ], f"self-eval pass {pass_num}")

            issues = json.loads(issues_path.read_text()) if issues_path.exists() else []

            if not issues:
                self.logger.info(f"[agent] self-eval pass {pass_num}: clean, promoting to final")
                shutil.copy2(str(preview_path), str(final_path))
                return final_path

            self.logger.warning(f"[agent] self-eval pass {pass_num}: {len(issues)} issue(s) found")
            session.session_notes.append(
                f"Self-eval pass {pass_num}: {len(issues)} issue(s), auto-fixed and re-rendered"
            )

            if pass_num == self.MAX_SELF_EVAL_PASSES or not fixed_edl_path.exists():
                break

            current_edl = fixed_edl_path
            self._render(current_edl, preview_path)

        self.logger.warning(
            f"[agent] self-eval: {self.MAX_SELF_EVAL_PASSES} passes done, issues remain — "
            "flagging to user for manual review"
        )
        shutil.copy2(str(preview_path), str(final_path))
        return final_path

    # ── Private: Upload ───────────────────────────────────────────────────────

    async def _upload(
        self,
        video_path: Path,
        metadata: dict,
        concept: Optional[dict] = None,
        topic: str = "",
    ):
        title = metadata.get("title", f"Video {datetime.now().strftime('%Y-%m-%d')}")
        description = metadata.get("description", "")
        tags = metadata.get("tags", [])
        thumbnail_path = None
        try:
            from helpers.thumbnail import generate_thumbnail

            thumbnail_path = generate_thumbnail(
                metadata=metadata,
                concept=concept or {},
                work_dir=video_path.parent,
                topic=topic,
            )
            self.logger.info(f"[agent] thumbnail: {thumbnail_path}")
        except Exception as e:
            self.logger.warning(f"[agent] thumbnail generation failed: {e}")
        try:
            video_id = self.uploader.upload_video(
                str(video_path),
                title=title,
                description=description,
                tags=tags[:15],
                is_public=False,
                thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
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
