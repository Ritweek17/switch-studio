"""Editor agent (`agents.editor`): runs tools.verify + OVERCLAIM check -> review@1."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
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
from tools.verify import verify_draft

AGENT_DIR = Path(__file__).resolve().parent

_NUMBER_WORD_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen)\b",
    re.IGNORECASE,
)

_OVERCLAIM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\btests?\s+included\b", re.IGNORECASE),
        "'tests included' is not backed by a cited fact; cite a file fact for a test file or drop the phrase.",
    ),
    (
        re.compile(
            r"\b(?:zero\s+bugs|bug-free|100%\s+reliable|production-ready|blazing\s+fast|instant(?:ly)?|seamlessly)\b",
            re.IGNORECASE,
        ),
        "Unverified quality or performance superlative is not supported by cited repository facts.",
    ),
    (
        re.compile(r"\b(?:faster|slower|speedup|throughput|latency)\b", re.IGNORECASE),
        "Performance claim is not supported by cited repository facts.",
    ),
    (
        re.compile(r"\b(?:twice|double|triple|half)\s+as\b", re.IGNORECASE),
        "Derived ratio or comparison is not supported by cited repository facts; cite raw counts as digits.",
    ),
]


def run_verifier(
    snapshot: dict[str, Any],
    facts_doc: dict[str, Any],
    draft_doc: dict[str, Any],
    *,
    snapshot_path: str | Path | None = None,
    facts_path: str | Path | None = None,
    draft_path: str | Path | None = None,
    verdict_out_path: str | Path | None = None,
) -> dict[str, Any]:
    """Execute `python -m tools.verify` if available and paths are on disk, else `verify_draft`."""
    if (
        snapshot_path is not None
        and facts_path is not None
        and draft_path is not None
        and Path(snapshot_path).is_file()
        and Path(facts_path).is_file()
        and Path(draft_path).is_file()
    ):
        cmd = [
            sys.executable,
            "-m",
            "tools.verify",
            "--snapshot",
            str(snapshot_path),
            "--facts",
            str(facts_path),
            "--draft",
            str(draft_path),
        ]
        if verdict_out_path is not None:
            Path(verdict_out_path).parent.mkdir(parents=True, exist_ok=True)
            cmd.extend(["--out", str(verdict_out_path)])
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode in (0, 1):
            try:
                if verdict_out_path is not None and Path(verdict_out_path).is_file():
                    return load_document(verdict_out_path)
                verdict = json.loads(proc.stdout)
                return ensure_valid(verdict)
            except (DocumentError, json.JSONDecodeError):
                pass

    verdict = verify_draft(snapshot, facts_doc, draft_doc)
    ensure_valid(verdict)
    if verdict_out_path is not None:
        write_document(verdict, verdict_out_path)
    return verdict


def detect_overclaim(
    sentence: dict[str, Any],
    facts_by_id: dict[str, dict[str, Any]],
) -> str | None:
    """Return an explanatory note if `sentence` overclaims beyond its cited facts, else None."""
    text = sentence.get("text", "")
    cited_facts = [facts_by_id[fid] for fid in sentence.get("facts", []) if fid in facts_by_id]
    if not cited_facts:
        return None

    cited_subjects_and_paths = " ".join(
        f"{f.get('subject', '')} {f.get('path', '')}" for f in cited_facts
    ).lower()
    cited_file_paths = [
        f.get("path", "").lower() for f in cited_facts if f.get("kind") == "file"
    ]

    for pattern, note in _OVERCLAIM_PATTERNS:
        match = pattern.search(text)
        if match:
            matched_phrase = match.group(0).lower()
            # If the phrase is 'tests included', check whether a test file is actually cited
            if "test" in matched_phrase:
                if any("test" in p for p in cited_file_paths) or matched_phrase in cited_subjects_and_paths:
                    continue
                return note
            if matched_phrase not in cited_subjects_and_paths:
                return note

    # Check if prose mentions a specific repo file path (e.g. `foo/bar.py`) not in cited facts
    for path_match in re.finditer(r"\b[a-zA-Z0-9_.\-]+/[a-zA-Z0-9_./\-]+\.[a-zA-Z0-9]+\b", text):
        mentioned_path = path_match.group(0).lower()
        if mentioned_path not in cited_subjects_and_paths:
            return (
                f"Path '{path_match.group(0)}' is not backed by a cited fact; "
                f"cite a file fact for '{path_match.group(0)}' or remove the path."
            )

    # Check if a spelled-out number word is used without ANY cited count fact or subject word
    has_count_fact = any(f.get("kind") == "count" for f in cited_facts)
    num_word_match = _NUMBER_WORD_RE.search(text)
    if num_word_match and not has_count_fact:
        word = num_word_match.group(0)
        if word.lower() not in cited_subjects_and_paths:
            return (
                f"Spelled-out number word '{word}' is not backed by a cited count fact; "
                "cite a count fact and write raw digits."
            )

    return None


def run_editor(
    snapshot: dict[str, Any],
    facts_doc: dict[str, Any],
    draft_doc: dict[str, Any],
    *,
    loop: int = 1,
    verdict_doc: dict[str, Any] | None = None,
    verdict_path: str | Path | None = None,
    snapshot_path: str | Path | None = None,
    facts_path: str | Path | None = None,
    draft_path: str | Path | None = None,
    created_at: str | None = None,
    use_llm: bool = False,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Run Editor review (`studio/review@1`) on `draft_doc`.

    1. Runs `tools.verify` first (or uses `verdict_doc` if already provided) and
       copies all deterministic errors per sentence.
    2. Adds `OVERCLAIM` where prose asserts more than its cited facts.
    3. Enforces the 2-loop cap: `handoff = "writer"` if any sentence is rejected
       and `loop < 2`; otherwise `handoff = "humans"`.
    4. Never deletes sentences.
    """
    ensure_valid(snapshot)
    ensure_valid(facts_doc)
    ensure_valid(draft_doc)
    if loop not in (1, 2):
        raise ValueError(f"loop must be 1 or 2, got {loop!r}")

    run_id = draft_doc["run_id"]
    rev = draft_doc["revision"]
    resolved_verdict_path = (
        str(verdict_path) if verdict_path is not None else f"runs/{run_id}/verdict.r{rev}.json"
    )

    if verdict_doc is None:
        verdict_doc = run_verifier(
            snapshot,
            facts_doc,
            draft_doc,
            snapshot_path=snapshot_path,
            facts_path=facts_path,
            draft_path=draft_path,
            verdict_out_path=verdict_path,
        )
    else:
        ensure_valid(verdict_doc)

    facts_by_id = {f["id"]: f for f in facts_doc.get("facts", [])}
    verdict_by_sid = {s["id"]: s for s in verdict_doc.get("sentences", [])}

    # Optional LLM OVERCLAIM judgments
    llm_overclaims: dict[str, str] = {}
    if use_llm:
        try:
            prompt = load_prompt(AGENT_DIR)
            llm_resp = call_llm_json(
                prompt,
                {
                    "run_id": run_id,
                    "loop": loop,
                    "verdict_path": resolved_verdict_path,
                    "facts": facts_doc,
                    "draft": draft_doc,
                    "verdict": verdict_doc,
                },
                api_key=api_key,
            )
            for item in llm_resp.get("sentences", []):
                if isinstance(item, dict) and "OVERCLAIM" in item.get("errors", []):
                    llm_overclaims[item.get("id", "")] = str(
                        item.get("note") or "Sentence overclaims beyond cited facts."
                    )
        except AgentExecutionError:
            pass

    review_sentences: list[dict[str, Any]] = []
    any_rejected = False

    for sent in draft_doc.get("sentences", []):
        sid = sent["id"]
        v_sent = verdict_by_sid.get(sid, {"ok": True, "errors": []})
        errors: list[str] = list(v_sent.get("errors", []))
        note: str | None = None

        overclaim_note = detect_overclaim(sent, facts_by_id) or llm_overclaims.get(sid)
        if overclaim_note is not None:
            if "OVERCLAIM" not in errors:
                errors.append("OVERCLAIM")
            note = overclaim_note

        status = "rejected" if errors else "ok"
        if status == "rejected":
            any_rejected = True

        entry: dict[str, Any] = {
            "id": sid,
            "status": status,
            "errors": errors,
        }
        if note:
            entry["note"] = note
        review_sentences.append(entry)

    handoff = "writer" if (any_rejected and loop < 2) else "humans"

    review_doc: dict[str, Any] = {
        "schema": "studio/review@1",
        "run_id": run_id,
        "agent": "editor",
        "created_at": created_at or utc_now(),
        "draft_revision": rev,
        "loop": loop,
        "verdict_path": resolved_verdict_path,
        "sentences": review_sentences,
        "handoff": handoff,
    }
    return ensure_valid(review_doc)


