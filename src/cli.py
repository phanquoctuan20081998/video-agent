"""
CLI Interface for Video Agent
"""

import asyncio
from pathlib import Path
import click
from loguru import logger

from src.core import config
from src.modules import VideoAgent


@click.group()
def cli():
    """Video Agent CLI - Automated video generation and YouTube upload"""
    pass


@cli.command()
@click.option(
    "--topic",
    "-t",
    required=True,
    help="Video topic"
)
@click.option(
    "--keywords",
    "-k",
    multiple=True,
    help="Additional keywords"
)
@click.option(
    "--duration",
    "-d",
    default=60,
    type=int,
    help="Video duration in seconds"
)
@click.option(
    "--upload",
    "-u",
    is_flag=True,
    help="Auto-upload to YouTube"
)
@click.option(
    "--effects/--no-effects",
    default=True,
    help="Generate lightweight edit effects and mix them into the edit"
)
@click.option(
    "--effects-mode",
    type=click.Choice(["seedance", "local"]),
    default="local",
    show_default=True,
    help="Effect generation mode: local cut accents or Seedance video clips"
)
@click.option(
    "--mode",
    type=click.Choice(["hybrid", "edl", "storyboard"]),
    default="edl",
    show_default=True,
    help="Pipeline mode: edl (stock footage + effects), hybrid, or storyboard"
)
@click.option(
    "--stop-at",
    type=click.Choice(["concept", "script", "terms", "audio", "materials", "storyboard", "render"]),
    default=None,
    help="Stop pipeline at this step and return intermediate result"
)
@click.option(
    "--batch",
    "-b",
    default=1,
    type=int,
    help="Generate N versions and pick the best (default: 1)"
)
@click.option(
    "--title",
    help="Custom title for YouTube upload"
)
def generate(topic, keywords, duration, upload, effects, effects_mode, mode, stop_at, batch, title):
    """Generate a video from topic"""
    try:
        # Initialize config and create directories
        config.create_dirs()
        
        # Setup logging
        logger.remove()
        logger.add(
            "logs/video_agent.log",
            rotation="500 MB",
            retention="7 days",
            level=config.settings.log_level
        )
        logger.add(
            lambda msg: click.echo(msg, err=True),
            level=config.settings.log_level
        )
        
        logger.info("Starting video generation...")
        logger.info(f"Topic: {topic}")
        logger.info(f"Duration: {duration}s")
        
        # Run async generation
        async def _generate():
            agent = VideoAgent()
            try:
                video_path = await agent.generate_video(
                    topic=topic,
                    keywords=list(keywords),
                    duration=float(duration),
                    auto_upload=upload,
                    apply_effects=effects,
                    effects_mode=effects_mode,
                    mode=mode,
                    stop_at=stop_at,
                    batch_count=batch,
                )
                
                if video_path:
                    click.secho(
                        f"✓ Video generated: {video_path}",
                        fg="green"
                    )
                    return video_path
                else:
                    click.secho(
                        "✗ Video generation failed",
                        fg="red"
                    )
                    return None
            finally:
                await agent.close()
        
        video_path = asyncio.run(_generate())
        
        if not video_path:
            exit(1)
    
    except Exception as e:
        logger.error(f"Error: {e}")
        click.secho(f"Error: {e}", fg="red")
        exit(1)


