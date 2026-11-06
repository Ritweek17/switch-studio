"""Shared stdlib helpers for the Switch Studio command line tools."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def utc_now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: str | Path, document: Any) -> None:
    """Write JSON atomically, creating parent directories as needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def emit_or_write(document: Any, output: str | None) -> None:
    data = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if output:
        write_json(output, document)
    else:
        print(data, end="")
