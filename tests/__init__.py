"""
Test package initialization
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

# Pytest configuration
def pytest_configure(config):
    """Pytest configuration hook"""
    pass