@cli.command()
@click.option("--region", "-r", default="VN", help="Region code (US, VN, etc.)")
def trending(region):
    """Show trending topics from YouTube, Google Trends, TikTok"""
    try:
        config.create_dirs()
        
        logger.remove()
        logger.add(
            lambda msg: click.echo(msg, err=True),
            level="INFO"
        )
        
        async def _get_trending():
            from src.modules import ContentSearcher
            
            searcher = ContentSearcher()

            import asyncio as _aio
            yt_task = searcher.search_youtube_trending(region=region, max_results=10)
            gt_task = searcher.fetch_google_trends(geo=region, max_results=15)
            tt_task = searcher.fetch_tiktok_trending(region=region, max_results=10)
            yt_res, gt_res, tt_res = await _aio.gather(
                yt_task, gt_task, tt_task, return_exceptions=True
            )

            yt_content = yt_res if not isinstance(yt_res, Exception) else []
            gt_terms = gt_res if not isinstance(gt_res, Exception) else []
            tt_content = tt_res if not isinstance(tt_res, Exception) else []

            if yt_content:
                click.secho(f"\n{'='*60}", fg="cyan")
                click.secho(f" YouTube Trending ({region})", fg="cyan", bold=True)
                click.secho(f"{'='*60}", fg="cyan")
                for i, item in enumerate(yt_content, 1):
                    click.echo(f"\n  {i}. {item.title}")
                    click.echo(f"     Views: {item.views:,}  |  Likes: {item.likes:,}  |  "
                               f"Velocity: {item.velocity:,.0f} v/hr  |  "
                               f"Engagement: {item.engagement_rate:.2%}")
                    if item.keywords:
                        click.echo(f"     Tags: {', '.join(item.keywords[:5])}")

            if gt_terms:
                click.secho(f"\n{'='*60}", fg="green")
                click.secho(f" Google Trends — Hot Searches ({region})", fg="green", bold=True)
                click.secho(f"{'='*60}", fg="green")
                for i, term in enumerate(gt_terms, 1):
                    click.echo(f"  {i:>2}. {term}")

            if tt_content:
                click.secho(f"\n{'='*60}", fg="magenta")
                click.secho(f" TikTok Trending Hashtags ({region})", fg="magenta", bold=True)
                click.secho(f"{'='*60}", fg="magenta")
                for i, item in enumerate(tt_content, 1):
                    click.echo(f"  {i:>2}. {item.title}  —  {item.description}")

            if not yt_content and not gt_terms and not tt_content:
                click.secho("No trending content found", fg="yellow")

        asyncio.run(_get_trending())
    
    except Exception as e:
        logger.error(f"Error: {e}")
        click.secho(f"Error: {e}", fg="red")
        exit(1)


@cli.command()
@click.option("--topic", "-t", required=True, help="Topic or niche to research")
@click.option("--region", "-r", default="VN", help="Region code (US, VN, etc.)")
@click.option("--output", "-o", default=None, help="Save JSON report to file")
@click.option("--subreddits", "-s", multiple=True, default=["videos", "Damnthatsinteresting"],
              help="Subreddits to scan")
