# 🎬 Video Agent - YouTube Automation System

Autonomous AI agent for automated video generation, editing, and YouTube uploading. Search trending content, generate scripts, create videos, add voiceovers, and upload to YouTube completely automatically.

## ✨ Features

### Core Features
- **🔍 Content Search**: Search YouTube trending videos and viral content
- **📹 Stock Video Integration**: Fetch videos from Pexels, Pixabay, and Coverr
- **🎨 Video Editing**: Auto-cut, concatenate, color grade, and apply effects
- **🎤 Voice & Subtitles**: Generate voiceovers with ElevenLabs, create subtitles with Whisper
- **🎵 Audio Processing**: Add background music, audio effects, and sound design
- **📤 YouTube Upload**: Automatic video upload with metadata, scheduling, and playlist management
- **🤖 AI-Powered**: OpenRouter integration for LLM-based content generation and planning

### Supported Platforms
- YouTube (fully integrated)
- Stock Video Sources: Pexels, Pixabay, Coverr
- TTS Providers: ElevenLabs, Edge TTS (free)
- LLM Providers: OpenRouter (ByteDance Seedance 2.0, etc.)

## 📋 Requirements

### System Requirements
- Python 3.11+
- FFmpeg (for video processing)
- 4GB+ RAM (8GB+ recommended)
- GPU optional (for faster processing)

