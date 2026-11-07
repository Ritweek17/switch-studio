"""Tests for decision replay and human command gating (tools.apply / CONTRACT.md §8)."""

import json
from pathlib import Path

import pytest
from sandbox.apply import parse_and_apply_command
from schemas.validate import load_document, validate
from tools.apply import replay_decisions

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "schemas" / "examples"


@pytest.fixture
def base_snapshot():
    return load_document(EXAMPLES_DIR / "snapshot.json")


@pytest.fixture
def base_facts():
    return load_document(EXAMPLES_DIR / "facts.json")


@pytest.fixture
def base_draft():
    return load_document(EXAMPLES_DIR / "draft.r0.json")


def test_approve_sentence(base_draft):
    """approve S1 transitions sentence from pending to approved."""
    sentence_states = {
        s["id"]: {"id": s["id"], "text": s["text"], "facts": s["facts"], "status": "pending"}
        for s in base_draft["sentences"]
    }
    decision = parse_and_apply_command("approve S1", base_draft, sentence_states)
    assert decision["result"]["accepted"] is True
    assert decision["result"]["error"] is None
    assert sentence_states["S1"]["status"] == "approved"
    assert sentence_states["S2"]["status"] == "pending"


def test_approve_all(base_draft):
    """approve all transitions all pending sentences to approved."""
    sentence_states = {
        s["id"]: {"id": s["id"], "text": s["text"], "facts": s["facts"], "status": "pending"}
        for s in base_draft["sentences"]
    }
    decision = parse_and_apply_command("approve all", base_draft, sentence_states)
    assert decision["result"]["accepted"] is True
    assert all(s["status"] == "approved" for s in sentence_states.values())


def test_reject_drops_sentence_from_effective_draft(base_draft):
    """reject S4 marks S4 rejected, and replay drops S4 from effective draft."""
    sentence_states = {
        s["id"]: {"id": s["id"], "text": s["text"], "facts": s["facts"], "status": "pending"}
        for s in base_draft["sentences"]
    }
    decision = parse_and_apply_command(
        "reject S4: Unbacked runtime assertion.",
        base_draft,
        sentence_states,
    )
    assert decision["result"]["accepted"] is True
    assert sentence_states["S4"]["status"] == "rejected"

    effective_draft, final_states = replay_decisions(base_draft, [decision])
    assert any(s["id"] == "S4" for s in base_draft["sentences"])
    # S4 must be dropped from effective draft
    assert not any(s["id"] == "S4" for s in effective_draft["sentences"])
    assert len(effective_draft["sentences"]) == len(base_draft["sentences"]) - 1
    # Effective draft must validate against draft.schema.json
    errs = validate(effective_draft)
    assert errs == []


def test_verified_edit_auto_approves(base_snapshot, base_facts, base_draft):
    """An edit with valid citations passes verification and is auto-approved."""
    sentence_states = {
        s["id"]: {"id": s["id"], "text": s["text"], "facts": s["facts"], "status": "pending"}
        for s in base_draft["sentences"]
    }
    # S4 was uncited in draft.r0.json; edit S4 with valid facts F4 and F5 and supported numbers (3)
    edit_cmd = "edit S4: Exactly 3 contributors landed commits. [F4][F5]"
    decision = parse_and_apply_command(
        edit_cmd,
        base_draft,
        sentence_states,
        snapshot=base_snapshot,
        facts_doc=base_facts,
    )
    assert decision["result"]["accepted"] is True
    assert decision["result"]["error"] is None
    assert decision["facts"] == ["F4", "F5"]
    assert sentence_states["S4"]["status"] == "approved"
    assert sentence_states["S4"]["text"] == "Exactly 3 contributors landed commits."
    assert sentence_states["S4"]["facts"] == ["F4", "F5"]


