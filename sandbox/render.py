"""Markdown renderer for Switch Studio shipped output (CONTRACT.md §8)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from schemas.validate import load_document


def render_final_markdown(draft: dict, facts_doc: dict) -> str:
    """Render effective draft and facts doc into final.md format."""
    facts_by_id = {f["id"]: f for f in facts_doc.get("facts", [])}

    sentence_chunks = []
    cited_fact_ids: list[str] = []

    for s in draft.get("sentences", []):
        s_facts = s.get("facts", [])
        for fid in s_facts:
            if fid not in cited_fact_ids:
                cited_fact_ids.append(fid)
        tags = "".join(f"[{fid}]" for fid in s_facts)
        text = s.get("text", "").rstrip()
        if tags:
            sentence_chunks.append(f"{text} {tags}")
        else:
            sentence_chunks.append(text)

    body = " ".join(sentence_chunks)

    # Sort cited fact IDs naturally (F1, F2, F3...)
    def _fact_sort_key(fid: str) -> int:
        digits = "".join(ch for ch in fid if ch.isdigit())
        return int(digits) if digits else 999999

    sorted_ids = sorted(cited_fact_ids, key=_fact_sort_key)

    sources = ["---", "**Sources**"]
    for fid in sorted_ids:
        f = facts_by_id.get(fid)
        if not f:
            continue
        c_hash = f.get("commit", "")[:7]
        kind = f.get("kind")
        if kind == "commit":
            sources.append(f"- {fid}: {c_hash} {f.get('subject', '')}")
        elif kind == "file":
            sources.append(f"- {fid}: {c_hash} {f.get('path', '')} ({f.get('status', '')})")
        elif kind == "count":
            sources.append(f"- {fid}: {c_hash} {f.get('metric', '')} ({f.get('scope', '')}) = {f.get('value', '')}")
        else:
            sources.append(f"- {fid}: {c_hash} {f.get('statement', '')}")

    return body + "\n\n" + "\n".join(sources) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render final.md from draft and facts")
    parser.add_argument("--draft", required=True, help="Path to draft JSON")
    parser.add_argument("--facts", required=True, help="Path to facts JSON")
    parser.add_argument("--out", required=True, help="Path to output final.md")
    args = parser.parse_args(argv)

    try:
        draft = load_document(args.draft)
        facts = load_document(args.facts)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

    md = render_final_markdown(draft, facts)
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
