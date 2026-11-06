"""Decision application and replay for Switch Studio (implements CONTRACT.md §8)."""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

from schemas.validate import load_document, validate


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_and_apply_command(
    raw_command: str,
    draft: dict,
    sentence_states: dict[str, dict],
    snapshot: dict | None = None,
    facts_doc: dict | None = None,
    user: str = "@human",
    run_id: str | None = None,
) -> dict:
    """Parse one raw command string, evaluate it against state, and return studio/decision@1."""
    from sandbox.verifier import verify_draft, verify_facts, verify_sentence

    cmd = raw_command.strip()
    run_id = run_id or draft.get("run_id", "demo-001")
    draft_rev = draft.get("revision", 0)

    # 1. approve all
    if cmd == "approve all":
        decision = {
            "schema": "studio/decision@1",
            "run_id": run_id,
            "agent": "human",
            "created_at": _iso_now(),
            "user": user,
            "draft_revision": draft_rev,
            "command": "approve_all",
            "result": {"accepted": True, "error": None},
        }
        for s in sentence_states.values():
            if s["status"] == "pending":
                s["status"] = "approved"
        return decision

    # 2. approve S<N>
    m_app = re.fullmatch(r"approve\s+(S[1-9][0-9]*)", cmd)
    if m_app:
        sid = m_app.group(1)
        if sid not in sentence_states:
            return {
                "schema": "studio/decision@1",
                "run_id": run_id,
                "agent": "human",
                "created_at": _iso_now(),
                "user": user,
                "draft_revision": draft_rev,
                "command": "approve",
                "sentence_id": sid,
                "result": {"accepted": False, "error": "UNKNOWN_SENTENCE"},
            }
        sentence_states[sid]["status"] = "approved"
        return {
            "schema": "studio/decision@1",
            "run_id": run_id,
            "agent": "human",
            "created_at": _iso_now(),
            "user": user,
            "draft_revision": draft_rev,
            "command": "approve",
            "sentence_id": sid,
            "result": {"accepted": True, "error": None},
        }

    # 3. reject S<N>: <reason>
    m_rej = re.fullmatch(r"reject\s+(S[1-9][0-9]*)(?::\s*(.*))?", cmd)
    if m_rej:
        sid = m_rej.group(1)
        reason = m_rej.group(2) or "Rejected by human."
        if sid not in sentence_states:
            return {
                "schema": "studio/decision@1",
                "run_id": run_id,
                "agent": "human",
                "created_at": _iso_now(),
                "user": user,
                "draft_revision": draft_rev,
                "command": "reject",
                "sentence_id": sid,
                "reason": reason,
                "result": {"accepted": False, "error": "UNKNOWN_SENTENCE"},
            }
        sentence_states[sid]["status"] = "rejected"
        sentence_states[sid]["reason"] = reason
        return {
            "schema": "studio/decision@1",
            "run_id": run_id,
            "agent": "human",
            "created_at": _iso_now(),
            "user": user,
            "draft_revision": draft_rev,
            "command": "reject",
            "sentence_id": sid,
            "reason": reason,
            "result": {"accepted": True, "error": None},
        }

    # 4. edit S<N>: <new text> [F2][F5]
    m_edit = re.fullmatch(r"edit\s+(S[1-9][0-9]*):\s*(.*)", cmd)
    if m_edit:
        sid = m_edit.group(1)
        content = m_edit.group(2).strip()
        if sid not in sentence_states:
            return {
                "schema": "studio/decision@1",
                "run_id": run_id,
                "agent": "human",
                "created_at": _iso_now(),
                "user": user,
                "draft_revision": draft_rev,
                "command": "edit",
                "sentence_id": sid,
                "text": content,
                "result": {"accepted": False, "error": "UNKNOWN_SENTENCE"},
            }

        # Extract trailing [F...] tags
        tag_match = re.search(r"((?:\[F[1-9][0-9]*\])+)\s*$", content)
        if not tag_match:
            return {
                "schema": "studio/decision@1",
                "run_id": run_id,
                "agent": "human",
                "created_at": _iso_now(),
                "user": user,
                "draft_revision": draft_rev,
                "command": "edit",
                "sentence_id": sid,
                "text": content,
                "facts": [],
                "result": {"accepted": False, "error": "EDIT_NO_CITATION"},
            }

        tags_str = tag_match.group(1)
        facts = re.findall(r"\[(F[1-9][0-9]*)\]", tags_str)
        text_body = content[: tag_match.start()].strip()

        # An edit is never accepted without complete verification context.
        if snapshot is None or facts_doc is None:
            return {
                "schema": "studio/decision@1", "run_id": run_id, "agent": "human", "created_at": _iso_now(),
                "user": user, "draft_revision": draft_rev, "command": "edit", "sentence_id": sid,
                "text": text_body, "facts": facts, "result": {"accepted": False, "error": "EDIT_VERIFY_FAILED"},
            }
        else:
            fact_results = verify_facts(snapshot, facts_doc)
            facts_by_id = {f["id"]: f for f in facts_doc.get("facts", [])}
            facts_ok = {r["id"]: r["ok"] for r in fact_results}
            commits_by_hash = {c["hash"]: c for c in snapshot.get("commits", [])}

            candidate_sentence = {"id": sid, "text": text_body, "facts": facts}
            s_errors = verify_sentence(candidate_sentence, facts_by_id, facts_ok, commits_by_hash)
            if s_errors:
                return {
                    "schema": "studio/decision@1",
                    "run_id": run_id,
                    "agent": "human",
                    "created_at": _iso_now(),
                    "user": user,
                    "draft_revision": draft_rev,
                    "command": "edit",
                    "sentence_id": sid,
                    "text": text_body,
                    "facts": facts,
                    "result": {"accepted": False, "error": "EDIT_VERIFY_FAILED"},
                }

        sentence_states[sid]["text"] = text_body
        sentence_states[sid]["facts"] = facts
        sentence_states[sid]["status"] = "approved"

        return {
            "schema": "studio/decision@1",
            "run_id": run_id,
            "agent": "human",
            "created_at": _iso_now(),
            "user": user,
            "draft_revision": draft_rev,
            "command": "edit",
            "sentence_id": sid,
            "text": text_body,
            "facts": facts,
            "result": {"accepted": True, "error": None},
        }

    # 5. ship
    if cmd == "ship":
        # Check if any sentence is pending
        has_pending = any(s["status"] == "pending" for s in sentence_states.values())
        if has_pending:
            return {
                "schema": "studio/decision@1",
                "run_id": run_id,
                "agent": "human",
                "created_at": _iso_now(),
                "user": user,
                "draft_revision": draft_rev,
                "command": "ship",
                "result": {"accepted": False, "error": "SHIP_BLOCKED"},
            }

        # Shipping is never allowed without the complete verification context.
        if snapshot is None or facts_doc is None:
            return {
                "schema": "studio/decision@1", "run_id": run_id, "agent": "human", "created_at": _iso_now(),
                "user": user, "draft_revision": draft_rev, "command": "ship",
                "result": {"accepted": False, "error": "SHIP_BLOCKED"},
            }
        # Check if surviving sentences pass verification.
        surviving = [s for s in sentence_states.values() if s["status"] != "rejected"]
        if snapshot is not None and facts_doc is not None:
            test_draft = {
                "schema": "studio/draft@1",
                "run_id": run_id,
                "agent": "human",
                "created_at": _iso_now(),
                "revision": draft_rev,
                "format": draft.get("format", "howiswitch"),
                "sentences": surviving,
            }
            v = verify_draft(snapshot, facts_doc, test_draft)
            if not v["pass"]:
                return {
                    "schema": "studio/decision@1",
                    "run_id": run_id,
                    "agent": "human",
                    "created_at": _iso_now(),
                    "user": user,
                    "draft_revision": draft_rev,
                    "command": "ship",
                    "result": {"accepted": False, "error": "SHIP_BLOCKED"},
                }

        return {
            "schema": "studio/decision@1",
            "run_id": run_id,
            "agent": "human",
            "created_at": _iso_now(),
            "user": user,
            "draft_revision": draft_rev,
            "command": "ship",
            "result": {"accepted": True, "error": None},
        }

    return {
        "schema": "studio/decision@1",
        "run_id": run_id,
        "agent": "human",
        "created_at": _iso_now(),
        "user": user,
        "draft_revision": draft_rev,
        "command": "approve",
        "result": {"accepted": False, "error": "PARSE_ERROR"},
    }


