"""Deterministic offline Scout, Writer, and Editor implementations."""

from __future__ import annotations

import datetime
from typing import Any

from sandbox.verifier import verify_draft


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_mock_scout(snapshot: dict[str, Any], run_id: str, snapshot_rel_path: str) -> dict[str, Any]:
    """Produce stable, schema-valid facts from a saved snapshot."""
    commits = snapshot["commits"]
    head = snapshot["head"]
    if not commits:
        raise ValueError("snapshot must contain at least one commit")
    first_commit = commits[0]
    first_file = first_commit.get("files", [None])[0]
    if first_file is None:
        raise ValueError("newest commit must contain at least one changed file")
    lines_added = sum(file.get("added") or 0 for file in first_commit["files"])
    contributors = len({commit["author_email"] for commit in commits})
    facts = [
        {"id": "F1", "kind": "commit", "commit": first_commit["hash"], "subject": first_commit["subject"], "author_name": first_commit["author_name"], "date": first_commit["date"], "statement": f"{first_commit['author_name']} committed '{first_commit['subject']}'."},
        {"id": "F2", "kind": "file", "commit": first_commit["hash"], "path": first_file["path"], "status": first_file["status"], "statement": f"{first_file['path']} changed in {first_commit['hash'][:7]}."},
        {"id": "F3", "kind": "count", "commit": first_commit["hash"], "metric": "lines_added", "scope": "commit", "value": lines_added, "statement": f"Commit {first_commit['hash'][:7]} added {lines_added} lines."},
        {"id": "F4", "kind": "count", "commit": head, "metric": "commits", "scope": "run", "value": len(commits), "statement": f"There are {len(commits)} commits in {snapshot['range']}."},
        {"id": "F5", "kind": "count", "commit": head, "metric": "contributors", "scope": "run", "value": contributors, "statement": f"{contributors} people contributed in {snapshot['range']}."},
    ]
    return {"schema": "studio/facts@1", "run_id": run_id, "agent": "scout", "created_at": _iso_now(), "snapshot": {"path": snapshot_rel_path, "range": snapshot["range"], "head": head}, "facts": facts}


def run_mock_writer(
    facts_doc: dict[str, Any], run_id: str, fmt: str = "howiswitch", revision: int = 0,
    previous_draft: dict[str, Any] | None = None, previous_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce drafts; revisions retain every sentence identity.

    Revision zero intentionally includes one unsupported sentence to exercise the
    Editor-to-Writer handoff. Later revisions change only rejected sentences.
    """
    facts = {fact["id"]: fact for fact in facts_doc["facts"]}
    if revision == 0:
        sentences = [
            {"id": "S1", "text": f"{facts['F1']['author_name']} landed changes in {facts['F2']['path']}.", "facts": ["F1", "F2"]},
            {"id": "S2", "text": f"That commit added {facts['F3']['value']} lines.", "facts": ["F3"]},
            {"id": "S3", "text": f"{facts['F5']['value']} contributors landed {facts['F4']['value']} commits across the range.", "facts": ["F4", "F5"]},
            {"id": "S4", "text": "The entire system runs in 10 milliseconds.", "facts": []},
        ]
    else:
        if previous_draft is None or previous_review is None:
            raise ValueError("writer revisions require the previous draft and review")
        rejected = {sentence["id"] for sentence in previous_review["sentences"] if sentence["status"] == "rejected"}
        sentences = []
        for sentence in previous_draft["sentences"]:
            if sentence["id"] == "S4" and "S4" in rejected:
                sentences.append({"id": "S4", "text": f"{facts['F5']['value']} contributors contributed across the range.", "facts": ["F5"]})
            else:
                sentences.append({"id": sentence["id"], "text": sentence["text"], "facts": list(sentence["facts"])})
    return {"schema": "studio/draft@1", "run_id": run_id, "agent": "writer", "created_at": _iso_now(), "revision": revision, "format": fmt, "sentences": sentences}


def run_mock_editor(snapshot: dict[str, Any], facts_doc: dict[str, Any], draft_doc: dict[str, Any], loop: int, verdict_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run deterministic verification and translate it into an Editor review."""
    verdict = verify_draft(snapshot, facts_doc, draft_doc)
    review_sentences = []
    has_rejected = False
    for result in verdict["sentences"]:
        if result["errors"]:
            has_rejected = True
            review_sentences.append({"id": result["id"], "status": "rejected", "errors": result["errors"], "note": f"Sentence {result['id']} failed verification: {', '.join(result['errors'])}."})
        else:
            review_sentences.append({"id": result["id"], "status": "ok", "errors": []})
    return verdict, {"schema": "studio/review@1", "run_id": draft_doc["run_id"], "agent": "editor", "created_at": _iso_now(), "draft_revision": draft_doc["revision"], "loop": loop, "verdict_path": verdict_path, "sentences": review_sentences, "handoff": "writer" if has_rejected and loop < 2 else "humans"}
