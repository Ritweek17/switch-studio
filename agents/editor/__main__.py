"""CLI entry point for `python -m agents.editor`."""

from __future__ import annotations

import sys

from agents.editor.agent import main

if __name__ == "__main__":
    sys.exit(main())
