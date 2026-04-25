# tests/conftest.py
# Shared pytest configuration — eliminates the sys.path.insert boilerplate
# that was repeated in every test file.
import sys
from pathlib import Path

# Make the project root importable without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))
