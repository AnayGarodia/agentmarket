# OWNS: parsing unified diff format and producing structured change statistics and risk flags
# NOT OWNS: fetching diffs from git/GitHub (use git commands externally), LLM code review
# INVARIANTS: pure text parsing only; never executes any code or makes network requests
# DECISIONS: regex-based risk detection without LLM — deterministic and auditable

import re
from typing import Optional

MAX_DIFF_BYTES = 500_000
HIGH_CHURN_THRESHOLD = 200
TOP_FILES_COUNT = 3

# File type extension mapping
_EXT_TO_TYPE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java", ".kt": "java",
    ".sql": "sql",
    ".yaml": "yaml", ".yml": "yaml",
    ".json": "json",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".md": "markdown", ".mdx": "markdown",
}

# Risk flag path pattern matchers
_AUTH_PATTERNS = re.compile(
    r"auth|login|session|token|oauth|jwt|password|credential", re.IGNORECASE
)
_PAYMENT_PATTERNS = re.compile(
    r"payment|billing|stripe|wallet|ledger|transaction|charge", re.IGNORECASE
)
_SECURITY_PATTERNS = re.compile(
    r"security|crypto|encrypt|decrypt|hash|permission|acl|rbac", re.IGNORECASE
)
_MIGRATION_PATTERNS = re.compile(r"migration|migrate|alembic", re.IGNORECASE)
_MIGRATION_SQL_PATTERN = re.compile(r"\d{4}_.*\.sql$")
_TEST_PATTERNS = re.compile(r"test_|_test|spec\.|\.spec\.|/tests/|/test/", re.IGNORECASE)
_CONFIG_PATTERN = re.compile(r"(^|/)\.env(\..+)?$|(^|/)config\.|settings\.|secrets\.", re.IGNORECASE)
_DEP_FILES = {
    "requirements.txt", "package.json", "package-lock.json", "yarn.lock",
    "go.mod", "go.sum", "cargo.toml", "cargo.lock", "pipfile", "pyproject.toml",
}
_DEP_PATTERN = re.compile(r"requirements.*\.txt$", re.IGNORECASE)

# Secret detection patterns (applied to added lines only)
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_header", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("generic_secret", re.compile(
        r"(?i)(api_key|secret|password|token|passwd)\s*[=:]\s*['\"][A-Za-z0-9+/]{16,}['\"]"
    )),
    ("stripe_key", re.compile(r"sk_(live|test)_[A-Za-z0-9]{24,}")),
]


def _detect_file_type(path: str) -> str:
    """Determine language category from file extension or known filenames."""
    lower = path.lower()
    basename = lower.rsplit("/", 1)[-1]
    if basename == "dockerfile" or basename.startswith("dockerfile."):
        return "dockerfile"
    ext = "." + lower.rsplit(".", 1)[-1] if "." in basename else ""
    return _EXT_TO_TYPE.get(ext, "other")


def _compute_risk_flags(path: str, additions: int, deletions: int, secret_found: bool) -> list[str]:
    """Return list of risk flag strings for a single file based on its path and stats."""
    flags: list[str] = []
    lower_path = path.lower()
    basename = lower_path.rsplit("/", 1)[-1]

    if _MIGRATION_PATTERNS.search(lower_path) or _MIGRATION_SQL_PATTERN.search(basename):
        flags.append("migration")
    if _AUTH_PATTERNS.search(lower_path):
        flags.append("auth_code")
    if _PAYMENT_PATTERNS.search(lower_path):
        flags.append("payment_code")
    if _SECURITY_PATTERNS.search(lower_path):
        flags.append("security_code")
    if _TEST_PATTERNS.search(lower_path):
        flags.append("test_file")
    if _CONFIG_PATTERN.search(lower_path):
        flags.append("config_file")

    dep_base = basename.split("/")[-1] if "/" in basename else basename
    if dep_base in _DEP_FILES or _DEP_PATTERN.match(basename):
        flags.append("dependency_file")

    if additions + deletions > HIGH_CHURN_THRESHOLD:
        flags.append("high_churn")
    if secret_found:
        flags.append("secret_pattern")

    return flags


