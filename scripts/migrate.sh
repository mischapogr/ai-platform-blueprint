#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Additive, idempotent migration; never delete existing data volumes.
for migration in infra/compose/postgres/[0-9]*.sql; do
  ./scripts/compose.sh exec -T db psql -U platform_admin -d platform -v ON_ERROR_STOP=1 \
    < "$migration"
done
