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

Requires Python 3.11+; `pytest` is the only development dependency. On Windows, use `python` rather than assuming `python3` is installed.

```bash
python --version
pytest --version
```

Run all tests across schema validation, deterministic claim verification, and human decision replay:

```bash
pytest -W error -p no:anyio -p no:cacheprovider
```

Run individual test suites:

```bash
pytest tests/test_schemas.py    # Schema conformance and payload_ref resolution
pytest tests/test_verify.py     # Verifier rules and safeguard checks
pytest tests/test_decisions.py  # Decision replay and ship gating
pytest tests/test_tools.py      # Deterministic CLI tools
pytest agents/tests             # Agent logic & prompt wrappers
pytest infra/test_bot.py        # Switch room WebSocket bridge
```

Validate repository fixtures:

```bash
python -m schemas.validate --examples
python -m schemas.validate fixtures/demo-repo.snapshot.json
```

Run the end-to-end integration demo (M4):

```bash
python -m demo.run_demo --offline-only   # End-to-end fixture demo + 100% vs 50% grader
python -m demo.run_demo --live-only      # Live Switch #studio room bridge (see docs/SETUP.md)
```

Run the offline sandbox pipeline (M3a reference):

```bash
python -m sandbox.run
python -m sandbox.run --format howiswitch
python -m sandbox.run --format blog_section
```