def replay_decisions(draft: dict, decisions: list[dict]) -> tuple[dict, dict[str, dict]]:
    """Replay a sequence of parsed decision documents on a draft.

    Returns (effective_draft, final_sentence_states).
    """
    sentence_states: dict[str, dict] = {
        s["id"]: {
            "id": s["id"],
            "text": s.get("text", ""),
            "facts": list(s.get("facts", [])),
            "status": "pending",
            "reason": None,
        }
        for s in draft.get("sentences", [])
    }

    for dec in decisions:
        res = dec.get("result", {})
        if not res.get("accepted"):
            continue

        cmd = dec.get("command")
        sid = dec.get("sentence_id")

        if cmd == "approve" and sid in sentence_states:
            sentence_states[sid]["status"] = "approved"
        elif cmd == "approve_all":
            for s in sentence_states.values():
                if s["status"] == "pending":
                    s["status"] = "approved"
        elif cmd == "reject" and sid in sentence_states:
            sentence_states[sid]["status"] = "rejected"
            sentence_states[sid]["reason"] = dec.get("reason")
        elif cmd == "edit" and sid in sentence_states:
            if "text" in dec:
                sentence_states[sid]["text"] = dec["text"]
            if "facts" in dec:
                sentence_states[sid]["facts"] = dec["facts"]
            sentence_states[sid]["status"] = "approved"

    # Effective draft contains non-rejected sentences only
    effective_sentences = [
        {"id": s["id"], "text": s["text"], "facts": s["facts"]}
        for s in sentence_states.values()
        if s["status"] != "rejected"
    ]

    effective_draft = {
        "schema": "studio/draft@1",
        "run_id": draft.get("run_id", "demo-001"),
        "agent": "human",
        "created_at": _iso_now(),
        "revision": draft.get("revision", 0) + 1,
        "format": draft.get("format", "howiswitch"),
        "sentences": effective_sentences,
    }

    return effective_draft, sentence_states


def apply_decisions(draft: dict, decisions: list[dict]) -> dict:
    """Helper returning only the effective draft."""
    effective_draft, _ = replay_decisions(draft, decisions)
    return effective_draft


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay decisions on a draft")
    parser.add_argument("--draft", required=True, help="Path to draft JSON")
    parser.add_argument("--decisions", required=True, help="Path to decisions.jsonl")
    parser.add_argument("--out", required=True, help="Path to output effective draft JSON")
    args = parser.parse_args(argv)

    try:
        draft = load_document(args.draft)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

    decisions = []
    p = Path(args.decisions)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                dec = json.loads(line)
                errs = validate(dec)
                if errs:
                    print(json.dumps({"error": f"invalid decision line: {errs}"}), file=sys.stderr)
                    return 2
                decisions.append(dec)
            except json.JSONDecodeError as exc:
                print(json.dumps({"error": f"corrupt json in decisions: {exc}"}), file=sys.stderr)
                return 2

    effective_draft = apply_decisions(draft, decisions)
    errs = validate(effective_draft)
    if errs:
        print(json.dumps({"error": "invalid effective draft generated", "details": errs}), file=sys.stderr)
        return 2

    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(effective_draft, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
