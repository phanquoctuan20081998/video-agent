"""
Main entry point for Video Agent
"""

import asyncio
from pathlib import Path
from loguru import logger

from src.core import config
from src.modules import VideoAgent


async def main():
    """Main function"""
    try:
        # Setup
        config.create_dirs()
        
        logger.remove()
        logger.add(
            "logs/video_agent.log",
            rotation="500 MB",
            retention="7 days",
            level=config.settings.log_level
        )
        logger.add(
            lambda msg: print(msg),
            level=config.settings.log_level
        )
        
        logger.info("=" * 60)
        logger.info("Video Agent Started")
        logger.info("=" * 60)
        
        # Create agent
        agent = VideoAgent()
        
        try:
            # Example: Generate video for trending topic
            video_path = await agent.generate_video(
                topic="AI Trends",
                keywords=["artificial intelligence", "machine learning"],
                duration=60.0,
                auto_upload=False  # Set to True to upload automatically
            )
            
            if video_path:
                logger.info(f"✓ Video generated successfully: {video_path}")
            else:
                logger.error("✗ Video generation failed")
        
        finally:
            await agent.close()
        
        logger.info("=" * 60)
        logger.info("Video Agent Completed")
        logger.info("=" * 60)
    
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
