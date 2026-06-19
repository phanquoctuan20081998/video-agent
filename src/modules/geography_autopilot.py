"""
Geography-channel autopilot.

Runs one complete research -> generate -> private upload -> review email cycle.
Designed for launchd on a Mac mini.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from src.core import config
from src.modules.agent import VideoAgent
from src.modules.content_search import ContentSearcher
from src.modules.email_notifier import EmailNotifier
from src.modules.youtube_uploader import YouTubeUploader


SEED_QUERIES = [
    "geography explained countries",
    "why countries are rich poor geography",
    "world map explained",
    "geopolitics geography explained",
    "địa lý thế giới vì sao",
]

REDDIT_SUBREDDITS = ["geography", "MapPorn", "geopolitics"]
GOOGLE_TRENDS_GEOS = ["VN", "US"]


@dataclass
class AutopilotResult:
    run_id: str
    topic: str
    video_path: Optional[str]
    youtube_url: Optional[str]
    manifest_path: str


class GeographyAutopilot:
    """Semi-autonomous producer for a geography-only YouTube channel."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or Path(config.settings.output_dir) / "autopilot"
        self.runs_dir = self.base_dir / "runs"
        self.state_path = self.base_dir / "state.json"
        self.logger = logger
        self.content_searcher = ContentSearcher()
        self.notifier = EmailNotifier()
        self.uploader = YouTubeUploader()

    async def run_once(
        self,
        *,
        slot: str,
        duration: Optional[int] = None,
        mode: Optional[str] = None,
        dry_run: bool = False,
    ) -> AutopilotResult:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

        run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slot}"
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        state = self._load_state()
        trend_context = await self._collect_trend_context()
        candidates = self._generate_topic_candidates(trend_context, state)
        topic_spec = self._pick_topic(candidates, state)
        topic = topic_spec["topic"]
        keywords = topic_spec.get("keywords") or []
        self.logger.info(f"[autopilot] selected topic: {topic}")

        manifest: dict[str, Any] = {
            "run_id": run_id,
            "slot": slot,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "topic": topic,
            "topic_spec": topic_spec,
            "trend_context": trend_context,
            "dry_run": dry_run,
        }

        video_path: Optional[str] = None
        youtube_url: Optional[str] = None

        if not dry_run:
            original_output_dir = config.settings.output_dir
            config.settings.output_dir = str(run_dir)
            agent = VideoAgent()
            try:
                video_path = await agent.generate_video(
                    topic=topic,
                    keywords=list(keywords),
                    duration=float(duration or config.settings.autopilot_duration_s),
                    auto_upload=False,
                    apply_effects=False,
                    mode=mode or config.settings.autopilot_mode,
                    confirm_strategy=False,
                )
                if not video_path:
                    raise RuntimeError("video generation returned no output")

                work_dir = run_dir / "edit"
                script = (work_dir / "script.txt").read_text() if (work_dir / "script.txt").exists() else topic
                metadata = await agent._generate_seo(script, work_dir)
                metadata = self._harden_metadata(metadata, topic)
                (work_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False))

                video_id = self.uploader.upload_video(
                    video_path,
                    title=metadata["title"],
                    description=metadata["description"],
                    tags=metadata.get("tags", [])[:15],
                    category_id="27",
                    is_public=False,
                )
                youtube_url = f"https://www.youtube.com/watch?v={video_id}"
                manifest["youtube_video_id"] = video_id
                manifest["youtube_url"] = youtube_url
                manifest["metadata"] = metadata
            finally:
                await agent.close()
                config.settings.output_dir = original_output_dir

        manifest["video_path"] = video_path
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

        state.setdefault("used_topics", []).append(
            {
                "topic": topic,
                "run_id": run_id,
                "created_at": manifest["created_at"],
                "youtube_url": youtube_url,
            }
        )
        state["used_topics"] = state["used_topics"][-200:]
        self._save_state(state)

        self._send_review_notice(
            run_id=run_id,
            slot=slot,
            topic=topic,
            video_path=Path(video_path) if video_path else None,
            youtube_url=youtube_url,
            manifest_path=manifest_path,
            dry_run=dry_run,
        )

        return AutopilotResult(
            run_id=run_id,
            topic=topic,
            video_path=video_path,
            youtube_url=youtube_url,
            manifest_path=str(manifest_path),
        )

    async def _collect_trend_context(self) -> str:
        cached = self._load_cached_trend_context()
        if cached:
            self.logger.info("[autopilot] using cached trend context")
            return cached

        blocks: list[str] = []
        query_limit = max(0, min(len(SEED_QUERIES), config.settings.autopilot_trend_query_limit))
        for query in SEED_QUERIES[:query_limit]:
            try:
                results = await self.content_searcher.search_topic_on_youtube(query, max_results=6)
            except Exception as e:
                self.logger.warning(f"[autopilot] trend query failed for '{query}': {e}")
                results = []
            if self.content_searcher.youtube_quota_exceeded:
                self.logger.warning("[autopilot] YouTube search quota exceeded; stopping live trend search")
                break
            if not results:
                continue
            lines = [f"Query: {query}"]
            for item in results[:6]:
                lines.append(
                    f"- {item.title} | views={item.views} | likes={item.likes} | url={item.url}"
                )
            blocks.append("\n".join(lines))

        # Google Trends — what people are actually searching right now (no key needed).
        for geo in GOOGLE_TRENDS_GEOS:
            try:
                terms = await self.content_searcher.fetch_google_trends(geo=geo, max_results=15)
            except Exception as e:
                self.logger.warning(f"[autopilot] Google Trends fetch failed for geo={geo}: {e}")
                terms = []
            if terms:
                blocks.append(f"Google Trends (geo={geo}):\n" + ", ".join(terms))

        # Reddit hot posts — social-media signal for what's resonating right now.
        for subreddit in REDDIT_SUBREDDITS:
            try:
                results = await self.content_searcher.search_reddit_hot(subreddit, max_results=8)
            except Exception as e:
                self.logger.warning(f"[autopilot] Reddit fetch failed for r/{subreddit}: {e}")
                results = []
            if not results:
                continue
            lines = [f"Reddit r/{subreddit}:"]
            for item in results[:8]:
                lines.append(f"- {item.title} | score={item.likes} | url={item.url}")
            blocks.append("\n".join(lines))

        if not blocks:
            return "No live trend context available. Generate evergreen geography ideas for Vietnam-facing viewers."
        context = "\n\n".join(blocks)
        self._save_cached_trend_context(context)
        return context

    def _trend_cache_path(self) -> Path:
        return self.base_dir / "trend_context_cache.json"

    def _load_cached_trend_context(self) -> Optional[str]:
        path = self._trend_cache_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            created_at = datetime.fromisoformat(data.get("created_at", ""))
            ttl = timedelta(hours=max(0, config.settings.autopilot_trend_cache_hours))
            if ttl.total_seconds() <= 0 or datetime.now() - created_at > ttl:
                return None
            context = data.get("context")
            return context if isinstance(context, str) and context.strip() else None
        except Exception as e:
            self.logger.warning(f"[autopilot] trend cache invalid: {e}")
            return None

    def _save_cached_trend_context(self, context: str) -> None:
        self._trend_cache_path().write_text(json.dumps({
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "context": context,
        }, indent=2, ensure_ascii=False))

    def _generate_topic_candidates(self, trend_context: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        candidates_path = self.base_dir / "latest_topic_candidates.json"
        used = [item.get("topic", "") for item in state.get("used_topics", [])[-80:]]
        used_path = self.base_dir / "used_topics_context.txt"
        used_path.write_text("\n".join(used), encoding="utf-8")
        trend_path = self.base_dir / "trend_context.txt"
        trend_path.write_text(trend_context, encoding="utf-8")

        self._run_helper(
            [
                "helpers/llm_task.py",
                "--task",
                "generate_geo_topics",
                "--input",
                str(trend_path),
                "--context",
                str(used_path),
                "--output",
                str(candidates_path),
            ],
            "generate geography topic candidates",
        )
        try:
            data = json.loads(candidates_path.read_text())
            if isinstance(data, list) and data:
                return data
        except Exception as e:
            self.logger.warning(f"[autopilot] invalid topic candidates: {e}")
        return [
            {
                "topic": "Vì sao địa lý có thể quyết định số phận của một quốc gia?",
                "angle": "A bright map-led explainer about geography shaping everyday life.",
                "keywords": ["world map geography aerial landscape", "mountains rivers city aerial"],
                "score": 0.5,
                "reason": "safe evergreen fallback",
            }
        ]

    @staticmethod
    def _pick_topic(candidates: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
        used = {str(item.get("topic", "")).strip().lower() for item in state.get("used_topics", [])}
        for candidate in sorted(candidates, key=lambda item: float(item.get("score", 0)), reverse=True):
            topic = str(candidate.get("topic", "")).strip()
            if topic and topic.lower() not in used:
                return candidate
        return candidates[0]

    @staticmethod
    def _harden_metadata(metadata: dict[str, Any], topic: str) -> dict[str, Any]:
        title = str(metadata.get("title") or topic).strip()[:95]
        description = str(metadata.get("description") or "").strip()
        if "duyệt" not in description.lower():
            description = f"{description}\n\nVideo địa lý tự động tạo, đang chờ duyệt trước khi đăng public.".strip()
        tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
        tags = list(dict.fromkeys([*tags, "địa lý", "geography", "bản đồ", "giải thích"]))
        return {
            **metadata,
            "title": title,
            "description": description,
            "tags": tags,
        }

    def _send_review_notice(
        self,
        *,
        run_id: str,
        slot: str,
        topic: str,
        video_path: Optional[Path],
        youtube_url: Optional[str],
        manifest_path: Path,
        dry_run: bool,
    ) -> None:
        body = "\n".join(
            [
                "Một video địa lý mới đã được tạo bởi autopilot.",
                "",
                f"Run: {run_id}",
                f"Slot: {slot}",
                f"Topic: {topic}",
                f"YouTube draft/private: {youtube_url or 'not uploaded'}",
                f"Local video: {video_path or 'not generated'}",
                f"Manifest: {manifest_path}",
                f"Dry run: {dry_run}",
                "",
                "Việc public/schedule video vẫn do người duyệt quyết định trong YouTube Studio.",
            ]
        )
        self.notifier.send_review_email(
            subject=f"[Video Agent] Review geography draft: {topic[:70]}",
            body=body,
            attachment_path=video_path,
        )

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except json.JSONDecodeError:
                self.logger.warning("[autopilot] state.json invalid; starting fresh")
        return {"used_topics": []}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))

    @staticmethod
    def _run_helper(args: list[str], label: str = "") -> str:
        cmd = [sys.executable] + args
        logger.info(f"[autopilot] {label or 'helper'}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"{label} failed: {result.stderr[-500:]}")
        return result.stdout


def launchd_plist(repo_dir: Path, label: str = "com.videoagent.geography-autopilot") -> str:
    python_path = repo_dir / "venv" / "bin" / "python"
    if not python_path.exists():
        python_path = Path(sys.executable)
    stdout = repo_dir / "logs" / "autopilot.launchd.out.log"
    stderr = repo_dir / "logs" / "autopilot.launchd.err.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>WorkingDirectory</key>
  <string>{repo_dir}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python_path}</string>
    <string>-m</string>
    <string>src.cli</string>
    <string>autopilot</string>
    <string>run</string>
    <string>--slot</string>
    <string>scheduled</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict>
      <key>Hour</key>
      <integer>6</integer>
      <key>Minute</key>
      <integer>0</integer>
    </dict>
    <dict>
      <key>Hour</key>
      <integer>17</integer>
      <key>Minute</key>
      <integer>0</integer>
    </dict>
  </array>
  <key>StandardOutPath</key>
  <string>{stdout}</string>
  <key>StandardErrorPath</key>
  <string>{stderr}</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
"""
