"""Deterministic claim verifier for Switch Studio (implements CONTRACT.md §4 and §6)."""

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


def verify_facts(snapshot: dict, facts_doc: dict) -> list[dict]:
    """Verify each fact in facts_doc against the repo snapshot.

    Returns a list of dicts: {"id": fact_id, "ok": bool, "reason": str | None}.
    """
    commits_by_hash = {c["hash"]: c for c in snapshot.get("commits", [])}
    head_hash = snapshot.get("head")
    results = []

    for fact in facts_doc.get("facts", []):
        fid = fact.get("id", "")
        kind = fact.get("kind")
        commit_hash = fact.get("commit")

        if not commit_hash or not isinstance(commit_hash, str):
            results.append({"id": fid, "ok": False, "reason": "missing or invalid commit hash"})
            continue

        if kind == "commit":
            commit = commits_by_hash.get(commit_hash)
            if not commit:
                results.append({"id": fid, "ok": False, "reason": f"commit {commit_hash} not found in snapshot"})
                continue
            if (
                commit.get("subject") == fact.get("subject")
                and commit.get("author_name") == fact.get("author_name")
                and commit.get("date") == fact.get("date")
            ):
                results.append({"id": fid, "ok": True, "reason": None})
            else:
                mismatches = []
                if commit.get("subject") != fact.get("subject"):
                    mismatches.append(f"subject {fact.get('subject')!r} != {commit.get('subject')!r}")
                if commit.get("author_name") != fact.get("author_name"):
                    mismatches.append(f"author_name {fact.get('author_name')!r} != {commit.get('author_name')!r}")
                if commit.get("date") != fact.get("date"):
                    mismatches.append(f"date {fact.get('date')!r} != {commit.get('date')!r}")
                results.append({"id": fid, "ok": False, "reason": "; ".join(mismatches)})

        elif kind == "file":
            commit = commits_by_hash.get(commit_hash)
            if not commit:
                results.append({"id": fid, "ok": False, "reason": f"commit {commit_hash} not found in snapshot"})
                continue
            path = fact.get("path")
            status = fact.get("status")
            matched = any(
                f.get("path") == path and f.get("status") == status
                for f in commit.get("files", [])
            )
            if matched:
                results.append({"id": fid, "ok": True, "reason": None})
            else:
                results.append({"id": fid, "ok": False, "reason": f"file {path!r} with status {status!r} not in commit {commit_hash[:7]}"})

        elif kind == "count":
            metric = fact.get("metric")
            scope = fact.get("scope")
            expected_val = fact.get("value")
            author_email = fact.get("author_email")

            allowed_metrics = {"commits", "files_changed", "lines_added", "lines_deleted", "contributors"}
            if metric not in allowed_metrics:
                results.append({"id": fid, "ok": False, "reason": f"unknown metric {metric!r}"})
                continue

            # Validate scope and author_email per CONTRACT.md §4
            if metric == "commits":
                if scope != "run":
                    results.append({"id": fid, "ok": False, "reason": "metric 'commits' only allowed with scope 'run'"})
                    continue
            elif metric in {"files_changed", "lines_added", "lines_deleted"}:
                if scope not in {"commit", "run"}:
                    results.append({"id": fid, "ok": False, "reason": f"invalid scope {scope!r} for {metric}"})
                    continue
                if scope == "commit" and author_email is not None:
                    results.append({"id": fid, "ok": False, "reason": "author_email not allowed for scope 'commit'"})
                    continue
            elif metric == "contributors":
                if scope != "run":
                    results.append({"id": fid, "ok": False, "reason": "metric 'contributors' only allowed with scope 'run'"})
                    continue
                if author_email is not None:
                    results.append({"id": fid, "ok": False, "reason": "author_email never allowed for metric 'contributors'"})
                    continue

            # Check commit hash requirement per CONTRACT.md §4
            if scope == "run" and head_hash and commit_hash != head_hash:
                results.append({"id": fid, "ok": False, "reason": f"run-scoped fact commit must match snapshot head {head_hash}"})
                continue

            if scope == "commit":
                commit = commits_by_hash.get(commit_hash)
                if not commit:
                    results.append({"id": fid, "ok": False, "reason": f"commit {commit_hash} not found in snapshot"})
                    continue
                files = commit.get("files", [])
                if metric == "files_changed":
                    computed = len(set(f.get("path") for f in files if "path" in f))
                elif metric == "lines_added":
                    computed = sum((f.get("added") or 0) for f in files)
                elif metric == "lines_deleted":
                    computed = sum((f.get("deleted") or 0) for f in files)
                else:
                    computed = None
            else:  # scope == "run"
                commits = snapshot.get("commits", [])
                if author_email:
                    commits = [c for c in commits if c.get("author_email") == author_email]
                if metric == "commits":
                    computed = len(commits)
                elif metric == "files_changed":
                    all_paths = {f.get("path") for c in commits for f in c.get("files", []) if "path" in f}
                    computed = len(all_paths)
                elif metric == "lines_added":
                    computed = sum((f.get("added") or 0) for c in commits for f in c.get("files", []))
                elif metric == "lines_deleted":
                    computed = sum((f.get("deleted") or 0) for c in commits for f in c.get("files", []))
                elif metric == "contributors":
                    all_authors = {c.get("author_email") for c in commits if c.get("author_email")}
                    computed = len(all_authors)
                else:
                    computed = None

            if computed == expected_val:
                results.append({"id": fid, "ok": True, "reason": None})
            else:
                results.append({"id": fid, "ok": False, "reason": f"recomputed {metric} ({scope}) gave {computed}, expected {expected_val}"})

        else:
            results.append({"id": fid, "ok": False, "reason": f"unsupported fact kind {kind!r}"})

    return results


