# Initial local validation — 2026-11-02

Date: 2026-11-02. Linux amd64, Python 3.12.3, Docker Engine 29.9.2,
Compose 5.6.1 and uv 0.9.16. Runtime dependencies are locked in `uv.lock`
and exported with hashes in `requirements.txt`.

| Check | Observed result |
|---|---|
| Ruff lint/format and shell syntax | passed |
| Unit tests | 20 passed; includes session/policy checks, PDF extraction, DNS target checks and mocked Responses request |
| Compose deployment | nine services with successful healthchecks; existing data volumes preserved |
| Browser workflow | local unlock, create collection, ingest text, observe ready status, retrieve referenced answer |
| Browser layout | desktop 1440×1000 and mobile 390×844; screenshots inspected; no horizontal overflow |
| Knowledge integration | two isolated collections, background ingestion, text upload, deletion and local-only policy rejection |
| Worker recovery | stop worker, queue a source, restart worker, observe completed ingestion |
| Knowledge restore | isolated encrypted restore compared collections, source metadata, raw-upload hashes, all chunks and vectors |
| Legacy integration | INSERT idempotency, validation, proxy 413, actual pgvector retrieval and fixture cleanup |
| Telemetry | Prometheus scrape/rules, Grafana dashboard/datasources, actual Loki query through Grafana |
| Legacy restore | complete document/vector comparison; live DB unchanged; corrupted/wrong-key archives rejected |
| DB outage | readiness/query 503, liveness 200, alert firing, reachable runbook |
| Recovery | readiness/retrieval recovered, alert cleared, document snapshot unchanged |
| Load smoke | 20 liveness requests with four concurrent clients; observed max 0.015s in this run; no capacity claim |
| Python runtime audit | no known vulnerabilities; hash verification enabled |
| API/frontend image scan | superseded by the 2026-11-03 passing Alpine snapshot below; [report](SECURITY-SCAN.md) |
| CI workflow | actionlint passed locally; no GitHub CI run performed |
| Mermaid docs | eight diagrams rendered successfully to SVG; links and uppercase filenames checked |

OpenAI request construction was checked with a mocked SDK. No real provider key
was available, no paid model/embedding call was made, and account credit activation
or balance was not verified. `make model-smoke` is an explicitly invoked live
check after local key setup; it is excluded from offline tests and CI.

## Reproduce

```bash
make init
make up
make lint test
make knowledge-test
make integration
make failure-test
make load-test
make scan
make image-scan  # blocks HIGH/CRITICAL findings, including unfixed ones
```

Run write/restore/outage tests sequentially, without simultaneous imports or source
edits. Synthetic test collections and sources are removed by exact IDs in cleanup;
the user corpus is preserved. Knowledge restore comparisons assume quiescent data.

Local `.local/evidence/` contains ignored logs, scan reports, screenshots and
expected restore snapshots. Snapshots may contain source text and must remain
private. `.private/` holds local source manifests; `.secrets/` holds workspace,
provider and backup credentials. None are uploaded as CI artifacts. CI uploads
only the vulnerability report. The image scanner mounts only its image archive,
report file and vulnerability cache, not the knowledge snapshots or secrets.

## Limits of this evidence

Not established: live OpenAI response quality or model access, end-to-end semantic
embedding calls, automatic source refresh, concurrent multi-user isolation,
malicious-PDF sandbox qualification, crash recovery at every lease boundary,
production capacity, full browser accessibility, remote deployment, Hetzner/Ansible,
TLS, offsite disaster recovery, rollback, zero downtime or complete security approval.
Tempo and distributed tracing remain planned, not active.

The stack remains running locally. `make down` stops this project's containers
without deleting data volumes. The [workspace guide](KNOWLEDGE-WORKSPACE.md)
explains collection boundaries and model setup.

## CI scan remediation — 2026-11-03

The previously failing scan used Debian 13.7 and reported 45 HIGH package findings.
The API/worker now builds from a digest-pinned Alpine Python image with identical
hash-locked dependencies and binary wheels only. The strict local Trivy scan
passes with zero HIGH/CRITICAL findings across detected OS and Python packages.
This is not a suppression or a relaxed severity threshold.

The scan now emits a readable log/job summary and uploads both JSON and Markdown
reports. Regression tests cover unfixed HIGH findings, scanner operational errors
and invalid reports. All **26 unit tests** and Ruff checks pass. Native extensions
and authenticated encryption were exercised inside the Alpine image with no
network access or credentials. actionlint passed for the updated workflow.

An isolated Compose project with fresh empty provider credentials and separate
ports is used for runtime regression qualification, preserving the existing user
workspace. Actual qualification times remain 2026-11-03, independent of commit
timestamp normalization. Remote CI results must be checked on the fixed revision.

The isolated Alpine qualification passed: startup, legacy and knowledge ingestion,
collection isolation, worker stop/start recovery, both encrypted restore comparisons,
DB outage/alert/recovery and the 20-request load smoke. Test containers and volumes
were removed after qualification. No provider key or paid model call was used.
The initial unbounded-resolver trial timed out the Prometheus scrape; with explicit
resolver retry limits, the DB-down scrape stayed up at approximately 1.01 seconds
and `DatabaseUnavailable` fired. Scrape and alert deadlines were not relaxed.
