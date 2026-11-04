# Glossary

Terms used across Switch Studio code, tickets, and room messages. Use these words; avoid the listed synonyms.

| Term | Meaning | Avoid |
|---|---|---|
| **Room** | The shared Switch channel `#studio` where 4 humans and 3 agents work. | channel, chat |
| **Run** | One end-to-end pass over one `range` of one repo, identified by `run_id`. Config: `studio/run@1`. | session, job |
| **Range** | The git revision range a run covers (e.g. `v0.1..HEAD`; default last 50 commits). | window |
| **Snapshot** | `studio/snapshot@1`: commits and file stats for a range, from a local git clone. | dump, export |
| **Fixture** | A saved snapshot in `/fixtures`, used for offline runs. | mock |
| **Mode** | `fixture` (offline) or `live` (local clone of the real repo). | |
| **Scout / Writer / Editor** | The three agents: facts → draft → review. | bot (reserved for the infra bot) |
| **Fact** | A numbered (`F1…Fn`) verifiable claim about the repo; always carries a commit hash. | finding, evidence |
| **Fact kind** | `commit`, `file`, or `count`. | |
| **Metric** | The closed list of countable things: `commits`, `files_changed`, `lines_added`, `lines_deleted`, `contributors`. | stat |
| **Scope** | What a count covers: `commit` (one commit) or `run` (the whole range). | |
| **Draft** | `studio/draft@1`: ordered sentences with citations. Has a `revision` number. | post (until shipped) |
| **Sentence** | One draft unit `S1…Sn`: `text` plus cited `facts[]`. | line, claim |
| **Citation** | A fact ID in a sentence's `facts[]`. Rendered as `[F3]`. | reference, source |
| **Verifier** | `tools.verify`: deterministic code that checks facts against the snapshot and sentences against their citations. Emits a **verdict**. | checker, validator (reserved for schema validation) |
| **Verdict** | `studio/verdict@1`: pass/fail per fact and per sentence. | report |
| **Review** | `studio/review@1`: the Editor's per-sentence accept/reject, built on a verdict, plus `OVERCLAIM`. | |
| **Overclaim** | Editor judgment: text asserts more than its cited facts. The only non-deterministic rejection. | |
| **Loop** | One Editor → Writer revision round; at most 2. | iteration |
| **Decision** | `studio/decision@1`: one parsed human command, appended to `decisions.jsonl`. | vote |
| **Ship** | The human command that, if gated checks pass, writes `final.md`. Never posts anywhere. | publish, post |
| **Infra bot** | M1's room bridge that parses human commands and moves documents between room and `runs/`. | |
| **Overflow envelope** | A room JSON block carrying only envelope fields + `payload_ref` to a file in `runs/<run_id>/`. | |
| **Baseline** | The plain one-shot LLM output used in the before/after demo. | control |
| **Grade** | `studio/grade@1`: the same supported-sentence score applied to baseline and Switch Studio output. | eval |
| **Schema validation** | `schemas.validate`: shape checking of documents. Not the same as verification. | |
