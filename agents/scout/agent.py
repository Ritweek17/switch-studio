"""Scout agent (`agents.scout`): snapshot@1 -> facts@1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agents._common import (
    DEFAULT_OVERFLOW_BYTES,
    AgentExecutionError,
    call_llm_json,
    ensure_valid,
    format_room_message,
    load_document,
    load_prompt,
    utc_now,
    write_document,
)
from schemas.validate import DocumentError

AGENT_DIR = Path(__file__).resolve().parent

ALLOWED_METRICS = {
    "commits",
    "files_changed",
    "lines_added",
    "lines_deleted",
    "contributors",
}

_STATUS_VERBS = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
}


def compute_count_metric(
    snapshot: dict[str, Any],
    metric: str,
    scope: str,
    *,
    commit_hash: str | None = None,
    author_email: str | None = None,
) -> int:
    """Deterministically compute any closed-list count metric over `snapshot`."""
    if metric not in ALLOWED_METRICS:
        raise ValueError(f"unsupported metric {metric!r}")
    if scope not in {"commit", "run"}:
        raise ValueError(f"unsupported scope {scope!r}")

    commits = snapshot["commits"]
    if scope == "commit":
        if metric in {"commits", "contributors"}:
            raise ValueError(f"metric {metric!r} only supports scope 'run'")
        if author_email is not None:
            raise ValueError("author_email is only allowed when scope == 'run'")
        matched = [c for c in commits if c["hash"] == commit_hash]
        if not matched:
            raise ValueError(f"commit {commit_hash!r} not in snapshot")
        target_commits = matched
    else:
        if metric == "contributors" and author_email is not None:
            raise ValueError("author_email is never allowed for metric 'contributors'")
        target_commits = (
            [c for c in commits if c["author_email"] == author_email]
            if author_email is not None
            else list(commits)
        )

    if metric == "commits":
        return len(target_commits)
    if metric == "contributors":
        return len({c["author_email"] for c in target_commits})
    if metric == "files_changed":
        return len({f["path"] for c in target_commits for f in c.get("files", [])})
    if metric == "lines_added":
        return sum((f.get("added") or 0) for c in target_commits for f in c.get("files", []))
    if metric == "lines_deleted":
        return sum((f.get("deleted") or 0) for c in target_commits for f in c.get("files", []))
    raise ValueError(f"unhandled metric {metric!r}")


def verify_fact_against_snapshot(fact: dict[str, Any], snapshot: dict[str, Any]) -> str | None:
    """Return None if `fact` is 100% accurate against `snapshot`, else an error reason."""
    commits_by_hash = {c["hash"]: c for c in snapshot.get("commits", [])}
    kind = fact.get("kind")
    commit_hash = fact.get("commit")

    if kind == "commit":
        commit = commits_by_hash.get(commit_hash)
        if not commit:
            return f"commit {commit_hash!r} not found in snapshot"
        for field in ("subject", "author_name", "date"):
            if fact.get(field) != commit.get(field):
                return f"commit.{field} mismatch: {fact.get(field)!r} != {commit.get(field)!r}"
        return None

    if kind == "file":
        commit = commits_by_hash.get(commit_hash)
        if not commit:
            return f"commit {commit_hash!r} not found in snapshot"
        for f in commit.get("files", []):
            if f["path"] == fact.get("path") and f["status"] == fact.get("status"):
                return None
        return f"file {fact.get('path')!r} ({fact.get('status')!r}) not in commit {commit_hash[:7]}"

    if kind == "count":
        scope = fact.get("scope")
        metric = fact.get("metric")
        author_email = fact.get("author_email")
        if scope == "run" and commit_hash != snapshot.get("head"):
            return f"run-scoped count fact must use snapshot.head ({snapshot.get('head')})"
        if scope == "commit" and commit_hash not in commits_by_hash:
            return f"commit {commit_hash!r} not found in snapshot"
        try:
            expected = compute_count_metric(
                snapshot,
                metric,
                scope,
                commit_hash=commit_hash,
                author_email=author_email,
            )
        except ValueError as exc:
            return str(exc)
        if fact.get("value") != expected:
            return f"count mismatch for {metric} ({scope}): got {fact.get('value')}, expected {expected}"
        return None

    return f"unknown fact kind {kind!r}"


def _extract_deterministic_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a comprehensive, ordered list of verified facts from `snapshot`."""
    facts: list[dict[str, Any]] = []
    commits = snapshot.get("commits", [])
    head = snapshot["head"]
    rev_range = snapshot["range"]

    def next_id() -> str:
        return f"F{len(facts) + 1}"

    # Per-commit facts (cap to top 10 commits if snapshot is very large, keeping all key facts)
    for commit in commits[:10]:
        chash = commit["hash"]
        short = chash[:7]
        facts.append(
            {
                "id": next_id(),
                "kind": "commit",
                "commit": chash,
                "subject": commit["subject"],
                "author_name": commit["author_name"],
                "date": commit["date"],
                "statement": f"{commit['author_name']} committed '{commit['subject']}'.",
            }
        )

        files = commit.get("files", [])
        if files:
            primary = files[0]
            verb = _STATUS_VERBS.get(primary["status"], "touched")
            facts.append(
                {
                    "id": next_id(),
                    "kind": "file",
                    "commit": chash,
                    "path": primary["path"],
                    "status": primary["status"],
                    "statement": f"{primary['path']} was {verb} in {short}.",
                }
            )

        added = compute_count_metric(snapshot, "lines_added", "commit", commit_hash=chash)
        if added > 0:
            facts.append(
                {
                    "id": next_id(),
                    "kind": "count",
                    "commit": chash,
                    "metric": "lines_added",
                    "scope": "commit",
                    "value": added,
                    "statement": f"Commit {short} added {added} lines.",
                }
            )

        deleted = compute_count_metric(snapshot, "lines_deleted", "commit", commit_hash=chash)
        if deleted > 0:
            facts.append(
                {
                    "id": next_id(),
                    "kind": "count",
                    "commit": chash,
                    "metric": "lines_deleted",
                    "scope": "commit",
                    "value": deleted,
                    "statement": f"Commit {short} deleted {deleted} lines.",
                }
            )

    # Run-level summary facts tied to snapshot.head
    total_commits = compute_count_metric(snapshot, "commits", "run")
    facts.append(
        {
            "id": next_id(),
            "kind": "count",
            "commit": head,
            "metric": "commits",
            "scope": "run",
            "value": total_commits,
            "statement": f"There are {total_commits} commits in {rev_range}.",
        }
    )

    total_contributors = compute_count_metric(snapshot, "contributors", "run")
    facts.append(
        {
            "id": next_id(),
            "kind": "count",
            "commit": head,
            "metric": "contributors",
            "scope": "run",
            "value": total_contributors,
            "statement": f"{total_contributors} contributors committed in {rev_range}.",
        }
    )

    total_files = compute_count_metric(snapshot, "files_changed", "run")
    facts.append(
        {
            "id": next_id(),
            "kind": "count",
            "commit": head,
            "metric": "files_changed",
            "scope": "run",
            "value": total_files,
            "statement": f"{total_files} distinct files changed across {rev_range}.",
        }
    )

    total_added = compute_count_metric(snapshot, "lines_added", "run")
    facts.append(
        {
            "id": next_id(),
            "kind": "count",
            "commit": head,
            "metric": "lines_added",
            "scope": "run",
            "value": total_added,
            "statement": f"Commits in {rev_range} added {total_added} lines in total.",
        }
    )

    return facts


