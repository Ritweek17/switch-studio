## Module

<!-- M0 / M1 / M2 / M3a / M3b / M4 -->

Closes #

## What changed

<!-- One or two sentences, in GLOSSARY.md vocabulary. -->

## How to verify

<!-- Exact commands a reviewer runs, e.g. `python -m tools.verify --snapshot ... --facts ... --draft ...` -->

## Checklist

- [ ] Only touched folders my module owns (see `AGENTS.md`)
- [ ] No change to `CONTRACT.md` / `schemas/` (or this is an M0 PR that updates contract, schemas, and examples together)
- [ ] Standard library only (no new dependencies)
- [ ] `python -m schemas.validate --examples` passes
- [ ] `pytest` passes
- [ ] New decision? Added an ADR in `docs/adr/` and a line in `docs/DECISIONS.md`
