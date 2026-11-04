# 0007. Room messages: summary plus one JSON block; overflow to `runs/`

- **Status:** Accepted · **Date:** 2026-11-04 · **Owner:** Vivek (M0)

## Context
`#studio` is read by humans and parsed by agents and the infra bot. Switch's Mattermost/Matrix bridge renders fenced ```` ```json ```` blocks natively, but snapshots or drafts can be large.

## Decision
- Every agent message: a short human summary, then exactly one fenced JSON block holding one `studio/*` document.
- Oversized documents are written to `runs/<run_id>/…`; the message carries an overflow envelope with `payload_ref: {path, sha256}`.
- `runs/` is gitignored; anyone may write it, nobody commits it.

## Consequences
- One parser for all agents; humans never read raw JSON to follow along.
- Consumers must resolve `payload_ref` via `schemas.validate.load_document`.
