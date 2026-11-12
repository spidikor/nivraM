"""Compatibility import for the native acceptance fixture."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nivram.native_dialog_fixture import NativeFileChoice
