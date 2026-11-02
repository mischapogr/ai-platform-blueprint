#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec docker compose --project-name ai-platform-blueprint \
  -f infra/compose/compose.yaml -f infra/compose/compose.observability.yaml "$@"
