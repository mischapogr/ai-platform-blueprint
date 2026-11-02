#!/usr/bin/env bash
# Local deployment only. Remote delivery and rollback are roadmap M3/M4.
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/compose.sh up --detach --wait --wait-timeout 90 db redis
./scripts/migrate.sh
./scripts/compose.sh up --detach --build --wait --wait-timeout 180
.venv/bin/python scripts/seed.py
.venv/bin/python scripts/smoke.py
