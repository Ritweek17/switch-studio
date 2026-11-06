"""Parity tests between `verify_draft_locally` and `tools.verify` across golden cases."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agents.editor.agent import verify_draft_locally
from schemas.validate import validate

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_verify_cases.json"


def _load_golden_cases() -> list[dict[str, Any]]:
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return data["cases"]


def _has_tools_verify() -> bool:
    try:
        return importlib.util.find_spec("tools.verify") is not None
    except ModuleNotFoundError:
        return False


def _normalize_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    """Extract deterministic comparison fields from a studio/verdict@1 document."""
    return {
        "pass": verdict["pass"],
        "facts": [{"id": f["id"], "ok": f["ok"]} for f in verdict["facts"]],
        "sentences": [
            {"id": s["id"], "ok": s["ok"], "errors": list(s["errors"])}
            for s in verdict["sentences"]
        ],
        "summary": dict(verdict["summary"]),
    }


@pytest.mark.parametrize(
    "case",
    _load_golden_cases(),
    ids=lambda c: c["name"],
)
def test_verify_draft_locally_golden_cases(case: dict[str, Any]) -> None:
    verdict = verify_draft_locally(
        case["snapshot"],
        case["facts"],
        case["draft"],
        created_at="2026-11-04T10:16:05Z",
    )
    assert validate(verdict) == []
    assert _normalize_verdict(verdict) == case["expected"]


@pytest.mark.parametrize(
    "case",
    _load_golden_cases(),
    ids=lambda c: c["name"],
)
def test_tools_verify_parity_with_local_verifier(
    case: dict[str, Any],
    tmp_path: Path,
) -> None:
    if not _has_tools_verify():
        pytest.skip("tools.verify is not present in this branch yet")

    snap_file = tmp_path / "snapshot.json"
    facts_file = tmp_path / "facts.json"
    draft_file = tmp_path / "draft.json"
    verdict_file = tmp_path / "verdict.json"

    snap_file.write_text(json.dumps(case["snapshot"], indent=2), encoding="utf-8")
    facts_file.write_text(json.dumps(case["facts"], indent=2), encoding="utf-8")
    draft_file.write_text(json.dumps(case["draft"], indent=2), encoding="utf-8")

    local_verdict = verify_draft_locally(
        case["snapshot"],
        case["facts"],
        case["draft"],
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.verify",
            "--snapshot",
            str(snap_file),
            "--facts",
            str(facts_file),
            "--draft",
            str(draft_file),
            "--out",
            str(verdict_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode in (0, 1), f"tools.verify failed: {proc.stderr}"
    tool_verdict = json.loads(verdict_file.read_text(encoding="utf-8"))
    assert validate(tool_verdict) == []
    assert _normalize_verdict(tool_verdict) == _normalize_verdict(local_verdict)
    assert _normalize_verdict(tool_verdict) == case["expected"]
