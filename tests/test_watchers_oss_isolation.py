# SPDX-License-Identifier: Apache-2.0
"""OSS-mode isolation: watchers must not call out to aztea.ai.

The watcher feature performs three categories of outbound network call:
  1. Fingerprint fetches (HTTP / git / pypi / npm) — user-supplied targets,
     NOT aztea.ai.
  2. Webhook delivery — user-supplied URL, NOT aztea.ai.
  3. Email delivery — user-supplied SMTP, NOT aztea.ai.

This test asserts that no path inside core.watchers ever issues a request
against ``aztea.ai`` or ``api.aztea.ai``. A failure here would mean a hosted
boundary leak.
"""

from __future__ import annotations

import os

# Strip hosted-mode env before importing core.watchers; the OSS build must
# not depend on it.
for _var in ("AZTEA_HOSTED_API_URL", "AZTEA_HOSTED_API_KEY"):
    os.environ.pop(_var, None)

from unittest.mock import patch

import pytest

from core import watchers as _watchers  # noqa: E402
from core.watchers import delivery as _delivery  # noqa: E402
from core.watchers import fingerprint as _fingerprint  # noqa: E402


def _explode_if_aztea(*args, **kwargs):
    url = args[0] if args else kwargs.get("url", "")
    if "aztea.ai" in str(url):
        raise AssertionError(
            f"OSS-mode watcher made a hosted-aztea call: {url!r}"
        )

    class _R:
        status_code = 200
        content = b"ok"
        headers = {}

        def iter_content(self, chunk_size=1024):
            yield self.content

        def close(self):
            pass

        def json(self):
            return {"info": {"version": "1.0.0"}, "version": "1.0.0"}

    return _R()


def test_http_fingerprint_does_not_touch_aztea_ai():
    with patch("core.watchers.fingerprint.requests.get", side_effect=_explode_if_aztea):
        fp, err = _fingerprint.fingerprint_target("http", "https://example.com", {})
    assert err is None
    assert fp is not None


def test_manifest_fingerprint_does_not_touch_aztea_ai():
    # Both pypi and npm.
    with patch("core.watchers.fingerprint.requests.get", side_effect=_explode_if_aztea):
        fp, err = _fingerprint.fingerprint_target(
            "manifest", "ignored", {"registry": "pypi", "package": "requests"}
        )
        assert err is None
        fp2, err2 = _fingerprint.fingerprint_target(
            "manifest", "ignored", {"registry": "npm", "package": "react"}
        )
        assert err2 is None


def test_webhook_delivery_does_not_touch_aztea_ai():
    posted: list[str] = []

    def _post(url, **kwargs):
        if "aztea.ai" in str(url):
            raise AssertionError(
                f"OSS-mode watcher webhook called hosted aztea: {url!r}"
            )
        posted.append(url)

        class _R:
            status_code = 200

        return _R()

    run = {
        "watcher_id": "wtch_x",
        "run_id": "wrun_x",
        "started_at": "2026-05-09T00:00:00+00:00",
        "fingerprint": "abc",
        "target_kind": "http",
        "target_url": "https://example.com",
        "delivery_webhook_url": "https://customer-hook.example.com/inbox",
        "delivery_email": None,
        "delivery_secret": "shhh",
        "owner_user_id": "user:1",
    }
    job = {"job_id": "j1", "agent_id": "a1", "status": "complete"}
    with patch("core.watchers.delivery.requests.post", side_effect=_post):
        _delivery.deliver_run(run, job)
    assert posted == ["https://customer-hook.example.com/inbox"]


def test_aztea_hosted_url_blocked_at_url_security():
    # If a customer accidentally sets target_url to api.aztea.ai we don't
    # want to special-case it — what we DO assert is that the watcher
    # doesn't pre-bake any hosted endpoints anywhere.
    sources = []
    import inspect
    for module in (_watchers, _watchers.crud, _watchers.fingerprint,
                   _watchers.sweeper, _delivery):
        try:
            sources.append(inspect.getsource(module))
        except (OSError, TypeError):
            continue
    blob = "\n".join(sources)
    assert "aztea.ai" not in blob, (
        "core.watchers must not bake in aztea.ai URLs. Customer-supplied "
        "URLs are validated at runtime; hardcoded ones break the OSS "
        "boundary."
    )
