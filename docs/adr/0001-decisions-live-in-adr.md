# 0001. Decisions live in `docs/adr/`; `DECISIONS.md` is an index

- **Status:** Accepted
- **Date:** 2026-11-04
- **Owner:** Vivek (M0)

## Context

The M0 plan listed `docs/DECISIONS.md`, while the engineering skills (`/grill-with-docs`, `/domain-modeling`, `/improve-codebase-architecture`) read and write decisions as numbered ADRs in `docs/adr/`. Keeping both as sources would drift.

## Decision

- Every decision is one file: `docs/adr/NNNN-<slug>.md`.
- `docs/DECISIONS.md` is a human-scannable index of ADRs only, updated in the same commit as each new ADR.
- `CONTRACT.md` holds interfaces and links to ADRs for rationale.

## Consequences

- Skills and humans read the same source; no duplicate decision text.
- Adding a decision costs one extra index line.
