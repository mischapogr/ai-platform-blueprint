#!/usr/bin/env bash
# Scanner gets only the image archive, report file and vulnerability cache, never the Docker socket.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .local/evidence .local/trivy-user-cache
archive="$PWD/.local/evidence/api-image.tar"
trap 'rm -f "$archive"' EXIT
docker save -o "$archive" ai-platform-blueprint-api:local
rm -f .local/evidence/trivy.json .local/evidence/trivy-summary.md
(umask 077; : > .local/evidence/trivy.json)
scan_status=0
docker run --rm \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --user "$(id -u):$(id -g)" --env TRIVY_CACHE_DIR=/cache \
  -v "$archive:/input/api-image.tar:ro" \
  -v "$PWD/.local/evidence/trivy.json:/output/trivy.json" \
  -v "$PWD/.local/trivy-user-cache:/cache" \
  aquasec/trivy:0.76.1 image --input /input/api-image.tar \
  --scanners vuln --severity HIGH,CRITICAL --exit-code 1 --ignorefile '' \
  --format json --output /output/trivy.json || scan_status=$?
report_status=0
python3 scripts/summarize-image-scan.py .local/evidence/trivy.json "$scan_status" || report_status=$?
# Preserve scanner/network/tool failures even when a readable report says no findings.
if (( scan_status != 0 )); then
  exit "$scan_status"
fi
exit "$report_status"
