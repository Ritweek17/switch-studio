"""Tests for deterministic verifier error codes and safeguard rules (CONTRACT.md §4, §6)."""

import json
from pathlib import Path

import pytest
from sandbox.verifier import verify_draft, verify_facts, verify_sentence
from schemas.validate import load_document, validate

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


def test_example_verdict_matches_output(base_snapshot, base_facts, base_draft):
    """Running verify on example documents must match the example verdict."""
    verdict = verify_draft(base_snapshot, base_facts, base_draft)
    expected_verdict = load_document(EXAMPLES_DIR / "verdict.r0.json")

    assert verdict["schema"] == "studio/verdict@1"
    assert verdict["pass"] is False
    assert len(verdict["facts"]) == len(expected_verdict["facts"])
    assert len(verdict["sentences"]) == len(expected_verdict["sentences"])

    for vf, ef in zip(verdict["facts"], expected_verdict["facts"]):
        assert vf["id"] == ef["id"]
        assert vf["ok"] == ef["ok"]

    for vs, es in zip(verdict["sentences"], expected_verdict["sentences"]):
        assert vs["id"] == es["id"]
        assert vs["ok"] == es["ok"]
        assert vs["errors"] == es["errors"]


def test_verifier_triggers_no_citation(base_snapshot, base_facts):
    """Sentences with empty facts[] must trigger NO_CITATION."""
    draft = {
        "schema": "studio/draft@1",
        "run_id": "demo-001",
        "agent": "writer",
        "created_at": "2026-11-04T10:16:00Z",
        "revision": 0,
        "format": "howiswitch",
        "sentences": [
            {
                "id": "S1",
                "text": "This sentence has absolutely no citations.",
                "facts": [],
            }
        ],
    }
    verdict = verify_draft(base_snapshot, base_facts, draft)
    assert verdict["pass"] is False
    s1 = verdict["sentences"][0]
    assert s1["ok"] is False
    assert "NO_CITATION" in s1["errors"]


def test_verifier_triggers_unknown_fact(base_snapshot, base_facts):
    """Citing a fact ID not present in facts doc must trigger UNKNOWN_FACT."""
    draft = {
        "schema": "studio/draft@1",
        "run_id": "demo-001",
        "agent": "writer",
        "created_at": "2026-11-04T10:16:00Z",
        "revision": 0,
        "format": "howiswitch",
        "sentences": [
            {
                "id": "S1",
                "text": "Saurabh shipped a claim verifier.",
                "facts": ["F999"],
            }
        ],
    }
    verdict = verify_draft(base_snapshot, base_facts, draft)
    assert verdict["pass"] is False
    s1 = verdict["sentences"][0]
    assert s1["ok"] is False
    assert "UNKNOWN_FACT" in s1["errors"]


def test_verifier_triggers_fact_invalid(base_snapshot, base_facts):
    """Citing a fact that fails verification must trigger FACT_INVALID."""
    # Modify F3 with an incorrect lines_added count
    facts = json.loads(json.dumps(base_facts))
    facts["facts"][2]["value"] = 999999  # was 246

    draft = {
        "schema": "studio/draft@1",
        "run_id": "demo-001",
        "agent": "writer",
        "created_at": "2026-11-04T10:16:00Z",
        "revision": 0,
        "format": "howiswitch",
        "sentences": [
            {
                "id": "S1",
                "text": "That commit added 999999 lines.",
                "facts": ["F3"],
            }
        ],
    }
    verdict = verify_draft(base_snapshot, facts, draft)
    assert verdict["pass"] is False
    # F3 should fail its own check
    f3_res = next(f for f in verdict["facts"] if f["id"] == "F3")
    assert f3_res["ok"] is False
    # S1 should trigger FACT_INVALID
    s1 = verdict["sentences"][0]
    assert "FACT_INVALID" in s1["errors"]


def test_verifier_triggers_number_unsupported(base_snapshot, base_facts):
    """Unbacked number tokens in prose must trigger NUMBER_UNSUPPORTED."""
    draft = {
        "schema": "studio/draft@1",
        "run_id": "demo-001",
        "agent": "writer",
        "created_at": "2026-11-04T10:16:00Z",
        "revision": 0,
        "format": "howiswitch",
        "sentences": [
            {
                "id": "S1",
                "text": "Saurabh shipped code in 45 minutes with 500 lines.",
                "facts": ["F1", "F2"],
            }
        ],
    }
    verdict = verify_draft(base_snapshot, base_facts, draft)
    assert verdict["pass"] is False
    s1 = verdict["sentences"][0]
    assert "NUMBER_UNSUPPORTED" in s1["errors"]


def test_supported_count_does_not_authorize_percentage(base_snapshot, base_facts):
    """A raw count cannot be repurposed as the derived claim "246%"."""
    draft = {
        "schema": "studio/draft@1", "run_id": "demo-001", "agent": "writer",
        "created_at": "2026-11-04T10:16:00Z", "revision": 0, "format": "howiswitch",
        "sentences": [{"id": "S1", "text": "That commit added 246% more lines.", "facts": ["F3"]}],
    }
    sentence = verify_draft(base_snapshot, base_facts, draft)["sentences"][0]
    assert "NUMBER_UNSUPPORTED" in sentence["errors"]


