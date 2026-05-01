.PHONY: dev test test-venv docker migrate demo lint evals smoke alerts launch-check

dev:
	uvicorn server:app --reload

# Prefer project venv when present (avoids Anaconda/numpy segfaults and version skew).
test-venv:
	@bash -c 'set -e; test -d .venv && . .venv/bin/activate; export API_KEY=$${API_KEY:-test-master-key}; python -m pytest -q tests'

test:
	pytest tests/ -v

docker:
	docker compose up --build

migrate:
	python -m core.migrate

demo:
	python scripts/seed-demo.py

lint:
	flake8 .

# Launch readiness gates: each one is intended to be a hard CI/cron check.
# evals: runs the deterministic agent contract suite (tests/test_agent_golden_evals.py)
# smoke: runs the buyer-path harness against $$AZTEA_BASE_URL (needs AZTEA_API_KEY)
# alerts: collects /ops metrics and exits non-zero on any critical alert
# launch-check: bundles evals + alerts (smoke is excluded — it requires a live key)
evals:
	@bash -c 'set -e; test -d .venv && . .venv/bin/activate; python -m pytest -q tests/test_agent_golden_evals.py tests/test_launch_alerts.py'

smoke:
	@bash -c 'test -d .venv && . .venv/bin/activate; python scripts/production_smoke.py'

alerts:
	@bash -c 'test -d .venv && . .venv/bin/activate; python scripts/launch_alerts.py'

launch-check: evals
	@bash -c 'if [ -n "$$AZTEA_API_KEY" ]; then python scripts/launch_alerts.py; else echo "(skip alerts — set AZTEA_API_KEY to run them)"; fi'