def _scan_secrets(added_lines: list[str]) -> list[str]:
    """Return names of secret patterns matched in added lines (not the values)."""
    found: set[str] = set()
    for line in added_lines:
        for name, pattern in _SECRET_PATTERNS:
            if name not in found and pattern.search(line):
                found.add(name)
    return sorted(found)


def _classify_file_status(old_path: Optional[str], new_path: Optional[str]) -> str:
    """Map old/new path combination to a status string."""
    if old_path is None or old_path == "/dev/null":
        return "added"
    if new_path is None or new_path == "/dev/null":
        return "deleted"
    if old_path != new_path:
        return "renamed"
    return "modified"


def _strip_path_prefix(raw: str) -> str:
    """Remove a/ or b/ prefix added by git diff."""
    if raw.startswith("a/") or raw.startswith("b/"):
        return raw[2:]
    return raw


def _parse_diff_lines(lines: list[str]) -> list[dict]:
    """Parse unified diff lines into per-file records with addition/deletion counts."""
    files: list[dict] = []
    current: Optional[dict] = None
    old_raw: Optional[str] = None
    new_raw: Optional[str] = None

    for line in lines:
        if line.startswith("diff --git"):
            if current is not None:
                files.append(current)
            old_raw = None
            new_raw = None
            current = None
            continue

        if line.startswith("rename from "):
            old_raw = line[len("rename from "):].strip()
            continue
        if line.startswith("rename to "):
            new_raw = line[len("rename to "):].strip()
            continue

        if line.startswith("--- "):
            raw = line[4:].strip().split("\t")[0]
            old_raw = None if raw == "/dev/null" else _strip_path_prefix(raw)
            continue

        if line.startswith("+++ "):
            raw = line[4:].strip().split("\t")[0]
            new_raw = None if raw == "/dev/null" else _strip_path_prefix(raw)
            path = new_raw if new_raw else old_raw
            current = {
                "path": path or "",
                "old_path": old_raw if old_raw != new_raw else None,
                "additions": 0,
                "deletions": 0,
                "added_lines": [],
                "binary": False,
            }
            continue

        if line.startswith("Binary files") and "differ" in line:
            if current is not None:
                current["binary"] = True
            continue

        if current is None:
            continue

        if line.startswith("+") and not line.startswith("+++"):
            current["additions"] += 1
            current["added_lines"].append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            current["deletions"] += 1

    if current is not None:
        files.append(current)

    return files


def _build_file_record(raw: dict) -> dict:
    """Convert a raw parsed file dict into the output schema record."""
    path: str = raw["path"]
    old_path: Optional[str] = raw.get("old_path")
    additions: int = raw["additions"]
    deletions: int = raw["deletions"]
    binary: bool = raw["binary"]
    added_lines: list[str] = raw.get("added_lines", [])

    secrets_found = _scan_secrets(added_lines)
    secret_hit = bool(secrets_found)
    flags = _compute_risk_flags(path, additions, deletions, secret_hit)
    status = _classify_file_status(old_path if old_path else path, path)
    if old_path is None and "added" in flags:
        pass  # status already computed above

    return {
        "path": path,
        "old_path": old_path,
        "status": status,
        "additions": additions,
        "deletions": deletions,
        "file_type": _detect_file_type(path),
        "risk_flags": flags,
        "binary": binary,
        "_secrets": secrets_found,  # internal; stripped before output
    }