def verify_sentence(
    sentence: dict,
    facts_by_id: dict[str, dict],
    facts_ok: dict[str, bool],
    commits_by_hash: dict[str, dict],
) -> list[str]:
    """Verify one sentence against its citations following CONTRACT.md §6."""
    errors: list[str] = []
    cited_ids = sentence.get("facts", [])
    text = sentence.get("text", "")

    # Step 1: Citations
    if not cited_ids:
        errors.append("NO_CITATION")
    for fid in cited_ids:
        if fid not in facts_by_id:
            errors.append("UNKNOWN_FACT")
        elif not facts_ok.get(fid, False):
            errors.append("FACT_INVALID")

    # Collect valid cited facts & their commits
    valid_cited_facts = [facts_by_id[fid] for fid in cited_ids if fid in facts_by_id and facts_ok.get(fid, False)]

    # Step 2: Strip before scanning text
    # 2a: Strip [F\d+] tags
    cleaned_text = re.sub(r"\[F\d+\]", " ", text)
    # 2b: Strip \bS\d+\b sentence IDs
    cleaned_text = re.sub(r"\bS\d+\b", " ", cleaned_text)
    # 2c: Strip ISO dates whose date part equals the date of a cited commit
    cited_dates = set()
    for f in valid_cited_facts:
        c_hash = f.get("commit")
        if c_hash in commits_by_hash:
            c_date = commits_by_hash[c_hash].get("date", "")
            if len(c_date) >= 10:
                cited_dates.add(c_date[:10])
        if f.get("kind") == "commit" and f.get("date"):
            cited_dates.add(f["date"][:10])

    def _strip_date_match(m: re.Match) -> str:
        date_str = m.group(1)
        if date_str[:10] in cited_dates:
            return " " * len(date_str)
        return date_str

    cleaned_text = re.sub(
        r"\b(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?Z)?)\b",
        _strip_date_match,
        cleaned_text,
    )

    # Step 3: Hashes
    # Match \b[0-9a-f]{7,40}\b that contains at least one letter a-f (case-insensitive)
    hash_candidates = re.finditer(r"\b([0-9a-fA-F]{7,40})\b", cleaned_text)
    cited_commit_hashes = {f.get("commit", "").lower() for f in valid_cited_facts}

    for m in hash_candidates:
        tok = m.group(1)
        if re.search(r"[a-fA-F]", tok):
            tok_lower = tok.lower()
            is_prefix = any(c.startswith(tok_lower) for c in cited_commit_hashes if c)
            if not is_prefix:
                errors.append("HASH_UNSUPPORTED")

    # Remove all hash tokens (7-40 hex chars containing at least one letter) before Step 4
    cleaned_text = re.sub(r"\b(?=[0-9a-fA-F]{7,40}\b)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{7,40}\b", " ", cleaned_text)

    # Step 4: Numbers
    # Extract supported numbers from cited facts
    supported_numbers: set[int | float] = set()
    for f in valid_cited_facts:
        if f.get("kind") == "count":
            val = f.get("value")
            if isinstance(val, (int, float)):
                supported_numbers.add(val)
        elif f.get("kind") == "commit":
            for n_str in re.findall(r"\d+", f.get("subject", "")):
                supported_numbers.add(int(n_str))
        elif f.get("kind") == "file":
            for n_str in re.findall(r"\d+", f.get("path", "")):
                supported_numbers.add(int(n_str))

    # Match number tokens not glued to word, path, or version (no [\w/.\-] immediately before it)
    # Comma-separated thousands removed, optional decimal and %
    number_pattern = re.compile(
        r"(?<![\w/.\-])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(%?)"
    )

    for m in number_pattern.finditer(cleaned_text):
        raw_num = m.group(1).replace(",", "")
        decimal_part = m.group(2)
        has_pct = bool(m.group(3))

        if decimal_part is not None:
            num_val: int | float = float(f"{raw_num}.{decimal_part}")
        else:
            num_val = int(raw_num)

        # Derived percentages are not raw count facts. Even a matching numeric
        # portion cannot turn a count such as 246 into the claim "246%".
        if has_pct:
            errors.append("NUMBER_UNSUPPORTED")
        else:
            if num_val not in supported_numbers:
                errors.append("NUMBER_UNSUPPORTED")

    # Return distinct errors preserving order
    return list(dict.fromkeys(errors))


