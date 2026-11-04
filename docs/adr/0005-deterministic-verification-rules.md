# 0005. Deterministic verification: exact matches, closed metric list

- **Status:** Accepted · **Date:** 2026-11-04 · **Owner:** Vivek (M0)

## Context
The proof must come from plain code, not an LLM. Loose matching ("~40", rounding, derived ratios) reintroduces judgment.

## Decision
- Count facts name a metric from a closed list (`commits`, `files_changed`, `lines_added`, `lines_deleted`, `contributors`) with a `scope` (`commit` | `run`); the verifier recomputes and requires an exact match.
- Every number in a sentence must equal a cited count `value`, or a number token in a cited fact's `subject` or `path`.
- Hex tokens of 7+ chars with a letter must prefix a cited commit.
- Before scanning: strip citation tags, sentence IDs, and dates equal to a cited commit's date.
- No derived numbers (%, ratios) in v1. Number words are not checked.
- Error codes are a closed set of five; exit codes 0/1/2.

## Consequences
- Some true sentences fail ("40% fewer lines"); writers must cite raw counts.
- Number words are a known gap, recorded in `docs/NOT-BUILT-YET.md`.
