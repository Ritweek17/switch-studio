# 0004. Facts and drafts are structured; citations live only in `facts[]`

- **Status:** Accepted · **Date:** 2026-11-04 · **Owner:** Vivek (M0)

## Context
The core rule ("no sentence survives without a verifiable reference") needs code to know exactly which sentence cites which fact. Splitting free prose into sentences is unreliable.

## Decision
- Scout emits numbered facts `F1…Fn` per `run_id`, of three kinds: `commit`, `file`, `count`. Every fact carries a full commit hash.
- Writer emits `sentences: [{id: "S1", text, facts: ["F3"]}]`; humans and the Editor address sentences by ID.
- Citations live only in `facts[]`; `text` never contains `[F…]` tags. Tags are added by the renderer.

## Consequences
- No heuristic sentence splitting except in the grader (which must handle the uncited baseline).
- `[F2]` can never be misread as the number 2 inside text.
