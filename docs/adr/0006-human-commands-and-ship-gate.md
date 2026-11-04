# 0006. Human commands: reject drops, verified edit approves, ship is gated

- **Status:** Accepted · **Date:** 2026-11-04 · **Owner:** Vivek (M0)

## Context
Four humans approve in the room during a live demo. Fewer verbs means fewer mistakes; agents must never silently remove content.

## Decision
- Commands: `approve S3`, `approve all`, `reject S3: <reason>`, `edit S3: <text> [Fn]`, `ship`. Any one human suffices.
- `reject` drops the sentence from the final output (no separate `drop`); `edit` covers rewrites.
- An `edit` is re-verified; passing auto-approves it, failing or uncited edits are refused.
- `ship` requires no `pending` sentences and a passing verifier on the non-rejected ones; it writes `runs/<run_id>/final.md` and posts nothing.
- Decisions are appended to `decisions.jsonl`; state is derived by replay.
- Editor ↔ Writer revise at most 2 loops before humans take over; only humans remove sentences.

## Consequences
- The room log is the audit trail; replay is deterministic and testable offline.
- No auto-posting to X, by design.
