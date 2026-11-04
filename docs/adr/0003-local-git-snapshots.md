# 0003. Repo data comes from local git snapshots; fixtures are saved snapshots

- **Status:** Accepted · **Date:** 2026-11-04 · **Owner:** Vivek (M0)

## Context
Scope is one GitHub repo, read-only, offline fixtures first and live repo second. The GitHub API brings auth, rate limits, and network failures into a live demo.

## Decision
- Read a local `git clone` with `git` commands; tools never touch the network.
- `tools.snapshot` emits `studio/snapshot@1`; files in `/fixtures` are saved snapshots, so offline and live are identical downstream.
- One run covers one revision `range` (default last 50 commits), shared by snapshot, facts, verifier, and grader.
- Merge commits excluded (`--no-merges`); they double-count file changes and inflate line totals.
- Contributor identity is `author_email`.

## Consequences
- PRs, issues, stars, and reviews are out of reach (listed in `docs/NOT-BUILT-YET.md`).
- Any count is reproducible from the snapshot alone.
