#!/usr/bin/env python3
"""
Example usage of Video Agent programmatically
"""

import asyncio
from src.modules import VideoAgent
from src.core import config
from loguru import logger


async def example_1_simple_generation():
    """Example 1: Simple video generation"""
    print("=" * 60)
    print("Example 1: Simple Video Generation")
    print("=" * 60)
    
    config.create_dirs()
    agent = VideoAgent()
    
    try:
        video_path = await agent.generate_video(
            topic="Machine Learning Basics",
            keywords=["AI", "deep learning"],
            duration=60.0,
            auto_upload=False
        )
        
        if video_path:
            print(f"✓ Video generated: {video_path}")
        else:
            print("✗ Video generation failed")
    
    finally:
        await agent.close()


async def example_2_trending_search():
    """Example 2: Search trending content"""
    print("\n" + "=" * 60)
    print("Example 2: Search Trending Content")
    print("=" * 60)
    
    from src.modules import ContentSearcher
    
    searcher = ContentSearcher()
    content = await searcher.search_youtube_trending(max_results=5)
    
    for i, item in enumerate(content, 1):
        print(f"\n{i}. {item.title}")
        print(f"   Views: {item.views:,}")
        print(f"   Likes: {item.likes:,}")
        print(f"   Keywords: {', '.join(item.keywords[:3])}")


async def example_3_stock_videos():
    """Example 3: Fetch stock videos"""
    print("\n" + "=" * 60)
    print("Example 3: Fetch Stock Videos")
    print("=" * 60)
    
    from src.modules import StockVideoFetcher
    
    fetcher = StockVideoFetcher()
    videos = await fetcher.search_all_sources("sunset", max_results_per_source=3)
    
    print(f"\nFound {len(videos)} videos:\n")
    for i, video in enumerate(videos[:5], 1):
        print(f"{i}. {video.source.upper()}")
        print(f"   ID: {video.id}")
        print(f"   Duration: {video.duration}s")
        print(f"   Size: {video.width}x{video.height}")


async def example_4_video_editing():
    """Example 4: Video editing operations"""
    print("\n" + "=" * 60)
    print("Example 4: Video Editing")
    print("=" * 60)
    
    from src.modules import VideoEditor
    import os
    
    editor = VideoEditor()
    
    # Note: This requires actual video files to work
    print("\nAvailable operations:")
    print("- cut_video(input_path, start, end)")
    print("- concatenate_videos(video_paths)")
    print("- add_background_music(video, audio, music_volume)")
    print("- add_text_overlay(video, text, position)")
    print("- resize_video(video, target_size)")
    print("- apply_color_grade(video, preset)")
    print("\nPresets: warm_cinematic, cool_blue, vintage, neutral")


async def example_5_voiceover():
    """Example 5: Generate voiceover"""
    print("\n" + "=" * 60)
    print("Example 5: Voiceover Generation")
    print("=" * 60)
    
    from src.modules import VoiceoverGenerator
    
    config.create_dirs()
    
    voiceover_gen = VoiceoverGenerator("elevenlabs")
    
    script = "Welcome to our video about artificial intelligence!"
    print(f"\nScript: {script}")
    print("Generating voiceover...")
    
    try:
        audio_path = await voiceover_gen.generate_voiceover(
            text=script,
            voice_id="Rachel",
            output_path="./temp/example_voiceover.mp3"
        )
        print(f"✓ Voiceover saved: {audio_path}")
    except Exception as e:
        print(f"Note: Requires ELEVENLABS_API_KEY")
        print(f"Error: {e}")


async def example_6_subtitles():
    """Example 6: Generate subtitles"""
    print("\n" + "=" * 60)
    print("Example 6: Subtitle Generation")
    print("=" * 60)
    
    from src.modules import SubtitleGenerator
    
    subtitle_gen = SubtitleGenerator("whisper")
    
    # Example audio path
    audio_path = "./temp/example_voiceover.mp3"
    
    if os.path.exists(audio_path):
        print(f"\nGenerating subtitles from: {audio_path}")
        try:
            srt_path = await subtitle_gen.generate_subtitles(
                audio_path=audio_path,
                output_path="./temp/example_subtitles.srt",
                language="en"
            )
            print(f"✓ Subtitles saved: {srt_path}")
        except Exception as e:
            print(f"Error: {e}")
    else:
        print(f"Note: Audio file not found")
        print("Subtitle generation requires audio input")


async def example_7_llm_integration():
    """Example 7: LLM integration for script generation"""
    print("\n" + "=" * 60)
    print("Example 7: LLM Integration")
    print("=" * 60)
    
    from src.core import OpenRouterLLM, LLMConfig, LLMMessage
    
    config.create_dirs()
    
    llm_config = LLMConfig(
        api_key=config.settings.openrouter_api_key,
        model=config.settings.llm_model
    )
    
    llm = OpenRouterLLM(llm_config)
    
    prompt = "Write a 30-second script about artificial intelligence for a YouTube video."
    message = LLMMessage(role="user", content=prompt)
    
    print(f"Prompt: {prompt}\n")
    
    try:
        response = await llm.chat([message])
        print(f"Response (first 200 chars):\n{response.content[:200]}...\n")
        print(f"Model: {response.model}")
        print(f"Tokens used: {response.tokens_used}")
    except Exception as e:
        print(f"Note: Requires OPENROUTER_API_KEY")
        print(f"Error: {e}")
    
    await llm.close()


async def main():
    """Run all examples"""
    
    # Configure logging
    logger.remove()
    logger.add(lambda msg: print(msg), level="INFO")
    
    print("\n🎬 Video Agent - Usage Examples\n")
    
    # Run examples
    try:
        # Uncomment examples to run:
        
        # await example_1_simple_generation()
        # await example_2_trending_search()
        # await example_3_stock_videos()
        await example_4_video_editing()
        await example_5_voiceover()
        await example_6_subtitles()
        await example_7_llm_integration()
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        logger.error(f"Error: {e}")
        raise
    
    print("\n" + "=" * 60)
    print("Examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    import os
    
    # Ensure config is loaded
    config.create_dirs()
    
    asyncio.run(main())
