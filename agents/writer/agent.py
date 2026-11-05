"""Writer agent (`agents.writer`): facts@1 (+ optional draft@1 & review@1) -> draft@1."""

from __future__ import annotations

import argparse
import json
import re
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

_INLINE_TAG_RE = re.compile(r"\s*\[F\d+\]")


def strip_inline_tags(text: str) -> str:
    """Remove any accidental `[F...]` citation tags from sentence text."""
    cleaned = _INLINE_TAG_RE.sub("", text).strip()
    cleaned = re.sub(r"\s+([.,;!?])", r"\1", cleaned)
    return cleaned


def _compose_sentence_for_commit_group(
    commit_hash: str,
    group: list[dict[str, Any]],
    *,
    fmt: str,
) -> tuple[str, list[str]]:
    """Build a deterministically verifiable sentence and its cited fact IDs for a commit."""
    commit_fact = next((f for f in group if f["kind"] == "commit"), None)
    file_facts = [f for f in group if f["kind"] == "file"]
    added_fact = next(
        (
            f
            for f in group
            if f["kind"] == "count" and f.get("scope") == "commit" and f.get("metric") == "lines_added"
        ),
        None,
    )
    deleted_fact = next(
        (
            f
            for f in group
            if f["kind"] == "count" and f.get("scope") == "commit" and f.get("metric") == "lines_deleted"
        ),
        None,
    )

    cited: list[str] = []
    short_hash = commit_hash[:7]

    if commit_fact and file_facts:
        primary_file = file_facts[0]
        cited.extend([commit_fact["id"], primary_file["id"]])
        author = commit_fact["author_name"]
        subject = commit_fact["subject"]
        path = primary_file["path"]
        if fmt == "blog_section":
            text = f"In commit {short_hash}, {author} landed '{subject}' touching {path}."
        else:
            text = f"{author} landed '{subject}' in {path} ({short_hash})."
        return text, cited

    if commit_fact and added_fact:
        cited.extend([commit_fact["id"], added_fact["id"]])
        author = commit_fact["author_name"]
        subject = commit_fact["subject"]
        val = added_fact["value"]
        return f"{author} committed '{subject}' ({short_hash}), adding {val} lines.", cited

    if commit_fact:
        cited.append(commit_fact["id"])
        return (
            f"{commit_fact['author_name']} committed '{commit_fact['subject']}' ({short_hash}).",
            cited,
        )

    if file_facts:
        pf = file_facts[0]
        cited.append(pf["id"])
        return f"Commit {short_hash} updated {pf['path']}.", cited

    if added_fact:
        cited.append(added_fact["id"])
        return f"Commit {short_hash} added {added_fact['value']} lines.", cited

    if deleted_fact:
        cited.append(deleted_fact["id"])
        return f"Commit {short_hash} deleted {deleted_fact['value']} lines.", cited

    first = group[0]
    return strip_inline_tags(first["statement"]), [first["id"]]


def _compose_sentence_for_single_fact(fact: dict[str, Any]) -> tuple[str, list[str]]:
    """Build a strictly verified sentence backed by a single fact."""
    fid = fact["id"]
    kind = fact["kind"]
    short = fact["commit"][:7]

    if kind == "commit":
        return (
            f"{fact['author_name']} committed '{fact['subject']}' ({short}).",
            [fid],
        )
    if kind == "file":
        status_word = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}.get(
            fact["status"], "updated"
        )
        return (f"Commit {short} {status_word} {fact['path']}.", [fid])
    if kind == "count":
        metric = fact["metric"]
        scope = fact["scope"]
        val = fact["value"]
        if scope == "commit":
            if metric == "lines_added":
                return (f"Commit {short} added {val} lines.", [fid])
            if metric == "lines_deleted":
                return (f"Commit {short} deleted {val} lines.", [fid])
            if metric == "files_changed":
                return (f"Commit {short} changed {val} files.", [fid])
        else:
            if metric == "commits":
                return (f"The run covers {val} commits at head {short}.", [fid])
            if metric == "contributors":
                return (f"{val} contributors authored commits in this range.", [fid])
            if metric == "files_changed":
                return (f"{val} files changed across the run.", [fid])
            if metric == "lines_added":
                return (f"The run added {val} lines in total.", [fid])
            if metric == "lines_deleted":
                return (f"The run deleted {val} lines in total.", [fid])
    return (strip_inline_tags(fact["statement"]), [fid])


