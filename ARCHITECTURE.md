# Architecture Guide

## Overview

Video Agent is built using a modular architecture with clear separation of concerns:

```
┌─────────────────────────────────────────────────────┐
│            CLI / Python API Interface               │
│  (src/cli.py, src/main.py)                          │
└────────┬──────────────────────────────────────┬────┘
         │                                      │
         │                                      │
    ┌────▼────────────────────────────────┐    │
    │    VideoAgent Orchestrator           │    │
    │  (src/modules/agent.py)              │    │
    └────┬──────────┬──────────┬──────────┬┘    │
         │          │          │          │      │
    ┌────▼───┐  ┌───▼───┐  ┌──▼──┐  ┌───▼────┐│
    │ Content│  │ Stock │  │Video│  │ Voice &│││
    │ Search │  │ Video │  │Editor│ │Subtitle││
    │        │  │Fetcher│  │      │  │        ││
    └─────┬──┘  └───┬───┘  └──┬───┘  └───┬────┘│
          │         │         │          │     │
    ┌────▼──────────▼─────────▼──────────▼────┐│
    │    YouTube Uploader                    ││
    │  (src/modules/youtube_uploader.py)     ││
    └────────────────────────────────────────┘│
         │                                     │
    ┌────▼──────────────────────────────┐     │
    │   Core Modules                     │     │
    │ • LLM Integration (OpenRouter)    │     │
    │ • Config Management               │     │
    └───────────────────────────────────┘     │
                                              │
                                        External APIs
                                        • YouTube API
                                        • OpenRouter
                                        • Pexels/Pixabay
                                        • ElevenLabs
```

## Module Responsibilities

### 1. **Core Modules** (`src/core/`)

#### `config.py`
- Environment variable management
- TOML configuration file parsing
- Centralized settings access
- Directory creation and setup

#### `llm.py`
- OpenRouter API client
- Streaming and non-streaming chat
- Message formatting
- Response parsing

### 2. **Content Search** (`content_search.py`)
- YouTube Trending API integration
- Topic-based search
- Video metrics aggregation
- Content ranking

### 3. **Stock Video Fetching** (`video_fetcher.py`)
- Multi-source support (Pexels, Pixabay, Coverr)
- Async batch fetching
- Quality filtering
- URL validation

### 4. **Video Editing** (`video_editor.py`)
- MoviePy-based video processing
- Cutting and concatenation
- Color grading (OpenCV)
- Text overlay and resizing

### 5. **Voice & Subtitles** (`voice_subtitle.py`)
- ElevenLabs TTS integration
- Edge TTS fallback (free)
- Whisper-based transcription
- SRT subtitle generation

### 6. **YouTube Upload** (`youtube_uploader.py`)
- OAuth authentication
- Video upload with metadata
- Thumbnail upload
- Scheduling and playlist management

### 7. **Agent Orchestrator** (`agent.py`)
- Workflow coordination
- Error handling and retries
- Progress tracking
- Result aggregation

## Data Flow

### Video Generation Pipeline

```
1. Input Topic
        ↓
2. Search Trending Content (YouTube)
        ↓
3. LLM Generates Plan (OpenRouter)
   ├─ Concept
   ├─ Script
   └─ Keywords
        ↓
4. Fetch Stock Videos
   ├─ Pexels
   ├─ Pixabay
   └─ Coverr
        ↓
5. Generate Voiceover (ElevenLabs/Edge TTS)
        ↓
6. Generate Subtitles (Whisper)
        ↓
7. Edit Video
   ├─ Concatenate clips
   ├─ Add audio
   ├─ Color grade
   └─ Resize
        ↓
8. Upload to YouTube
        ↓
9. Output: Video ID
```

## Async/Concurrency Model

- **Async I/O**: All API calls are asynchronous using `httpx.AsyncClient`
- **Concurrent Requests**: Multiple stock video APIs queried in parallel
- **Background Tasks**: Video processing can run in background
- **Task Management**: Python asyncio for coordination

## Configuration Hierarchy

1. **Environment Variables** (highest priority)
   - `.env` file
   - System environment

2. **TOML Configuration**
   - `config/config.toml`
   - Section-based grouping

3. **Default Values** (lowest priority)
   - Hardcoded defaults in code

## Error Handling Strategy

- **Graceful Degradation**: Continue with alternative sources if one fails
- **Retry Logic**: Tenacity library for API retries
- **Logging**: Structured logging with Loguru
- **User Feedback**: Clear CLI error messages

## Performance Considerations

- **Async Operations**: Non-blocking I/O throughout
- **Batch Processing**: Concurrent API requests
- **Caching**: Temporary file management
- **GPU Support**: Optional GPU acceleration for video processing
- **Memory Management**: Cleanup of temporary files

## Extension Points

### Add New Stock Video Source
1. Create method in `StockVideoFetcher`
2. Implement API client
3. Add to configuration
4. Update `search_all_sources()`

### Add New LLM Provider
1. Extend `OpenRouterLLM` or create new provider
2. Update `config.py` to support provider
3. Modify `agent.py` to use new provider

### Add New Video Effect
1. Create method in `VideoEditor`
2. Use MoviePy or OpenCV
3. Add configuration option
4. Call from `_edit_video()`

## API Integration Points

### YouTube API
- Authentication: OAuth 2.0
- Video Upload: multipart/form-data
- Metadata: Snippet, Status objects

### OpenRouter
- Authentication: Bearer token
- Models: 700+ available
- Streaming: SSE support

### Stock Video APIs
- Authentication: API key headers
- Rate Limiting: Implement backoff
- Pagination: Handle cursor-based results

### ElevenLabs
- TTS: /v1/text-to-speech/{voice_id}
- Scribe: /v1/audio-to-text (transcription)
- Voices: 30+ voices available

## Testing Strategy

- **Unit Tests**: Individual module functions
- **Integration Tests**: Full workflow testing
- **Mock APIs**: Stub external services
- **Fixtures**: Pre-recorded API responses

## Deployment Considerations

### Development
- Local .env configuration
- SQLite for caching (future)
- Direct file output

### Production
- Environment-based secrets
- Rate limiting implementation
- Error monitoring (Sentry)
- Batch job scheduling
- Video output to cloud storage

## Future Improvements

1. Database integration for video history
2. Web UI with Streamlit
3. Multi-platform support (TikTok, Instagram)
4. Advanced video effects library
5. Real-time processing updates
6. Analytics dashboard
7. Custom model fine-tuning
