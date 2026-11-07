"""Switch Room bridge for Switch Studio.

The bot speaks Switch's WebSocket agent protocol using only the Python
standard library. Room messages are inputs; all durable files stay under
``runs/<run_id>/``. On ``ship``, it posts ``final.md`` into ``#studio`` and
never auto-publishes to external platforms.

Configuration is read from the environment (and, for local runs, ``.env``).
See ``docs/SETUP.md`` for the Switch Console setup and required credentials.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

try:
    from schemas.validate import DocumentError, load_document, validate
except ImportError:  # Allows direct execution while keeping module mode preferred.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from schemas.validate import DocumentError, load_document, validate


RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.IGNORECASE | re.DOTALL)
FACT_RE = re.compile(r"^F[1-9][0-9]*$")
TAG_BLOCK_RE = re.compile(r"(?P<tags>(?:\s*\[F[1-9][0-9]*\])+\s*)$")
MAX_SHIP_MESSAGE_CHARS = 3500

DOCUMENT_NAMES = {
    "studio/run@1": "run.json",
    "studio/snapshot@1": "snapshot.json",
    "studio/facts@1": "facts.json",
    "studio/verdict@1": "verdict.r{revision}.json",
    "studio/review@1": "review.r{revision}.json",
    "studio/grade@1": "grade.json",
}


class BotError(RuntimeError):
    """A safe, user-facing error in local or Switch configuration."""


class ConnectionTakenOver(BotError):
    """Switch has transferred this agent's room connection to another client."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_env_file(path: Path) -> None:
    """Load simple KEY=value lines without replacing explicitly set variables."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = re.split(r"\s+#", value, maxsplit=1)[0].strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def extract_json_document(message: str) -> dict[str, Any] | None:
    """Return the sole fenced JSON document in a Switch room message."""
    blocks = JSON_BLOCK_RE.findall(message)
    if not blocks:
        return None
    if len(blocks) != 1:
        raise BotError("agent messages must contain exactly one fenced ```json block")
    try:
        doc = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise BotError(f"room JSON block is invalid: {exc.msg}") from exc
    if not isinstance(doc, dict):
        raise BotError("room JSON block must contain a JSON object")
    return doc


def parse_command(text: str) -> dict[str, Any] | None:
    """Parse the five human commands defined in CONTRACT.md §8.

    Unknown ordinary room messages return None. A malformed command prefix is
    represented as a ``PARSE_ERROR`` decision, so the audit trail records an
    attempted command without widening the command language.
    """
    command = text.strip()
    if command == "ship":
        return {"command": "ship"}
    if command.startswith("ship "):
        return {"command": "ship", "_parse_error": True}
    if command == "approve all":
        return {"command": "approve_all"}

    match = re.fullmatch(r"approve\s+(S[1-9][0-9]*)", command)
    if match:
        return {"command": "approve", "sentence_id": match.group(1)}

    match = re.fullmatch(r"reject\s+(S[1-9][0-9]*)\s*:\s*(.+)", command, re.DOTALL)
    if match:
        return {"command": "reject", "sentence_id": match.group(1), "reason": match.group(2).strip()}

    match = re.fullmatch(r"edit\s+(S[1-9][0-9]*)\s*:\s*(.+)", command, re.DOTALL)
    if match:
        content = match.group(2).strip()
        tags_match = TAG_BLOCK_RE.search(content)
        if not tags_match:
            return {
                "command": "edit",
                "sentence_id": match.group(1),
                "text": content,
                "facts": [],
            }
        tags = re.findall(r"\[(F[1-9][0-9]*)\]", tags_match.group("tags"))
        new_text = content[:tags_match.start()].strip()
        return {
            "command": "edit",
            "sentence_id": match.group(1),
            "text": new_text,
            "facts": tags,
        }

    for prefix, command_name in (
        ("approve", "approve"),
        ("reject", "reject"),
        ("edit", "edit"),
    ):
        if command == prefix or command.startswith(prefix + " "):
            sentence = re.search(r"\b(S[1-9][0-9]*)\b", command)
            return {
                "command": command_name,
                **({"sentence_id": sentence.group(1)} if sentence else {}),
                "_parse_error": True,
            }
    return None


class RunStore:
    """Validated, traversal-safe reader/writer rooted at runs/<run_id>."""

    def __init__(self, repo_root: Path, run_id: str, runs_dir: Path | None = None):
        if not RUN_ID_RE.fullmatch(run_id):
            raise BotError("run_id must match ^[a-z0-9][a-z0-9-]{2,39}$")
        self.repo_root = repo_root.resolve()
        self.runs_root = (runs_dir or (repo_root / os.getenv("STUDIO_RUNS_DIR", "runs"))).resolve()
        self.run_id = run_id
        self.run_dir = (self.runs_root / run_id).resolve()
        if not self.run_dir.is_relative_to(self.runs_root):
            raise BotError("run directory escapes STUDIO_RUNS_DIR")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.decisions_path = self.run_dir / "decisions.jsonl"

    def read_overflow(self, envelope: dict[str, Any]) -> dict[str, Any]:
        errors = validate(envelope)
        if errors:
            raise BotError("invalid overflow envelope: " + "; ".join(errors))
        if envelope.get("run_id") != self.run_id:
            raise BotError("overflow envelope run_id does not match the active run")
        ref = envelope["payload_ref"]
        rel = Path(ref["path"])
        if rel.is_absolute() or ".." in rel.parts or rel.parts[:2] != ("runs", self.run_id):
            raise BotError("payload_ref.path must stay under runs/<run_id>/")
        target = (self.repo_root / rel).resolve()
        if not target.is_relative_to(self.run_dir):
            raise BotError("payload_ref.path resolves outside the active run")
        try:
            payload_bytes = target.read_bytes()
        except OSError as exc:
            raise BotError(f"cannot read overflow payload {rel}: {exc}") from exc
        if hashlib.sha256(payload_bytes).hexdigest() != ref["sha256"]:
            raise BotError("overflow payload sha256 does not match payload_ref")

        # load_document performs the contract's envelope/payload schema checks.
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", dir=self.run_dir, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(envelope, handle)
        try:
            return load_document(temp_path, base_dir=self.repo_root)
        except DocumentError as exc:
            raise BotError("invalid overflow payload: " + "; ".join(exc.errors)) from exc
        finally:
            temp_path.unlink(missing_ok=True)

    def store_document(self, doc: dict[str, Any]) -> Path:
        errors = validate(doc)
        if errors:
            raise BotError("invalid room document: " + "; ".join(errors))
        if doc.get("run_id") not in (None, self.run_id):
            raise BotError("room document run_id does not match the active run")
        schema = doc["schema"]
        if schema == "studio/draft@1":
            name = f"draft.r{doc['revision']}.json"
        elif schema == "studio/verdict@1":
            name = f"verdict.r{doc['draft_revision']}.json"
        elif schema == "studio/review@1":
            name = f"review.r{doc['draft_revision']}.json"
        else:
            name = DOCUMENT_NAMES.get(schema)
        if not name:
            raise BotError(f"unsupported room document schema {schema!r}")
        path = self.run_dir / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
        return path

    def load_json(self, name: str, *, required: bool = True) -> dict[str, Any] | None:
        path = self.run_dir / name
        if not path.exists():
            if required:
                raise BotError(f"required run artifact is missing: {path.relative_to(self.repo_root)}")
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BotError(f"cannot read {path}: {exc}") from exc
        errors = validate(doc)
        if errors:
            raise BotError(f"invalid {name}: " + "; ".join(errors))
        return doc

    def latest_draft(self) -> tuple[Path, dict[str, Any]]:
        candidates: list[tuple[int, Path]] = []
        for path in self.run_dir.glob("draft.r*.json"):
            match = re.fullmatch(r"draft\.r(\d+)\.json", path.name)
            if match:
                candidates.append((int(match.group(1)), path))
        if not candidates:
            raise BotError(f"no draft.r<N>.json exists in {self.run_dir}")
        _, path = max(candidates, key=lambda item: item[0])
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BotError(f"cannot read latest draft {path}: {exc}") from exc
        errors = validate(draft)
        if errors:
            raise BotError("invalid latest draft: " + "; ".join(errors))
        if draft["run_id"] != self.run_id:
            raise BotError("latest draft run_id does not match the active run")
        return path, draft

    def decisions(self) -> list[dict[str, Any]]:
        if not self.decisions_path.exists():
            return []
        result = []
        for number, line in enumerate(self.decisions_path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BotError(f"invalid decisions.jsonl line {number}: {exc}") from exc
            errors = validate(item)
            if errors:
                raise BotError(f"invalid decision on line {number}: " + "; ".join(errors))
            result.append(item)
        return result

    def sentence_states(self, draft: dict[str, Any]) -> dict[str, str]:
        states = {sentence["id"]: "pending" for sentence in draft["sentences"]}
        for decision in self.decisions():
            if not decision["result"]["accepted"]:
                continue
            command = decision["command"]
            sentence_id = decision.get("sentence_id")
            if command == "approve" and sentence_id in states and states[sentence_id] == "pending":
                states[sentence_id] = "approved"
            elif command == "approve_all":
                states = {key: "approved" if value == "pending" else value for key, value in states.items()}
            elif command == "reject" and sentence_id in states:
                states[sentence_id] = "rejected"
            elif command == "edit" and sentence_id in states:
                states[sentence_id] = "approved"
        return states

    def append_decision(
        self,
        user: str,
        draft_revision: int,
        command: str,
        *,
        accepted: bool,
        error: str | None = None,
        sentence_id: str | None = None,
        reason: str | None = None,
        text: str | None = None,
        facts: list[str] | None = None,
    ) -> dict[str, Any]:
        user = normalize_user(user)
        decision: dict[str, Any] = {
            "schema": "studio/decision@1",
            "run_id": self.run_id,
            "agent": "human",
            "created_at": utc_now(),
            "user": user,
            "draft_revision": draft_revision,
            "command": command,
        }
        if sentence_id is not None:
            decision["sentence_id"] = sentence_id
        if reason is not None:
            decision["reason"] = reason
        if text is not None:
            decision["text"] = text
        if facts is not None:
            decision["facts"] = facts
        decision["result"] = {"accepted": accepted, "error": error}
        errors = validate(decision)
        if errors:
            raise BotError("refusing to write invalid decision: " + "; ".join(errors))
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.decisions_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(decision, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return decision


def normalize_user(value: str) -> str:
    candidate = value.strip().lstrip("@")
    candidate = re.sub(r"[^A-Za-z0-9-]+", "-", candidate).strip("-")
    if not candidate:
        candidate = "unknown"
    return "@" + candidate[:64]


def run_tool(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )


def command_result_error(result: subprocess.CompletedProcess[str], action: str) -> str:
    detail = (result.stderr or result.stdout).strip()
    return f"{action} failed (exit {result.returncode})" + (f": {detail}" if detail else "")


class CommandProcessor:
    def __init__(self, store: RunStore, *, repo_root: Path | None = None):
        self.store = store
        self.repo_root = (repo_root or store.repo_root).resolve()

    def process(self, text: str, user: str) -> dict[str, Any] | None:
        parsed = parse_command(text)
        if parsed is None:
            return None
        _, draft = self.store.latest_draft()
        revision = draft["revision"]
        if parsed.pop("_parse_error", False):
            return self.store.append_decision(
                user,
                revision,
                parsed["command"],
                accepted=False,
                error="PARSE_ERROR",
                sentence_id=parsed.get("sentence_id"),
            )

        command = parsed["command"]
        sentence_id = parsed.get("sentence_id")
        if command in {"approve", "reject", "edit"}:
            if sentence_id not in {sentence["id"] for sentence in draft["sentences"]}:
                return self.store.append_decision(
                    user,
                    revision,
                    command,
                    accepted=False,
                    error="UNKNOWN_SENTENCE",
                    sentence_id=sentence_id,
                    reason=parsed.get("reason"),
                    text=parsed.get("text"),
                    facts=parsed.get("facts"),
                )

        if command == "approve":
            states = self.store.sentence_states(draft)
            if states[sentence_id] != "pending":
                return self.store.append_decision(user, revision, command, accepted=False, error="PARSE_ERROR", sentence_id=sentence_id)
            return self.store.append_decision(user, revision, command, accepted=True, sentence_id=sentence_id)

        if command == "approve_all":
            return self.store.append_decision(user, revision, command, accepted=True)

        if command == "reject":
            states = self.store.sentence_states(draft)
            if states[sentence_id] != "pending":
                return self.store.append_decision(user, revision, command, accepted=False, error="PARSE_ERROR", sentence_id=sentence_id, reason=parsed["reason"])
            return self.store.append_decision(user, revision, command, accepted=True, sentence_id=sentence_id, reason=parsed["reason"])

        if command == "edit":
            facts = parsed["facts"]
            if not facts:
                return self.store.append_decision(
                    user, revision, command, accepted=False, error="EDIT_NO_CITATION",
                    sentence_id=sentence_id, text=parsed["text"], facts=[],
                )
            if any(not FACT_RE.fullmatch(fact) for fact in facts):
                return self.store.append_decision(user, revision, command, accepted=False, error="EDIT_VERIFY_FAILED", sentence_id=sentence_id, text=parsed["text"], facts=facts)

            candidate = json.loads(json.dumps(draft))
            candidate["agent"] = "human"
            candidate["revision"] = revision + 1
            candidate["created_at"] = utc_now()
            sentence = next(sentence for sentence in candidate["sentences"] if sentence["id"] == sentence_id)
            sentence["text"] = parsed["text"]
            sentence["facts"] = facts
            errors = validate(candidate)
            if errors:
                return self.store.append_decision(user, revision, command, accepted=False, error="EDIT_VERIFY_FAILED", sentence_id=sentence_id, text=parsed["text"], facts=facts)

            facts_path = self.store.run_dir / "facts.json"
            snapshot_path = self.store.run_dir / "snapshot.json"
            if not facts_path.exists() or not snapshot_path.exists():
                return self.store.append_decision(user, revision, command, accepted=False, error="EDIT_VERIFY_FAILED", sentence_id=sentence_id, text=parsed["text"], facts=facts)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", dir=self.store.run_dir, delete=False) as handle:
                candidate_path = Path(handle.name)
                # Verify the edited sentence independently. An unrelated
                # failing sentence must not prevent a valid human edit from
                # being accepted and recorded as a new full-draft revision.
                verify_candidate = json.loads(json.dumps(candidate))
                verify_candidate["sentences"] = [sentence]
                json.dump(verify_candidate, handle)
            verdict_path = self.store.run_dir / f"verdict.r{candidate['revision']}.json"
            try:
                result = run_tool(
                    self.repo_root,
                    "tools.verify",
                    "--snapshot", str(snapshot_path),
                    "--facts", str(facts_path),
                    "--draft", str(candidate_path),
                    "--out", str(verdict_path),
                )
                if result.returncode != 0 or not verdict_path.exists():
                    return self.store.append_decision(user, revision, command, accepted=False, error="EDIT_VERIFY_FAILED", sentence_id=sentence_id, text=parsed["text"], facts=facts)
                try:
                    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return self.store.append_decision(user, revision, command, accepted=False, error="EDIT_VERIFY_FAILED", sentence_id=sentence_id, text=parsed["text"], facts=facts)
                if verdict.get("pass") is not True:
                    return self.store.append_decision(user, revision, command, accepted=False, error="EDIT_VERIFY_FAILED", sentence_id=sentence_id, text=parsed["text"], facts=facts)
                target = self.store.run_dir / f"draft.r{candidate['revision']}.json"
                target.write_text(json.dumps(candidate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                return self.store.append_decision(user, revision, command, accepted=True, sentence_id=sentence_id, text=parsed["text"], facts=facts)
            finally:
                candidate_path.unlink(missing_ok=True)

        if command == "ship":
            states = self.store.sentence_states(draft)
            if any(state == "pending" for state in states.values()):
                return self.store.append_decision(user, revision, command, accepted=False, error="SHIP_BLOCKED")
            effective_path = self.store.run_dir / f"effective.r{revision}.json"
            applied = run_tool(
                self.repo_root,
                "tools.apply",
                "--draft", str(self.store.run_dir / f"draft.r{revision}.json"),
                "--decisions", str(self.store.decisions_path),
                "--out", str(effective_path),
            )
            if applied.returncode != 0 or not effective_path.exists():
                return self.store.append_decision(user, revision, command, accepted=False, error="SHIP_BLOCKED")
            verdict_path = self.store.run_dir / f"verdict.r{revision}.ship.json"
            verified = run_tool(
                self.repo_root,
                "tools.verify",
                "--snapshot", str(self.store.run_dir / "snapshot.json"),
                "--facts", str(self.store.run_dir / "facts.json"),
                "--draft", str(effective_path),
                "--out", str(verdict_path),
            )
            if verified.returncode != 0 or not verdict_path.exists():
                return self.store.append_decision(user, revision, command, accepted=False, error="SHIP_BLOCKED")
            try:
                verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return self.store.append_decision(user, revision, command, accepted=False, error="SHIP_BLOCKED")
            if verdict.get("pass") is not True:
                return self.store.append_decision(user, revision, command, accepted=False, error="SHIP_BLOCKED")
            rendered = run_tool(
                self.repo_root,
                "tools.render",
                "--draft", str(effective_path),
                "--facts", str(self.store.run_dir / "facts.json"),
                "--out", str(self.store.run_dir / "final.md"),
            )
            if rendered.returncode != 0 or not (self.store.run_dir / "final.md").exists():
                return self.store.append_decision(user, revision, command, accepted=False, error="SHIP_BLOCKED")
            return self.store.append_decision(user, revision, command, accepted=True)

        return None


def message_from_event(event: dict[str, Any]) -> tuple[str, str] | None:
    if isinstance(event.get("data"), dict):
        event = {**event["data"], "type": event["data"].get("type") or event.get("event") or event.get("type")}
    if event.get("type") != "message":
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    body = payload.get("body")
    if not isinstance(body, str):
        return None
    sender = payload.get("sender")
    if isinstance(sender, dict):
        sender = sender.get("id") or sender.get("name") or sender.get("agent_id")
    user = payload.get("on_behalf_of") or payload.get("sender_name") or sender or "unknown"
    return body, str(user)


def event_is_human_message(
    event: dict[str, Any], *, bot_agent_id: str = "", known_agent_identities: set[str] | None = None,
) -> bool:
    if isinstance(event.get("data"), dict):
        event = {**event["data"], "type": event["data"].get("type") or event.get("event") or event.get("type")}
    if event.get("type") != "message":
        return False
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    if payload.get("on_behalf_of"):
        return True
    kind = str(payload.get("sender_kind") or payload.get("sender_type") or "").lower()
    if kind in {"user", "human", "person"}:
        return True
    if kind in {"agent", "bot", "system"}:
        return False
    identities = {item.casefold() for item in (known_agent_identities or set()) if item}
    if bot_agent_id:
        identities.add(bot_agent_id.casefold())
    sender = payload.get("sender")
    if isinstance(sender, dict):
        kind = str(sender.get("kind") or sender.get("type") or "").lower()
        if kind in {"agent", "bot", "system"}:
            return False
        if kind in {"user", "human", "person"}:
            return True
        sender = sender.get("id") or sender.get("agent_id") or sender.get("name")
    sender_name = payload.get("sender_name")
    if str(sender or "").casefold() in identities or str(sender_name or "").casefold() in identities:
        return False
    return bool(sender or sender_name)


def process_room_message(
    store: RunStore,
    text: str,
    user: str,
    *,
    repo_root: Path | None = None,
    allow_commands: bool = True,
) -> list[str]:
    """Persist any contract document and execute a human command if present."""
    messages: list[str] = []
    try:
        doc = extract_json_document(text)
        if doc is not None:
            if "payload_ref" in doc:
                doc = store.read_overflow(doc)
            path = store.store_document(doc)
            messages.append(f"stored {doc['schema']} at {path.relative_to(store.repo_root)}")
    except (BotError, DocumentError) as exc:
        messages.append(f"document ignored: {exc}")

    if allow_commands:
        try:
            decision = CommandProcessor(store, repo_root=repo_root).process(text, user)
            if decision is not None:
                outcome = "accepted" if decision["result"]["accepted"] else f"refused ({decision['result']['error']})"
                messages.append(f"{decision['command']} {outcome}")
        except BotError as exc:
            messages.append(f"command could not run: {exc}")
    return messages


def format_shipped_message(
    run_id: str,
    content: str,
    *,
    max_chars: int = MAX_SHIP_MESSAGE_CHARS,
) -> str:
    """Format runs/<run_id>/final.md as a plain room message within max_chars."""
    rel_path = f"runs/{run_id}/final.md"
    header = f"Run {run_id} — shipped final.md:"
    body = content.strip()
    full_message = f"{header}\n\n{body}"
    if len(full_message) <= max_chars:
        return full_message
    footer = f"\n\n... (truncated; full file saved at {rel_path})"
    budget = max(0, max_chars - len(header) - 2 - len(footer))
    return f"{header}\n\n{body[:budget].rstrip()}{footer}"


class RoomWorkflow:
    """Kick off and advance Scout → Writer ↔ Editor → human handoffs."""

    def __init__(self, store: RunStore, client: SwitchClient, repo_root: Path):
        self.store = store
        self.client = client
        self.repo_root = repo_root.resolve()
        self.room_id = ""
        self.scout = os.getenv("STUDIO_SCOUT_AGENT", "codex.scout.ritweek")
        self.writer = os.getenv("STUDIO_WRITER_AGENT", "codex.writer.ritweek")
        self.editor = os.getenv("STUDIO_EDITOR_AGENT", "codex.editor.ritweek")
        self.identities = {name.casefold() for name in (self.scout, self.writer, self.editor)}
        self.identities.update(item.casefold() for item in (
            os.getenv("STUDIO_SCOUT_AGENT_ID", ""),
            os.getenv("STUDIO_WRITER_AGENT_ID", ""),
            os.getenv("STUDIO_EDITOR_AGENT_ID", ""),
        ) if item)
        self.handoff_path = self.store.run_dir / "handoffs.json"
        try:
            saved = json.loads(self.handoff_path.read_text(encoding="utf-8"))
            self.sent_handoffs = set(saved.get("sent", [])) if isinstance(saved, dict) else set()
        except (OSError, json.JSONDecodeError):
            self.sent_handoffs = set()

    def _post_once(self, key: str, content: str) -> None:
        if key in self.sent_handoffs:
            return
        self.client.post_message(self.room_id, content)
        self.sent_handoffs.add(key)
        temporary = self.handoff_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"run_id": self.store.run_id, "sent": sorted(self.sent_handoffs)}, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.handoff_path)

    def _post_task(self, key: str, agent: str, instructions: str) -> None:
        self._post_once(key, f"@{agent} {instructions}")

    def _snapshot(self) -> str:
        return f"runs/{self.store.run_id}/snapshot.json"

    def _draft_revisions(self) -> list[tuple[int, Path]]:
        revisions = []
        for path in self.store.run_dir.glob("draft.r*.json"):
            match = re.fullmatch(r"draft\.r(\d+)\.json", path.name)
            if match:
                revisions.append((int(match.group(1)), path))
        return sorted(revisions)

    def kickoff(self, room_id: str) -> None:
        """Resume the next agent handoff based on durable run artifacts."""
        self.room_id = room_id
        if not (self.store.run_dir / "snapshot.json").is_file():
            raise BotError(f"missing run snapshot: runs/{self.store.run_id}/snapshot.json")
        if not (self.store.run_dir / "facts.json").is_file():
            self._post_task("scout", self.scout, f"Start Switch Studio run {self.store.run_id}. Read {self._snapshot()}, follow CONTRACT.md §§2–4, and post one valid studio/facts@1 JSON block with numbered facts tied to full commit hashes.")
            return
        revisions = self._draft_revisions()
        if not revisions:
            self._request_writer()
            return
        revision = revisions[-1][0]
        review_path = self.store.run_dir / f"review.r{revision}.json"
        if not review_path.is_file():
            self._request_editor(revision)
            return
        review = json.loads(review_path.read_text(encoding="utf-8"))
        if review.get("handoff") == "writer" and int(review.get("loop", 2)) < 2 and not (self.store.run_dir / f"draft.r{revision + 1}.json").exists():
            self._request_writer_revision(revision)

    def _request_writer(self) -> None:
        self._post_task("writer:r0", self.writer, f"Write revision r0 for run {self.store.run_id} using runs/{self.store.run_id}/facts.json. Follow CONTRACT.md §§2, 5–7 and post one valid studio/draft@1 JSON block.")

    def _request_editor(self, revision: int) -> None:
        self._post_task(f"editor:r{revision}", self.editor, f"Review run {self.store.run_id} draft revision {revision}. Read {self._snapshot()}, runs/{self.store.run_id}/facts.json, and runs/{self.store.run_id}/draft.r{revision}.json. Run python -m tools.verify first, then post one valid studio/review@1 JSON block with handoff per CONTRACT.md §7.")

    def _request_writer_revision(self, revision: int) -> None:
        self._post_task(f"writer:r{revision + 1}", self.writer, f"Revise run {self.store.run_id} from draft r{revision}. Read runs/{self.store.run_id}/draft.r{revision}.json and review.r{revision}.json; change rejected sentences only, increment revision, and post one valid studio/draft@1 JSON block.")

    def _advance_document(self, doc: dict[str, Any]) -> None:
        schema = doc.get("schema")
        if schema == "studio/facts@1":
            self._request_writer()
        elif schema == "studio/draft@1" and doc.get("agent") == "writer":
            self._request_editor(int(doc["revision"]))
        elif schema == "studio/review@1":
            revision = int(doc["draft_revision"])
            if doc.get("handoff") == "writer" and int(doc.get("loop", 2)) < 2:
                self._request_writer_revision(revision)
            elif doc.get("handoff") == "humans":
                self._post_once(f"humans:r{revision}", f"Switch Studio run {self.store.run_id} is ready for human review. Use `approve S<N>`, `approve all`, `reject S<N>: reason`, or `edit S<N>: replacement [F1]`; then send `ship`.")

    def handle(self, event: dict[str, Any]) -> None:
        message = message_from_event(event)
        if message is None:
            return
        text, speaker = message
        is_human = event_is_human_message(event, bot_agent_id=self.client.agent_id, known_agent_identities=self.identities)
        if is_human:
            reports = process_room_message(self.store, text, speaker, repo_root=self.repo_root, allow_commands=True)
            if parse_command(text) is not None:
                status = next((item for item in reports if item.startswith(("approve ", "approve_all ", "reject ", "edit ", "ship "))), "command recorded")
                self.client.post_message(self.room_id, f"Run {self.store.run_id}: {status}.")
                if status == "ship accepted":
                    final_path = self.store.run_dir / "final.md"
                    if final_path.is_file():
                        final_text = final_path.read_text(encoding="utf-8")
                        self.client.post_message(
                            self.room_id,
                            format_shipped_message(self.store.run_id, final_text),
                        )
            return
        try:
            doc = extract_json_document(text)
            if doc is None:
                return
            if "payload_ref" in doc:
                doc = self.store.read_overflow(doc)
            self.store.store_document(doc)
            self._advance_document(doc)
        except (BotError, DocumentError) as exc:
            print(f"Switch room document ignored: {exc}", file=sys.stderr)


class WebSocket:
    """Minimal RFC 6455 client sufficient for Switch text/control frames."""

    def __init__(self, sock: socket.socket):
        self.sock = sock

    def receive_exactly(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.sock.recv(size - len(chunks))
            if not chunk:
                raise BotError("Switch WebSocket closed unexpectedly")
            chunks.extend(chunk)
        return bytes(chunks)

    def receive_frame(self) -> tuple[bool, int, bytes]:
        first, second = self.receive_exactly(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        if first & 0x70:
            raise BotError("Switch WebSocket sent unsupported extensions")
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self.receive_exactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.receive_exactly(8))[0]
        if length > 16 * 1024 * 1024:
            raise BotError("Switch WebSocket frame exceeds 16 MiB")
        mask = self.receive_exactly(4) if masked else b""
        payload = self.receive_exactly(length)
        if masked:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        if not fin and opcode >= 8:
            raise BotError("Switch WebSocket sent a fragmented control frame")
        return fin, opcode, payload

    def send_frame(self, opcode: int, payload: bytes) -> None:
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = secrets.token_bytes(4)
        header.extend(mask)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send_json(self, value: dict[str, Any]) -> None:
        self.send_frame(1, json.dumps(value, separators=(",", ":")).encode("utf-8"))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class SwitchClient:
    """Stdlib Switch client using the current WebSocket agent protocol."""

    def __init__(self, base_url: str, agent_id: str, token: str, room: str, *, state_path: Path | None = None):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.token = token
        self.room = room
        self.state_path = state_path
        saved: dict[str, Any] = {}
        if state_path and state_path.is_file():
            try:
                saved = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                saved = {}
        self.connection_id = str(saved.get("connection_id") or uuid.uuid4())
        self._cursor = int(saved.get("cursor") or 0)
        self._generation = saved.get("generation")
        self._room_id = str(saved.get("room_id") or "")
        self._persist_state()

    def _persist_state(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps({
            "connection_id": self.connection_id,
            "cursor": self._cursor,
            "generation": self._generation,
            "room_id": self._room_id,
        }, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, *, timeout: float = 20) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise BotError(f"Switch request {method} {path} failed: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BotError(f"Switch returned invalid JSON for {path}") from exc

    def resolve_room(self) -> str:
        if self.room and not self.room.startswith("#"):
            self._room_id = self.room
            return self._room_id
        path = f"/agents/{quote(self.agent_id, safe='')}/ops/list_rooms"
        response = self._request("POST", path, {})
        result = response.get("result", response) if isinstance(response, dict) else response
        candidates: list[Any] = []
        if isinstance(result, list):
            candidates = result
        elif isinstance(result, dict):
            for key in ("rooms", "items", "results"):
                if isinstance(result.get(key), list):
                    candidates = result[key]
                    break
        for room in candidates:
            if not isinstance(room, dict):
                continue
            name = str(room.get("name") or room.get("room_name") or room.get("display_name") or "")
            if name.lstrip("#").lower() == self.room.lstrip("#").lower() and room.get("id"):
                self._room_id = str(room["id"])
                self._persist_state()
                return self._room_id
        raise BotError(f"could not resolve {self.room!r}; set SWITCH_ROOM_ID to its Switch room id")

    def post_message(self, room_id: str, content: str) -> None:
        path = f"/agents/{quote(self.agent_id, safe='')}/message"
        self._request("POST", path, {"room_id": room_id, "content": content})

    def _socket_url(self, room_id: str) -> tuple[str, str, int, str, str]:
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise BotError("SWITCH_BASE_URL must use http:// or https:// and include a host")
        secure = parsed.scheme == "https"
        scheme = "wss" if secure else "ws"
        port = parsed.port or (443 if secure else 80)
        host_header = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
        query_values: dict[str, str] = {
            "connection_id": self.connection_id,
            "scope": "single",
            "filter": "all",
            "start_from": str(self._cursor) if self._cursor else "head",
            "rooms": room_id,
            "client": "switch-studio-infra",
            "client_version": "0.1.0",
        }
        if self._generation is not None:
            query_values["expected_generation"] = str(self._generation)
        prefix = parsed.path.rstrip("/")
        path = f"{prefix}/agents/{quote(self.agent_id, safe='')}/connection/ws?{urlencode(query_values)}"
        return scheme, parsed.hostname, port, host_header, path

    def _open_websocket(self, room_id: str) -> WebSocket:
        scheme, host, port, host_header, path = self._socket_url(room_id)
        secure = scheme == "wss"
        sock = socket.create_connection((host, port), timeout=20)
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Authorization: Bearer {self.token}\r\n\r\n"
        ).encode("ascii")
        try:
            sock.sendall(request)
            response = bytearray()
            while not response.endswith(b"\r\n\r\n"):
                chunk = sock.recv(1)
                if not chunk:
                    raise BotError("Switch closed during WebSocket handshake")
                response.extend(chunk)
                if len(response) > 65536:
                    raise BotError("Switch WebSocket handshake headers are too large")
            lines = response.decode("iso-8859-1").split("\r\n")
            if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
                raise BotError(f"Switch WebSocket upgrade failed: {lines[0]}")
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    name, value = line.split(":", 1)
                    headers[name.strip().lower()] = value.strip()
            expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
            if headers.get("sec-websocket-accept") != expected:
                raise BotError("Switch WebSocket handshake returned an invalid Sec-WebSocket-Accept")
            sock.settimeout(30)
            return WebSocket(sock)
        except Exception:
            sock.close()
            raise

    def _handle_frame(self, frame: dict[str, Any], on_event: Callable[[dict[str, Any]], None]) -> bool:
        """Handle one Switch frame. Return false after a final takeover."""
        kind = str(frame.get("event") or frame.get("type") or "")
        data = frame.get("data")
        if isinstance(data, dict):
            event = dict(data)
            event.setdefault("type", data.get("type") or kind)
        else:
            event = dict(frame)
            event.setdefault("type", kind)
        event_kind = str(event.get("type") or "")
        if event_kind == "connection_state":
            if isinstance(event.get("cursor"), int):
                self._cursor = max(self._cursor, event["cursor"])
            self._generation = event.get("generation", self._generation)
            self._persist_state()
        elif event_kind == "ping":
            return True
        elif event_kind == "evicted":
            code = str(event.get("code") or "")
            if code == "taken_over":
                raise ConnectionTakenOver("Switch reports this connection was taken over; it will not reconnect")
            raise BotError(f"Switch evicted this connection ({code or 'unknown'}): {event.get('reason', '')}")
        elif event_kind == "refused":
            raise BotError(f"Switch refused the connection ({event.get('status')}): {event.get('detail', '')}")
        elif event_kind == "gap":
            if isinstance(event.get("resumed_at"), int):
                self._cursor = event["resumed_at"]
                self._persist_state()
        on_event(event)
        sequence = frame.get("id")
        if not isinstance(sequence, int):
            sequence = event.get("sequence")
        if isinstance(sequence, int):
            self._cursor = max(self._cursor, sequence)
            self._persist_state()
        return True

    def _event_stream(self, room_id: str, on_event: Callable[[dict[str, Any]], None]) -> None:
        websocket = self._open_websocket(room_id)
        fragment_opcode: int | None = None
        fragments = bytearray()
        try:
            while True:
                try:
                    fin, opcode, payload = websocket.receive_frame()
                except socket.timeout as exc:
                    raise BotError("Switch WebSocket timed out") from exc
                if opcode == 8:
                    return
                if opcode == 9:
                    websocket.send_frame(10, payload)
                    continue
                if opcode == 10:
                    continue
                if opcode == 1:
                    fragment_opcode = opcode
                    fragments = bytearray(payload)
                elif opcode == 0 and fragment_opcode is not None:
                    fragments.extend(payload)
                else:
                    raise BotError(f"Switch sent unsupported WebSocket opcode {opcode}")
                if not fin:
                    continue
                try:
                    frame = json.loads(fragments.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    # A valid fragment can be incomplete until continuation.
                    continue
                if not isinstance(frame, dict):
                    raise BotError("Switch WebSocket frame must contain a JSON object")
                data = frame.get("data")
                frame_kind = frame.get("event") or frame.get("type")
                if isinstance(data, dict):
                    frame_kind = data.get("type") or frame_kind
                if frame_kind == "ping":
                    websocket.send_json({"type": "pong", "cursor": self._cursor})
                self._handle_frame(frame, on_event)
                fragment_opcode = None
                fragments.clear()
        finally:
            websocket.close()

    def listen(
        self,
        on_event: Callable[[dict[str, Any]], None],
        on_ready: Callable[[str], None] | None = None,
    ) -> None:
        room_id = self._room_id or self.resolve_room()
        print(f"Switch infra bot listening in {self.room} ({room_id})")
        if on_ready:
            on_ready(room_id)
        backoff = 1
        while True:
            try:
                self._event_stream(room_id, on_event)
                backoff = 1
            except KeyboardInterrupt:
                raise
            except ConnectionTakenOver:
                raise
            except (BotError, OSError, ssl.SSLError) as exc:
                print(f"Switch connection: {exc}", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(backoff * 2, 15)


def infer_run_id(repo_root: Path, explicit: str | None) -> str:
    run_id = explicit or os.getenv("STUDIO_RUN_ID")
    if run_id:
        return run_id
    runs_root = (repo_root / os.getenv("STUDIO_RUNS_DIR", "runs")).resolve()
    candidates = []
    if runs_root.exists():
        for path in runs_root.glob("*/run.json"):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if RUN_ID_RE.fullmatch(str(doc.get("run_id", ""))):
                candidates.append((path.stat().st_mtime, doc["run_id"]))
    if not candidates:
        raise BotError("no active run; pass --run-id or set STUDIO_RUN_ID after creating runs/<run_id>/run.json")
    return max(candidates)[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Switch Studio repository root")
    parser.add_argument("--run-id", help="run_id for this room bridge; defaults to STUDIO_RUN_ID/latest run.json")
    parser.add_argument("--offline-jsonl", type=Path, help="process one JSON Switch event per line, then exit")
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    load_env_file(repo_root / ".env")
    try:
        run_id = infer_run_id(repo_root, args.run_id)
        store = RunStore(repo_root, run_id)
        if args.offline_jsonl:
            for line in args.offline_jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                message = message_from_event(event)
                if message is None:
                    continue
                for report in process_room_message(store, *message, repo_root=repo_root, allow_commands=event_is_human_message(event)):
                    print(report)
            return 0

        base_url = os.getenv("SWITCH_BASE_URL", "").strip()
        token = os.getenv("SWITCH_TOKEN", "").strip()
        agent_id = os.getenv("SWITCH_AGENT_ID", "").strip()
        room = os.getenv("SWITCH_ROOM_ID", "").strip() or os.getenv("SWITCH_ROOM", "#studio").strip()
        missing = [name for name, value in (("SWITCH_BASE_URL", base_url), ("SWITCH_TOKEN", token), ("SWITCH_AGENT_ID", agent_id), ("SWITCH_ROOM or SWITCH_ROOM_ID", room)) if not value]
        if missing:
            raise BotError("missing Switch configuration: " + ", ".join(missing))
        client = SwitchClient(base_url, agent_id, token, room, state_path=store.run_dir / "switch-connection.json")
        workflow = RoomWorkflow(store, client, repo_root)

        def on_event(event: dict[str, Any]) -> None:
            workflow.handle(event)

        client.listen(on_event, workflow.kickoff)
        return 0
    except (BotError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"infra bot: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
