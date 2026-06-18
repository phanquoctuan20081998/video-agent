# API Keys Setup Guide

This guide explains how to obtain and configure API keys for Video Agent.

## Quick Setup

Run the setup script to create `.env` and `config.toml` files:

```bash
chmod +x setup.sh && ./setup.sh  # macOS/Linux
# OR
setup.bat                         # Windows
```

Then update `.env` with your API keys.

## API Keys

### 1. OpenRouter (LLM)

Used for: Script generation, video concept planning

**Why OpenRouter?**
- Access to 700+ LLM models
- Pay-per-token (cost-effective)
- No rate limits per model
- Supports video generation models like ByteDance Seedance 2.0

**Setup Steps:**

1. Visit: https://openrouter.ai/auth/sign-up
2. Create account and verify email
3. Go to: https://openrouter.ai/keys
4. Create new API key
5. Copy key to `.env`:
   ```
   OPENROUTER_API_KEY=sk-xxx...
   ```

**Models Available:**
```toml
[llm]
model = "bytedance/seedance-2.0"     # Recommended for video
model = "openai/gpt-4-turbo"         # High quality
model = "anthropic/claude-3-opus"    # Advanced reasoning
model = "meta-llama/llama-2-70b"     # Open source
```

**Cost Estimate:** ~$0.01-0.05 per video (script generation only)

---

### 2. YouTube API

Used for: Trending content search, video upload, playlist management

**Setup Steps:**

1. Go to: https://console.cloud.google.com/
2. Create new project (or use existing)
3. Enable APIs:
   - YouTube Data API v3
   - YouTube Reporting API
4. Create OAuth 2.0 credentials:
   - Application type: Desktop app
   - Download JSON file → save as `youtube_oauth.json`
5. Create API key:
   - Copy to `.env`:
   ```
   YOUTUBE_DEVELOPER_KEY=AIza...
   YOUTUBE_CLIENT_ID=xxx...client...
   YOUTUBE_CLIENT_SECRET=xxx...secret...
   ```

**First Run:**
When you first upload a video, you'll be prompted to authorize in browser.
Token is saved as `youtube_token.pickle` for future use.

**Quotas:**
- Free tier: 10,000 API quota units per day
- One search: 100 units
- One video upload: 1,600 units
- ~6 videos per day on free tier

---

### 3. Pexels (Stock Videos)

Used for: High-quality stock video fetching

**Setup Steps:**

1. Visit: https://www.pexels.com/api/
2. Click "Get Started"
3. Create account
4. Go to: https://www.pexels.com/api/
5. Copy API key to `.env`:
   ```
   PEXELS_API_KEY=xxx...
   ```

**Quotas:**
- Free tier: 200 API calls per hour
- ~50-100 videos per day

---

### 4. Pixabay (Stock Videos)

Used for: Alternative stock video source

**Setup Steps:**

1. Visit: https://pixabay.com/api/
2. Register for free account
3. Go to: https://pixabay.com/api/docs/
4. Copy API key to `.env`:
   ```
   PIXABAY_API_KEY=xxx...
   ```

**Quotas:**
- Free tier: 50 requests per hour
- Unlimited videos returned per request

---

### 5. ElevenLabs (Voice Synthesis)

Used for: High-quality voiceover generation

**Setup Steps:**

1. Visit: https://elevenlabs.io/
2. Sign up for free account
3. Go to: https://elevenlabs.io/app/settings/api-keys
4. Create new API key
5. Copy to `.env`:
   ```
   ELEVENLABS_API_KEY=xxx...
   ```

**Features:**
- 30+ realistic voices
- Multiple languages
- Real-time voice cloning (paid)
- Streaming API support

**Free Tier:**
- 10,000 characters per month
- ~5-10 videos per month

**Pricing:** $5/month for 100K characters (~50 videos)

**Voice Options:**
```toml
[audio]
voice_id = "Rachel"        # Female US English
voice_id = "Bella"         # Female British English
voice_id = "George"        # Male US English
# Many more available in UI
```

