# Switch Studio

4 humans + 3 Codex-based agents (`Scout`, `Writer`, `Editor`) in a shared Switch room (`#studio`) turn repository commit activity into a `#HowISwitch` post or blog section where **no sentence survives unless it points to a commit hash, file, or count that deterministic CLI code verifies**.

---

## How It Works

1. **`Scout`** reads a repo snapshot (`studio/snapshot@1`) and posts a numbered list of facts (`F1…Fn`), each tied to a 40-character commit hash (`studio/facts@1`).
2. **`Writer`** drafts structured sentences (`S1…Sn`) citing only those fact IDs (`studio/draft@1`).
3. **Deterministic Verifier (`tools.verify`)** checks every fact against the snapshot and every sentence against its cited facts (`studio/verdict@1`).
4. **`Editor`** runs the verifier first, adds `OVERCLAIM` if prose stretches beyond its cited facts, and loops with `Writer` (at most 2 loops) before handing off to humans (`studio/review@1`).
5. **Humans in `#studio`** run `approve S3`, `approve all`, `reject S3: <reason>`, `edit S3: <text> [Fn]`, and `ship` (`studio/decision@1`) to produce `runs/<run_id>/final.md`.
6. **Before/After Grader (`tools.grade`)** scores a plain one-shot LLM baseline against Switch Studio's shipped output using the same deterministic check (`studio/grade@1`).

---

## Key Docs

- [`CONTRACT.md`](CONTRACT.md) — Exact schemas, CLI flags, room message format, human commands, and verification rules.
- [`GLOSSARY.md`](GLOSSARY.md) — Canonical vocabulary across code, prompts, and issues.
- [`AGENTS.md`](AGENTS.md) — Folder ownership and repo rules.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — System diagram and module boundaries.
- [`docs/SETUP.md`](docs/SETUP.md) — Environment and Switch runtime setup.
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — Index of Architecture Decision Records in [`docs/adr/`](docs/adr/).
- [`docs/NOT-BUILT-YET.md`](docs/NOT-BUILT-YET.md) — Explicit scope cuts and known v1 limitations.

---

## Quick Verification (Zero Dependencies)

Requires **Python 3.11+** (standard library only):

```bash
python3 -m schemas.validate --examples
```

---

## Running Tests

> **Owner:** Uday (`@udayjain06`, M3a) — expand this section as `/tests` and `/sandbox` land.

```bash
pytest
```
