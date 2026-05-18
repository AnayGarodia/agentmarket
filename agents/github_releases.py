"""
github_releases.py — Fetch recent releases for a GitHub repo.

Input:
  {
    "repo": "owner/repo",       # required, "anthropics/anthropic-sdk-python"
    "limit": 5,                  # optional, max 30, default 5
    "since_version": "v1.7.0"   # optional, return only releases newer than this
  }

Output:
  {
    "repo": str,
    "release_count": int,
    "releases": [{
      "tag_name": str,
      "name": str | null,
      "published_at": str,
      "is_prerelease": bool,
      "is_draft": bool,
      "body": str | null,
      "html_url": str,
      "asset_count": int,
      "assets": [{"name": str, "download_url": str, "size_bytes": int}]
    }],
    "latest_tag": str | null,
    "rate_limit_remaining": int | null
  }

OWNS: single-page GitHub Releases REST call + lightweight version filtering.
NOT OWNS: changelog parsing, upgrade-impact analysis, or release notes
          rewriting — those are downstream concerns.
INVARIANTS:
  * Without GITHUB_TOKEN the agent uses unauthenticated requests (60/h IP
    rate limit). The rate-limit headers are surfaced so callers can pace
    themselves.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

from agents._contracts import agent_error as _err


_LOG = logging.getLogger(__name__)

_GH_RELEASES_API = "https://api.github.com/repos/{repo}/releases"
_USER_AGENT = "Aztea-GitHub-Releases/1.0"
_TIMEOUT_S = 10
_MAX_LIMIT = 30
_DEFAULT_LIMIT = 5
_MAX_BODY_CHARS = 4_000
_MAX_ASSETS = 10
_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")
_SEMVER_PARTS_RE = re.compile(r"\d+")


def _gh_headers() -> dict[str, str]:
    """Pure-ish: build the request headers, including optional auth token."""
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _version_tuple(tag: str) -> tuple[int, ...] | None:
    """Pure: extract a coarse numeric tuple from a tag for since-comparison.

    Why: not strict semver — we just want a monotone comparison that
    handles ``v1.2.3``, ``1.2.3-rc1``, ``release-1.2.3``. Returns None
    when the tag carries no numeric component (the comparison then
    treats the tag as un-filterable and includes it).
    """
    parts = _SEMVER_PARTS_RE.findall(tag or "")
    if not parts:
        return None
    return tuple(int(p) for p in parts[:4])


def _shape_asset(asset: dict) -> dict[str, Any]:
    """Pure: shape one GitHub release asset into the agent's record."""
    return {
        "name": str(asset.get("name") or ""),
        "download_url": str(asset.get("browser_download_url") or ""),
        "size_bytes": int(asset.get("size") or 0),
        "content_type": str(asset.get("content_type") or ""),
    }


def _shape_release(release: dict) -> dict[str, Any]:
    """Pure: shape one GitHub release into the agent's record (body truncated)."""
    body = release.get("body")
    if isinstance(body, str) and len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS] + "\n…[truncated]"
    raw_assets = release.get("assets") or []
    assets = [_shape_asset(a) for a in raw_assets[:_MAX_ASSETS] if isinstance(a, dict)]
    return {
        "tag_name": str(release.get("tag_name") or ""),
        "name": release.get("name") or None,
        "published_at": str(release.get("published_at") or ""),
        "is_prerelease": bool(release.get("prerelease")),
        "is_draft": bool(release.get("draft")),
        "body": body if isinstance(body, str) else None,
        "html_url": str(release.get("html_url") or ""),
        "asset_count": len(raw_assets) if isinstance(raw_assets, list) else 0,
        "assets": assets,
    }


def _filter_since(releases: list[dict], since_version: str | None) -> list[dict]:
    """Pure: drop releases whose tag is ≤ ``since_version`` by coarse version tuple."""
    if not since_version:
        return releases
    threshold = _version_tuple(since_version)
    if threshold is None:
        return releases
    out: list[dict] = []
    for r in releases:
        tup = _version_tuple(str(r.get("tag_name") or ""))
        if tup is None or tup > threshold:
            out.append(r)
    return out


def run(payload: dict) -> dict:
    """Fetch recent GitHub releases for a repository (unauthenticated by default)."""
    if not isinstance(payload, dict):
        return _err("github_releases.bad_input",
                    f"payload must be dict, got {type(payload).__name__}")
    repo = str(payload.get("repo") or "").strip()
    if not repo:
        return _err("github_releases.missing_repo",
                    "'repo' is required (format: owner/repo)")
    if not _REPO_PATTERN.match(repo):
        return _err(
            "github_releases.invalid_repo",
            "repo must be 'owner/repo' with only alphanumerics, '_', '-', '.'",
            details={"received": repo},
        )
    try:
        raw_limit = int(payload.get("limit") or _DEFAULT_LIMIT)
    except (TypeError, ValueError):
        raw_limit = _DEFAULT_LIMIT
    limit = max(1, min(raw_limit, _MAX_LIMIT))
    since = payload.get("since_version")
    since_version = str(since).strip() if isinstance(since, str) else None
    url = _GH_RELEASES_API.format(repo=repo)
    try:
        resp = requests.get(
            url, params={"per_page": _MAX_LIMIT},
            timeout=_TIMEOUT_S, headers=_gh_headers(),
        )
    except requests.exceptions.Timeout:
        return _err("github_releases.timeout", "GitHub API timed out")
    except Exception as exc:  # noqa: BLE001 — defensive net layer
        return _err(
            "github_releases.fetch_failed",
            f"Could not reach GitHub API: {type(exc).__name__}",
        )
    if resp.status_code == 404:
        return _err(
            "github_releases.repo_not_found",
            f"Repository '{repo}' not found or has no releases.",
        )
    if resp.status_code == 403:
        return _err(
            "github_releases.rate_limited",
            "GitHub API rate limit hit. Set GITHUB_TOKEN env to lift "
            "the 60/h unauthenticated cap to 5000/h.",
            details={"reset_at": resp.headers.get("X-RateLimit-Reset")},
        )
    if resp.status_code != 200:
        return _err(
            "github_releases.api_error",
            f"GitHub API returned status {resp.status_code}",
        )
    try:
        releases = resp.json()
    except ValueError:
        return _err("github_releases.bad_response", "GitHub returned non-JSON")
    if not isinstance(releases, list):
        return _err("github_releases.bad_response",
                    "GitHub returned a non-list payload")
    releases = _filter_since(releases, since_version)
    releases = releases[:limit]
    shaped = [_shape_release(r) for r in releases if isinstance(r, dict)]
    rate_remaining_raw = resp.headers.get("X-RateLimit-Remaining")
    try:
        rate_remaining = int(rate_remaining_raw) if rate_remaining_raw else None
    except (TypeError, ValueError):
        rate_remaining = None
    return {
        "repo": repo,
        "release_count": len(shaped),
        "releases": shaped,
        "latest_tag": shaped[0]["tag_name"] if shaped else None,
        "rate_limit_remaining": rate_remaining,
    }
