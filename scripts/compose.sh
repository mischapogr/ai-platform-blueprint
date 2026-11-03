#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
files=(-f infra/compose/compose.yaml -f infra/compose/compose.observability.yaml)
if [[ "${PLATFORM_TRACING:-0}" == 1 ]]; then
  files+=(-f infra/compose/compose.tracing.yaml)
fi
if [[ "${PLATFORM_LOCAL_MODELS:-0}" == 1 ]]; then
  files+=(-f infra/compose/compose.models.yaml)
fi
exec docker compose --project-name ai-platform-blueprint "${files[@]}" "$@"