def _compute_risk_level(risk_summary: dict) -> str:
    """Derive the overall risk level from the aggregated risk summary fields."""
    if risk_summary["secret_patterns_found"]:
        return "critical"
    has_migration = bool(risk_summary["migration_files"])
    if has_migration and (risk_summary["auth_changes"] or risk_summary["payment_changes"]):
        return "critical"
    if has_migration or risk_summary["auth_changes"] or risk_summary["payment_changes"]:
        return "high"
    if risk_summary["security_changes"]:
        return "high"
    if risk_summary["test_deletions"] > 50 or risk_summary["dependency_changes"]:
        return "medium"
    return "low"


def _aggregate_risk_summary(file_records: list[dict]) -> dict:
    """Build risk_summary by folding over all file records."""
    all_flags: list[str] = []
    migration_files: list[str] = []
    auth_changes = False
    payment_changes = False
    security_changes = False
    test_additions = 0
    test_deletions = 0
    dependency_changes = False
    secret_patterns: set[str] = set()

    for rec in file_records:
        flags = rec["risk_flags"]
        all_flags.extend(flags)

        if "migration" in flags:
            migration_files.append(rec["path"])
        if "auth_code" in flags:
            auth_changes = True
        if "payment_code" in flags:
            payment_changes = True
        if "security_code" in flags:
            security_changes = True
        if "test_file" in flags:
            test_additions += rec["additions"]
            test_deletions += rec["deletions"]
        if "dependency_file" in flags:
            dependency_changes = True
        secret_patterns.update(rec.get("_secrets", []))

    summary = {
        "flags": sorted(set(all_flags)),
        "migration_files": migration_files,
        "auth_changes": auth_changes,
        "payment_changes": payment_changes,
        "security_changes": security_changes,
        "test_additions": test_additions,
        "test_deletions": test_deletions,
        "dependency_changes": dependency_changes,
        "secret_patterns_found": sorted(secret_patterns),
    }
    summary["level"] = _compute_risk_level(summary)
    return summary


def _strip_internal_fields(records: list[dict]) -> list[dict]:
    """Remove internal keys (prefixed with _) before returning output."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in records]


def run(payload: dict) -> dict:
    """
    Parse a unified diff and return structured risk analysis.

    Why: accurate line counts and risk-pattern detection require real parsing;
    chat approximations produce wrong churn numbers and miss secret leaks.
    """
    diff: str = payload.get("diff", "")

    if not isinstance(diff, str) or diff == "":
        return {"error": {"code": "diff_analyzer.missing_diff", "message": "No diff provided."}}

    if len(diff.encode("utf-8")) > MAX_DIFF_BYTES:
        return {
            "error": {
                "code": "diff_analyzer.diff_too_large",
                "message": f"Diff exceeds {MAX_DIFF_BYTES // 1_000}KB limit.",
            }
        }

    if not diff.strip():
        return {
            "files_changed": 0,
            "total_additions": 0,
            "total_deletions": 0,
            "total_churn": 0,
            "files": [],
            "risk_summary": {
                "level": "low",
                "flags": [],
                "migration_files": [],
                "auth_changes": False,
                "payment_changes": False,
                "security_changes": False,
                "test_additions": 0,
                "test_deletions": 0,
                "dependency_changes": False,
                "secret_patterns_found": [],
            },
            "largest_files": [],
        }

    lines = diff.splitlines()
    raw_files = _parse_diff_lines(lines)
    file_records = [_build_file_record(r) for r in raw_files]

    total_additions = sum(r["additions"] for r in file_records)
    total_deletions = sum(r["deletions"] for r in file_records)
    total_churn = total_additions + total_deletions

    risk_summary = _aggregate_risk_summary(file_records)

    sorted_by_churn = sorted(
        file_records,
        key=lambda r: r["additions"] + r["deletions"],
        reverse=True,
    )
    largest_files = [r["path"] for r in sorted_by_churn[:TOP_FILES_COUNT]]

    return {
        "files_changed": len(file_records),
        "total_additions": total_additions,
        "total_deletions": total_deletions,
        "total_churn": total_churn,
        "files": _strip_internal_fields(file_records),
        "risk_summary": risk_summary,
        "largest_files": largest_files,
    }