def research(topic, region, output, subreddits):
    """Deep viral market research — cross-platform trend analysis + AI-generated video ideas"""
    try:
        config.create_dirs()
        logger.remove()
        logger.add(lambda msg: click.echo(msg, err=True), level="INFO")

        async def _research():
            import json as _json
            from src.modules import ContentSearcher

            searcher = ContentSearcher()

            click.secho(f"\n Researching '{topic}' across all platforms...\n", fg="cyan", bold=True)

            report = await searcher.research_viral_topics(
                topic=topic, region=region, subreddits=list(subreddits)
            )

            # Show raw signal counts
            click.secho(f"{'='*60}", fg="cyan")
            click.secho(f" NICHE RESEARCH: '{topic}'", fg="cyan", bold=True)
            click.echo(f"  Competitor videos: {len(report.get('competitor_videos', []))} videos")
            click.echo(f"  Rising in niche:   {len(report.get('rising_in_niche', []))} videos")
            gtr = report.get('google_related_searches', {})
            click.echo(f"  Google related:    {len(gtr.get('related_queries', []))} queries, "
                       f"{len(gtr.get('related_topics', []))} topics")
            click.echo(f"  Web research:      {len(report.get('web_research', []))} results")
            click.echo(f"  Reddit niche:      {len(report.get('reddit_niche_posts', []))} posts")
            click.echo(f"  Subreddits:        {', '.join(report.get('subreddits_used', []))}")
            click.secho(f"{'='*60}\n", fg="cyan")

            # Show competitor videos
            competitors = report.get("competitor_videos", [])
            if competitors:
                click.secho(f" Top competitor videos for '{topic}':", fg="yellow", bold=True)
                for i, v in enumerate(competitors[:8], 1):
                    click.echo(f"  {i}. {v['title']}")
                    click.echo(f"     {v['views']:,} views | engagement {v['engagement']:.2%}")
                click.echo()

            # Show Google Trends related queries for the topic
            related_queries = gtr.get("related_queries", [])
            if related_queries:
                click.secho(" Google Trends — what people search about this topic:", fg="green", bold=True)
                for rq in related_queries[:10]:
                    click.echo(f"  • {rq['query']}  ({rq.get('growth', '')})")
                click.echo()

            # Show web search highlights
            web_results = report.get("web_research", [])
            if web_results:
                click.secho(" Web research — existing content in this niche:", fg="yellow", bold=True)
                for i, wr in enumerate(web_results[:8], 1):
                    click.echo(f"  {i}. {wr['title']}")
                    click.echo(f"     {wr.get('snippet', '')[:120]}")
                    click.echo(f"     {wr.get('source', '')}")
                click.echo()

            # Show top rising videos (highest velocity)
            rising = report.get("rising_in_niche", [])
            if rising:
                click.secho(f" Rising in '{topic}' niche (last 72h):", fg="yellow", bold=True)
                for i, v in enumerate(rising[:5], 1):
                    click.echo(f"  {i}. {v['title']}")
                    click.echo(f"     {v['views']:,} views | {v['velocity']:,.0f} v/hr | "
                               f"engagement {v['engagement']:.2%}")
                click.echo()

            # Feed to LLM for niche video idea generation
            click.secho(f" Generating video ideas for '{topic}' niche via AI...\n", fg="green", bold=True)
            import subprocess, sys
            report_json = _json.dumps(report, ensure_ascii=False, default=str)
            project_root = Path(__file__).resolve().parent.parent
            out_path = Path(output) if output else project_root / "outputs" / "edit" / "market_research.json"
            out_path = out_path.resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)

            # Fetch already-published videos from YouTube channel to avoid repeats
            published_titles: list[str] = []
            try:
                from src.modules.youtube_uploader import YouTubeUploader
                uploader = YouTubeUploader()
                published_titles = uploader.get_channel_titles(max_results=200)
                if published_titles:
                    click.secho(f" Found {len(published_titles)} published videos on channel (will exclude from ideas)", fg="cyan")
            except Exception as e:
                click.secho(f" Could not fetch channel videos (skipping dedup): {e}", fg="yellow")

            # Write research data as context, topic as input — include published titles for exclusion
            report["_published_channel_titles"] = published_titles
            report_json = _json.dumps(report, ensure_ascii=False, default=str)
            tmp_context = out_path.parent / "_research_context_tmp.json"
            tmp_context.write_text(report_json, encoding="utf-8")

            result = subprocess.run(
                [sys.executable, "helpers/llm_task.py",
                 "--task", "research_market",
                 "--input", topic,
                 "--context", str(tmp_context),
                 "--output", str(out_path)],
                capture_output=True, text=True, cwd=str(project_root)
            )
            tmp_context.unlink(missing_ok=True)

            if result.returncode == 0 and out_path.exists():
                ideas = _json.loads(out_path.read_text(encoding="utf-8"))
                viral_ideas = ideas.get("viral_video_ideas", [])

                # Show niche analysis first
                niche = ideas.get("niche_analysis", {})
                if niche:
                    click.secho(f"{'='*60}", fg="magenta")
                    click.secho(f" NICHE ANALYSIS: '{topic}'", fg="magenta", bold=True)
                    click.secho(f"{'='*60}", fg="magenta")
                    if niche.get("underserved_subtopics"):
                        click.echo("\n  Underserved subtopics (opportunity!):")
                        for s in niche["underserved_subtopics"]:
                            click.echo(f"    → {s}")
                    if niche.get("content_gaps"):
                        click.echo("\n  Content gaps (what competitors miss):")
                        for g in niche["content_gaps"]:
                            click.echo(f"    → {g}")
                    if niche.get("audience_questions"):
                        click.echo("\n  Questions your audience is asking:")
                        for q in niche["audience_questions"]:
                            click.echo(f"    ? {q}")
                    click.echo()

                if viral_ideas:
                    click.secho(f"{'='*60}", fg="green")
                    click.secho(f" TOP VIDEO IDEAS FOR '{topic.upper()}'", fg="green", bold=True)
                    click.secho(f"{'='*60}", fg="green")
                    for idea in viral_ideas[:10]:
                        score = idea.get("virality_score", 0)
                        bar = "█" * int(score / 5) + "░" * (20 - int(score / 5))
                        click.echo(f"\n  #{idea.get('rank', '?')}  [{bar}] {score}/100")
                        click.secho(f"  {idea.get('title', '')}", fg="white", bold=True)
                        if idea.get("title_vi"):
                            click.echo(f"  VI: {idea['title_vi']}")
                        click.echo(f"  Hook: {idea.get('hook', '')}")
                        click.echo(f"  Angle: {idea.get('angle', '')}")
                        click.echo(f"  Why viral: {idea.get('why_viral', '')}")
                        if idea.get("evidence"):
                            click.echo(f"  Evidence: {idea['evidence']}")
                        click.echo(f"  Gap: {idea.get('content_gap', '')}")
                        click.echo(f"  Search: {idea.get('estimated_search_volume', '')} | "
                                   f"Competition: {idea.get('competition_level', '')}")
                        click.echo(f"  Keywords: {', '.join(idea.get('keywords', []))}")
                    click.echo()
                    click.secho(f"  Full report saved → {out_path}", fg="cyan")
                else:
                    click.secho("AI returned no ideas — check LLM output", fg="yellow")
            else:
                click.secho(f"LLM task failed: {result.stderr[:500]}", fg="red")
                # Still save raw trend data
                out_path.write_text(report_json, encoding="utf-8")
                click.secho(f"Raw trend data saved → {out_path}", fg="yellow")

        asyncio.run(_research())

    except Exception as e:
        logger.error(f"Error: {e}")
        click.secho(f"Error: {e}", fg="red")
        exit(1)


