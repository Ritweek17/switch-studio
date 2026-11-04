# Not Built Yet (Scope Cuts & Known v1 Gaps)

> **Owner:** Uday (`@udayjain06`, M3a). Keep this list honest for judges and teammates.

Switch Studio deliberately cuts scope so our core guarantee—**every surviving sentence cites a repo fact verified by deterministic code**—is rock-solid in the hackathon demo.

## Deliberate Scope Cuts

1. **No auto-posting to X or blogs:** `ship` writes `runs/<run_id>/final.md` and stops ([ADR-0006](adr/0006-human-commands-and-ship-gate.md)). Publishing always stays with a human.
2. **One local git repository, read-only:** Tools run local `git` commands against one clone or read a saved fixture snapshot ([ADR-0003](adr/0003-local-git-snapshots.md)). No writes to the target repo, no network calls inside `/tools`.
3. **No GitHub API metadata:** PR review comments, issue threads, stars, and CI run logs are not ingested in v1. Only git commit history and file stats are in `studio/snapshot@1`.
4. **Merge commits excluded:** `tools.snapshot` runs with `--no-merges` so merge commits do not double-count changed files or inflate line counts ([ADR-0003](adr/0003-local-git-snapshots.md)).

## Known Verification Gaps in v1

1. **Spelled-out number words are not checked:** The deterministic verifier checks digit tokens (`3`, `246`, `1,024`) and hex hashes (`9f3c2a1`), not English words like `"three"` or `"a dozen"` ([ADR-0005](adr/0005-deterministic-verification-rules.md)). Writer prompts forbid spelling out counts, and the Editor flags them as `OVERCLAIM` if they slip through.
2. **No calculated or derived numbers (ratios, percentages, speedups):** Every number in a sentence must match a cited `count` fact's exact `value` or a number token in a cited commit's `subject`/`path` ([ADR-0005](adr/0005-deterministic-verification-rules.md)). Claims like `"40% fewer lines"` or `"2x faster"` fail with `NUMBER_UNSUPPORTED`.
3. **Heuristic sentence splitting in `tools.grade` only:** Inside Switch Studio, drafts are structured JSON (`S1…Sn`) so code never guesses sentence boundaries ([ADR-0004](adr/0004-structured-facts-and-sentences.md)). Only `tools.grade` splits free-form markdown on `. ! ?` so it can score an uncited one-shot baseline on equal footing.
