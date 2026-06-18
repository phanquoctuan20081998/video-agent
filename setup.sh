#!/bin/bash
# Setup script for Video Agent

set -e

echo "🎬 Video Agent Setup Script"
echo "=============================="
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python version: $PYTHON_VERSION"

# Create virtual environment if not exists
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3.11 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate
echo "✓ Virtual environment activated"

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
echo "✓ Dependencies installed"

# Check FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "⚠ FFmpeg not found. Installing..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install ffmpeg
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        sudo apt-get install ffmpeg
    fi
else
    echo "✓ FFmpeg found"
fi

# Check yt-dlp
if ! command -v yt-dlp &> /dev/null; then
    echo "⚠ yt-dlp not found. Installing..."
    pip install yt-dlp
else
    echo "✓ yt-dlp found"
fi

# Copy configuration files
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "✓ Created .env (please update with your API keys)"
fi

if [ ! -f "config/config.toml" ]; then
    cp config/config.example.toml config/config.toml
    echo "✓ Created config/config.toml"
fi

# Create necessary directories
mkdir -p temp outputs logs

echo ""
echo "=============================="
echo "✓ Setup completed successfully!"
echo ""
echo "Next steps:"
echo "1. Update .env with your API keys"
echo "2. Update config/config.toml if needed"
echo "3. Run: python -m src.cli test-api (to test connections)"
echo "4. Run: python -m src.cli generate --topic 'Your Topic'"
echo ""
