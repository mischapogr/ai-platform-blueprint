# API image security scan

Current local snapshot: 2026-11-03, Trivy 0.76.1, Linux amd64.
This is a scoped vulnerability scan, not a complete security audit.

## Current result

- **0 HIGH and 0 CRITICAL** findings in the API/worker image.
- Both OS packages and installed Python packages were detected and scanned.
- Gate policy remains HIGH/CRITICAL, including unfixed vulnerabilities. No allowlist,
  `ignore-unfixed`, `continue-on-error` or vulnerability suppression is used.
- The API/worker runtime now uses a digest-pinned Alpine Python base with the same
  hash-locked Python dependencies. Binary wheels are required; no compiler/toolchain
  is installed to build dependencies in the runtime image.

Image ID: `sha256:52f197597753914fef8e8b5db47f1ab91e395b65afdffa6ea460c7253f345927`.
Base: `python:3.12-alpine3.24@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8`.
Detected OS: alpine 3.24.3.
Scan time: `2026-11-03T05:39:41.326392418Z`.

This snapshot applies to the identified image only. Later builds and vulnerability
DB updates require another scan. ARM64 is not qualified by this local amd64 run.

## Why the previous GitHub run failed

The 2026-11-02 Debian 13.7 image produced 45 HIGH package findings across nine CVE
IDs, with no fixed version supplied in that report. Trivy correctly returned 1;
Make translated that into exit code 2. The scanner had completed successfully,
but JSON-only output hid the findings from the job log.

The new base removes that affected Debian package set. This does not assert that
upstream Debian has fixed those CVEs or that every service image is vulnerability-free.
The previous failing run is [41524422926](https://github.com/mischapogr/ai-platform-blueprint/actions/runs/41524422926).

## Runtime compatibility

The Alpine transition initially exposed a DNS retry difference during the DB-stop
scenario: `/metrics` exceeded Prometheus's four-second scrape timeout, so the
DB gauge could not reach the alert evaluator. API resolver options explicitly
bound DNS retries (`timeout:1`, `attempts:1`) while retaining the existing DB
connection/statement timeouts, scrape deadline and alert hold time. Qualification
must cover this outage/recovery path, not just startup and an empty vulnerability report.

## Reproduction and failure behavior

```bash
make image-scan
```

The scan keeps `.local/evidence/trivy.json` and emits a readable package/advisory
summary to stdout, `.local/evidence/trivy-summary.md` and GitHub's job summary.
CI uploads both reports. Previous report files are removed before scanning.
Only the image archive, report file and vulnerability cache are mounted into the
scanner; it receives no Docker socket, credentials or knowledge snapshots.

Regression checks cover a clean report, an unfixed HIGH finding, an operational
scanner failure and invalid/empty reports. Findings remain blocking, and scanner
failures are never replaced by a successful report-rendering exit status.

## Evidence boundary

The local strict gate passes. The fixed revision still needs an actual GitHub CI
run before remote CI success can be claimed. Other service images, MEDIUM/LOW
findings, malicious-input reachability, secret/IaC scans, host security and a full
independent review are outside this snapshot. Production approval is not granted.
