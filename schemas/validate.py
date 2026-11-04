"""Stdlib-only validator for the studio/* JSON documents.

Supports exactly the JSON Schema subset our schemas use:
$ref (local "#/$defs/..."), type, const, enum, pattern, minLength,
minimum, minItems, required, properties, additionalProperties, items, oneOf.

Usage:
    python -m schemas.validate FILE [FILE ...]   # exit 0 valid, 2 invalid
    python -m schemas.validate --examples        # validate schemas/examples/*

Library:
    from schemas.validate import validate, load_document
    errors = validate(doc)            # [] when valid
    doc = load_document("runs/demo-001/facts.json")  # resolves payload_ref, raises on invalid
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SCHEMA_DIR / "examples"

SCHEMA_FILES = {
    "studio/run@1": "run.schema.json",
    "studio/snapshot@1": "snapshot.schema.json",
    "studio/facts@1": "facts.schema.json",
    "studio/draft@1": "draft.schema.json",
    "studio/verdict@1": "verdict.schema.json",
    "studio/review@1": "review.schema.json",
    "studio/decision@1": "decision.schema.json",
    "studio/grade@1": "grade.schema.json",
}

ENVELOPE_KEYS = {"schema", "run_id", "agent", "created_at", "payload_ref"}

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


class DocumentError(Exception):
    """Raised by load_document when a document is unreadable or invalid."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _same(a: Any, b: Any) -> bool:
    # JSON equality without Python's True == 1 coercion.
    return type(a) is type(b) and a == b


def load_schema(name: str) -> dict:
    if name not in SCHEMA_FILES:
        raise KeyError(f"unknown schema {name!r}")
    return json.loads((SCHEMA_DIR / SCHEMA_FILES[name]).read_text())


def _check(value: Any, schema: dict, root: dict, path: str) -> list[str]:
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            return [f"{path}: unsupported $ref {ref!r}"]
        return _check(value, root["$defs"][ref.removeprefix("#/$defs/")], root, path)

    if "oneOf" in schema:
        results = [_check(value, sub, root, path) for sub in schema["oneOf"]]
        passing = [r for r in results if not r]
        if len(passing) == 1:
            return []
        if len(passing) > 1:
            return [f"{path}: matches more than one oneOf branch"]
        return min(results, key=len)

    errors: list[str] = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](value) for t in types):
            return [f"{path}: expected {'|'.join(types)}, got {type(value).__name__}"]
    if "const" in schema and not _same(value, schema["const"]):
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and not any(_same(value, e) for e in schema["enum"]):
        errors.append(f"{path}: {value!r} not in {schema['enum']!r}")

    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
    if _TYPES["number"](value) and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path}: {value} < {schema['minimum']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                errors += _check(item, schema["items"], root, f"{path}[{i}]")

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required {key!r}")
        for key, item in value.items():
            if key in props:
                errors += _check(item, props[key], root, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += _check(item, schema["additionalProperties"], root, f"{path}.{key}")
    return errors


def validate(doc: Any) -> list[str]:
    """Validate an in-memory document against the schema named in doc['schema'].

    A document carrying `payload_ref` is an overflow envelope: only its
    envelope is checked here. Use load_document() to resolve and check the payload.
    """
    if not isinstance(doc, dict) or not isinstance(doc.get("schema"), str):
        return ["$: document must be an object with a string 'schema' field"]
    if doc["schema"] not in SCHEMA_FILES:
        return [f"$.schema: unknown schema {doc['schema']!r}"]
    if "payload_ref" in doc:
        ref = doc["payload_ref"]
        errors = [f"$: unexpected property {k!r} in overflow envelope" for k in doc if k not in ENVELOPE_KEYS]
        if not (isinstance(ref, dict) and isinstance(ref.get("path"), str)
                and isinstance(ref.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])):
            errors.append("$.payload_ref: needs string 'path' and 64-hex 'sha256'")
        return errors
    schema = load_schema(doc["schema"])
    return _check(doc, schema, schema, "$")


def load_document(path: str | Path, base_dir: str | Path = ".") -> dict:
    """Read, resolve payload_ref (checking sha256), and validate. Raises DocumentError."""
    try:
        raw = Path(path).read_bytes()
        doc = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise DocumentError([f"{path}: {exc}"]) from exc
    errors = validate(doc)
    if errors:
        raise DocumentError(errors)
    if "payload_ref" in doc:
        target = Path(base_dir) / doc["payload_ref"]["path"]
        try:
            body = target.read_bytes()
        except OSError as exc:
            raise DocumentError([f"$.payload_ref.path: {exc}"]) from exc
        if hashlib.sha256(body).hexdigest() != doc["payload_ref"]["sha256"]:
            raise DocumentError([f"$.payload_ref.sha256: mismatch for {target}"])
        payload = json.loads(body)
        if payload.get("schema") != doc["schema"]:
            raise DocumentError([f"{target}: schema {payload.get('schema')!r} != envelope {doc['schema']!r}"])
        errors = validate(payload)
        if errors:
            raise DocumentError(errors)
        return payload
    return doc


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__.strip())
        return 0 if argv else 2
    files = sorted(EXAMPLES_DIR.glob("*.json")) if argv == ["--examples"] else [Path(a) for a in argv]
    failed = False
    for f in files:
        try:
            load_document(f)
            print(f"ok  {f}")
        except DocumentError as exc:
            failed = True
            print(json.dumps({"error": "invalid document", "path": str(f), "details": exc.errors}), file=sys.stderr)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
