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
At this initial validation point Tempo and distributed tracing were still planned;
the later B03 qualification below supersedes that tracing limitation.

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

## Synthetic retrieval baseline — 2026-11-03

B01 now has a [reproducible benchmark](RETRIEVAL-EVALUATION.md) with 14 synthetic
sources and 48 labeled questions, split evenly into calibration and regression
sets. The runner builds a dedicated image and invokes the actual ingestion and
retrieval functions against a fresh PostgreSQL/pgvector database on an internal
Docker network. No existing workspace volume or secret is mounted, and no model
provider is called. Chunking settings and the retrieval cutoff were extracted
into named constants without changing their values or ranking behavior.

Observed local results:

- `make lint test`: Ruff checks passed; all 30 unit tests passed, including metric,
  relevance-label and regression-gate checks.
- Baseline recording followed by `make retrieval-eval` on a fresh disposable
  database: passed with identical per-case retrieval results and aggregate metrics.
- Regression split: Recall@5 0.815789 and MRR@5 0.721053; four of five unanswerable
  questions returned passages. Across both splits, nine of ten did so.
- No collection leakage observed. The high unanswerable false-positive rate is
  retained as a visible limitation, rather than hidden by baseline tuning.
- Updated CI workflow passed local actionlint validation. The workflow now runs
  the quality gate; no remote CI execution was verified for this change.
- Cleanup audit found no remaining benchmark-labeled containers or networks.

Reviewed synthetic measurements are in `tests/evaluation/baseline.json`. Full
per-case reports remain in ignored `.local/evidence/ai-blueprint-eval-*.json`.
Source hashes identify the tested uncommitted application and benchmark code in
addition to the parent Git revision. These results do not qualify HTTP/session
behavior, worker scheduling/crash recovery, live semantic embeddings, generated
answer quality or production relevance. No existing workspace integration or
outage test was rerun for this change.

## Tracing and queue ownership — 2026-11-03

B03 and B04 add optional manual OpenTelemetry/Tempo tracing, durable traceparent
propagation and ownership fencing for ingestion attempts. Migration 003 adds
nullable attempt-token and traceparent columns. Deployment stops the previous
worker before applying the additive migrations and starting the new version.
Existing volumes and workspace contents were not changed by qualification.

Local evidence:

- Ruff checks and all 34 unit tests passed, including exception/HTTP privacy,
  traceparent propagation and resource-attribute checks.
- `make retrieval-eval` retained the B01 baseline without regeneration or tuning.
- `make queue-test` covers 13 scenarios: crashes after claim, during processing,
  before commit and after commit; stale success/failure; expired ownership and
  retry exhaustion; manual retry; non-retryable errors; timeout handling; bounded
  provider retries/backoff; deletion; Redis loss; and skipping a locked source.
- `make tracing-test` verified actual OTLP export into Tempo, HTTP-to-worker-child
  parent links, two ingestion attempts within one trace, provider error status,
  absence of a synthetic private-data canary and successful retrieval during a
  refused exporter connection. The provider failure occurs before network access;
  it is not a live model qualification.
- The first extended queue rerun exceeded the 120-second deadline under the
  Redis client's default retry/backoff policy. Client retries were then explicitly
  disabled for these best-effort operations. The final queue test retains that
  deadline and requires processing during Redis refusal within five seconds.
- The updated locked dependencies passed pip-audit. The qualification image's
  strict Trivy scan reported zero HIGH/CRITICAL findings without suppression.
- The optional Compose configuration and updated workflow passed local validation
  (`compose config --quiet`, actionlint); no remote CI execution was verified.

The input hash in final reports covers application/worker/evaluation code, schema,
corpus and locked runtime inputs; the runner rejects edits during execution.
Reports remain in ignored `.local/evidence/ai-blueprint-eval-*.json`. The timeout
handler uses an injected exception rather than a 180-second wall-clock run. No
production throughput, malicious-PDF isolation, live semantic quality, remote
deployment or offsite recovery claim follows from these checks.

## Local infrastructure qualification — 2026-11-03

The owner selected a fully local reference environment and a standard OCI registry.
This evidence is separate from the original Compose workspace and GitHub Actions.
Terraform 1.16.5 created real Ubuntu QEMU guests using KVM, 2 vCPUs and 4 GiB RAM;
Ansible Core 2.20.2 configured the guest. No cloud resources were provisioned.

- API bundle `api-release-608812d5e0fc`: strict Trivy HIGH=0, CRITICAL=0,
  CycloneDX SBOM, checksum/source provenance and license files.
- TLS/authenticated OCI publication preserved each scanned/pinned image config
  identity. Cosign 3.1.4 verified the local-key envelope; altered payload and
  unrelated-key checks failed as required. No external signing service was used.
- Final current-image upgrade/rollback also passed all three checks in
  `.local/evidence/ai-blueprint-rollback-cc33f316ee52.json`: preserved existing
  index, candidate writes readable after rollback, unsupported local-provider
  metadata refused before rollback.
- The original and recovered guests each passed 11 synthetic acceptance checks: HTTPS UI, HTTP redirect,
  unauthenticated/origin denial, blocked legacy/management routes, ingestion/chat,
  HTTP 429, guest disk/memory metrics, outage liveness and external firing/resolved
  database alerts. The registry/alert receiver remained outside the guest.
- Fresh-disk recovery removed the primary guest/disk, created `recovery`, regenerated
  runtime credentials and restored from authenticated encrypted storage with the
  separate controller escrow key. Collections, source SHA-256 payload hashes,
  chunks and vectors matched. The post-backup write was absent; restored search
  and new ingestion passed. Wrong-key/corrupt archives were rejected before restore. A subsequent manual
  recovery-guest backup also verified HTTP 404 for a missing archive.
- Measured synthetic RPO window: **5.18 s** from backup start to the drill incident.
  Measured RTO: **150.04 s**, including destruction, fresh VM creation, provisioning,
  signed-image deployment, restore and application verification. These workstation
  measurements are not service-level objectives or physical-host-loss evidence.
- Ruff and 62 unit tests passed, including TLS-origin/cookie checks and the local
  registry query-authentication/archive/recovery boundaries. New CI results are
  unverified; the live acceptance exercises HTTP APIs, not a new browser session.

Ignored receipts: `.local/lab/signing-receipt.json`, `acceptance.json`,
`recovery-receipt.json`, VM metadata and Ansible logs. Reproduction, port discovery,
trust/key handling and cleanup are in [LOCAL-LAB.md](LOCAL-LAB.md).

Remaining scope: human answer review (B06), a trusted CMS connector (B10), full
runtime dependency security/release policy, scheduled backups/failure escalation,
independent key custody, cloud deployment and physical offsite recovery.
