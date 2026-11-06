#!/usr/bin/env python3
"""Run the offline fixture workflow, then optionally start the live Room bot."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.editor.agent import run_editor  # noqa: E402
from agents.scout.agent import run_scout  # noqa: E402
from agents.writer.agent import run_writer  # noqa: E402
from infra.bot import RunStore, load_env_file, process_room_message, utc_now  # noqa: E402


class DemoError(RuntimeError):
    pass


def run_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:4]}"


def require_tools(modules: tuple[str, ...]) -> None:
    missing = []
    for name in modules:
        try:
            available = importlib.util.find_spec(name) is not None
        except ModuleNotFoundError:
            available = False
        if not available:
            missing.append(name)
    if missing:
        raise DemoError(
            "required project modules have not landed on this branch yet: "
            + ", ".join(missing)
            + ". The demo is wired to the CONTRACT.md CLI and can run after those modules are merged."
        )


def checked(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode:
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        raise DemoError(f"command failed with exit {result.returncode}: {' '.join(command)}")
    return result


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_example(name: str) -> dict[str, Any]:
    path = ROOT / "schemas" / "examples" / name
    return json.loads(path.read_text(encoding="utf-8"))


def live_range(repo_path: Path) -> str:
    configured = os.getenv("STUDIO_RANGE", "HEAD~50..HEAD").strip()
    if configured != "HEAD~50..HEAD":
        return configured
    history = subprocess.run(
        ["git", "-C", str(repo_path), "rev-list", "--count", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if history.returncode != 0:
        raise DemoError(f"cannot inspect git history in {repo_path}: {history.stderr.strip()}")
    # The stock range needs 51 ancestors to resolve. For a smaller local clone,
    # HEAD means all commits currently available and keeps the one-run range
    # identical for snapshot, facts, verifier, and grading.
    if int(history.stdout.strip()) <= 50:
        return "HEAD"
    return configured


def fixture_stage() -> Path:
    require_tools(("tools.verify", "tools.apply", "tools.render", "tools.grade"))
    identifier = run_id("demo-fixture")
    run_dir = ROOT / "runs" / identifier
    run_dir.mkdir(parents=True, exist_ok=False)

    snapshot = read_example("snapshot.json")

    run = {
        "schema": "studio/run@1",
        "run_id": identifier,
        "agent": "tool",
        "created_at": utc_now(),
        "mode": "fixture",
        "repo_path": "schemas/examples/snapshot.json",
        "range": snapshot["range"],
        "snapshot_path": f"runs/{identifier}/snapshot.json",
        "format": os.getenv("STUDIO_FORMAT", "howiswitch"),
    }
    write_json(run_dir / "run.json", run)
    write_json(run_dir / "snapshot.json", snapshot)
    store = RunStore(ROOT, identifier)

    # Route each real M2 document through the same room parser as live messages.
    def post_document(summary: str, document: dict[str, Any]) -> None:
        body = f"{summary}\n\n```json\n{json.dumps(document, indent=2)}\n```"
        reports = process_room_message(store, body, "fixture-agent", repo_root=ROOT)
        for report in reports:
            print(f"  {report}")

    facts = run_scout(snapshot, run_id=identifier, snapshot_path=f"runs/{identifier}/snapshot.json")
    post_document("Scout's verified facts for the fixture run.", facts)

    draft0 = run_writer(facts, fmt=run["format"])
    # Seed an intentionally unverified quality claim so Editor rejects it and
    # Writer demonstrates the contract's revision loop before human review.
    draft0["sentences"][0]["text"] += " The change is zero bugs."
    post_document("Writer's first draft r0 for the fixture run.", draft0)

    review0 = run_editor(
        snapshot, facts, draft0, loop=1,
        snapshot_path=run_dir / "snapshot.json", facts_path=run_dir / "facts.json",
        draft_path=run_dir / "draft.r0.json", verdict_path=run_dir / "verdict.r0.json",
    )
    post_document("Editor verified r0 and handed rejected sentences to Writer.", review0)

    draft1 = run_writer(facts, previous_draft=draft0, review=review0)
    post_document("Writer revised rejected sentences in r1.", draft1)
    review1 = run_editor(
        snapshot, facts, draft1, loop=2,
        snapshot_path=run_dir / "snapshot.json", facts_path=run_dir / "facts.json",
        draft_path=run_dir / "draft.r1.json", verdict_path=run_dir / "verdict.r1.json",
    )
    post_document("Editor verified r1 and handed off to humans.", review1)

    if review0["handoff"] != "writer" or review1["handoff"] != "humans":
        raise DemoError("fixture must demonstrate the Writer revision and human handoff")

    print(f"\nOffline fixture run: {identifier}")
    print("Room commands:")
    commands = (
        ("approve S1", "@judge"),
        ("reject S2: Remove the unsupported phrase 'tests included'.", "@judge"),
        ("edit S4: Saurabh added the claim verifier CLI in commit 9f3c2a1. [F1]", "@judge"),
        ("approve all", "@judge"),
        ("ship", "@judge"),
    )
    for command, user in commands:
        decision = process_room_message(store, command, user, repo_root=ROOT)
        print(f"  > {command}")
        for report in decision:
            print(f"    {report}")

    final_path = run_dir / "final.md"
    if not final_path.exists():
        raise DemoError(f"fixture workflow did not ship a final document: {final_path}")

    grade_command = [
        sys.executable, "-m", "tools.grade",
        "--snapshot", str(run_dir / "snapshot.json"),
        "--input", "baseline=demo/baseline.md",
        "--input", f"studio=runs/{identifier}/final.md",
        "--out", str(run_dir / "grade.json"),
    ]
    try:
        checked(grade_command)
        grade_doc = json.loads((run_dir / "grade.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoError("tools.grade did not write a valid grade.json") from exc
    results = {item["label"]: item for item in grade_doc.get("results", [])}
    baseline = results.get("baseline")
    studio = results.get("studio")
    if not baseline or not studio:
        raise DemoError("grade output must include baseline and studio results")
    print("\nBefore / after score")
    print(f"  baseline: {baseline['supported_pct']:.1f}% supported; {baseline['numbers_unsupported']} unsupported numbers")
    print(f"  studio:   {studio['supported_pct']:.1f}% supported; {studio['numbers_unsupported']} unsupported numbers")
    print(f"  verified result: {studio['supported_pct']:.1f}%")
    if studio["supported_pct"] != 100.0:
        raise DemoError("the fixture's shipped Studio output did not grade at 100%")
    if baseline["supported_pct"] >= studio["supported_pct"]:
        print("  The baseline did not score lower; inspect demo/baseline.md before presenting.")
    print(f"  final: {final_path.relative_to(ROOT)}")
    return run_dir


def live_stage() -> None:
    require_tools(("tools.snapshot",))
    load_env_file(ROOT / ".env")
    missing_switch = [
        name for name in ("SWITCH_BASE_URL", "SWITCH_TOKEN", "SWITCH_AGENT_ID")
        if not os.getenv(name, "").strip()
    ]
    if missing_switch:
        raise DemoError(
            "live stage needs Switch credentials from docs/SETUP.md; missing "
            + ", ".join(missing_switch)
            + ". The offline fixture and grading stage completed successfully."
        )
    mode = os.getenv("STUDIO_MODE", "live").strip()
    if mode != "live":
        print("STUDIO_MODE is not live; using the local checkout as the live demo repository.")
    repo_path = Path(os.getenv("STUDIO_REPO_PATH", str(ROOT))).expanduser()
    if not repo_path.is_absolute():
        repo_path = ROOT / repo_path
    repo_path = repo_path.resolve()
    if not (repo_path / ".git").exists():
        raise DemoError(f"STUDIO_REPO_PATH is not a local git repository: {repo_path}")
    identifier = run_id("demo-live")
    run_dir = ROOT / "runs" / identifier
    run_dir.mkdir(parents=True, exist_ok=False)
    revision_range = live_range(repo_path)
    snapshot_path = run_dir / "snapshot.json"
    checked([
        sys.executable, "-m", "tools.snapshot",
        "--repo", str(repo_path),
        "--range", revision_range,
        "--out", str(snapshot_path),
    ])
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    run = {
        "schema": "studio/run@1",
        "run_id": identifier,
        "agent": "tool",
        "created_at": utc_now(),
        "mode": "live",
        "repo_path": str(repo_path),
        "range": revision_range,
        "snapshot_path": f"runs/{identifier}/snapshot.json",
        "format": os.getenv("STUDIO_FORMAT", "howiswitch"),
    }
    write_json(run_dir / "run.json", run)

    print(f"\nLive run: {identifier}")
    print(f"Snapshot: {snapshot.get('repo', {}).get('name', repo_path.name)} {revision_range}")
    print("In #studio, ask Scout to read this snapshot and post facts@1, then ask")
    print("Writer to draft from those facts and Editor to verify/review. Use the")
    print("same human commands as the fixture stage; the bot will save final.md locally.")
    command = [sys.executable, "-m", "infra.bot", "--repo-root", str(ROOT), "--run-id", identifier]
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except KeyboardInterrupt:
        print("\nLive infra bot stopped.")
    except subprocess.CalledProcessError as exc:
        raise DemoError(f"live infra bot exited with status {exc.returncode}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--offline-only", action="store_true", help="run and grade the fixture flow, then exit")
    group.add_argument("--live-only", action="store_true", help="skip the fixture flow and start the live bot")
    args = parser.parse_args(argv)
    try:
        if not args.live_only:
            fixture_stage()
        if not args.offline_only:
            live_stage()
        return 0
    except (DemoError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"demo: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