def test_edit_refused_when_no_citation(base_snapshot, base_facts, base_draft):
    """An edit without trailing citations is refused with EDIT_NO_CITATION."""
    sentence_states = {
        s["id"]: {"id": s["id"], "text": s["text"], "facts": s["facts"], "status": "pending"}
        for s in base_draft["sentences"]
    }
    decision = parse_and_apply_command(
        "edit S4: Text without any citations at the end",
        base_draft,
        sentence_states,
        snapshot=base_snapshot,
        facts_doc=base_facts,
    )
    assert decision["result"]["accepted"] is False
    assert decision["result"]["error"] == "EDIT_NO_CITATION"
    assert sentence_states["S4"]["status"] == "pending"


def test_edit_refused_when_verification_fails(base_snapshot, base_facts, base_draft):
    """An edit that fails verification is refused with EDIT_VERIFY_FAILED."""
    sentence_states = {
        s["id"]: {"id": s["id"], "text": s["text"], "facts": s["facts"], "status": "pending"}
        for s in base_draft["sentences"]
    }
    # Cite F1, but text claims an unsupported number (9999)
    decision = parse_and_apply_command(
        "edit S4: Commit added 9999 files into git. [F1]",
        base_draft,
        sentence_states,
        snapshot=base_snapshot,
        facts_doc=base_facts,
    )
    assert decision["result"]["accepted"] is False
    assert decision["result"]["error"] == "EDIT_VERIFY_FAILED"
    assert sentence_states["S4"]["status"] == "pending"


def test_ship_blocked_when_pending_sentences_exist(base_snapshot, base_facts, base_draft):
    """ship is refused with SHIP_BLOCKED when any sentence is still pending."""
    sentence_states = {
        s["id"]: {"id": s["id"], "text": s["text"], "facts": s["facts"], "status": "pending"}
        for s in base_draft["sentences"]
    }
    # Only approve S1, leaving S2, S3, S4 pending
    parse_and_apply_command("approve S1", base_draft, sentence_states)

    decision = parse_and_apply_command(
        "ship",
        base_draft,
        sentence_states,
        snapshot=base_snapshot,
        facts_doc=base_facts,
    )
    assert decision["result"]["accepted"] is False
    assert decision["result"]["error"] == "SHIP_BLOCKED"


def test_ship_blocked_without_verification_context(base_draft):
    """The offline sandbox must not treat missing verifier inputs as success."""
    states = {
        s["id"]: {"id": s["id"], "text": s["text"], "facts": s["facts"], "status": "approved"}
        for s in base_draft["sentences"]
    }
    decision = parse_and_apply_command("ship", base_draft, states)
    assert decision["result"] == {"accepted": False, "error": "SHIP_BLOCKED"}


def test_ship_blocked_when_surviving_sentences_fail_verification(base_snapshot, base_facts, base_draft):
    """ship is refused with SHIP_BLOCKED if surviving non-rejected sentences fail verify."""
    sentence_states = {
        s["id"]: {"id": s["id"], "text": s["text"], "facts": s["facts"], "status": "pending"}
        for s in base_draft["sentences"]
    }
    # Approve all sentences, including S4 which has no citations and will fail verify
    for s in sentence_states.values():
        s["status"] = "approved"

    decision = parse_and_apply_command(
        "ship",
        base_draft,
        sentence_states,
        snapshot=base_snapshot,
        facts_doc=base_facts,
    )
    assert decision["result"]["accepted"] is False
    assert decision["result"]["error"] == "SHIP_BLOCKED"


def test_ship_succeeds_when_all_decided_and_surviving_pass(base_snapshot, base_facts, base_draft):
    """ship succeeds when pending count is 0 and non-rejected sentences verify cleanly."""
    sentence_states = {
        s["id"]: {"id": s["id"], "text": s["text"], "facts": s["facts"], "status": "pending"}
        for s in base_draft["sentences"]
    }
    # Approve valid sentences S1, S2, S3; reject invalid S4
    parse_and_apply_command("approve S1", base_draft, sentence_states)
    parse_and_apply_command("approve S2", base_draft, sentence_states)
    parse_and_apply_command("approve S3", base_draft, sentence_states)
    parse_and_apply_command("reject S4: Uncited claim", base_draft, sentence_states)

    assert not any(s["status"] == "pending" for s in sentence_states.values())

    decision = parse_and_apply_command(
        "ship",
        base_draft,
        sentence_states,
        snapshot=base_snapshot,
        facts_doc=base_facts,
    )
    assert decision["result"]["accepted"] is True
    assert decision["result"]["error"] is None


