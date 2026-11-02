#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Additive, idempotent migration; never delete existing data volumes.
./scripts/compose.sh exec -T db psql -U platform_admin -d platform -v ON_ERROR_STOP=1 \
  < infra/compose/postgres/002-knowledge.sql
