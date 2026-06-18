#!/usr/bin/env python3
"""
Video Agent - Main CLI Entry Point
"""

import sys
import subprocess

def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Video Agent CLI")
        print("Usage: video-agent <command> [options]")
        print("\nCommands:")
        print("  generate     Generate a video from topic")
        print("  trending     Show trending content")
        print("  test-api     Test API connections")
        print("  init-config  Initialize configuration files")
        sys.exit(1)
    
    # Forward to CLI module
    subprocess.run([sys.executable, "-m", "src.cli"] + sys.argv[1:])

if __name__ == "__main__":
    main()
