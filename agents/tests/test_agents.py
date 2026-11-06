"""Unit and integration tests for Switch Studio M2 agents (Scout, Writer, Editor)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents._common import (
    extract_message_json,
    format_room_message,
    utc_now,
    write_document,
)
from agents.editor import (
    detect_overclaim,
    main as editor_main,
    run_editor,
    run_verifier,
)
from agents.scout import (
    compute_count_metric,
    main as scout_main,
    run_scout,
    verify_fact_against_snapshot,
)
from agents.writer import (
    main as writer_main,
    run_writer,
    strip_inline_tags,
)
from schemas.validate import DocumentError, EXAMPLES_DIR, load_document, validate


def _load_example(name: str) -> dict:
    return load_document(EXAMPLES_DIR / name)


def test_common_utc_now_and_room_message_roundtrip(tmp_path: Path) -> None:
    ts = utc_now()
    assert len(ts) == 20 and ts.endswith("Z")

    facts = _load_example("facts.json")
    inline_msg = format_room_message("Scout summary.", facts, max_inline_bytes=10_000)
    assert "```json" in inline_msg
    extracted = extract_message_json(inline_msg)
    assert extracted == facts

    # Force overflow envelope via max_inline_bytes=10
    rel_overflow = "runs/demo-001/facts.json"
    overflow_msg = format_room_message(
        "Scout overflow summary.",
        facts,
        overflow_path=rel_overflow,
        max_inline_bytes=10,
        base_dir=tmp_path,
    )
    assert '"payload_ref"' in overflow_msg
    assert (tmp_path / rel_overflow).is_file()
    resolved = extract_message_json(overflow_msg, base_dir=tmp_path)
    assert resolved == facts


def test_scout_metrics_and_fact_extraction() -> None:
    snapshot = _load_example("snapshot.json")

    assert compute_count_metric(snapshot, "commits", "run") == 3
    assert compute_count_metric(snapshot, "contributors", "run") == 3
    assert compute_count_metric(snapshot, "files_changed", "run") == 5
    assert compute_count_metric(snapshot, "lines_added", "run") == 899
    assert compute_count_metric(snapshot, "lines_deleted", "run") == 1
    assert (
        compute_count_metric(
            snapshot,
            "lines_added",
            "commit",
            commit_hash="9f3c2a1e7b4d5c6a8e9f0a1b2c3d4e5f6a7b8c9d",
        )
        == 246
    )

    with pytest.raises(ValueError):
        compute_count_metric(snapshot, "commits", "commit", commit_hash=snapshot["head"])
    with pytest.raises(ValueError):
        compute_count_metric(
            snapshot, "contributors", "run", author_email="saurabh@example.com"
        )

    facts_doc = run_scout(
        snapshot,
        run_id="demo-001",
        snapshot_path="runs/demo-001/snapshot.json",
    )
    assert validate(facts_doc) == []
    assert facts_doc["agent"] == "scout"
    assert len(facts_doc["facts"]) > 0
    for idx, fact in enumerate(facts_doc["facts"], start=1):
        assert fact["id"] == f"F{idx}"
        assert len(fact["commit"]) == 40
        assert verify_fact_against_snapshot(fact, snapshot) is None


def test_writer_initial_draft_and_revision_invariants() -> None:
    assert strip_inline_tags("Added 246 lines [F3] [F4].") == "Added 246 lines."

    snapshot = _load_example("snapshot.json")
    facts_doc = _load_example("facts.json")

    draft_r0 = run_writer(facts_doc, fmt="howiswitch")
    assert validate(draft_r0) == []
    assert draft_r0["revision"] == 0
    for sent in draft_r0["sentences"]:
        assert "[F" not in sent["text"]
        assert len(sent["facts"]) >= 1
        assert detect_overclaim(sent, {f["id"]: f for f in facts_doc["facts"]}) is None

    verdict_r0 = run_verifier(snapshot, facts_doc, draft_r0)
    assert verdict_r0["pass"] is True

    # Revision test on worked example r0 -> r1
    example_draft_r0 = _load_example("draft.r0.json")
    example_review_r0 = _load_example("review.r0.json")

    draft_r1 = run_writer(
        facts_doc,
        previous_draft=example_draft_r0,
        review=example_review_r0,
    )
    assert validate(draft_r1) == []
    assert draft_r1["revision"] == 1
    assert [s["id"] for s in draft_r1["sentences"]] == ["S1", "S2", "S3", "S4"]

    # Approved ('ok') sentences S1 and S3 must be untouched
    assert draft_r1["sentences"][0] == example_draft_r0["sentences"][0]
    assert draft_r1["sentences"][2] == example_draft_r0["sentences"][2]

    # Rejected sentences S2 and S4 must be rewritten and pass verification + overclaim checks
    assert draft_r1["sentences"][1] != example_draft_r0["sentences"][1]
    assert draft_r1["sentences"][3] != example_draft_r0["sentences"][3]

    review_r1 = run_editor(snapshot, facts_doc, draft_r1, loop=2)
    assert validate(review_r1) == []
    assert all(s["status"] == "ok" for s in review_r1["sentences"])
    assert review_r1["handoff"] == "humans"


def test_editor_reproduces_worked_example_and_enforces_two_loop_cap() -> None:
    snapshot = _load_example("snapshot.json")
    facts_doc = _load_example("facts.json")
    draft_r0 = _load_example("draft.r0.json")
    expected_review_r0 = _load_example("review.r0.json")

    review_loop1 = run_editor(
        snapshot,
        facts_doc,
        draft_r0,
        loop=1,
        verdict_path="runs/demo-001/verdict.r0.json",
        created_at="2026-11-04T10:16:20Z",
    )
    assert validate(review_loop1) == []
    assert review_loop1["handoff"] == "writer"
    assert [
        {"id": s["id"], "status": s["status"], "errors": s["errors"]}
        for s in review_loop1["sentences"]
    ] == [
        {"id": s["id"], "status": s["status"], "errors": s["errors"]}
        for s in expected_review_r0["sentences"]
    ]

    # On loop 2, handoff must be "humans" even when rejections remain
    review_loop2 = run_editor(snapshot, facts_doc, draft_r0, loop=2)
    assert validate(review_loop2) == []
    assert any(s["status"] == "rejected" for s in review_loop2["sentences"])
    assert review_loop2["handoff"] == "humans"


def test_cli_entrypoints_end_to_end(tmp_path: Path) -> None:
    snap_path = EXAMPLES_DIR / "snapshot.json"
    facts_out = tmp_path / "facts.json"
    draft_out = tmp_path / "draft.r0.json"
    verdict_out = tmp_path / "verdict.r0.json"
    review_out = tmp_path / "review.r0.json"

    assert (
        scout_main(
            [
                "--snapshot",
                str(snap_path),
                "--run-id",
                "demo-001",
                "--out",
                str(facts_out),
            ]
        )
        == 0
    )
    assert load_document(facts_out)["schema"] == "studio/facts@1"

    assert (
        writer_main(
            [
                "--facts",
                str(facts_out),
                "--format",
                "howiswitch",
                "--out",
                str(draft_out),
            ]
        )
        == 0
    )
    assert load_document(draft_out)["schema"] == "studio/draft@1"

    assert (
        editor_main(
            [
                "--snapshot",
                str(snap_path),
                "--facts",
                str(facts_out),
                "--draft",
                str(draft_out),
                "--loop",
                "1",
                "--verdict",
                str(verdict_out),
                "--out",
                str(review_out),
            ]
        )
        == 0
    )
    review_doc = load_document(review_out)
    assert review_doc["schema"] == "studio/review@1"
    assert review_doc["handoff"] == "humans"

    # Invalid input path returns exit code 2
    bad_path = tmp_path / "nonexistent.json"
    write_document(_load_example("run.json"), tmp_path / "wrong_schema.json")
    assert scout_main(["--snapshot", str(bad_path), "--run-id", "demo-001"]) == 2
    assert (
        scout_main(
            ["--snapshot", str(tmp_path / "wrong_schema.json"), "--run-id", "demo-001"]
        )
        == 2
    )
    with pytest.raises(DocumentError):
        extract_message_json("No fenced json block here")