@cli.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True),
    help="Path to config file"
)
def init_config(config):
    """Initialize configuration files"""
    try:
        from shutil import copy
        from pathlib import Path
        
        # Copy example config
        if not Path("config/config.toml").exists():
            copy("config/config.example.toml", "config/config.toml")
            click.secho("✓ Created config/config.toml", fg="green")
        
        # Copy example env
        if not Path(".env").exists():
            copy(".env.example", ".env")
            click.secho("✓ Created .env file", fg="green")
            click.echo("\nPlease update .env with your API keys:")
            click.echo("  - OPENROUTER_API_KEY")
            click.echo("  - YOUTUBE_DEVELOPER_KEY")
            click.echo("  - PEXELS_API_KEY")
            click.echo("  - ELEVENLABS_API_KEY")
        else:
            click.secho("Configuration already exists", fg="yellow")
    
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        exit(1)


@cli.command()
def test_api():
    """Test API connections"""
    try:
        config.create_dirs()
        
        logger.remove()
        logger.add(lambda msg: click.echo(msg, err=True), level="INFO")
        
        click.echo("Testing API connections...\n")
        
        # Test OpenRouter
        click.echo("1. OpenRouter API... ", nl=False)
        if config.settings.openrouter_api_key:
            click.secho("✓ Configured", fg="green")
        else:
            click.secho("✗ Not configured", fg="red")
        
        # Test YouTube API
        click.echo("2. YouTube API... ", nl=False)
        if config.settings.youtube_developer_key:
            click.secho("✓ Configured", fg="green")
        else:
            click.secho("✗ Not configured", fg="red")
        
        # Test Pexels
        click.echo("3. Pexels API... ", nl=False)
        if config.settings.pexels_api_key:
            click.secho("✓ Configured", fg="green")
        else:
            click.secho("✗ Not configured", fg="red")
        
        # Test ElevenLabs
        click.echo("4. ElevenLabs API... ", nl=False)
        if config.settings.elevenlabs_api_key:
            click.secho("✓ Configured", fg="green")
        else:
            click.secho("✗ Not configured", fg="red")
    
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        exit(1)