def test_verifier_triggers_hash_unsupported(base_snapshot, base_facts):
    """Hex hashes not matching cited commit prefixes must trigger HASH_UNSUPPORTED."""
    draft = {
        "schema": "studio/draft@1",
        "run_id": "demo-001",
        "agent": "writer",
        "created_at": "2026-11-04T10:16:00Z",
        "revision": 0,
        "format": "howiswitch",
        "sentences": [
            {
                "id": "S1",
                "text": "Commit deadbeef1234 fixed all bugs.",
                "facts": ["F1", "F2"],
            }
        ],
    }
    verdict = verify_draft(base_snapshot, base_facts, draft)
    assert verdict["pass"] is False
    s1 = verdict["sentences"][0]
    assert "HASH_UNSUPPORTED" in s1["errors"]


def test_safeguards_do_not_trigger_number_unsupported(base_snapshot, base_facts):
    """Safeguards: [F2], sentence ID S1, version string v0.1, cited commit date,

    and tool paths like tools/verify2.py must NOT trigger NUMBER_UNSUPPORTED.
    """
    draft = {
        "schema": "studio/draft@1",
        "run_id": "demo-001",
        "agent": "writer",
        "created_at": "2026-11-04T10:16:00Z",
        "revision": 0,
        "format": "howiswitch",
        "sentences": [
            {
                "id": "S1",
                "text": "On 2026-11-04 Saurabh shipped tools/verify2.py in v0.1 as described in S1 [F2].",
                "facts": ["F1", "F2"],
            }
        ],
    }
    # F1 has commit date 2026-11-04T08:12:00Z, so 2026-11-04 is a cited commit date.
    # F1 subject is "Add claim verifier CLI (#12)", so 12 is supported if present, but no raw numbers here.
    # [F2], S1, v0.1, tools/verify2.py must all be ignored by the number checker.
    verdict = verify_draft(base_snapshot, base_facts, draft)
    s1 = verdict["sentences"][0]
    assert "NUMBER_UNSUPPORTED" not in s1["errors"]
    assert s1["ok"] is True
    assert s1["errors"] == []


def test_valid_hash_prefix_is_accepted(base_snapshot, base_facts):
    """A valid 7-char hex prefix of a cited commit must not trigger HASH_UNSUPPORTED."""
    # Commit for F1 starts with 9f3c2a1
    draft = {
        "schema": "studio/draft@1",
        "run_id": "demo-001",
        "agent": "writer",
        "created_at": "2026-11-04T10:16:00Z",
        "revision": 0,
        "format": "howiswitch",
        "sentences": [
            {
                "id": "S1",
                "text": "Commit 9f3c2a1 added the claim verifier in tools/verify.py.",
                "facts": ["F1", "F2"],
            }
        ],
    }
    verdict = verify_draft(base_snapshot, base_facts, draft)
    s1 = verdict["sentences"][0]
    assert "HASH_UNSUPPORTED" not in s1["errors"]
    assert "NUMBER_UNSUPPORTED" not in s1["errors"]
    assert s1["ok"] is True


def test_fact_verification_rules(base_snapshot):
    """Test fact verifier across commit, file, and count rules."""
    # 1. Invalid commit fact (subject mismatch)
    bad_commit_facts = {
        "facts": [
            {
                "id": "F1",
                "kind": "commit",
                "commit": "9f3c2a1e7b4d5c6a8e9f0a1b2c3d4e5f6a7b8c9d",
                "subject": "Wrong subject line",
                "author_name": "Saurabh",
                "date": "2026-11-04T08:12:00Z",
                "statement": "...",
            }
        ]
    }
    res = verify_facts(base_snapshot, bad_commit_facts)
    assert res[0]["ok"] is False

    # 2. Invalid file fact (path not in commit)
    bad_file_facts = {
        "facts": [
            {
                "id": "F2",
                "kind": "file",
                "commit": "9f3c2a1e7b4d5c6a8e9f0a1b2c3d4e5f6a7b8c9d",
                "path": "nonexistent/file.py",
                "status": "A",
                "statement": "...",
            }
        ]
    }
    res = verify_facts(base_snapshot, bad_file_facts)
    assert res[0]["ok"] is False

    # 3. Invalid count fact (wrong metric or scope)
    bad_count_facts = {
        "facts": [
            {
                "id": "F3",
                "kind": "count",
                "commit": base_snapshot["head"],
                "metric": "invalid_metric",
                "scope": "run",
                "value": 10,
                "statement": "...",
            },
            {
                "id": "F4",
                "kind": "count",
                "commit": base_snapshot["head"],
                "metric": "commits",
                "scope": "commit",  # invalid: commits metric only allows scope 'run'
                "value": 1,
                "statement": "...",
            },
            {
                "id": "F5",
                "kind": "count",
                "commit": base_snapshot["head"],
                "metric": "contributors",
                "scope": "run",
                "author_email": "saurabh@example.com",  # invalid: author_email never allowed for contributors
                "value": 1,
                "statement": "...",
            },
        ]
    }
    res = verify_facts(base_snapshot, bad_count_facts)
    assert all(r["ok"] is False for r in res)


def test_number_from_subject_and_path_is_supported(base_snapshot, base_facts):
    """Numbers occurring in cited commit subjects (e.g. #12) or file paths are supported."""
    # F1 has subject "Add claim verifier CLI (#12)" -> 12 is supported
    draft = {
        "schema": "studio/draft@1",
        "run_id": "demo-001",
        "agent": "writer",
        "created_at": "2026-11-04T10:16:00Z",
        "revision": 0,
        "format": "howiswitch",
        "sentences": [
            {
                "id": "S1",
                "text": "Saurabh addressed issue 12 in tools/verify.py.",
                "facts": ["F1", "F2"],
            }
        ],
    }
    verdict = verify_draft(base_snapshot, base_facts, draft)
    s1 = verdict["sentences"][0]
    assert "NUMBER_UNSUPPORTED" not in s1["errors"]
    assert s1["ok"] is True