---

## Configuration File

### `.env` Template

```bash
# OpenRouter (LLM)
OPENROUTER_API_KEY=sk-your-key-here
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=bytedance/seedance-2.0

# YouTube
YOUTUBE_DEVELOPER_KEY=AIza...
YOUTUBE_CLIENT_ID=xxx.apps.googleusercontent.com
YOUTUBE_CLIENT_SECRET=GOCSPX-...

# Stock Videos
PEXELS_API_KEY=xxx
PIXABAY_API_KEY=xxx
COVERR_API_KEY=xxx  # Optional

# Voice & Audio
ELEVENLABS_API_KEY=xxx
VOICE_ID=Rachel

# General
LOG_LEVEL=INFO
DEBUG=false
```

### `config.toml` Template

```toml
[llm]
provider = "openrouter"
model = "bytedance/seedance-2.0"
temperature = 0.7
max_tokens = 4000

[video]
output_format = "mp4"
bitrate = "3000k"
frame_rate = 30

[stock_videos]
pexels_api_key = ""  # From .env
pixabay_api_key = ""
preferred_sources = ["pexels", "pixabay", "coverr"]

[audio]
tts_provider = "elevenlabs"
voice_id = "Rachel"
sample_rate = 44100

[youtube]
upload_auto = false
```

---

## Security Best Practices

### Do's ✓
- Store API keys in `.env` file
- Rotate keys regularly
- Use different keys per environment
- Keep `.env` in `.gitignore`

### Don'ts ✗
- Don't commit `.env` to Git
- Don't share API keys publicly
- Don't use same key across environments
- Don't log API keys

### If Compromised
1. Immediately rotate/delete compromised key
2. Create new API key
3. Update `.env`
4. Monitor API usage for suspicious activity

---

## Testing API Keys

Run the test command to verify all connections:

```bash
python -m src.cli test-api
```

Expected output:
```
Testing API connections...

1. OpenRouter API... ✓ Configured
2. YouTube API... ✓ Configured
3. Pexels API... ✓ Configured
4. ElevenLabs API... ✓ Configured
```

---

## Cost Breakdown (Per Video)

| Service | Cost | Notes |
|---------|------|-------|
| OpenRouter | $0.01-0.05 | Script generation only |
| YouTube API | Free | 10K quota/day included |
| Pexels | Free | 200 requests/hour |
| Pixabay | Free | 50 requests/hour |
| ElevenLabs | $0.10-0.30 | Depends on length |
| **Total** | **~$0.15-0.40** | Per video |

---

## Troubleshooting

### "API Key not configured"
- Check `.env` file exists
- Verify key is correctly copied
- Run `python -m src.cli test-api`

### "Invalid API Key"
- Re-generate and copy key
- Remove quotes if accidentally included
- Check for trailing spaces

### Rate Limit Exceeded
- Wait for quota reset (usually hourly)
- Use backup API keys
- Reduce batch size in config

### YouTube OAuth Error
- Delete `youtube_token.pickle`
- Re-authenticate on next run
- Check OAuth scopes in Google Console

### No Stock Videos Found
- Verify Pexels/Pixabay keys
- Check internet connection
- Try different search terms

---

## Alternative/Free Options

### Free Voice Synthesis
Instead of ElevenLabs, use Edge TTS:
```toml
[audio]
tts_provider = "edge_tts"  # Free
```

Pros: Free, good quality
Cons: Fewer voice options

### Free LLM Models
Use public models via OpenRouter:
```toml
[llm]
model = "meta-llama/llama-2-70b"  # Free tier available
```

---

## Next Steps

1. ✓ Get all API keys
2. ✓ Update `.env` and `config.toml`
3. ✓ Run `python -m src.cli test-api`
4. ✓ Generate first video: `python -m src.cli generate --topic "Your Topic"`

Happy video generation! 🎬
