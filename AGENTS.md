# AGENTS.md

Switch Studio: 4 humans + 3 agents (Scout, Writer, Editor) in the `#studio` room turn repo activity into a `#HowISwitch` post where every sentence cites a fact that deterministic code verifies.

**Before any change, read [`CONTRACT.md`](CONTRACT.md)** (interfaces and rules) and [`GLOSSARY.md`](GLOSSARY.md) (vocabulary).

## Folder ownership

One owner per folder. Edit only folders your module owns; `.github/CODEOWNERS` enforces review.

| Module | Owner | Folders |
|---|---|---|
| M0 Foundation | Vivek | root files, `CONTRACT.md`, `schemas/`, `docs/` (except below) |
| M1 Switch Runtime + M4 Integration/Demo | Ritweek | `infra/`, `demo/`, `docs/SETUP.md` |
| M2 Agent Logic + Prompts | Vivek | `agents/<name>/` |
| M3a Fixtures + Sandbox + Tests | Uday | `fixtures/`, `sandbox/`, `tests/`, `docs/NOT-BUILT-YET.md`, README test section |
| M3b Deterministic Tools | Saurabh | `tools/` |

## Rules

- **Contract changes go through M0.** Only Vivek edits `CONTRACT.md` and `schemas/`. Need a change? Open an issue labelled `needs-triage` describing it; build against the current contract meanwhile.
- **Python 3.11+, standard library only.** `pytest` is the sole dev dependency.
- **Tools stay offline and read-only:** no network, never write to the analyzed repo, JSON to stdout.
- **`runs/` is scratch:** write freely, never commit.
- **Validate before a PR:** `python -m schemas.validate --examples` plus any documents your change produces.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `Ritweek17/switch-studio` (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one root `GLOSSARY.md` plus `docs/adr/`. Decisions are recorded **only** as ADRs in `docs/adr/`; `docs/DECISIONS.md` is an index of those ADRs, never a second source. See `docs/agents/domain.md`.