@cli.group()
def autopilot():
    """Geography-channel autopilot commands"""
    pass


@autopilot.command("doctor")
def autopilot_doctor():
    """Check autopilot configuration"""
    checks = [
        ("OPENROUTER_API_KEY", bool(config.settings.openrouter_api_key)),
        ("ELEVENLABS_API_KEY", bool(config.settings.elevenlabs_api_key)),
        ("PEXELS_API_KEY or PIXABAY_API_KEY", bool(config.settings.pexels_api_key or config.settings.pixabay_api_key)),
        ("YOUTUBE_DEVELOPER_KEY", bool(config.settings.youtube_developer_key)),
        ("YouTube OAuth files", Path("youtube_oauth.json").exists() or Path("youtube_token.pickle").exists()),
        ("SMTP_HOST", bool(config.settings.smtp_host)),
        ("AUTOPILOT_REVIEW_EMAIL_TO", bool(config.settings.autopilot_review_email_to)),
        ("AUTOPILOT_REVIEW_EMAIL_FROM", bool(config.settings.autopilot_review_email_from)),
    ]
    for label, ok in checks:
        click.echo(f"{'✓' if ok else '✗'} {label}")


@autopilot.command("run")
@click.option("--slot", default="manual", show_default=True, help="Run slot label: morning/evening/manual")
@click.option("--duration", type=int, default=None, help="Override target duration in seconds")
@click.option("--mode", type=click.Choice(["edl", "hybrid", "storyboard"]), default=None, help="Override pipeline mode")
@click.option("--dry-run", is_flag=True, help="Research/select a topic and email manifest without generating/uploading")
def autopilot_run(slot, duration, mode, dry_run):
    """Run one full geography autopilot cycle"""
    config.create_dirs()
    logger.remove()
    logger.add("logs/autopilot.log", rotation="200 MB", retention="14 days", level=config.settings.log_level)
    logger.add(lambda msg: click.echo(msg, err=True), level=config.settings.log_level)

    async def _run():
        from src.modules.geography_autopilot import GeographyAutopilot

        agent = GeographyAutopilot()
        return await agent.run_once(slot=slot, duration=duration, mode=mode, dry_run=dry_run)

    try:
        result = asyncio.run(_run())
        click.secho(f"✓ Autopilot run: {result.run_id}", fg="green")
        click.echo(f"Topic: {result.topic}")
        click.echo(f"Video: {result.video_path}")
        click.echo(f"YouTube: {result.youtube_url}")
        click.echo(f"Manifest: {result.manifest_path}")
    except Exception as e:
        logger.error(f"Autopilot failed: {e}")
        click.secho(f"Autopilot failed: {e}", fg="red")
        exit(1)


@autopilot.command("write-launchd")
@click.option("--output", type=click.Path(), default="outputs/autopilot/com.videoagent.geography-autopilot.plist", show_default=True)
def autopilot_write_launchd(output):
    """Write a launchd plist that runs at 06:00 and 17:00"""
    from src.modules.geography_autopilot import launchd_plist

    repo_dir = Path.cwd()
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(launchd_plist(repo_dir))
    click.secho(f"✓ Wrote {out_path}", fg="green")
    click.echo("Install on macOS with:")
    click.echo(f"  cp {out_path} ~/Library/LaunchAgents/com.videoagent.geography-autopilot.plist")
    click.echo("  launchctl load ~/Library/LaunchAgents/com.videoagent.geography-autopilot.plist")


if __name__ == "__main__":
    cli()
