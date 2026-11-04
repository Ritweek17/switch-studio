"""Tests for schemas.validate and load_document."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from schemas.validate import DocumentError, load_document, validate

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "schemas" / "examples"
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def test_validate_all_example_documents():
    """All 8 schema example documents must validate with zero errors."""
    example_files = list(EXAMPLES_DIR.glob("*.json"))
    assert len(example_files) == 8, f"Expected 8 example files, found {len(example_files)}"
    for f in example_files:
        doc = load_document(f)
        errors = validate(doc)
        assert errors == [], f"Validation failed for {f.name}: {errors}"


def test_validate_demo_repo_fixture():
    """demo-repo.snapshot.json fixture must validate cleanly."""
    fixture_file = FIXTURES_DIR / "demo-repo.snapshot.json"
    assert fixture_file.exists()
    doc = load_document(fixture_file)
    errors = validate(doc)
    assert errors == []
    assert doc["schema"] == "studio/snapshot@1"
    assert len(doc["commits"]) >= 3


def test_validate_rejects_non_dict_or_missing_schema():
    assert validate("not a dict") != []
    assert validate(123) != []
    assert validate([]) != []
    assert validate({}) != []
    assert validate({"schema": 123}) != []
    assert validate({"schema": "studio/unknown@1"}) != []


def test_load_document_rejects_malformed_json(tmp_path: Path):
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"schema": "studio/run@1",', encoding="utf-8")
    with pytest.raises(DocumentError) as exc_info:
        load_document(malformed)
    assert "Expecting" in str(exc_info.value)


def test_validate_rejects_missing_required_fields():
    doc = json.loads((EXAMPLES_DIR / "run.json").read_text())
    del doc["run_id"]
    errors = validate(doc)
    assert any("missing required 'run_id'" in e for e in errors)


def test_validate_rejects_unexpected_properties():
    doc = json.loads((EXAMPLES_DIR / "run.json").read_text())
    doc["unexpected_extra_field"] = "surprise"
    errors = validate(doc)
    assert any("unexpected property 'unexpected_extra_field'" in e for e in errors)


def test_validate_rejects_pattern_mismatches():
    # Bad run_id pattern
    doc = json.loads((EXAMPLES_DIR / "run.json").read_text())
    doc["run_id"] = "UPPERCASE_NOT_ALLOWED"
    errors = validate(doc)
    assert any("run_id" in e for e in errors)

    # Bad timestamp pattern
    doc["run_id"] = "valid-run-01"
    doc["created_at"] = "2026/11/04 10:14:00"
    errors = validate(doc)
    assert any("created_at" in e for e in errors)


def test_validate_rejects_enum_violations():
    doc = json.loads((EXAMPLES_DIR / "run.json").read_text())
    doc["mode"] = "invalid_mode"
    errors = validate(doc)
    assert any("mode" in e for e in errors)

    doc["mode"] = "fixture"
    doc["format"] = "tweet"  # Not in ["howiswitch", "blog_section"]
    errors = validate(doc)
    assert any("format" in e for e in errors)


def test_validate_rejects_snapshot_malformed_commits():
    doc = json.loads((EXAMPLES_DIR / "snapshot.json").read_text())
    # Bad commit hash length / pattern
    doc["commits"][0]["hash"] = "tooshort"
    errors = validate(doc)
    assert any("hash" in e for e in errors)

    # Bad file status
    doc["commits"][0]["hash"] = "9f3c2a1e7b4d5c6a8e9f0a1b2c3d4e5f6a7b8c9d"
    doc["commits"][0]["files"][0]["status"] = "Z"
    errors = validate(doc)
    assert any("status" in e for e in errors)


def test_validate_rejects_negative_numbers():
    doc = json.loads((EXAMPLES_DIR / "snapshot.json").read_text())
    doc["commits"][0]["files"][0]["added"] = -5
    errors = validate(doc)
    assert any("added" in e for e in errors)


def test_payload_ref_resolution_success(tmp_path: Path):
    """load_document correctly resolves payload_ref with valid sha256."""
    target_payload = json.loads((EXAMPLES_DIR / "facts.json").read_text())
    payload_bytes = json.dumps(target_payload, indent=2).encode("utf-8")
    payload_sha = hashlib.sha256(payload_bytes).hexdigest()

    runs_dir = tmp_path / "runs" / "demo-001"
    runs_dir.mkdir(parents=True)
    payload_file = runs_dir / "facts.json"
    payload_file.write_bytes(payload_bytes)

    envelope = {
        "schema": "studio/facts@1",
        "run_id": "demo-001",
        "agent": "scout",
        "created_at": "2026-11-04T10:15:00Z",
        "payload_ref": {
            "path": "runs/demo-001/facts.json",
            "sha256": payload_sha,
        },
    }
    # Validate the envelope itself
    envelope_errors = validate(envelope)
    assert envelope_errors == []

    envelope_file = tmp_path / "envelope.json"
    envelope_file.write_text(json.dumps(envelope))

    loaded = load_document(envelope_file, base_dir=tmp_path)
    assert loaded["schema"] == "studio/facts@1"
    assert loaded["run_id"] == "demo-001"
    assert len(loaded["facts"]) == len(target_payload["facts"])


def test_payload_ref_sha256_mismatch_raises(tmp_path: Path):
    """load_document rejects payload_ref if sha256 does not match file bytes."""
    payload_file = tmp_path / "facts.json"
    payload_file.write_text(json.dumps({"schema": "studio/facts@1"}))

    envelope = {
        "schema": "studio/facts@1",
        "run_id": "demo-001",
        "agent": "scout",
        "created_at": "2026-11-04T10:15:00Z",
        "payload_ref": {
            "path": "facts.json",
            "sha256": "0" * 64,  # intentionally wrong sha256
        },
    }
    envelope_file = tmp_path / "envelope.json"
    envelope_file.write_text(json.dumps(envelope))

    with pytest.raises(DocumentError) as exc_info:
        load_document(envelope_file, base_dir=tmp_path)
    assert "mismatch" in str(exc_info.value)


def test_payload_ref_missing_file_raises(tmp_path: Path):
    """load_document rejects payload_ref if target path does not exist."""
    envelope = {
        "schema": "studio/facts@1",
        "run_id": "demo-001",
        "agent": "scout",
        "created_at": "2026-11-04T10:15:00Z",
        "payload_ref": {
            "path": "nonexistent.json",
            "sha256": "a" * 64,
        },
    }
    envelope_file = tmp_path / "envelope.json"
    envelope_file.write_text(json.dumps(envelope))

    with pytest.raises(DocumentError):
        load_document(envelope_file, base_dir=tmp_path)


def test_payload_ref_schema_mismatch_raises(tmp_path: Path):
    """load_document rejects payload_ref if payload schema differs from envelope."""
    payload_doc = json.loads((EXAMPLES_DIR / "run.json").read_text())
    payload_bytes = json.dumps(payload_doc).encode("utf-8")
    payload_sha = hashlib.sha256(payload_bytes).hexdigest()

    payload_file = tmp_path / "run.json"
    payload_file.write_bytes(payload_bytes)

    envelope = {
        "schema": "studio/facts@1",  # Envelope claims facts@1
        "run_id": "demo-001",
        "agent": "scout",
        "created_at": "2026-11-04T10:15:00Z",
        "payload_ref": {
            "path": "run.json",  # But payload is run@1
            "sha256": payload_sha,
        },
    }
    envelope_file = tmp_path / "envelope.json"
    envelope_file.write_text(json.dumps(envelope))

    with pytest.raises(DocumentError) as exc_info:
        load_document(envelope_file, base_dir=tmp_path)
    assert "schema 'studio/run@1' != envelope 'studio/facts@1'" in str(exc_info.value)


def test_payload_ref_invalid_envelope_shape():
    """validate() rejects malformed overflow envelopes."""
    bad_ref = {
        "schema": "studio/facts@1",
        "run_id": "demo-001",
        "agent": "scout",
        "created_at": "2026-11-04T10:15:00Z",
        "payload_ref": {
            "path": "facts.json",
            "sha256": "not-64-hex",
        },
    }
    errors = validate(bad_ref)
    assert any("payload_ref" in e for e in errors)

