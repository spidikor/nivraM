"""Run the same bounded native acceptance harness shipped with the candidate."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nivram.acceptance import main

if __name__ == '__main__':
    raise SystemExit(main())
