"""
CLI Interface for Video Agent
"""

import asyncio
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
    "--title",
    help="Custom title for YouTube upload"
)
def generate(topic, keywords, duration, upload, effects, effects_mode, mode, title):
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
def trending():
    """Show trending topics"""
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
            content = await searcher.search_youtube_trending(max_results=10)
            
            if content:
                click.echo("\nTrending Topics on YouTube:\n")
                for i, item in enumerate(content, 1):
                    click.echo(f"{i}. {item.title}")
                    click.echo(f"   Views: {item.views:,}")
                    click.echo(f"   Keywords: {', '.join(item.keywords[:3])}")
                    click.echo()
            else:
                click.secho("No trending content found", fg="yellow")
        
        asyncio.run(_get_trending())
    
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


if __name__ == "__main__":
    cli()
