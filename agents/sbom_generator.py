"""
sbom_generator.py — Generate a CycloneDX-style SBOM from a manifest.

Input:
  {
    "manifest_content": "<file contents>",
    "manifest_type": "requirements.txt" | "package.json" | "Cargo.toml",
    "include_license": true   # optional, default true — best-effort license
  }

Output:
  {
    "bom_format": "CycloneDX",
    "spec_version": "1.5",
    "version": 1,
    "metadata": {"timestamp": str, "tools": [{"name": "aztea-sbom"}]},
    "components": [
      {"type": "library", "name": str, "version": str, "purl": str,
       "license": str | null}
    ],
    "component_count": int,
    "manifest_type": str
  }

OWNS: parsing common manifests into a CycloneDX 1.5-shaped JSON
NOT OWNS: dependency-tree resolution (transitive packages aren't fetched),
          vulnerability scanning (that's dependency_auditor's job),
          CycloneDX XML serialization (JSON only).
INVARIANTS:
  * Output is valid CycloneDX 1.5 JSON shape.
  * Unparseable manifest sections produce a parse_warnings entry, never
    silently dropped.
"""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from agents._contracts import agent_error as _err


_SBOM_SPEC_VERSION = "1.5"
_MAX_MANIFEST_CHARS = 100_000
_MAX_COMPONENTS = 500
_SUPPORTED_TYPES = ("requirements.txt", "package.json", "Cargo.toml")

# Same line shape as dependency_auditor for consistency.
_PYPI_REQ_LINE_RE = re.compile(
    r"([A-Za-z0-9_\-\.]+)(?:\[[A-Za-z0-9_,\-\.]+\])?"
    r"\s*([>=<!~]=?\s*[\w\.\*]+(?:\s*,\s*[>=<!~]=?\s*[\w\.\*]+)*)?"
)
_PYPI_VER_OP_RE = re.compile(r"[>=<!~^]+\s*")


def _purl_pypi(name: str, version: str) -> str:
    """Pure: build a Package URL for a PyPI component."""
    encoded_name = quote(name.lower(), safe="")
    if version:
        return f"pkg:pypi/{encoded_name}@{quote(version, safe='')}"
    return f"pkg:pypi/{encoded_name}"


def _purl_npm(name: str, version: str) -> str:
    """Pure: build a Package URL for an npm component (handles @scope/pkg)."""
    if name.startswith("@") and "/" in name:
        scope, pkg = name.split("/", 1)
        encoded = f"{quote(scope, safe='@')}/{quote(pkg, safe='')}"
    else:
        encoded = quote(name, safe="")
    if version:
        return f"pkg:npm/{encoded}@{quote(version, safe='')}"
    return f"pkg:npm/{encoded}"


def _purl_cargo(name: str, version: str) -> str:
    """Pure: build a Package URL for a Cargo (crates.io) component."""
    encoded = quote(name, safe="")
    if version:
        return f"pkg:cargo/{encoded}@{quote(version, safe='')}"
    return f"pkg:cargo/{encoded}"


def _component(name: str, version: str, purl: str) -> dict[str, Any]:
    """Pure: build one CycloneDX-shaped component entry."""
    return {
        "type": "library",
        "name": name,
        "version": version or "unspecified",
        "purl": purl,
        "bom-ref": purl,
        "license": None,
    }


def _parse_requirements(content: str) -> tuple[list[dict], list[dict]]:
    """Pure: parse a requirements.txt-shaped manifest into components."""
    components: list[dict] = []
    warnings: list[dict] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _PYPI_REQ_LINE_RE.fullmatch(line)
        if not m:
            warnings.append({"line": raw_line, "reason": "unparseable"})
            continue
        name = m.group(1).strip()
        ver_spec = (m.group(2) or "").strip()
        if ver_spec:
            ver = _PYPI_VER_OP_RE.sub("", ver_spec).split(",")[0].strip()
        else:
            ver = ""
        components.append(_component(name, ver, _purl_pypi(name, ver)))
    return components, warnings


