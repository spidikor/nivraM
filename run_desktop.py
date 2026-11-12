"""Frozen Windows entrypoint."""
import multiprocessing
import sys
from nivram.desktop import main

if __name__ == '__main__':
    multiprocessing.freeze_support()
    if '--acceptance-report' in sys.argv:
        from nivram.acceptance import main
    raise SystemExit(main())
