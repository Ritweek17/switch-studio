"""CLI entry point for `python -m agents.scout`."""

from __future__ import annotations

import sys

from agents.scout.agent import main

if __name__ == "__main__":
    sys.exit(main())