### API Keys Required
- **OpenRouter**: For LLM API (https://openrouter.ai)
- **YouTube**: For video uploads (https://developers.google.com)
- **Stock Video APIs**: Pexels, Pixabay (free with limits)
- **ElevenLabs**: For voice synthesis (https://elevenlabs.io)

## 🚀 Quick Start

### 1. Clone Repository
```bash
git clone https://github.com/yourusername/video-agent.git
cd video-agent
```

### 2. Setup Python Environment
```bash
# Using uv (recommended)
uv python install 3.11
uv sync

# Or using pip + venv
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Install FFmpeg
```bash
# macOS
brew install ffmpeg yt-dlp

# Ubuntu/Debian
sudo apt-get install ffmpeg youtube-dl

# Windows
# Download from https://ffmpeg.org/download.html
```

### 4. Configure API Keys
```bash
# Copy example files
cp .env.example .env
cp config/config.example.toml config/config.toml

# Edit .env and add your API keys
nano .env
```

Required environment variables:
- `OPENROUTER_API_KEY`
- `YOUTUBE_DEVELOPER_KEY`
- `PEXELS_API_KEY`
- `ELEVENLABS_API_KEY`

### 5. Run Video Generation

```bash
# Generate video from topic (CLI)
python -m src.cli generate --topic "AI Trends" --duration 60 --upload

# Or use Python directly
python -m src.main

# View trending topics
python -m src.cli trending

# Test API connections
python -m src.cli test-api
```

## 📖 Usage Examples

### CLI Usage

```bash
# Basic video generation
python -m src.cli generate --topic "Machine Learning" --duration 60

# Geography/listicle video: stock footage + EDL cuts + local edit effects
python -m src.cli generate \
  --topic "37 sự thật địa lý khó tin về Ấn Độ" \
  --duration 180 \
  --mode edl \
  --effects \
  --effects-mode local

# With keywords and auto-upload
python -m src.cli generate \
  --topic "Web Development" \
  --keywords "Python" "FastAPI" \
  --duration 90 \
  --upload

# View trending content
python -m src.cli trending

# Test API configuration
python -m src.cli test-api
```

### Python API

```python
import asyncio
from src.modules import VideoAgent

async def main():
    agent = VideoAgent()
    
    try:
        # Generate video
        video_path = await agent.generate_video(
            topic="Artificial Intelligence",
            keywords=["AI", "machine learning"],
            duration=60.0,
            auto_upload=True  # Upload to YouTube
        )
        
        print(f"Video generated: {video_path}")
    finally:
        await agent.close()

asyncio.run(main())
```

## 🏗️ Project Structure

```
video-agent/
├── src/
│   ├── __init__.py
│   ├── main.py                 # Main entry point
│   ├── cli.py                  # CLI interface
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py          # Configuration management
│   │   └── llm.py             # OpenRouter LLM integration
│   └── modules/
│       ├── __init__.py
│       ├── agent.py           # Main orchestration agent
│       ├── content_search.py  # YouTube trending search
│       ├── video_fetcher.py   # Stock video fetching
│       ├── video_editor.py    # Video editing engine
│       ├── voice_subtitle.py  # Voice & subtitle generation
│       └── youtube_uploader.py # YouTube upload
├── config/
│   └── config.example.toml    # Configuration template
├── tests/                     # Unit tests
├── outputs/                   # Generated videos output
├── temp/                      # Temporary files
├── pyproject.toml            # Dependencies
├── .env.example              # Environment template
├── .gitignore                # Git ignore rules
└── README.md                 # This file
```

## ⚙️ Configuration

### config.toml
Main configuration file with settings for:
- LLM model selection and parameters
- Video output format and resolution
- Stock video API credentials
- YouTube upload settings
- Voice synthesis options
- Subtitle generation
- Video editing presets

### .env
Environment variables for sensitive credentials:
- API keys
- OAuth tokens
- Webhook URLs

## 🔄 Video Generation Pipeline

```
1. Content Search
   └─> Find trending content on YouTube

2. AI Planning
   └─> Generate concept and script using LLM

3. Voice Generation
   └─> Create voiceover using TTS

4. Transcript + Timing
   └─> Generate subtitles from audio

5. Hybrid Visual Planning
   ├─> EDL timing from transcript
   ├─> Remotion storyboard scenes
   ├─> Map/fact graphics for geography videos
   └─> Stock/AI b-roll when useful

6. Scene Assembly
   └─> Concatenate scenes, mix voiceover, music, and SFX

7. YouTube Upload
   └─> Upload with metadata and scheduling
```

## 🎯 Supported Models

### LLM Models (via OpenRouter)
- **ByteDance Seedance 2.0**: Recommended (video generation capable)
- OpenAI GPT-4
- Anthropic Claude
- Meta Llama
- And 700+ more models

### TTS Providers
- **ElevenLabs**: High-quality voices (~30 voices)
- **Edge TTS**: Free alternative (basic quality)

### Subtitle Providers
- **Whisper**: Local transcription (more accurate)
- **ElevenLabs Scribe**: Cloud-based API

## 📝 Configuration Examples

### Generate 60s landscape video
```bash
python -m src.cli generate \
  --topic "Tech News" \
  --duration 60 \
  --upload
```

### Generate 90s portrait video (for TikTok/Shorts)
In config.toml:
```toml
[video]
resolution_portrait = "1080x1920"
video_duration = 90
```

### Schedule video for later
```python
agent.uploader.schedule_video(
    video_path="output.mp4",
    title="Future Video",
    publish_time="2024-01-20T15:00:00Z"
)
```

## 🐛 Troubleshooting

### FFmpeg not found
```bash
# Set ffmpeg path in config.toml
[app]
ffmpeg_path = "/usr/local/bin/ffmpeg"  # macOS/Linux
ffmpeg_path = "C:\\ffmpeg\\bin\\ffmpeg.exe"  # Windows
```

### API connection errors
```bash
# Test API connections
python -m src.cli test-api

# Check your API keys
nano .env
```

### Memory/Performance issues
- Reduce `max_concurrent_jobs` in config.toml
- Use smaller Whisper model: `large-v3-turbo`
- Enable GPU in config for faster processing

## 📚 Dependencies

Key dependencies:
- `moviepy`: Video processing
- `opencv-python`: Video analysis
- `yt-dlp`: YouTube downloading
- `google-api-python-client`: YouTube API
- `openai`/`anthropic`: LLM integration
- `httpx`: Async HTTP client
- `faster-whisper`: Subtitle generation
- `pydub`: Audio processing
- `fastapi`: API framework (optional)

Full list in `pyproject.toml`

## 🔐 Security

### Best Practices
- Never commit `.env` file with real keys
- Use environment variables for production
- Rotate API keys regularly
- Use dedicated API keys per environment
- Implement rate limiting in production

## 📈 Performance Tips

1. **Use GPU**: Set `gpu_enabled = true` for faster video processing
2. **Parallel Processing**: Adjust `max_concurrent_jobs` based on system resources
3. **Model Selection**: Use faster LLM models for quicker script generation
4. **Whisper Model**: Use `large-v3-turbo` instead of `large-v3` for faster subtitles

## 🤝 Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Submit a pull request

## 📄 License

MIT License - see LICENSE file for details

## 🙏 Acknowledgments

- Based on concepts from MoneyPrinterTurbo and video-use
- Uses OpenRouter for LLM access
- Stock videos from Pexels, Pixabay, Coverr
- Voice synthesis powered by ElevenLabs and Edge TTS

## 📞 Support

For issues and questions:
- Open an issue on GitHub
- Check existing documentation
- Review configuration examples

## 🗺️ Roadmap

- [ ] Web UI (Streamlit)
- [ ] Video scheduling system
- [ ] Multi-platform support (TikTok, Instagram)
- [ ] Advanced video effects library
- [ ] Custom music generation
- [ ] Analytics and performance tracking
- [ ] Docker deployment
- [ ] API server mode

---

Made with ❤️ by Video Agent Team
