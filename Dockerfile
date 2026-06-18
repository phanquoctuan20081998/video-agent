FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create necessary directories
RUN mkdir -p temp outputs logs

# Expose port for potential API (future feature)
EXPOSE 8000

# Default command
CMD ["python", "-m", "src.cli", "generate", "--topic", "AI Trends", "--duration", "60"]
