# Troubleshooting Guide

## Common Issues and Solutions

### Installation Issues

#### FFmpeg Not Found
**Error:** `RuntimeError: No ffmpeg exe could be found`

**Solution:**
```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# Windows
# Download from https://ffmpeg.org/download.html
# Set path in config.toml
```

#### Python Version Mismatch
**Error:** `Python 3.11 or higher required`

**Solution:**
```bash
# Check version
python --version

# Install Python 3.11
# macOS: brew install python@3.11
# Ubuntu: sudo apt install python3.11
# Windows: Download from python.org
```

---

### Configuration Issues

#### API Keys Not Found
**Error:** `API key not configured`

**Solution:**
1. Check `.env` file exists: `ls .env`
2. Check key is set: `cat .env | grep OPENROUTER`
3. Reload environment: `source .venv/bin/activate`
4. Verify in Python:
   ```bash
   python -c "from src.core import config; print(config.settings.openrouter_api_key)"
   ```

#### Invalid API Key
**Error:** `401 Unauthorized` or similar

**Solution:**
1. Verify key is correct: `echo $OPENROUTER_API_KEY`
2. Check for typos or extra spaces
3. Regenerate key in provider's dashboard
4. Test with curl:
   ```bash
   curl -H "Authorization: Bearer YOUR_KEY" \
     https://openrouter.ai/api/v1/models
   ```

---

### Runtime Issues

#### Out of Memory
**Error:** `MemoryError` or system becomes unresponsive

**Solution:**
```toml
# config.toml - Reduce batch size
[processing]
batch_size = 2  # was 5
max_concurrent_jobs = 1  # was 3
gpu_enabled = false

# Reduce video quality
[video]
bitrate = "1000k"  # was "3000k"
```

#### GPU CUDA Errors
**Error:** `CUDA out of memory` or GPU detection issues

**Solution:**
```toml
# Disable GPU
[processing]
gpu_enabled = false
```

#### Movie Processing Errors
**Error:** `moviepy - error processing video`

**Solution:**
```bash
# Update moviepy
pip install --upgrade moviepy

# Check codec support
ffmpeg -codecs | grep h264

# Convert to compatible format first
ffmpeg -i input.mp4 -vcodec libx264 -acodec aac output.mp4
```

---

### API Issues

#### YouTube Authentication Error
**Error:** `error_code: 403` or authentication loop

**Solution:**
```bash
# Delete old token
rm youtube_token.pickle

# On next run, re-authenticate in browser
python -m src.cli generate --topic "test"

# Check credentials
cat youtube_oauth.json  # Should exist
```

#### YouTube Quota Exceeded
**Error:** `403 Quota exceeded`

**Solution:**
```bash
# Quotas reset daily
# Option 1: Wait until next day

# Option 2: Use different YouTube API key
# Create new project: https://console.cloud.google.com/

# Option 3: Disable auto-upload
[youtube]
upload_auto = false
```

#### Pexels/Pixabay Rate Limit
**Error:** `429 Too Many Requests`

**Solution:**
```toml
# Reduce concurrent requests
[processing]
max_concurrent_jobs = 1

# Reduce search scope
[search]
max_results = 5  # was 10
```

#### OpenRouter API Error
**Error:** `429 - Rate limited` or `503 - Service unavailable`

**Solution:**
```bash
# Check API status
curl https://openrouter.ai/api/v1/models

# Try different model
# config.toml
[llm]
model = "meta-llama/llama-2-70b"  # fallback

# Increase timeout
[llm]
timeout = 60  # seconds
```

---

### File Issues

#### Permission Denied
**Error:** `Permission denied` on setup.sh

**Solution:**
```bash
chmod +x setup.sh
./setup.sh
```

#### Cannot Write to Output Directory
**Error:** `Permission denied: ./outputs`

**Solution:**
```bash
# Check permissions
ls -la outputs/

# Fix ownership
sudo chown -R $USER:$USER ./outputs
chmod 755 ./outputs

# Or change output directory
mkdir ~/video_outputs
# Update config.toml
[paths]
output_dir = "~/video_outputs"
```

#### Too Many Open Files
**Error:** `OSError: [Errno 24] Too many open files`

**Solution:**
```bash
# Increase system limit (macOS/Linux)
ulimit -n 4096

# Or make permanent
# Add to ~/.zshrc or ~/.bashrc:
# ulimit -n 4096
```

---

### Subtitle/Voice Issues

