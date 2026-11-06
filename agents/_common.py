"""Shared utilities for Switch Studio M2 agents (Scout, Writer, Editor).

Stdlib-only helpers for:
- UTC ISO-8601 timestamps (`YYYY-MM-DDTHH:MM:SSZ`)
- Reading & writing validated `studio/*` documents
- Formatting `#studio` room messages (short summary + fenced JSON block)
- Creating `payload_ref` overflow envelopes when payloads exceed threshold
- Optional stdlib (`urllib.request`) OpenAI Chat Completions invocation
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from schemas.validate import DocumentError, load_document, validate

DEFAULT_OVERFLOW_BYTES = 3500
DEFAULT_MODEL = "gpt-4o-mini"

_FENCED_JSON_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


class AgentExecutionError(Exception):
    """Raised when an agent cannot produce a valid studio/* document."""


def utc_now() -> str:
    """Return current UTC timestamp matching `^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$`."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_prompt(agent_dir: Path) -> str:
    """Read the markdown system prompt for an agent."""
    return (agent_dir / "prompt.md").read_text(encoding="utf-8").strip()


def ensure_valid(doc: dict[str, Any]) -> dict[str, Any]:
    """Validate `doc` against its `studio/*` schema, raising DocumentError on failure."""
    errors = validate(doc)
    if errors:
        raise DocumentError(errors)
    return doc


def write_document(doc: dict[str, Any], out_path: str | Path) -> Path:
    """Validate `doc` and write formatted JSON to `out_path`, creating parent dirs."""
    ensure_valid(doc)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, indent=2) + "\n"
    target.write_text(body, encoding="utf-8")
    return target


def build_overflow_envelope(
    doc: dict[str, Any],
    payload_path: str | Path,
    *,
    base_dir: str | Path = ".",
) -> dict[str, Any]:
    """Write `doc` to `payload_path` and return a validated overflow envelope."""
    ensure_valid(doc)
    target = Path(base_dir) / payload_path
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(doc, indent=2) + "\n").encode("utf-8")
    target.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()

    rel_path = Path(payload_path).as_posix()
    envelope: dict[str, Any] = {
        "schema": doc["schema"],
        "run_id": doc["run_id"],
        "agent": doc["agent"],
        "created_at": doc["created_at"],
        "payload_ref": {
            "path": rel_path,
            "sha256": digest,
        },
    }
    ensure_valid(envelope)
    return envelope


def format_room_message(
    summary: str,
    doc: dict[str, Any],
    *,
    overflow_path: str | Path | None = None,
    max_inline_bytes: int = DEFAULT_OVERFLOW_BYTES,
    base_dir: str | Path = ".",
) -> str:
    """Format a `#studio` room message per ADR-0007.

    Returns a short human summary line followed by a single fenced ```json block.
    If `overflow_path` is provided and the serialized JSON exceeds `max_inline_bytes`
    (or `max_inline_bytes == 0`), writes the full document to `overflow_path` and
    embeds the `payload_ref` overflow envelope instead.
    """
    ensure_valid(doc)
    serialized = json.dumps(doc, indent=2)
    if overflow_path is not None and len(serialized.encode("utf-8")) > max_inline_bytes:
        emitted = build_overflow_envelope(doc, overflow_path, base_dir=base_dir)
        serialized = json.dumps(emitted, indent=2)
    clean_summary = summary.strip()
    return f"{clean_summary}\n\n```json\n{serialized}\n```\n"


def extract_message_json(
    message: str,
    *,
    base_dir: str | Path = ".",
) -> dict[str, Any]:
    """Extract and validate the single fenced ```json block from a room message.

    If the block is an overflow envelope (`payload_ref`), resolves and validates
    the referenced payload under `base_dir`.
    """
    matches = _FENCED_JSON_RE.findall(message)
    if len(matches) != 1:
        raise DocumentError([f"expected exactly 1 fenced ```json block, found {len(matches)}"])
    try:
        doc = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise DocumentError([f"invalid JSON in room message: {exc}"]) from exc

    errors = validate(doc)
    if errors:
        raise DocumentError(errors)

    if "payload_ref" in doc:
        target = Path(base_dir) / doc["payload_ref"]["path"]
        try:
            body = target.read_bytes()
        except OSError as exc:
            raise DocumentError([f"$.payload_ref.path: {exc}"]) from exc
        if hashlib.sha256(body).hexdigest() != doc["payload_ref"]["sha256"]:
            raise DocumentError([f"$.payload_ref.sha256: mismatch for {target}"])
        payload = json.loads(body)
        if payload.get("schema") != doc["schema"]:
            raise DocumentError(
                [f"{target}: schema {payload.get('schema')!r} != envelope {doc['schema']!r}"]
            )
        payload_errors = validate(payload)
        if payload_errors:
            raise DocumentError(payload_errors)
        return payload

    return doc


def call_llm_json(
    system_prompt: str,
    user_payload: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Call an OpenAI-compatible chat completions endpoint using Python stdlib only.

    Returns the parsed JSON object from the assistant response.
    Raises `AgentExecutionError` on network, HTTP, or JSON errors.
    """
    key = api_key or os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise AgentExecutionError("OPENAI_API_KEY is not set")

    resolved_model = model or os.environ.get("STUDIO_MODEL", DEFAULT_MODEL)
    resolved_url = (
        base_url
        or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        + "/chat/completions"
    )

    body = json.dumps(
        {
            "model": resolved_model,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, indent=2)},
            ],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        resolved_url,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AgentExecutionError(f"LLM request failed: {exc}") from exc

    try:
        envelope = json.loads(raw)
        content = envelope["choices"][0]["message"]["content"]
        if isinstance(content, str):
            fenced = _FENCED_JSON_RE.search(content)
            text = fenced.group(1) if fenced else content.strip()
            parsed = json.loads(text)
        else:
            parsed = content
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AgentExecutionError(f"Malformed LLM JSON response: {exc}") from exc

    if not isinstance(parsed, dict):
        raise AgentExecutionError("LLM response root must be a JSON object")
    return parsed


__all__ = [
    "AgentExecutionError",
    "DEFAULT_MODEL",
    "DEFAULT_OVERFLOW_BYTES",
    "build_overflow_envelope",
    "call_llm_json",
    "ensure_valid",
    "extract_message_json",
    "format_room_message",
    "load_document",
    "load_prompt",
    "utc_now",
    "write_document",
]