def summarize_review(review_doc: dict[str, Any]) -> str:
    """Return a concise 1-line human summary for `#studio`."""
    sentences = review_doc.get("sentences", [])
    total = len(sentences)
    ok_count = sum(1 for s in sentences if s["status"] == "ok")
    rejected_ids = [s["id"] for s in sentences if s["status"] == "rejected"]
    loop = review_doc["loop"]
    rev = review_doc["draft_revision"]
    handoff = review_doc["handoff"]

    if not rejected_ids:
        return (
            f"Editor approved all {total} sentences in `r{rev}` (loop {loop}); "
            f"handoff to `{handoff}`."
        )
    joined = ", ".join(rejected_ids)
    return (
        f"Editor reviewed `r{rev}` (loop {loop}): {ok_count}/{total} ok, "
        f"rejected {joined}; handoff to `{handoff}`."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents.editor",
        description="Run Editor agent: execute tools.verify + OVERCLAIM check -> studio/review@1.",
    )
    parser.add_argument("--snapshot", required=True, help="Path to studio/snapshot@1 JSON")
    parser.add_argument("--facts", required=True, help="Path to studio/facts@1 JSON")
    parser.add_argument("--draft", required=True, help="Path to studio/draft@1 JSON")
    parser.add_argument(
        "--loop",
        type=int,
        choices=[1, 2],
        default=1,
        help="Review loop number (1 or 2; default: 1)",
    )
    parser.add_argument("--verdict", help="Optional path to existing or output studio/verdict@1 JSON")
    parser.add_argument("--out", help="Optional path to write studio/review@1 JSON")
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
    parser.add_argument("--llm", action="store_true", help="Attempt LLM OVERCLAIM judgment via OPENAI_API_KEY")
    args = parser.parse_args(argv)

    try:
        snapshot = load_document(args.snapshot)
        facts_doc = load_document(args.facts)
        draft_doc = load_document(args.draft)

        existing_verdict = None
        if args.verdict and Path(args.verdict).is_file():
            existing_verdict = load_document(args.verdict)

        review_doc = run_editor(
            snapshot,
            facts_doc,
            draft_doc,
            loop=args.loop,
            verdict_doc=existing_verdict,
            verdict_path=args.verdict,
            snapshot_path=args.snapshot,
            facts_path=args.facts,
            draft_path=args.draft,
            use_llm=args.llm,
        )
        if args.out:
            write_document(review_doc, args.out)
        if args.room_message:
            rev = draft_doc["revision"]
            overflow_target = args.out or f"runs/{draft_doc['run_id']}/review.r{rev}.json"
            msg = format_room_message(
                summarize_review(review_doc),
                review_doc,
                overflow_path=overflow_target,
                max_inline_bytes=args.max_inline_bytes,
            )
            sys.stdout.write(msg)
        elif not args.out:
            sys.stdout.write(json.dumps(review_doc, indent=2) + "\n")
        return 0
    except (DocumentError, AgentExecutionError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "path": args.draft}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