def _build_initial_sentences(facts_doc: dict[str, Any], fmt: str) -> list[dict[str, Any]]:
    """Deterministically build initial draft sentences (`revision: 0`) from `facts_doc`."""
    facts = facts_doc.get("facts", [])
    sentences: list[dict[str, Any]] = []

    def add_sentence(text: str, cited_ids: list[str]) -> None:
        dedup_ids = list(dict.fromkeys(cited_ids))
        sentences.append(
            {
                "id": f"S{len(sentences) + 1}",
                "text": strip_inline_tags(text),
                "facts": dedup_ids,
            }
        )

    # Group commit-level facts by commit hash in order of appearance
    commit_order: list[str] = []
    by_commit: dict[str, list[dict[str, Any]]] = {}
    run_counts: dict[str, dict[str, Any]] = {}

    for fact in facts:
        if fact["kind"] == "count" and fact.get("scope") == "run" and "author_email" not in fact:
            run_counts.setdefault(fact["metric"], fact)
        else:
            chash = fact["commit"]
            if chash not in by_commit:
                commit_order.append(chash)
                by_commit[chash] = []
            by_commit[chash].append(fact)

    for chash in commit_order:
        group = by_commit[chash]
        text, cited = _compose_sentence_for_commit_group(chash, group, fmt=fmt)
        add_sentence(text, cited)

        # If this commit has both a commit+file sentence AND a lines_added count fact,
        # emit a dedicated sentence for the line count if we only have 1 commit or for the lead commit.
        commit_fact = next((f for f in group if f["kind"] == "commit"), None)
        file_facts = [f for f in group if f["kind"] == "file"]
        added_fact = next(
            (
                f
                for f in group
                if f["kind"] == "count"
                and f.get("scope") == "commit"
                and f.get("metric") == "lines_added"
            ),
            None,
        )
        if commit_fact and file_facts and added_fact and len(sentences) <= 2:
            short = chash[:7]
            add_sentence(
                f"Commit {short} added {added_fact['value']} lines.",
                [added_fact["id"]],
            )

    # Add run-level summary sentence combining commits + contributors (or whichever exist)
    commits_fact = run_counts.get("commits")
    contrib_fact = run_counts.get("contributors")
    files_fact = run_counts.get("files_changed")
    lines_fact = run_counts.get("lines_added")

    if commits_fact and contrib_fact:
        c_val = commits_fact["value"]
        p_val = contrib_fact["value"]
        add_sentence(
            f"{p_val} contributors landed {c_val} commits in this run.",
            [commits_fact["id"], contrib_fact["id"]],
        )
    elif commits_fact:
        add_sentence(*_compose_sentence_for_single_fact(commits_fact))
    elif contrib_fact:
        add_sentence(*_compose_sentence_for_single_fact(contrib_fact))

    if files_fact and lines_fact:
        add_sentence(
            f"Across {files_fact['value']} changed files, the run added {lines_fact['value']} lines.",
            [files_fact["id"], lines_fact["id"]],
        )
    elif not sentences and facts:
        text, cited = _compose_sentence_for_single_fact(facts[0])
        add_sentence(text, cited)

    return sentences


def _revise_rejected_sentence(
    sentence: dict[str, Any],
    facts_by_id: dict[str, dict[str, Any]],
    all_facts: list[dict[str, Any]],
    used_fact_ids: set[str],
) -> dict[str, Any]:
    """Rewrite a single rejected sentence using only verified facts while keeping its ID."""
    valid_cited = [fid for fid in sentence.get("facts", []) if fid in facts_by_id]

    if valid_cited:
        primary_fact = facts_by_id[valid_cited[0]]
        if len(valid_cited) >= 2:
            second_fact = facts_by_id[valid_cited[1]]
            if (
                primary_fact["kind"] == "count"
                and second_fact["kind"] == "count"
                and {primary_fact["metric"], second_fact["metric"]} == {"commits", "contributors"}
            ):
                commits_f = primary_fact if primary_fact["metric"] == "commits" else second_fact
                contrib_f = primary_fact if primary_fact["metric"] == "contributors" else second_fact
                return {
                    "id": sentence["id"],
                    "text": f"{contrib_f['value']} contributors landed {commits_f['value']} commits in this run.",
                    "facts": [commits_f["id"], contrib_f["id"]],
                }
            if primary_fact["kind"] == "commit" and second_fact["kind"] == "file":
                short = primary_fact["commit"][:7]
                return {
                    "id": sentence["id"],
                    "text": f"{primary_fact['author_name']} landed '{primary_fact['subject']}' in {second_fact['path']} ({short}).",
                    "facts": [primary_fact["id"], second_fact["id"]],
                }
        text, cited = _compose_sentence_for_single_fact(primary_fact)
        return {
            "id": sentence["id"],
            "text": text,
            "facts": cited,
        }

    # No valid citation on the rejected sentence: pick an unused fact (or the last fact)
    chosen = next((f for f in all_facts if f["id"] not in used_fact_ids), all_facts[-1])
    used_fact_ids.add(chosen["id"])
    text, cited = _compose_sentence_for_single_fact(chosen)
    return {
        "id": sentence["id"],
        "text": text,
        "facts": cited,
    }