def verify_draft(snapshot: dict, facts_doc: dict, draft_doc: dict) -> dict:
    """Run full verification on facts and draft sentences against snapshot.

    Returns studio/verdict@1 document.
    """
    fact_results = verify_facts(snapshot, facts_doc)
    facts_by_id = {f["id"]: f for f in facts_doc.get("facts", [])}
    facts_ok = {r["id"]: r["ok"] for r in fact_results}
    commits_by_hash = {c["hash"]: c for c in snapshot.get("commits", [])}

    sentence_results = []
    for s in draft_doc.get("sentences", []):
        s_errors = verify_sentence(s, facts_by_id, facts_ok, commits_by_hash)
        sentence_results.append({
            "id": s["id"],
            "ok": len(s_errors) == 0,
            "errors": s_errors,
        })

    all_facts_ok = all(r["ok"] for r in fact_results)
    all_sentences_ok = all(r["ok"] for r in sentence_results)
    overall_pass = all_facts_ok and all_sentences_ok

    verdict: dict[str, Any] = {
        "schema": "studio/verdict@1",
        "run_id": draft_doc.get("run_id", "demo-001"),
        "agent": "tool",
        "created_at": _iso_now(),
        "draft_revision": draft_doc.get("revision", 0),
        "pass": overall_pass,
        "facts": fact_results,
        "sentences": sentence_results,
        "summary": {
            "facts_total": len(fact_results),
            "facts_ok": sum(1 for r in fact_results if r["ok"]),
            "sentences_total": len(sentence_results),
            "sentences_ok": sum(1 for r in sentence_results if r["ok"]),
        },
    }
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic claim verifier")
    parser.add_argument("--snapshot", required=True, help="Path to snapshot JSON")
    parser.add_argument("--facts", required=True, help="Path to facts JSON")
    parser.add_argument("--draft", required=True, help="Path to draft JSON")
    parser.add_argument("--out", help="Path to write verdict JSON")
    args = parser.parse_args(argv)

    try:
        snapshot = load_document(args.snapshot)
        facts = load_document(args.facts)
        draft = load_document(args.draft)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

    verdict = verify_draft(snapshot, facts, draft)
    errs = validate(verdict)
    if errs:
        print(json.dumps({"error": "invalid verdict generated", "details": errs}), file=sys.stderr)
        return 2

    out_bytes = json.dumps(verdict, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out_bytes)
    else:
        print(out_bytes, end="")

    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
