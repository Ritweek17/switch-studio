"""End-to-end regression tests for the self-contained offline M3a sandbox."""

from pathlib import Path

import pytest

from sandbox.agents import run_mock_editor, run_mock_scout, run_mock_writer
from sandbox.run import orchestrate_offline_run
from schemas.validate import load_document


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "demo-repo.snapshot.json"


def test_writer_revision_preserves_sentence_ids():
    snapshot = load_document(FIXTURE)
    facts = run_mock_scout(snapshot, "demo-001", "runs/demo-001/snapshot.json")
    original = run_mock_writer(facts, "demo-001")
    _, review = run_mock_editor(snapshot, facts, original, 1, "runs/demo-001/verdict.r0.json")
    revised = run_mock_writer(facts, "demo-001", revision=1, previous_draft=original, previous_review=review)
    assert [sentence["id"] for sentence in revised["sentences"]] == ["S1", "S2", "S3", "S4"]
    assert revised["sentences"][:3] == original["sentences"][:3]
    assert revised["sentences"][3]["facts"] == ["F5"]


@pytest.mark.parametrize("fmt", ["howiswitch", "blog_section"])
def test_sandbox_runs_revision_loop_and_persists_artifacts(tmp_path: Path, fmt: str):
    run_dir = orchestrate_offline_run(FIXTURE, fmt=fmt, run_id=f"sandbox-{fmt.replace('_', '-')}", base_dir=tmp_path)
    expected = {
        "run.json", "snapshot.json", "facts.json", "draft.r0.json", "verdict.r0.json", "review.r0.json",
        "draft.r1.json", "verdict.r1.json", "review.r1.json", "decisions.jsonl", "effective_draft.json",
        "final_verdict.json", "ship.json", "run_summary.json", "final.md",
    }
    assert expected <= {path.name for path in run_dir.iterdir()}
    assert load_document(run_dir / "effective_draft.json")["revision"] == 2
    assert '"status": "shipped"' in (run_dir / "run_summary.json").read_text(encoding="utf-8")
    assert (run_dir / "final.md").read_text(encoding="utf-8").startswith("Elena Rostova")


def test_missing_fixture_and_collision_fail_without_overwriting(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        orchestrate_offline_run(tmp_path / "missing.json", run_id="missing-fixture", base_dir=tmp_path)
    first = orchestrate_offline_run(FIXTURE, run_id="collision-run", base_dir=tmp_path)
    with pytest.raises(FileExistsError):
        orchestrate_offline_run(FIXTURE, run_id="collision-run", base_dir=tmp_path)
    assert (first / "final.md").is_file()