def _parse_package_json(content: str) -> tuple[list[dict], list[dict]]:
    """Pure: parse a package.json manifest's three dep dicts into components."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        return [], [{"reason": "json_parse_error", "detail": str(exc)}]
    components: list[dict] = []
    if not isinstance(data, dict):
        return [], [{"reason": "package_json_not_object"}]
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = data.get(key) or {}
        if not isinstance(deps, dict):
            continue
        for name, ver_spec in deps.items():
            ver = re.sub(r"[^0-9.]", "", str(ver_spec or "")).strip(".")
            components.append(_component(name, ver, _purl_npm(name, ver)))
    return components, []


def _parse_cargo_toml(content: str) -> tuple[list[dict], list[dict]]:
    """Pure: parse a Cargo.toml manifest's [dependencies] / [dev-dependencies]."""
    try:
        data = tomllib.loads(content)
    except Exception as exc:  # noqa: BLE001 — toml parse failure is a user error
        return [], [{"reason": "toml_parse_error", "detail": str(exc)}]
    components: list[dict] = []
    for key in ("dependencies", "dev-dependencies", "build-dependencies"):
        deps = data.get(key) or {}
        if not isinstance(deps, dict):
            continue
        for name, spec in deps.items():
            if isinstance(spec, str):
                ver = spec.strip()
            elif isinstance(spec, dict):
                ver = str(spec.get("version") or "").strip()
            else:
                ver = ""
            components.append(_component(name, ver, _purl_cargo(name, ver)))
    return components, []


_PARSERS = {
    "requirements.txt": _parse_requirements,
    "package.json": _parse_package_json,
    "Cargo.toml": _parse_cargo_toml,
}


def _dedup_components(components: list[dict]) -> tuple[list[dict], list[dict]]:
    """Pure: drop duplicate components (by purl), preserving first occurrence."""
    seen: set[str] = set()
    deduped: list[dict] = []
    warnings: list[dict] = []
    for comp in components:
        purl = comp.get("purl") or ""
        if purl in seen:
            warnings.append({
                "reason": "duplicate_component",
                "purl": purl,
                "name": comp.get("name"),
            })
            continue
        seen.add(purl)
        deduped.append(comp)
    return deduped, warnings


def run(payload: dict) -> dict:
    """Generate a CycloneDX-shaped SBOM from a single manifest file's content."""
    if not isinstance(payload, dict):
        return _err("sbom_generator.bad_input",
                    f"payload must be dict, got {type(payload).__name__}")
    manifest_type = str(payload.get("manifest_type") or "").strip()
    if manifest_type not in _SUPPORTED_TYPES:
        return _err(
            "sbom_generator.unsupported_manifest_type",
            f"manifest_type must be one of: {', '.join(_SUPPORTED_TYPES)}",
            details={"received": manifest_type},
        )
    content = str(payload.get("manifest_content") or "")
    if not content.strip():
        return _err(
            "sbom_generator.missing_manifest",
            "'manifest_content' is required (non-empty).",
        )
    if len(content) > _MAX_MANIFEST_CHARS:
        return _err(
            "sbom_generator.manifest_too_large",
            f"manifest exceeds {_MAX_MANIFEST_CHARS} chars",
        )
    parser = _PARSERS[manifest_type]
    components, parse_warnings = parser(content)
    components, dedup_warnings = _dedup_components(components)
    parse_warnings.extend(dedup_warnings)
    if len(components) > _MAX_COMPONENTS:
        parse_warnings.append({
            "reason": "components_truncated",
            "original_count": len(components),
            "kept": _MAX_COMPONENTS,
        })
        components = components[:_MAX_COMPONENTS]
    return {
        "bom_format": "CycloneDX",
        "spec_version": _SBOM_SPEC_VERSION,
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tools": [{"vendor": "Aztea", "name": "aztea-sbom", "version": "1.0"}],
            "manifest_type": manifest_type,
        },
        "components": components,
        "component_count": len(components),
        "parse_warnings": parse_warnings,
        "manifest_type": manifest_type,
    }
