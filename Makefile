.PHONY: init up down status logs seed smoke test lint integration failure-test backup restore-test check scan image-scan configure-openai migrate knowledge-test load-test model-smoke
init:
	uv sync --frozen
	.venv/bin/python scripts/init-local.py
up:
	./scripts/deploy.sh
down:
	./scripts/compose.sh down
status:
	./scripts/compose.sh ps
logs:
	./scripts/compose.sh logs --tail=80
seed:
	.venv/bin/python scripts/seed.py
smoke:
	.venv/bin/python scripts/smoke.py
test:
	.venv/bin/pytest
lint:
	.venv/bin/ruff check apps scripts tests
	.venv/bin/ruff format --check apps scripts tests
integration:
	.venv/bin/python tests/integration/check_stack.py
failure-test:
	.venv/bin/python tests/failure/database_outage.py
backup:
	./scripts/backup.sh
restore-test:
	./scripts/restore-test.sh
check: lint test smoke integration knowledge-test failure-test load-test
scan:
	.venv/bin/pip-audit -r requirements.txt --disable-pip --require-hashes

image-scan:
	./scripts/scan-image.sh

configure-openai:
	.venv/bin/python scripts/configure-openai.py
migrate:
	./scripts/migrate.sh
knowledge-test:
	.venv/bin/python tests/integration/check_knowledge.py
load-test:
	.venv/bin/python tests/load/local_smoke.py

model-smoke:
	.venv/bin/python scripts/model-smoke.py
