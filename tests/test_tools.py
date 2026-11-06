"""Production CLI coverage for tools.snapshot/apply/render/grade."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from schemas.validate import load_document, validate
from tools.grade import grade_text
from tools.render import render_markdown
from tools.snapshot import create_snapshot

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "schemas" / "examples"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def test_snapshot_cli_reads_git_and_tracks_renames(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test Author")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "old.txt").write_text("one\ntwo\n", encoding="utf-8")
    _git(repo, "add", "old.txt")
    _git(repo, "commit", "-m", "Add original file (#12)")
    _git(repo, "mv", "old.txt", "new.txt")
    _git(repo, "commit", "-m", "Rename file")

    output = tmp_path / "snapshot.json"
    proc = subprocess.run(
        [sys.executable, "-m", "tools.snapshot", "--repo", str(repo), "--range", "HEAD", "--out", str(output)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    snapshot = load_document(output)
    assert snapshot["schema"] == "studio/snapshot@1"
    assert len(snapshot["commits"]) == 2
    renamed = snapshot["commits"][0]["files"]
    assert renamed == [{"path": "new.txt", "status": "R", "added": 0, "deleted": 0}]


def test_apply_cli_replays_reject_edit_and_writes_state(tmp_path: Path):
    draft = load_document(EXAMPLES / "draft.r0.json")
    decisions = [
        {"schema": "studio/decision@1", "run_id": draft["run_id"], "agent": "human", "created_at": "2026-11-04T10:16:00Z", "user": "@test", "draft_revision": 0, "command": "reject", "sentence_id": "S2", "reason": "drop", "result": {"accepted": True, "error": None}},
        {"schema": "studio/decision@1", "run_id": draft["run_id"], "agent": "human", "created_at": "2026-11-04T10:16:01Z", "user": "@test", "draft_revision": 0, "command": "edit", "sentence_id": "S4", "text": "Exactly 3 contributors landed commits.", "facts": ["F4", "F5"], "result": {"accepted": True, "error": None}},
    ]
    draft_path = tmp_path / "draft.json"
    decisions_path = tmp_path / "decisions.jsonl"
    out_path = tmp_path / "effective.json"
    states_path = tmp_path / "states.json"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    decisions_path.write_text("\n".join(json.dumps(item) for item in decisions) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "tools.apply", "--draft", str(draft_path), "--decisions", str(decisions_path), "--out", str(out_path), "--states-out", str(states_path)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    effective = load_document(out_path)
    states = json.loads(states_path.read_text(encoding="utf-8"))["sentences"]
    assert not any(sentence["id"] == "S2" for sentence in effective["sentences"])
    assert next(sentence for sentence in effective["sentences"] if sentence["id"] == "S4")["text"] == "Exactly 3 contributors landed commits."
    assert next(sentence for sentence in states if sentence["id"] == "S2")["status"] == "rejected"
    assert next(sentence for sentence in states if sentence["id"] == "S4")["status"] == "approved"


def test_render_outputs_citations_and_sources():
    draft = load_document(EXAMPLES / "draft.r0.json")
    facts = load_document(EXAMPLES / "facts.json")
    text = render_markdown(draft, facts)
    assert "[F1]" in text
    assert "**Sources**" in text
    assert "9f3c2a1" in text


def test_render_cli_writes_markdown(tmp_path: Path):
    output = tmp_path / "final.md"
    proc = subprocess.run(
        [sys.executable, "-m", "tools.render", "--draft", str(EXAMPLES / "draft.r0.json"), "--facts", str(EXAMPLES / "facts.json"), "--out", str(output)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "**Sources**" in output.read_text(encoding="utf-8")


def test_grade_counts_supported_and_unsupported_claims():
    snapshot = load_document(ROOT / "fixtures" / "demo-repo.snapshot.json")
    result = grade_text(snapshot, "Commit 1a2b3c4 addressed issue 105.\n\nCommit deadbeef claimed 9999 changes.\n\n---\n**Sources**\n- F1: 1a2b3c4 not prose\n")
    assert result["sentences_total"] == 2
    assert result["sentences_supported"] == 1
    assert result["supported_pct"] == 50.0
    assert result["hashes_fabricated"] == 1
    assert result["numbers_unsupported"] == 1


def test_grade_cli_writes_schema_valid_report(tmp_path: Path):
    snapshot = ROOT / "fixtures" / "demo-repo.snapshot.json"
    baseline = tmp_path / "baseline.md"
    studio = tmp_path / "studio.md"
    output = tmp_path / "grade.json"
    baseline.write_text("Commit deadbeef claimed 9999 changes.\n", encoding="utf-8")
    studio.write_text("Commit 1a2b3c4 addressed issue 105.\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "tools.grade", "--snapshot", str(snapshot), "--input", f"baseline={baseline}", "--input", f"studio={studio}", "--out", str(output)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = load_document(output)
    assert report["schema"] == "studio/grade@1"
    assert validate(report) == []
    assert next(result for result in report["results"] if result["label"] == "studio")["supported_pct"] == 100.0


def test_all_generated_documents_validate():
    for path in EXAMPLES.glob("*.json"):
        assert validate(load_document(path)) == []