def run_scout(
    snapshot: dict[str, Any],
    *,
    run_id: str,
    snapshot_path: str,
    created_at: str | None = None,
    use_llm: bool = False,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Generate a validated `studio/facts@1` document from `snapshot`.

    If `use_llm=True` and an API key is configured, attempts LLM extraction first
    and filters/validates every returned fact against `snapshot`, falling back to
    deterministic extraction if any contract or verification check fails.
    """
    errors = ensure_valid(snapshot)
    if snapshot.get("schema") != "studio/snapshot@1":
        raise DocumentError(["input must be a studio/snapshot@1 document"])
    del errors

    timestamp = created_at or utc_now()
    pointer = {
        "path": str(snapshot_path),
        "range": snapshot["range"],
        "head": snapshot["head"],
    }

    if use_llm:
        try:
            prompt = load_prompt(AGENT_DIR)
            llm_doc = call_llm_json(
                prompt,
                {
                    "run_id": run_id,
                    "created_at": timestamp,
                    "snapshot_path": str(snapshot_path),
                    "snapshot": snapshot,
                },
                api_key=api_key,
            )
            llm_doc["schema"] = "studio/facts@1"
            llm_doc["run_id"] = run_id
            llm_doc["agent"] = "scout"
            llm_doc["created_at"] = timestamp
            llm_doc["snapshot"] = pointer
            # Filter only facts that pass deterministic verification and re-number F1..Fn
            valid_facts: list[dict[str, Any]] = []
            for raw_fact in llm_doc.get("facts", []):
                if isinstance(raw_fact, dict) and verify_fact_against_snapshot(raw_fact, snapshot) is None:
                    fact_copy = dict(raw_fact)
                    fact_copy["id"] = f"F{len(valid_facts) + 1}"
                    valid_facts.append(fact_copy)
            if valid_facts:
                llm_doc["facts"] = valid_facts
                return ensure_valid(llm_doc)
        except (AgentExecutionError, DocumentError):
            pass

    doc: dict[str, Any] = {
        "schema": "studio/facts@1",
        "run_id": run_id,
        "agent": "scout",
        "created_at": timestamp,
        "snapshot": pointer,
        "facts": _extract_deterministic_facts(snapshot),
    }
    return ensure_valid(doc)


def summarize_facts(facts_doc: dict[str, Any]) -> str:
    """Return a concise 1-line human summary for `#studio`."""
    total = len(facts_doc.get("facts", []))
    rev_range = facts_doc["snapshot"]["range"]
    head_short = facts_doc["snapshot"]["head"][:7]
    return f"Scout extracted {total} verified facts from `{rev_range}` (head `{head_short}`)."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents.scout",
        description="Run Scout agent to extract studio/facts@1 from a studio/snapshot@1 file.",
    )
    parser.add_argument("--snapshot", required=True, help="Path to studio/snapshot@1 JSON")
    parser.add_argument("--run-id", required=True, help="Run identifier (e.g. demo-001)")
    parser.add_argument("--out", help="Optional path to write studio/facts@1 JSON")
    parser.add_argument(
        "--room-message",
        action="store_true",
        help="Emit a formatted #studio room message instead of raw JSON to stdout",
    )
    parser.add_argument(
        "--max-inline-bytes",
        type=int,
        default=DEFAULT_OVERFLOW_BYTES,
        help="Max inline bytes before emitting a payload_ref overflow envelope",
    )
    parser.add_argument("--llm", action="store_true", help="Attempt LLM extraction via OPENAI_API_KEY")
    args = parser.parse_args(argv)

    try:
        snapshot = load_document(args.snapshot)
        facts_doc = run_scout(
            snapshot,
            run_id=args.run_id,
            snapshot_path=args.snapshot,
            use_llm=args.llm,
        )
        if args.out:
            write_document(facts_doc, args.out)
        if args.room_message:
            overflow_target = args.out or f"runs/{args.run_id}/facts.json"
            msg = format_room_message(
                summarize_facts(facts_doc),
                facts_doc,
                overflow_path=overflow_target,
                max_inline_bytes=args.max_inline_bytes,
            )
            sys.stdout.write(msg)
        elif not args.out:
            sys.stdout.write(json.dumps(facts_doc, indent=2) + "\n")
        return 0
    except (DocumentError, AgentExecutionError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "path": args.snapshot}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
