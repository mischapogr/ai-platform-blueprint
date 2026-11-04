.PHONY: init up down status logs seed smoke test lint integration failure-test backup restore-test check scan image-scan configure-openai migrate knowledge-test load-test model-smoke retrieval-eval queue-test tracing-test local-model-test semantic-eval answer-eval release-package rollback-test lab-up lab-publish lab-vm lab-provision lab-deploy lab-check lab-recover lab-destroy lab-stop lab-backup lab-diagnose lab-monitoring-test lab-monitoring-outage-test
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

retrieval-eval:
	.venv/bin/python scripts/evaluate-retrieval.py

queue-test:
	.venv/bin/python scripts/evaluate-retrieval.py --suite queue

tracing-test:
	.venv/bin/python scripts/evaluate-retrieval.py --suite tracing

local-model-test:
	.venv/bin/python scripts/qualify-models.py --suite all

semantic-eval:
	.venv/bin/python scripts/qualify-models.py --suite semantic

answer-eval:
	.venv/bin/python scripts/qualify-models.py --suite answers

release-package:
	.venv/bin/python scripts/release-artifacts.py

rollback-test:
	.venv/bin/python scripts/qualify-rollback.py

LAB_VM ?= primary
lab-up:
	.venv/bin/python scripts/local-lab.py init
lab-publish:
	.venv/bin/python scripts/local-lab.py publish
lab-vm:
	.venv/bin/python scripts/local-lab.py vm-apply --name $(LAB_VM)
lab-provision:
	.venv/bin/python scripts/local-lab.py provision --name $(LAB_VM)
lab-deploy:
	.venv/bin/python scripts/local-lab.py deploy --name $(LAB_VM)
lab-check:
	.venv/bin/python scripts/local-lab.py check --name $(LAB_VM)
lab-recover:
	.venv/bin/python scripts/local-lab.py recover
lab-destroy:
	.venv/bin/python scripts/local-lab.py vm-remove --name $(LAB_VM)
lab-stop:
	.venv/bin/python scripts/local-lab.py stop

lab-backup:
	.venv/bin/python scripts/local-lab.py backup-run --name $(LAB_VM)

lab-diagnose:
	.venv/bin/python scripts/local-lab.py diagnose --name $(LAB_VM)

lab-monitoring-test:
	.venv/bin/python scripts/local-lab.py monitoring-check --name $(LAB_VM)

lab-monitoring-outage-test:
	.venv/bin/python scripts/local-lab.py monitoring-outage --name $(LAB_VM)

lab-runtime-audit:
	.venv/bin/python scripts/local-lab.py runtime-audit
lab-workflow-check:
	.venv/bin/python scripts/local-lab.py workflow-run --name $(LAB_VM)
lab-restore-check:
	.venv/bin/python scripts/local-lab.py restore-run --name $(LAB_VM)
lab-schedule-install:
	.venv/bin/python scripts/local-lab.py schedule-install --name $(LAB_VM)
lab-improvements-test:
	.venv/bin/python scripts/local-lab.py improvements-check --name $(LAB_VM)

lab-qualified-publish:
	.venv/bin/python scripts/local-lab.py publish-qualified

.PHONY: lab-database-migrate lab-model-enable lab-model-test
lab-database-migrate:
	.venv/bin/python scripts/local-lab.py database-migrate --name $(LAB_VM)
lab-model-enable:
	.venv/bin/python scripts/local-lab.py models-enable --name $(LAB_VM)
lab-model-test:
	.venv/bin/python scripts/local-lab.py models-check --name $(LAB_VM)

lab-model-eval:
	.venv/bin/python scripts/local-lab.py models-evaluate --name $(LAB_VM)
