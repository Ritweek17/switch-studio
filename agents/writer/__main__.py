"""CLI entry point for `python -m agents.writer`."""

from __future__ import annotations

import sys

from agents.writer.agent import main

if __name__ == "__main__":
    sys.exit(main())
