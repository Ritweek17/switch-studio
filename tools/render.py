"""Render an effective draft as cited markdown (CONTRACT.md §8)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from schemas.validate import load_document


def render_markdown(draft: dict[str, Any], facts_document: dict[str, Any]) -> str:
    facts_by_id = {fact["id"]: fact for fact in facts_document.get("facts", [])}
    cited: set[str] = set()
    paragraphs: list[str] = []
    for sentence in draft.get("sentences", []):
        fact_ids = sentence.get("facts", [])
        missing = [fact_id for fact_id in fact_ids if fact_id not in facts_by_id]
        if missing:
            raise ValueError(f"sentence {sentence.get('id')} cites unknown facts: {', '.join(missing)}")
        cited.update(fact_ids)
        text = sentence.get("text", "").strip()
        tags = "".join(f"[{fact_id}]" for fact_id in fact_ids)
        paragraphs.append(f"{text} {tags}".rstrip() if tags else text)
    body = " ".join(paragraphs)

    def fact_number(fact_id: str) -> tuple[int, str]:
        digits = fact_id[1:]
        return (int(digits) if digits.isdigit() else 0, fact_id)

    sources = ["---", "**Sources**"]
    for fact_id in sorted(cited, key=fact_number):
        fact = facts_by_id[fact_id]
        prefix = str(fact.get("commit", ""))[:7]
        if fact.get("kind") == "commit":
            detail = fact.get("subject", "")
        elif fact.get("kind") == "file":
            detail = f"{fact.get('path', '')} ({fact.get('status', '')})"
        elif fact.get("kind") == "count":
            detail = f"{fact.get('metric', '')} ({fact.get('scope', '')}) = {fact.get('value', '')}"
        else:
            detail = fact.get("statement", "")
        sources.append(f"- {fact_id}: {prefix} {detail}".rstrip())
    return body + "\n\n" + "\n".join(sources) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, help="effective draft JSON path")
    parser.add_argument("--facts", required=True, help="facts JSON path")
    parser.add_argument("--out", required=True, help="final markdown path")
    args = parser.parse_args(argv)
    try:
        draft = load_document(args.draft)
        facts = load_document(args.facts)
        if draft.get("schema") != "studio/draft@1" or facts.get("schema") != "studio/facts@1":
            raise ValueError("expected studio/draft@1 and studio/facts@1 documents")
        if draft.get("run_id") != facts.get("run_id"):
            raise ValueError("draft and facts belong to different runs")
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_markdown(draft, facts), encoding="utf-8")
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
