# 0002. Python 3.11+, standard library only

- **Status:** Accepted · **Date:** 2026-11-04 · **Owner:** Vivek (M0)

## Context
Four people, one hackathon, three code-owning modules (`/tools`, `/sandbox`, `/tests`) that must share parsing and validation. Install friction and build steps cost demo time.

## Decision
- Python 3.11+, standard library only, run as `python -m <pkg>.<name>`.
- `pytest` is the only dev dependency.
- No `jsonschema` package: `schemas/validate.py` implements the JSON Schema subset we use; `schemas/types.py` mirrors it with `TypedDict`s.

## Consequences
- Zero install beyond Python; tools run anywhere the demo runs.
- New schema keywords require extending `schemas/validate.py` first.
