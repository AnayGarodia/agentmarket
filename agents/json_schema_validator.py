"""
json_schema_validator.py — Validate a JSON document against a JSON Schema
using the real ``jsonschema`` library. No LLM. Returns structured per-path
errors.

Owns:
  - Parsing and basic safety bounds on document and schema sizes.
  - Iterating ``Draft202012Validator`` errors and projecting them into a
    Claude-Code-friendly shape (json_pointer + message + schema rule).
  - Defensive handling of unloadable schemas (returns a structured error,
    never an exception).

Does NOT own:
  - Fetching ``$ref`` URLs over the network. We disable remote refs to avoid
    SSRF; embedded $defs / local $ref still resolve.
  - Schema authoring. Caller provides the schema.

Input:
  {
    "document": object | array | str,         # required
    "schema": object,                         # required, must be a JSON Schema
    "draft": "2020-12" | "2019-09" | "7"     # optional, default "2020-12"
  }

Document may be passed as JSON-encoded string OR as already-parsed Python
object. Schema must be a Python object (parsed JSON).

Output:
  {
    "valid": bool,
    "draft": str,
    "error_count": int,
    "errors": [
      {
        "path": str,           # JSON pointer like "/items/3/name"
        "json_path": str,      # JSONPath-ish like "$.items[3].name"
        "message": str,
        "validator": str,      # which keyword triggered (required, type, enum...)
        "validator_value": any,
        "schema_path": str
      }
    ],
    "summary": str
  }
"""
from __future__ import annotations

import json
from typing import Any

try:
    from jsonschema import Draft202012Validator, Draft201909Validator, Draft7Validator
    from jsonschema.exceptions import SchemaError
    _JSONSCHEMA_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JSONSCHEMA_AVAILABLE = False
    SchemaError = Exception  # type: ignore[assignment, misc]

_MAX_DOCUMENT_CHARS = 200_000
_MAX_SCHEMA_CHARS = 50_000
_MAX_ERRORS = 100

_DRAFT_MAP: dict[str, Any] = {}
if _JSONSCHEMA_AVAILABLE:
    _DRAFT_MAP = {
        "2020-12": Draft202012Validator,
        "2019-09": Draft201909Validator,
        "7": Draft7Validator,
    }


def _err(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, **details}}


def _to_json_path(path: list[Any]) -> str:
    """Convert a jsonschema absolute_path deque to JSONPath syntax."""
    parts = ["$"]
    for piece in path:
        if isinstance(piece, int):
            parts.append(f"[{piece}]")
        else:
            parts.append(f".{piece}")
    return "".join(parts)


def _to_json_pointer(path: list[Any]) -> str:
    """Convert path to RFC 6901 JSON Pointer."""
    if not path:
        return ""
    encoded = []
    for piece in path:
        token = str(piece).replace("~", "~0").replace("/", "~1")
        encoded.append(token)
    return "/" + "/".join(encoded)


def run(payload: dict) -> dict:
    """Validate a JSON document against a JSON Schema."""
    if not _JSONSCHEMA_AVAILABLE:
        return _err(
            "json_schema_validator.tool_unavailable",
            "The 'jsonschema' Python package is not installed on this executor.",
            runtime_requirement="pip install jsonschema",
        )

    if not isinstance(payload, dict):
        return _err("json_schema_validator.invalid_payload", "payload must be an object")

    document = payload.get("document")
    if document is None:
        return _err("json_schema_validator.missing_document", "'document' is required")

    if isinstance(document, str):
        if len(document) > _MAX_DOCUMENT_CHARS:
            return _err(
                "json_schema_validator.document_too_large",
                f"document exceeds {_MAX_DOCUMENT_CHARS} chars",
            )
        try:
            document = json.loads(document)
        except json.JSONDecodeError as exc:
            return _err(
                "json_schema_validator.invalid_json",
                f"document is not valid JSON: {exc.msg} at line {exc.lineno} col {exc.colno}",
                line=exc.lineno,
                column=exc.colno,
            )

    schema = payload.get("schema")
    if not isinstance(schema, dict):
        return _err(
            "json_schema_validator.missing_schema",
            "'schema' is required and must be a JSON Schema object",
        )

    schema_serialized = json.dumps(schema)
    if len(schema_serialized) > _MAX_SCHEMA_CHARS:
        return _err(
            "json_schema_validator.schema_too_large",
            f"schema exceeds {_MAX_SCHEMA_CHARS} chars",
        )

    draft = str(payload.get("draft") or "2020-12").strip()
    if draft not in _DRAFT_MAP:
        return _err(
            "json_schema_validator.unsupported_draft",
            f"draft must be one of {sorted(_DRAFT_MAP.keys())}; got {draft!r}",
        )
    validator_cls = _DRAFT_MAP[draft]

    try:
        validator_cls.check_schema(schema)
    except SchemaError as exc:
        return _err(
            "json_schema_validator.invalid_schema",
            f"schema is not a valid JSON Schema: {exc.message}",
            schema_path=list(exc.absolute_path) if hasattr(exc, "absolute_path") else None,
        )
    except Exception as exc:
        return _err("json_schema_validator.invalid_schema", f"schema validation failed: {exc}")

    validator = validator_cls(schema)
    raw_errors = list(validator.iter_errors(document))

    projected: list[dict[str, Any]] = []
    for error in raw_errors[:_MAX_ERRORS]:
        path_list = list(error.absolute_path)
        schema_path_list = list(error.absolute_schema_path)
        projected.append(
            {
                "path": _to_json_pointer(path_list),
                "json_path": _to_json_path(path_list),
                "message": error.message,
                "validator": error.validator,
                "validator_value": error.validator_value if isinstance(
                    error.validator_value, (str, int, float, bool, list, dict, type(None))
                ) else str(error.validator_value),
                "schema_path": _to_json_pointer(schema_path_list),
            }
        )

    valid = len(raw_errors) == 0
    if valid:
        summary = "Document is valid against the supplied schema."
    elif len(raw_errors) == 1:
        summary = f"1 validation error: {raw_errors[0].message}"
    else:
        summary = f"{len(raw_errors)} validation errors. First: {raw_errors[0].message}"

    return {
        "valid": valid,
        "draft": draft,
        "error_count": len(raw_errors),
        "errors": projected,
        "truncated": len(raw_errors) > _MAX_ERRORS,
        "summary": summary,
    }
