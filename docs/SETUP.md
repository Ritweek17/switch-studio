# Setup Guide

> **Owner:** Ritweek (`@Ritweek17`, M1). Fill in the Switch runtime and `#studio` room instructions as M1 lands.

## Prerequisites

- **Python 3.11+** (standard library only for all runtime code and tools)
- **git** (for `tools.snapshot` on local clones)
- **pytest** (optional, dev-only for running `/tests`)

Verify the contract schemas and examples work out of the box:

```bash
python3 -m schemas.validate --examples
```

## Environment Variables

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Purpose | Default |
|---|---|---|
| `STUDIO_MODE` | `fixture` (offline) or `live` (local git clone) | `fixture` |
| `STUDIO_REPO_PATH` | Path to the local git repository analyzed in `live` mode | `fixtures/demo-repo` |
| `STUDIO_SNAPSHOT_PATH` | Path to the saved `studio/snapshot@1` JSON in `fixture` mode | `fixtures/demo-repo.snapshot.json` |
| `STUDIO_RANGE` | Git revision range for the run | `HEAD~50..HEAD` |
| `STUDIO_FORMAT` | Output format (`howiswitch` or `blog_section`) | `howiswitch` |
| `STUDIO_RUNS_DIR` | Scratch folder for run artifacts (gitignored) | `runs` |
| `SWITCH_BASE_URL` | Switch server URL (M1) | — |
| `SWITCH_TOKEN` | Auth token for the Switch `#studio` room bridge (M1) | — |
| `SWITCH_ROOM` | Shared room name | `#studio` |
| `OPENAI_API_KEY` | API key for Codex/LLM agent runs (M2/M3a) | — |

## Switch Runtime & `#studio` Room Setup (M1 — TODO)

<!-- Ritweek (M1): document how to start the Switch bridge, register Scout/Writer/Editor, and connect the 4 humans to #studio. -->

1. Provision the `#studio` room in Switch.
2. Register the three agent identities (`scout`, `writer`, `editor`) and the **infra bot**.
3. Start the infra bot (`python3 -m infra.bot`) so room messages and human commands sync with `runs/<run_id>/`.
