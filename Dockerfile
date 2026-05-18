FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/registry.db \
    WEB_CONCURRENCY=1 \
    GUNICORN_TIMEOUT=120 \
    GUNICORN_MAX_REQUESTS=1000 \
    GUNICORN_MAX_REQUESTS_JITTER=100

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Worker-side tool runtimes baked in so agents that shell out to specific
# binaries don't fail with `tool_unavailable` in prod. Each block notes which
# agent it serves so future image trims don't accidentally regress a path.
#
# pytest + coverage + pytest-asyncio: coverage_runner shells out to
# `coverage run --branch -m pytest`; ci_failure_reproducer re-runs failing
# pytest commands. Both returned 0%/low success because these were dev-only.
# checkov: hcl_terraform_analyzer invokes `checkov -d ... --output json`.
RUN pip install --no-cache-dir \
    pytest \
    pytest-asyncio \
    coverage \
    checkov

# hadolint: dockerfile_analyzer shells out to `hadolint --format json`. When
# absent, the agent falls back to regex heuristics and flags `degraded_mode`.
# Pin to a recent stable release; static binary, no apt dependency.
RUN curl -fsSL -o /usr/local/bin/hadolint \
        https://github.com/hadolint/hadolint/releases/download/v2.12.0/hadolint-Linux-x86_64 \
    && chmod +x /usr/local/bin/hadolint

# browser_agent + visual_regression + accessibility_auditor + lighthouse_auditor
# all need a real Chromium. `playwright install-deps` pulls in the (long) list
# of shared-libs Chromium needs on slim Debian, then `playwright install
# chromium` downloads the browser binary. We do this as root before dropping
# privileges. ~300MB image growth, but otherwise these agents return 0% success
# in prod.
RUN python -m playwright install-deps chromium \
    && python -m playwright install chromium \
    && rm -rf /var/lib/apt/lists/*

# lighthouse_auditor shells out to the Node-native lighthouse CLI. Installed
# globally so it's on PATH for the appuser. ~80MB.
# jest (and the npm CLI) is installed globally so ci_failure_reproducer can
# reproduce JS test failures the same way pytest covers Python ones.
RUN npm install -g lighthouse@11 jest@29 \
    && npm cache clean --force

# golang: ci_failure_reproducer also picks up `go test ...` commands from
# CI logs. Without a Go toolchain those re-runs reported "go: command not
# found" instead of the real failure. ~150MB but worth it for the agent to
# diagnose real Go failures rather than a missing-toolchain artifact.
RUN apt-get update \
    && apt-get install -y --no-install-recommends golang-go git \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["sh", "-c", "python -m core.migrate && exec gunicorn server:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers ${WEB_CONCURRENCY} \
  --bind 0.0.0.0:8000 \
  --timeout ${GUNICORN_TIMEOUT} \
  --max-requests ${GUNICORN_MAX_REQUESTS} \
  --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER} \
  --access-logfile - \
  --error-logfile -"]