def _revise_sentences_deterministically(
    previous_draft: dict[str, Any],
    review: dict[str, Any],
    facts_doc: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return updated sentences list where ONLY rejected sentences are modified."""
    all_facts = facts_doc.get("facts", [])
    facts_by_id = {f["id"]: f for f in all_facts}
    review_by_id = {s["id"]: s for s in review.get("sentences", [])}

    used_fact_ids: set[str] = set()
    for s in previous_draft.get("sentences", []):
        r_entry = review_by_id.get(s["id"])
        if r_entry and r_entry.get("status") == "ok":
            used_fact_ids.update(s.get("facts", []))

    updated: list[dict[str, Any]] = []
    for s in previous_draft.get("sentences", []):
        r_entry = review_by_id.get(s["id"])
        if r_entry is None or r_entry.get("status") == "ok":
            # Must remain completely untouched
            updated.append(
                {
                    "id": s["id"],
                    "text": s["text"],
                    "facts": list(s["facts"]),
                }
            )
        else:
            updated.append(
                _revise_rejected_sentence(s, facts_by_id, all_facts, used_fact_ids)
            )
    return updated


def run_writer(
    facts_doc: dict[str, Any],
    *,
    fmt: str = "howiswitch",
    previous_draft: dict[str, Any] | None = None,
    review: dict[str, Any] | None = None,
    created_at: str | None = None,
    use_llm: bool = False,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Produce a validated `studio/draft@1` document.

    - For initial draft (`previous_draft is None`), emits `revision: 0`.
    - For a revision loop (`previous_draft` and `review` provided), increments
      `revision` by 1, preserves all sentence IDs in order, and modifies ONLY
      sentences whose status in `review` is `"rejected"`.
    """
    ensure_valid(facts_doc)
    if facts_doc.get("schema") != "studio/facts@1":
        raise DocumentError(["facts_doc must be studio/facts@1"])
    if fmt not in {"howiswitch", "blog_section"}:
        raise ValueError(f"unsupported format {fmt!r}")

    if (previous_draft is None) != (review is None):
        raise ValueError("previous_draft and review must be provided together for revisions")

    run_id = facts_doc["run_id"]
    timestamp = created_at or utc_now()
    revision = 0
    target_format = fmt

    if previous_draft is not None and review is not None:
        ensure_valid(previous_draft)
        ensure_valid(review)
        if previous_draft.get("schema") != "studio/draft@1":
            raise DocumentError(["previous_draft must be studio/draft@1"])
        if review.get("schema") != "studio/review@1":
            raise DocumentError(["review must be studio/review@1"])
        revision = previous_draft["revision"] + 1
        target_format = previous_draft["format"]

    if use_llm:
        try:
            prompt = load_prompt(AGENT_DIR)
            payload: dict[str, Any] = {
                "run_id": run_id,
                "format": target_format,
                "revision": revision,
                "created_at": timestamp,
                "facts": facts_doc,
            }
            if previous_draft is not None and review is not None:
                payload["previous_draft"] = previous_draft
                payload["review"] = review

            llm_doc = call_llm_json(prompt, payload, api_key=api_key)
            llm_doc["schema"] = "studio/draft@1"
            llm_doc["run_id"] = run_id
            llm_doc["agent"] = "writer"
            llm_doc["created_at"] = timestamp
            llm_doc["revision"] = revision
            llm_doc["format"] = target_format

            if previous_draft is not None and review is not None:
                # Enforce contract invariant: keep all sentence IDs and never alter 'ok' sentences
                llm_by_id = {
                    s["id"]: s
                    for s in llm_doc.get("sentences", [])
                    if isinstance(s, dict) and "id" in s
                }
                review_by_id = {s["id"]: s for s in review.get("sentences", [])}
                merged_sentences: list[dict[str, Any]] = []
                for orig in previous_draft["sentences"]:
                    sid = orig["id"]
                    r_entry = review_by_id.get(sid)
                    if r_entry is None or r_entry.get("status") == "ok":
                        merged_sentences.append(
                            {
                                "id": sid,
                                "text": orig["text"],
                                "facts": list(orig["facts"]),
                            }
                        )
                    elif sid in llm_by_id:
                        cand = llm_by_id[sid]
                        merged_sentences.append(
                            {
                                "id": sid,
                                "text": strip_inline_tags(str(cand.get("text", ""))),
                                "facts": list(cand.get("facts", [])),
                            }
                        )
                    else:
                        raise AgentExecutionError(f"LLM dropped rejected sentence {sid}")
                llm_doc["sentences"] = merged_sentences
            else:
                for i, s in enumerate(llm_doc.get("sentences", [])):
                    if isinstance(s, dict):
                        s["id"] = f"S{i + 1}"
                        s["text"] = strip_inline_tags(str(s.get("text", "")))

            return ensure_valid(llm_doc)
        except (AgentExecutionError, DocumentError):
            pass

    if previous_draft is not None and review is not None:
        sentences = _revise_sentences_deterministically(previous_draft, review, facts_doc)
    else:
        sentences = _build_initial_sentences(facts_doc, target_format)

    doc: dict[str, Any] = {
        "schema": "studio/draft@1",
        "run_id": run_id,
        "agent": "writer",
        "created_at": timestamp,
        "revision": revision,
        "format": target_format,
        "sentences": sentences,
    }
    return ensure_valid(doc)


def summarize_draft(draft_doc: dict[str, Any]) -> str:
    """Return a concise 1-line human summary for `#studio`."""
    rev = draft_doc["revision"]
    count = len(draft_doc.get("sentences", []))
    fmt = draft_doc["format"]
    if rev == 0:
        return f"Writer drafted {count} sentences (`r0`, format `{fmt}`)."
    return f"Writer posted revision `r{rev}` ({count} sentences, format `{fmt}`)."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents.writer",
        description="Run Writer agent to produce studio/draft@1 from studio/facts@1.",
    )
    parser.add_argument("--facts", required=True, help="Path to studio/facts@1 JSON")
    parser.add_argument(
        "--format",
        choices=["howiswitch", "blog_section"],
        default="howiswitch",
        help="Target draft format (default: howiswitch)",
    )
    parser.add_argument("--previous-draft", help="Path to prior studio/draft@1 JSON (for revisions)")
    parser.add_argument("--review", help="Path to Editor studio/review@1 JSON (for revisions)")
    parser.add_argument("--out", help="Optional path to write studio/draft@1 JSON")
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
    parser.add_argument("--llm", action="store_true", help="Attempt LLM drafting via OPENAI_API_KEY")
    args = parser.parse_args(argv)

    try:
        facts_doc = load_document(args.facts)
        prev_draft = load_document(args.previous_draft) if args.previous_draft else None
        review_doc = load_document(args.review) if args.review else None
        draft_doc = run_writer(
            facts_doc,
            fmt=args.format,
            previous_draft=prev_draft,
            review=review_doc,
            use_llm=args.llm,
        )
        if args.out:
            write_document(draft_doc, args.out)
        if args.room_message:
            rev = draft_doc["revision"]
            overflow_target = args.out or f"runs/{draft_doc['run_id']}/draft.r{rev}.json"
            msg = format_room_message(
                summarize_draft(draft_doc),
                draft_doc,
                overflow_path=overflow_target,
                max_inline_bytes=args.max_inline_bytes,
            )
            sys.stdout.write(msg)
        elif not args.out:
            sys.stdout.write(json.dumps(draft_doc, indent=2) + "\n")
        return 0
    except (DocumentError, AgentExecutionError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "path": args.facts}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