def _make_decision(
    run_id: str,
    command: str,
    sentence_id: str | None = None,
    *,
    reason: str | None = None,
    text: str | None = None,
    facts: list[str] | None = None,
    accepted: bool = True,
    error: str | None = None,
) -> dict:
    doc = {
        "schema": "studio/decision@1",
        "run_id": run_id,
        "agent": "human",
        "created_at": "2026-11-04T10:16:00Z",
        "user": "@human",
        "draft_revision": 0,
        "command": command,
        "result": {"accepted": accepted, "error": error},
    }
    if sentence_id is not None:
        doc["sentence_id"] = sentence_id
    if reason is not None:
        doc["reason"] = reason
    if text is not None:
        doc["text"] = text
    if facts is not None:
        doc["facts"] = facts
    return doc


def test_replay_approve_then_edit(base_draft):
    """In replay_decisions, an edit after approve overrides the text and facts and stays approved."""
    run_id = base_draft["run_id"]
    decisions = [
        _make_decision(run_id, "approve", "S1"),
        _make_decision(
            run_id,
            "edit",
            "S1",
            text="Saurabh added the verifier CLI in commit 9f3c2a1.",
            facts=["F1"],
        ),
    ]
    effective_draft, final_states = replay_decisions(base_draft, decisions)
    assert final_states["S1"]["status"] == "approved"
    assert final_states["S1"]["text"] == "Saurabh added the verifier CLI in commit 9f3c2a1."
    assert final_states["S1"]["facts"] == ["F1"]
    s1 = next(s for s in effective_draft["sentences"] if s["id"] == "S1")
    assert s1["text"] == "Saurabh added the verifier CLI in commit 9f3c2a1."
    assert s1["facts"] == ["F1"]


def test_replay_approve_then_reject(base_draft):
    """In replay_decisions, a reject after approve marks the sentence rejected and drops it."""
    run_id = base_draft["run_id"]
    decisions = [
        _make_decision(run_id, "approve", "S1"),
        _make_decision(run_id, "reject", "S1", reason="Superseded by later summary."),
    ]
    effective_draft, final_states = replay_decisions(base_draft, decisions)
    assert final_states["S1"]["status"] == "rejected"
    assert final_states["S1"]["reason"] == "Superseded by later summary."
    assert not any(s["id"] == "S1" for s in effective_draft["sentences"])


def test_replay_reject_then_approve(base_draft):
    """In replay_decisions, an approve after reject restores the sentence to approved."""
    run_id = base_draft["run_id"]
    decisions = [
        _make_decision(run_id, "reject", "S1", reason="Mistakenly rejected."),
        _make_decision(run_id, "approve", "S1"),
    ]
    effective_draft, final_states = replay_decisions(base_draft, decisions)
    assert final_states["S1"]["status"] == "approved"
    assert final_states["S1"]["reason"] is None
    assert any(s["id"] == "S1" for s in effective_draft["sentences"])


def test_replay_edit_then_edit(base_draft):
    """In replay_decisions, a second edit overrides the first edit's text and facts."""
    run_id = base_draft["run_id"]
    decisions = [
        _make_decision(
            run_id,
            "edit",
            "S4",
            text="First human rewrite of S4.",
            facts=["F4"],
        ),
        _make_decision(
            run_id,
            "edit",
            "S4",
            text="Exactly 3 contributors landed commits.",
            facts=["F4", "F5"],
        ),
    ]
    effective_draft, final_states = replay_decisions(base_draft, decisions)
    assert final_states["S4"]["status"] == "approved"
    assert final_states["S4"]["text"] == "Exactly 3 contributors landed commits."
    assert final_states["S4"]["facts"] == ["F4", "F5"]
    s4 = next(s for s in effective_draft["sentences"] if s["id"] == "S4")
    assert s4["text"] == "Exactly 3 contributors landed commits."
    assert s4["facts"] == ["F4", "F5"]

