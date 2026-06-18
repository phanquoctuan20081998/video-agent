# Docker Usage Guide

## Quick Start

### 1. Build Image
```bash
docker build -t video-agent:latest .
```

### 2. Setup Environment
```bash
cp .env.example .env
# Edit .env with your API keys
cp config/config.example.toml config/config.toml
```

### 3. Run with Docker Compose
```bash
docker-compose up
```

### 4. Run with Docker (Manual)
```bash
docker run -it \
  -e OPENROUTER_API_KEY=your_key \
  -e YOUTUBE_DEVELOPER_KEY=your_key \
  -e PEXELS_API_KEY=your_key \
  -e ELEVENLABS_API_KEY=your_key \
  -v $(pwd)/outputs:/app/outputs \
  -v $(pwd)/logs:/app/logs \
  video-agent:latest
```

## Commands

### Generate Video
```bash
docker-compose exec video-agent python -m src.cli generate --topic "Your Topic"
```

### View Trending
```bash
docker-compose exec video-agent python -m src.cli trending
```

### Test APIs
```bash
docker-compose exec video-agent python -m src.cli test-api
```

## Output

Generated videos are saved to `./outputs/` directory on your host machine.

## Troubleshooting

### Permission Denied
```bash
chmod +x setup.sh
```

### Port Already in Use
```bash
docker-compose down
docker-compose up
```

### Out of Memory
Allocate more memory to Docker:
- Docker Desktop: Preferences → Resources → Memory
- Command line: Use `docker run -m 4g`

## Production Deployment

For production, consider:
- Use managed container registry (ECR, Docker Hub)
- Set resource limits
- Use secrets management (AWS Secrets Manager, etc.)
- Implement health checks
- Use multi-stage build for smaller images
