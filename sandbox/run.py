"""Offline, deterministic Switch Studio demonstration pipeline."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from sandbox.agents import run_mock_editor, run_mock_scout, run_mock_writer
from sandbox.apply import parse_and_apply_command, replay_decisions
from sandbox.render import render_final_markdown
from sandbox.verifier import verify_draft
from schemas.validate import load_document, validate


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_run_id() -> str:
    return "demo-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def _write_json(path: Path, document: dict[str, Any], validate_document: bool = True) -> None:
    if validate_document:
        errors = validate(document)
        if errors:
            raise ValueError(f"invalid {document.get('schema', 'JSON')} artifact: {errors}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_fixture(fixture_path: str | Path, root: Path) -> tuple[Path, dict[str, Any]]:
    fixture = Path(fixture_path)
    if not fixture.is_absolute():
        fixture = root / fixture
    if not fixture.is_file():
        raise FileNotFoundError(f"fixture file not found: {fixture}")
    snapshot = load_document(fixture)
    if snapshot["schema"] != "studio/snapshot@1":
        raise ValueError("fixture must be a studio/snapshot@1 document")
    return fixture, snapshot


def orchestrate_offline_run(fixture_path: str | Path = "fixtures/demo-repo.snapshot.json", fmt: str = "howiswitch", run_id: str | None = None, base_dir: Path | None = None) -> Path:
    """Run the complete pipeline and return its newly created artifact directory."""
    root = (base_dir or Path.cwd()).resolve()
    actual_run_id = run_id or _new_run_id()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,39}", actual_run_id):
        raise ValueError("run_id must match ^[a-z0-9][a-z0-9-]{2,39}$")
    if fmt not in {"howiswitch", "blog_section"}:
        raise ValueError("format must be howiswitch or blog_section")
    fixture, snapshot = _load_fixture(fixture_path, root)
    run_dir = root / "runs" / actual_run_id
    if run_dir.exists():
        raise FileExistsError(f"run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    try:
        _write_json(run_dir / "snapshot.json", snapshot)
        run_doc = {"schema": "studio/run@1", "run_id": actual_run_id, "agent": "tool", "created_at": _iso_now(), "mode": "fixture", "repo_path": str(fixture).replace("\\", "/"), "range": snapshot["range"], "snapshot_path": f"runs/{actual_run_id}/snapshot.json", "format": fmt}
        _write_json(run_dir / "run.json", run_doc)
        facts = run_mock_scout(snapshot, actual_run_id, run_doc["snapshot_path"])
        _write_json(run_dir / "facts.json", facts)
        draft = run_mock_writer(facts, actual_run_id, fmt=fmt)
        _write_json(run_dir / "draft.r0.json", draft)
        review: dict[str, Any] | None = None
        for loop in (1, 2):
            verdict, review = run_mock_editor(snapshot, facts, draft, loop, f"runs/{actual_run_id}/verdict.r{draft['revision']}.json")
            _write_json(run_dir / f"verdict.r{draft['revision']}.json", verdict)
            _write_json(run_dir / f"review.r{draft['revision']}.json", review)
            if review["handoff"] != "writer":
                break
            if loop == 2:
                raise RuntimeError("writer revision limit reached before handoff to humans")
            draft = run_mock_writer(facts, actual_run_id, fmt=fmt, revision=draft["revision"] + 1, previous_draft=draft, previous_review=review)
            _write_json(run_dir / f"draft.r{draft['revision']}.json", draft)
        if review is None or review["handoff"] == "writer":
            raise RuntimeError("editor requested an unresolved writer handoff")
        states = {sentence["id"]: {"id": sentence["id"], "text": sentence["text"], "facts": list(sentence["facts"]), "status": "pending"} for sentence in draft["sentences"]}
        decisions = []
        for sentence in review["sentences"]:
            if sentence["status"] == "rejected":
                decisions.append(parse_and_apply_command(f"reject {sentence['id']}: Unverifiable claim rejected after the revision limit.", draft, states, snapshot, facts, user="@sandbox", run_id=actual_run_id))
        decisions.append(parse_and_apply_command("approve all", draft, states, snapshot, facts, user="@sandbox", run_id=actual_run_id))
        effective_draft, _ = replay_decisions(draft, decisions)
        _write_json(run_dir / "effective_draft.json", effective_draft)
        final_verdict = verify_draft(snapshot, facts, effective_draft)
        _write_json(run_dir / "final_verdict.json", final_verdict)
        if not final_verdict["pass"]:
            raise RuntimeError("effective draft failed final verification")
        ship = parse_and_apply_command("ship", draft, states, snapshot, facts, user="@sandbox", run_id=actual_run_id)
        decisions.append(ship)
        if not ship["result"]["accepted"]:
            raise RuntimeError(f"ship was blocked: {ship['result']['error']}")
        (run_dir / "decisions.jsonl").write_text("\n".join(json.dumps(decision) for decision in decisions) + "\n", encoding="utf-8")
        _write_json(run_dir / "ship.json", ship)
        final_path = run_dir / "final.md"
        temporary = final_path.with_suffix(".md.tmp")
        temporary.write_text(render_final_markdown(effective_draft, facts), encoding="utf-8")
        os.replace(temporary, final_path)
        _write_json(run_dir / "run_summary.json", {"run_id": actual_run_id, "status": "shipped", "format": fmt, "final": "final.md"}, validate_document=False)
        return run_dir
    except Exception:
        final_path = run_dir / "final.md"
        if final_path.exists():
            final_path.unlink()
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Switch Studio's offline deterministic sandbox")
    parser.add_argument("--fixture", default="fixtures/demo-repo.snapshot.json", help="snapshot fixture path")
    parser.add_argument("--format", dest="fmt", default="howiswitch", choices=["howiswitch", "blog_section"])
    parser.add_argument("--run-id", help="optional unique run identifier")
    parser.add_argument("--output-dir", help="directory that receives runs/<run_id>")
    args = parser.parse_args(argv)
    try:
        run_dir = orchestrate_offline_run(args.fixture, args.fmt, args.run_id, Path(args.output_dir) if args.output_dir else None)
        print(f"ok  Run completed successfully in {run_dir}")
        return 0
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
