"""Replay validated human decisions and produce the effective draft."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from schemas.validate import load_document, validate
from tools._common import emit_or_write, utc_now


def replay_decisions(draft: dict[str, Any], decisions: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Replay accepted decision records in append order.

    Returns an effective human draft and the final status for every sentence.
    Only accepted, schema-valid decisions can change state.
    """
    states = {
        sentence["id"]: {
            "id": sentence["id"],
            "text": sentence["text"],
            "facts": list(sentence["facts"]),
            "status": "pending",
            "reason": None,
        }
        for sentence in draft.get("sentences", [])
    }
    for decision in decisions:
        if decision.get("result", {}).get("accepted") is not True:
            continue
        command = decision.get("command")
        sentence_id = decision.get("sentence_id")
        if command == "approve_all":
            for state in states.values():
                if state["status"] == "pending":
                    state["status"] = "approved"
        elif sentence_id in states:
            state = states[sentence_id]
            if command == "approve":
                state["status"] = "approved"
                state["reason"] = None
            elif command == "reject":
                state["status"] = "rejected"
                state["reason"] = decision.get("reason")
            elif command == "edit":
                state["text"] = decision["text"]
                if decision.get("facts") is not None:
                    state["facts"] = list(decision["facts"])
                state["status"] = "approved"
                state["reason"] = None

    effective = {
        "schema": "studio/draft@1",
        "run_id": draft["run_id"],
        "agent": "human",
        "created_at": utc_now(),
        "revision": draft["revision"] + 1,
        "format": draft["format"],
        "sentences": [
            {"id": state["id"], "text": state["text"], "facts": list(state["facts"])}
            for state in states.values()
            if state["status"] != "rejected"
        ],
    }
    errors = validate(effective)
    if errors:
        raise ValueError("effective draft failed schema validation: " + "; ".join(errors))
    return effective, states


def load_decisions(path: str | Path, run_id: str) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    source = Path(path)
    if not source.exists():
        return decisions
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            document = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in decisions line {number}: {exc}") from exc
        errors = validate(document)
        if errors or document.get("schema") != "studio/decision@1":
            raise ValueError(f"invalid decision at line {number}: {errors or 'wrong schema'}")
        if document.get("run_id") != run_id:
            raise ValueError(f"decision line {number} belongs to run {document.get('run_id')!r}, expected {run_id!r}")
        decisions.append(document)
    return decisions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, help="current draft JSON path")
    parser.add_argument("--decisions", required=True, help="append-ordered decisions.jsonl")
    parser.add_argument("--out", required=True, help="effective draft JSON path")
    parser.add_argument("--states-out", help="optional JSON sentence-state report path")
    args = parser.parse_args(argv)
    try:
        draft = load_document(args.draft)
        if draft.get("schema") != "studio/draft@1":
            raise ValueError("draft input must be studio/draft@1")
        decisions = load_decisions(args.decisions, draft["run_id"])
        effective, states = replay_decisions(draft, decisions)
        emit_or_write(effective, args.out)
        if args.states_out:
            emit_or_write({"run_id": draft["run_id"], "sentences": list(states.values())}, args.states_out)
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