#### Whisper Model Download Failed
**Error:** `Cannot find whisper model` or download timeout

**Solution:**
```bash
# Pre-download model
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo')"

# Or use manual download
# See: https://huggingface.co/Systran/faster-whisper-large-v3

# Use smaller model (faster)
[subtitles]
model_size = "base"  # or "small", "medium"
```

#### ElevenLabs Character Limit
**Error:** `Quota exceeded` on voiceover generation

**Solution:**
```bash
# Check usage: https://elevenlabs.io/app/subscription

# Option 1: Upgrade plan
# Option 2: Use shorter scripts
# Option 3: Use free Edge TTS
[audio]
tts_provider = "edge_tts"
```

#### No Audio Output
**Error:** Video generated but no sound

**Solution:**
```bash
# Check audio provider configured
[audio]
tts_provider = "elevenlabs"
voice_id = "Rachel"

# Test voiceover generation
python -c "from src.modules import VoiceoverGenerator; ..."

# Check audio codec
ffmpeg -i output.mp4 -c:a aac output_fixed.mp4
```

---

### Logging and Debugging

#### Enable Debug Mode
```bash
# CLI
python -m src.cli generate --debug

# .env
LOG_LEVEL=DEBUG
DEBUG=true

# Logs saved to
cat logs/video_agent.log
```

#### Verbose Output
```bash
# Python direct execution
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from src.modules import VideoAgent
# ... run code
"
```

#### Check Logs
```bash
# View logs
tail -f logs/video_agent.log

# Search for errors
grep "ERROR" logs/video_agent.log

# Count errors
grep "ERROR" logs/video_agent.log | wc -l
```

---

### Getting Help

#### Before Asking for Help

1. **Check logs:**
   ```bash
   tail -50 logs/video_agent.log
   ```

2. **Test APIs:**
   ```bash
   python -m src.cli test-api
   ```

3. **Check configuration:**
   ```bash
   cat .env | grep -v '#'
   cat config/config.toml | grep -v '^#'
   ```

4. **Search existing issues:**
   https://github.com/yourusername/video-agent/issues

#### Report Issues

When reporting issues, include:
```
- Error message (full stack trace)
- Last 20 lines of logs/video_agent.log
- Python version: python --version
- OS: uname -a
- FFmpeg version: ffmpeg -version
- Steps to reproduce
- Configuration (sanitized): cat config/config.toml
```

---

### Performance Optimization

#### Slow Video Processing
**Solution:**
```toml
# Enable GPU
[processing]
gpu_enabled = true

# Reduce output quality (faster)
[video]
bitrate = "1500k"
frame_rate = 24  # from 30

# Reduce search scope
[search]
max_results = 5
```

#### Slow LLM Responses
**Solution:**
```toml
# Use faster model
[llm]
model = "meta-llama/llama-2-7b"  # faster than 70b

# Reduce max tokens
max_tokens = 2000  # from 4000

# Lower temperature (more deterministic)
temperature = 0.5
```

#### Reduce Memory Usage
**Solution:**
```toml
# Smaller batch size
[processing]
batch_size = 1
max_concurrent_jobs = 1

# Cleanup temp files
cleanup_temp = true
```

---

### System-Specific Issues

#### macOS Issues

**Issue:** M1/M2 Chip Compatibility
```bash
# Install native Python
arch -arm64 brew install python@3.11

# Create venv with native Python
arch -arm64 python3.11 -m venv .venv
```

#### Linux Issues

**Issue:** Missing GLIBC
```bash
# Update glibc
sudo apt-get install libc-bin

# Or use Docker
docker-compose up
```

#### Windows Issues

**Issue:** PATH Issues
```bash
# Add Python to PATH:
set PATH=%PATH%;C:\Users\YourUsername\AppData\Local\Programs\Python\Python311

# Or use Windows Terminal with Python installed
```

---

## Quick Reference

| Issue | Command |
|-------|---------|
| Check config | `python -m src.cli test-api` |
| View logs | `tail -f logs/video_agent.log` |
| Clear temp | `rm -rf temp/*` |
| Reset auth | `rm youtube_token.pickle` |
| Check FFmpeg | `ffmpeg -version` |
| Check Python | `python --version` |
| Reinstall deps | `pip install -r requirements.txt --force-reinstall` |

---

Still having issues? Check the [GitHub Issues](https://github.com/yourusername/video-agent/issues) or create a new one!
