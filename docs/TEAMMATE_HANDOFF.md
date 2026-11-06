# 🚀 Teammate Handoff Kit: Switch Studio

Welcome to **Switch Studio**! This document gives every teammate the 2-minute pitch, their exact module assignment, and the golden rules so everyone can build independently without git conflicts.

---

## 💡 The 2-Minute Pitch (What We Are Building)

Every developer has seen AI write fake, hallucinated changelogs:
> *"We boosted system performance by 500% and merged 50 new features!"* (None of which actually happened in the repo).

**Switch Studio** is a shared Switch room (`#studio`) where **4 humans + 3 AI agents** turn git commit history into a `#HowISwitch` post or blog section with one unbending guarantee:

> **No sentence survives into the final post unless it points to a commit hash, file, or count in git verified by deterministic code (not an LLM).**

---

## 🔄 How The Workflow Works

1. **`Scout` (AI Agent):** Scans git commits and publishes numbered facts (`F1…Fn`) with real 40-char commit hashes.
2. **`Writer` (AI Agent):** Drafts structured sentences (`S1…Sn`) where each sentence cites only those fact IDs (`[F1]`, `[F3]`).
3. **`Deterministic Verifier` (Python CLI):** Recounts lines, checks commit hashes, and verifies that every number in every sentence is 100% real.
4. **`Editor` (AI Agent):** Flags unverified claims or exaggerations (`OVERCLAIM`) before handing off to humans.
5. **The 4 Humans (Us in `#studio`):** Review in the room. We type `approve S3`, `reject S4` (drops bad sentences), `edit S1: <new text> [F2]`, and `ship` to produce `final.md`.
6. **Grader:** Runs our before/after demo proving Switch Studio achieved 100% verified claims vs. wild hallucinations from a standard one-shot prompt.

---

## 👥 Who Owns What (Zero Merge Conflicts)

We strictly partitioned the codebase into independent folders using `.github/CODEOWNERS`. You only edit your assigned folder:

| Teammate | Module & Track | Folder | GitHub Issue |
|---|---|---|---|
| **Vivek** | **M0 Foundation** (Done!) & **M2 Agent Logic** | `/`, `schemas/`, `agents/` | [#1](https://github.com/Ritweek17/switch-studio/issues/1) (Closed) & [#4](https://github.com/Ritweek17/switch-studio/issues/4) |
| **Saurabh** | **M3b Deterministic Tools** | `/tools/` | [#2 (M3b)](https://github.com/Ritweek17/switch-studio/issues/2) |
| **Uday** | **M3a Fixtures & Pytest Sandbox** | `/fixtures/`, `/sandbox/`, `/tests/` | [#3 (M3a)](https://github.com/Ritweek17/switch-studio/issues/3) |
| **Ritweek** | **M1 Switch Runtime & M4 Live Demo** | `/infra/`, `/demo/` | [#5 (M1)](https://github.com/Ritweek17/switch-studio/issues/5) & [#6 (M4)](https://github.com/Ritweek17/switch-studio/issues/6) |

---

## ⚡ The 3 Golden Rules For Everyone

1. **Python 3.11+ Standard Library Only:** No `pip install` for runtime code or tools! `pytest` is the only dev dependency. Tools must run cleanly with zero install friction.
2. **Respect Folder Seams:** Only edit files in your assigned folder. Never modify `CONTRACT.md` or `schemas/` (Vivek owns M0).
3. **Contracts & Examples Are Already Locked:**
   * Need the exact JSON shapes? Check [`CONTRACT.md`](CONTRACT.md) and [`schemas/*.schema.json`](schemas/).
   * Need sample input/output files to test against? They are already written and verified under [`schemas/examples/`](schemas/examples/)!
   * Run `python3 -m schemas.validate --examples` to verify your setup.

---

## 🛠️ Step-by-Step for Each Teammate

### For Saurabh (`tools/` — Issue #2):
* You are building 5 clean CLI tools: `snapshot`, `verify`, `apply`, `render`, and `grade`.
* No network access, pure Python stdlib, JSON to stdout.
* All CLI flags and error codes (`NO_CITATION`, `NUMBER_UNSUPPORTED`, etc.) are explicitly spelled out in `CONTRACT.md` §10.
* You can test your CLI right away using the sample files in `schemas/examples/`!

### For Uday (`fixtures/`, `sandbox/`, `tests/` — Issue #3):
* You are building the offline fixture snapshots and the `pytest` suite.
* Build `fixtures/demo-repo.snapshot.json` so the demo works offline without needing live git.
* Write unit tests in `/tests/` testing verifier edge cases and human command replay.

### For Ritweek (`infra/`, `demo/` — Issues #5 & #6):
* You are building the Switch `#studio` room connection and the infra bot that parses human chat commands (`approve`, `reject`, `edit`, `ship`).
* Document room setup steps in `docs/SETUP.md`.
* Later, hook up the end-to-end demo script in `/demo/` for the pitch.

### For Vivek (`agents/` — Issue #4):
* Building the role prompts for `Scout`, `Writer`, and `Editor` in `/agents/`.
