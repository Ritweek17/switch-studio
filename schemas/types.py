"""TypedDict mirrors of schemas/*.schema.json for editor hints and type checking.

The .schema.json files are the source of truth; keep these in sync in the same commit.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

FactKind = Literal["commit", "file", "count"]
Metric = Literal["commits", "files_changed", "lines_added", "lines_deleted", "contributors"]
Scope = Literal["commit", "run"]
FileStatus = Literal["A", "M", "D", "R"]
Format = Literal["howiswitch", "blog_section"]
VerifyError = Literal["NO_CITATION", "UNKNOWN_FACT", "FACT_INVALID", "NUMBER_UNSUPPORTED", "HASH_UNSUPPORTED"]
ReviewError = Literal["NO_CITATION", "UNKNOWN_FACT", "FACT_INVALID", "NUMBER_UNSUPPORTED", "HASH_UNSUPPORTED", "OVERCLAIM"]
Command = Literal["approve", "approve_all", "reject", "edit", "ship"]
DecisionError = Literal["UNKNOWN_SENTENCE", "EDIT_NO_CITATION", "EDIT_VERIFY_FAILED", "SHIP_BLOCKED", "PARSE_ERROR"]


class PayloadRef(TypedDict):
    path: str
    sha256: str


class Run(TypedDict):
    schema: Literal["studio/run@1"]
    run_id: str
    agent: Literal["tool"]
    created_at: str
    mode: Literal["fixture", "live"]
    repo_path: str
    range: str
    snapshot_path: str
    format: Format


class SnapshotFile(TypedDict):
    path: str
    status: FileStatus
    added: int | None
    deleted: int | None


class SnapshotCommit(TypedDict):
    hash: str
    author_name: str
    author_email: str
    date: str
    subject: str
    files: list[SnapshotFile]


class SnapshotRepo(TypedDict):
    name: str


class Snapshot(TypedDict):
    schema: Literal["studio/snapshot@1"]
    generated_at: str
    repo: SnapshotRepo
    range: str
    head: str
    commits: list[SnapshotCommit]


class CommitFact(TypedDict):
    id: str
    kind: Literal["commit"]
    commit: str
    subject: str
    author_name: str
    date: str
    statement: str


class FileFact(TypedDict):
    id: str
    kind: Literal["file"]
    commit: str
    path: str
    status: FileStatus
    statement: str


class CountFact(TypedDict):
    id: str
    kind: Literal["count"]
    commit: str
    metric: Metric
    scope: Scope
    author_email: NotRequired[str]
    value: int
    statement: str


Fact = CommitFact | FileFact | CountFact


class SnapshotPointer(TypedDict):
    path: str
    range: str
    head: str


class Facts(TypedDict):
    schema: Literal["studio/facts@1"]
    run_id: str
    agent: Literal["scout"]
    created_at: str
    snapshot: SnapshotPointer
    facts: list[Fact]


class Sentence(TypedDict):
    id: str
    text: str
    facts: list[str]


class Draft(TypedDict):
    schema: Literal["studio/draft@1"]
    run_id: str
    agent: Literal["writer", "human"]
    created_at: str
    revision: int
    format: Format
    sentences: list[Sentence]


class FactResult(TypedDict):
    id: str
    ok: bool
    reason: str | None


class SentenceResult(TypedDict):
    id: str
    ok: bool
    errors: list[VerifyError]


class VerdictSummary(TypedDict):
    facts_total: int
    facts_ok: int
    sentences_total: int
    sentences_ok: int


# Functional syntax because "pass" is a Python keyword.
Verdict = TypedDict(
    "Verdict",
    {
        "schema": Literal["studio/verdict@1"],
        "run_id": str,
        "agent": Literal["tool"],
        "created_at": str,
        "draft_revision": int,
        "pass": bool,
        "facts": list[FactResult],
        "sentences": list[SentenceResult],
        "summary": VerdictSummary,
    },
)


class ReviewSentence(TypedDict):
    id: str
    status: Literal["ok", "rejected"]
    errors: list[ReviewError]
    note: NotRequired[str]


class Review(TypedDict):
    schema: Literal["studio/review@1"]
    run_id: str
    agent: Literal["editor"]
    created_at: str
    draft_revision: int
    loop: Literal[1, 2]
    verdict_path: str
    sentences: list[ReviewSentence]
    handoff: Literal["writer", "humans"]


class DecisionResult(TypedDict):
    accepted: bool
    error: DecisionError | None


class Decision(TypedDict):
    schema: Literal["studio/decision@1"]
    run_id: str
    agent: Literal["human"]
    created_at: str
    user: str
    draft_revision: int
    command: Command
    sentence_id: NotRequired[str]
    reason: NotRequired[str]
    text: NotRequired[str]
    facts: NotRequired[list[str]]
    result: DecisionResult


class GradeResult(TypedDict):
    label: str
    path: str
    sentences_total: int
    sentences_supported: int
    supported_pct: float
    claims_total: int
    claims_supported: int
    hashes_fabricated: int
    numbers_unsupported: int


class Grade(TypedDict):
    schema: Literal["studio/grade@1"]
    agent: Literal["tool"]
    created_at: str
    snapshot_path: str
    results: list[GradeResult]
