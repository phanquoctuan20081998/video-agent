# Video Agent - Quick Reference

## Installation

```bash
# Clone
git clone https://github.com/yourusername/video-agent.git
cd video-agent

# Setup (macOS/Linux)
chmod +x setup.sh && ./setup.sh

# Setup (Windows)
setup.bat
```

## Configuration

1. **Edit .env file**
   - Add OpenRouter API key
   - Add YouTube API key
   - Add Pexels/ElevenLabs keys

2. **Edit config/config.toml** (optional)
   - Adjust video settings
   - Change LLM model
   - Configure audio options

## Quick Commands

```bash
# Test APIs
python -m src.cli test-api

# Generate video
python -m src.cli generate --topic "Your Topic"

# View trending
python -m src.cli trending

# With Makefile
make run                 # Generate video
make trending           # Show trending content
make test-api           # Test API setup
make test               # Run tests
make lint               # Check code quality
```

## Using Docker

```bash
# Build
docker build -t video-agent .

# Run
docker-compose up

# Or manual
docker run -it \
  -e OPENROUTER_API_KEY=your_key \
  -v $(pwd)/outputs:/app/outputs \
  video-agent:latest
```

## Python API

```python
import asyncio
from src.modules import VideoAgent

async def main():
    agent = VideoAgent()
    try:
        video_path = await agent.generate_video(
            topic="AI Trends",
            duration=60.0,
            auto_upload=True
        )
        print(f"Generated: {video_path}")
    finally:
        await agent.close()

asyncio.run(main())
```

## Project Structure

```
video-agent/
├── src/
│   ├── core/             # LLM & config
│   ├── modules/          # Main features
│   └── cli.py           # Command line
├── config/              # Templates
├── tests/               # Unit tests
├── outputs/             # Generated videos
├── README.md            # Full guide
├── API_KEYS_SETUP.md    # Credentials
└── ARCHITECTURE.md      # Design docs
```

## Troubleshooting

```bash
# Check logs
tail -f logs/video_agent.log

# Reset YouTube auth
rm youtube_token.pickle

# Clear cache
make clean

# Test individual APIs
python -m src.cli test-api
```

## Performance Tips

- Use faster LLM model: `meta-llama/llama-2-7b`
- Enable GPU: Set `gpu_enabled = true` in config
- Reduce batch size for low-memory systems
- Use `edge_tts` for free voice synthesis

## Support

- Documentation: README.md
- Issues: GitHub Issues
- API Setup: API_KEYS_SETUP.md
- Troubleshooting: TROUBLESHOOTING.md
- Architecture: ARCHITECTURE.md

## Next Steps

1. ✓ Clone repository
2. ✓ Run setup.sh
3. ✓ Configure API keys in .env
4. ✓ Run `python -m src.cli test-api`
5. ✓ Generate first video: `python -m src.cli generate --topic "Your Topic"`

Happy video generation! 🎬
