"""Writer agent package (`studio/facts@1` -> `studio/draft@1`)."""

from __future__ import annotations

from agents.writer.agent import main, run_writer, strip_inline_tags, summarize_draft

__all__ = [
    "main",
    "run_writer",
    "strip_inline_tags",
    "summarize_draft",
]
