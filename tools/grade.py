"""Grade plain markdown against a snapshot using CONTRACT.md §9."""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

from schemas.validate import load_document, validate
from tools._common import emit_or_write

HASH_RE = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
NUMBER_RE = re.compile(r"(?<![\w/.\-])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(%?)")
SOURCE_FOOTER_RE = re.compile(r"(?ms)^---\s*\n\*\*Sources\*\*.*\Z")


def _metrics(snapshot: dict[str, Any]) -> set[int]:
    commits = snapshot.get("commits", [])
    values: set[int] = {len(commits)}

    def add_counts(rows: list[dict[str, Any]]) -> None:
        values.add(len({file.get("path") for row in rows for file in row.get("files", []) if file.get("path")}))
        values.add(sum(file.get("added") or 0 for row in rows for file in row.get("files", [])))
        values.add(sum(file.get("deleted") or 0 for row in rows for file in row.get("files", [])))

    add_counts(commits)
    values.add(len({row.get("author_email") for row in commits if row.get("author_email")}))
    for commit in commits:
        files = commit.get("files", [])
        values.add(len({file.get("path") for file in files if file.get("path")}))
        values.add(sum(file.get("added") or 0 for file in files))
        values.add(sum(file.get("deleted") or 0 for file in files))
    authors = {commit.get("author_email") for commit in commits if commit.get("author_email")}
    for author in authors:
        rows = [commit for commit in commits if commit.get("author_email") == author]
        values.add(len(rows))
        values.add(len({file.get("path") for row in rows for file in row.get("files", []) if file.get("path")}))
        values.add(sum(file.get("added") or 0 for row in rows for file in row.get("files", [])))
        values.add(sum(file.get("deleted") or 0 for row in rows for file in row.get("files", [])))
    return values


def _metadata_numbers(snapshot: dict[str, Any]) -> set[int]:
    values: set[int] = set()
    for commit in snapshot.get("commits", []):
        for text in [commit.get("subject", ""), *(file.get("path", "") for file in commit.get("files", []))]:
            values.update(int(number.replace(",", "")) for number in re.findall(r"\d{1,3}(?:,\d{3})+|\d+", text))
    return values


def _date_tokens(snapshot: dict[str, Any]) -> set[str]:
    return {str(commit.get("date", ""))[:10] for commit in snapshot.get("commits", []) if commit.get("date")}


def _prose(text: str) -> str:
    text = SOURCE_FOOTER_RE.sub("", text)
    return re.sub(r"\[F\d+\]", " ", text)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])(?:\s+|$)", text) if part.strip()]


def grade_text(snapshot: dict[str, Any], text: str) -> dict[str, int | float]:
    supported_numbers = _metrics(snapshot) | _metadata_numbers(snapshot)
    commit_hashes = [str(commit.get("hash", "")).lower() for commit in snapshot.get("commits", [])]
    dates = _date_tokens(snapshot)
    sentences_total = sentences_supported = claims_total = claims_supported = 0
    hashes_fabricated = numbers_unsupported = 0
    for sentence in _sentences(_prose(text)):
        sentences_total += 1
        clean = sentence
        for date in dates:
            if date:
                clean = re.sub(rf"\b{re.escape(date)}(?:T\d{{2}}:\d{{2}}(?::\d{{2}})?Z)?\b", " ", clean)
        sentence_claims = 0
        sentence_supported = True
        for match in list(HASH_RE.finditer(clean)):
            token = match.group(0).lower()
            if not re.search(r"[a-f]", token):
                continue
            sentence_claims += 1
            claims_total += 1
            if any(commit_hash.startswith(token) for commit_hash in commit_hashes):
                claims_supported += 1
            else:
                hashes_fabricated += 1
                sentence_supported = False
        clean = re.sub(r"\b(?=[0-9a-fA-F]{7,40}\b)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{7,40}\b", " ", clean)
        for match in NUMBER_RE.finditer(clean):
            raw = match.group(1).replace(",", "")
            decimal = match.group(2)
            is_percent = bool(match.group(3))
            value: int | float = float(f"{raw}.{decimal}") if decimal else int(raw)
            sentence_claims += 1
            claims_total += 1
            if not is_percent and value in supported_numbers:
                claims_supported += 1
            else:
                numbers_unsupported += 1
                sentence_supported = False
        if sentence_claims > 0 and sentence_supported:
            sentences_supported += 1
    return {
        "sentences_total": sentences_total,
        "sentences_supported": sentences_supported,
        "supported_pct": round((sentences_supported / sentences_total * 100) if sentences_total else 0.0, 1),
        "claims_total": claims_total,
        "claims_supported": claims_supported,
        "hashes_fabricated": hashes_fabricated,
        "numbers_unsupported": numbers_unsupported,
    }


def grade_inputs(snapshot: dict[str, Any], snapshot_path: str, inputs: list[tuple[str, str]]) -> dict[str, Any]:
    results = []
    for label, path in inputs:
        data = Path(path).read_text(encoding="utf-8")
        results.append({"label": label, "path": path, **grade_text(snapshot, data)})
    report = {
        "schema": "studio/grade@1",
        "agent": "tool",
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot_path": snapshot_path,
        "results": results,
    }
    errors = validate(report)
    if errors:
        raise ValueError("generated grade failed schema validation: " + "; ".join(errors))
    return report


def _parse_input(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("input must be LABEL=PATH")
    label, path = value.split("=", 1)
    if not re.fullmatch(r"[a-z0-9_-]+", label) or not path:
        raise argparse.ArgumentTypeError("input must use a valid lowercase label and non-empty path")
    return label, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="snapshot JSON path")
    parser.add_argument("--input", action="append", type=_parse_input, required=True, help="LABEL=PATH (repeatable)")
    parser.add_argument("--out", help="write grade JSON here (default: stdout)")
    args = parser.parse_args(argv)
    try:
        snapshot = load_document(args.snapshot)
        if snapshot.get("schema") != "studio/snapshot@1":
            raise ValueError("snapshot input must be studio/snapshot@1")
        report = grade_inputs(snapshot, args.snapshot, args.input)
        emit_or_write(report, args.out)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
