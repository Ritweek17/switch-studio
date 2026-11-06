"""Create a contract-valid snapshot from a local Git repository."""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from schemas.validate import validate
from tools._common import emit_or_write


class SnapshotError(ValueError):
    """A repository could not be represented as a Switch Studio snapshot."""


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise SnapshotError(detail or f"git {' '.join(args)} failed")
    return result.stdout


def _utc_iso(value: str) -> str:
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_numstat(data: bytes) -> dict[str, tuple[int | None, int | None]]:
    """Parse `git diff-tree --numstat -z`, including rename records."""
    fields = data.split(b"\0")
    result: dict[str, tuple[int | None, int | None]] = {}
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        parts = record.split(b"\t", 2)
        if len(parts) != 3:
            continue
        added_raw, deleted_raw, path_raw = parts
        added = None if added_raw == b"-" else int(added_raw)
        deleted = None if deleted_raw == b"-" else int(deleted_raw)
        path = path_raw.decode("utf-8", "replace")
        if not path:
            # In -z mode a rename has an empty pathname field followed by
            # old and new paths. Keep the destination, as CONTRACT.md §3 says.
            if index + 1 >= len(fields):
                raise SnapshotError("malformed rename record from git numstat")
            index += 1  # discard old path
            path = fields[index].decode("utf-8", "replace")
            index += 1
        result[path] = (added, deleted)
    return result


def _commit_files(repo: Path, commit: str) -> list[dict[str, Any]]:
    names = _git(repo, "diff-tree", "--root", "--no-commit-id", "--name-status", "-r", "-M", "-z", commit)
    stats = _parse_numstat(_git(repo, "diff-tree", "--root", "--no-commit-id", "--numstat", "-r", "-M", "-z", commit))
    tokens = names.split(b"\0")
    files: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        status_token = tokens[index]
        index += 1
        if not status_token:
            continue
        status_text = status_token.decode("ascii", "replace")
        status = status_text[0]
        if status not in {"A", "M", "D", "R"}:
            # Contract v1 intentionally has a closed status list.
            continue
        if index >= len(tokens):
            raise SnapshotError(f"missing path for {status_text} in commit {commit}")
        first_path = tokens[index].decode("utf-8", "replace")
        index += 1
        if status == "R":
            if index >= len(tokens):
                raise SnapshotError(f"missing destination path for rename in commit {commit}")
            path = tokens[index].decode("utf-8", "replace")
            index += 1
        else:
            path = first_path
        added, deleted = stats.get(path, (None, None))
        files.append({"path": path, "status": status, "added": added, "deleted": deleted})
    return files


def create_snapshot(repo_path: str | Path, revision_range: str | None = None) -> dict[str, Any]:
    """Read commits and diff stats from a local clone without changing it."""
    repo = Path(repo_path).expanduser().resolve()
    if not repo.is_dir():
        raise SnapshotError(f"repository directory does not exist: {repo}")
    _git(repo, "rev-parse", "--show-toplevel")
    actual_range = revision_range or "HEAD~50..HEAD"
    log_args = ["log", "--no-merges", "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1e"]
    if revision_range:
        log_args.append(revision_range)
    else:
        log_args.extend(["-n", "50", "HEAD"])
    records = _git(repo, *log_args).decode("utf-8", "replace").split("\x1e")
    commits: list[dict[str, Any]] = []
    for record in records:
        record = record.strip("\r\n")
        if not record:
            continue
        fields = record.split("\x1f", 4)
        if len(fields) != 5:
            raise SnapshotError("git returned an unparseable commit record")
        commit_hash, author_name, author_email, author_date, subject = fields
        commits.append({
            "hash": commit_hash,
            "author_name": author_name,
            "author_email": author_email,
            "date": _utc_iso(author_date),
            "subject": subject,
            "files": _commit_files(repo, commit_hash),
        })
    head_ref = revision_range.rsplit("..", 1)[-1] if revision_range and ".." in revision_range else (revision_range or "HEAD")
    head = _git(repo, "rev-parse", head_ref).decode("ascii").strip()
    top = _git(repo, "rev-parse", "--show-toplevel").decode("utf-8", "replace").strip()
    document = {
        "schema": "studio/snapshot@1",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": {"name": Path(top).name},
        "range": actual_range,
        "head": head,
        "commits": commits,
    }
    errors = validate(document)
    if errors:
        raise SnapshotError("generated snapshot failed schema validation: " + "; ".join(errors))
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="path to a local Git clone")
    parser.add_argument("--range", dest="revision_range", help="Git revision range; defaults to the latest 50 non-merge commits")
    parser.add_argument("--out", help="write snapshot JSON to this path (default: stdout)")
    args = parser.parse_args(argv)
    try:
        document = create_snapshot(args.repo, args.revision_range)
        emit_or_write(document, args.out)
        return 0
    except (OSError, SnapshotError, subprocess.SubprocessError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
