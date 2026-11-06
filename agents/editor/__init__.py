"""Editor agent package (`studio/draft@1` -> `studio/review@1`)."""

from __future__ import annotations

from agents.editor.agent import (
    detect_overclaim,
    main,
    run_editor,
    run_verifier,
    summarize_review,
)

__all__ = [
    "detect_overclaim",
    "main",
    "run_editor",
    "run_verifier",
    "summarize_review",
]
