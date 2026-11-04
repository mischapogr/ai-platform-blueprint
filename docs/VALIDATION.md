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

## VM ingestion and operator monitoring — 2026-11-04

The browser's generic PDF error was reproduced through the real VM TLS proxy with
an entirely synthetic 99,394-byte PDF. It returned HTTP 413 before reaching the API.
The broad workspace rule's implicit priority exceeded the upload route's explicit
priority, selecting the general 64 KiB limit. Router precedence is now explicit;
the read-only proxy also has bounded temporary buffering for uploads above its
1 MiB in-memory threshold. Non-JSON gateway failures retain their HTTP status in
the UI, with an API trace ID when available.

The VM profile now includes Grafana, Loki, Alloy and Tempo alongside Prometheus,
Alertmanager and node exporter. Proxy logs omit raw paths, identity, headers and
bodies; API logs use normalized routes. An actual trace review exposed shared
ambient HTTP context, corrected by explicit ingress roots while retaining valid
remote traceparent propagation. Configuration reloads, Redis/worker health checks,
metadata log rotation and monitoring-target alerts were also qualified.

Executed locally against the signed current deployment:

- **67 unit tests**, Ruff lint/format, JavaScript syntax, Ansible syntax and diff
  whitespace checks passed. The fetch-wrapper test uses the actual frontend
  function against non-JSON 413/502/500 responses.
- **16 monitoring checks** passed: all nine scrape targets UP, provisioned dashboard,
  authenticated Grafana, valid PDF ingestion/retrieval above 64 KiB and 1 MiB,
  application/proxy size limits retained, parser failure distinguishable, proxy/API
  log queries through Grafana and independent traces containing exactly the
  corresponding worker job. A synthetic document-content canary was absent from
  fetched logs/traces. This was HTTP/API qualification; no new browser visual QA
  session was performed.
- **4 Tempo-outage checks** passed: API readiness and retrieval remained functional,
  the independent local receiver received firing and resolved monitoring-target
  alerts naming Tempo, and Tempo recovered.
- **11 deployment checks** passed again, including TLS/auth/origin denial,
  legacy/management route denial, worker ingestion, rate limiting, node metrics
  and a bounded database outage with external firing/resolved notification.
- Final diagnosis found **12 VM services running**, all nine scrapes UP, API and
  database readiness healthy, Redis ping successful, worker heartbeat current,
  no observed OOM/restarts and no running-image mismatch against the verified
  local release signature. Registry TLS/auth returned HTTP 200. Secret-directory
  mode was 0700, TLS key 0400 and the guest-only proxy rotation timer active.
- API bundle `api-release-219e34c8f93a`: strict Trivy **HIGH=0, CRITICAL=0**,
  with SBOM/checksums/provenance. The signature and negative signature checks passed.

The deliberately invalid synthetic PDFs remain in clearly named diagnostic
collections and explain failed-job counts; they are learning fixtures. Existing
user sources were preserved. The original private PDF was not accessed or copied.

Private evidence: `.local/lab/observability-receipt.json`,
`monitoring-outage-receipt.json`, `acceptance.json`, `diagnostics/status.json` and
`alerts/events.jsonl`. The [operator guide](LOCAL-OBSERVABILITY.md) explains the
queries and repeatable commands; [requirements review](REQUIREMENTS-AUDIT.md)
records remaining gaps. New cloud/CI execution, every-runtime-image security
qualification, model VM activation, scheduled/offsite recovery and human answer
review remain unqualified. The 2026-11-03 recovery timing belongs to that historical
application-data profile; monitoring persistence and its effect on RTO were not
requalified by this run.

## Local operational gap closure — 2026-11-04

Added outside-guest HTTP/TLS/body probes, PostgreSQL/Redis exporters, per-service
CPU/memory/throttle/OOM history, a local Grafana Alertmanager inbox and scheduled
workflow/backup/isolated-restore checks. No external alert destination or paid
provider is configured. The monitor roles cannot read knowledge tables or Redis
keys; SQL text and slow/command logs are excluded from exporter metrics.

Executed against the owned recovery VM:

- **71 unit tests**, Ruff lint/format, JavaScript syntax and diff checks passed.
- Exporter builds and Blackbox passed strict scans with **HIGH=0, CRITICAL=0**.
  PostgreSQL exporter v0.20.1 was rebuilt with updated Go dependencies; Redis
  exporter uses a checksum-pinned upstream revision with explicit privacy flags.
- The local development registry received digest-pinned Redis 7.4, Traefik,
  Prometheus, Alertmanager, Loki and Alloy updates that passed strict scans. Their
  runtime configuration and end-to-end checks are qualified separately below.
- Backup used a shared exported PostgreSQL snapshot for both dump and comparison.
  Upload/download/checksum/authentication succeeded; wrong key, corruption and
  missing-archive paths were rejected. Comparison data is now encrypted rather
  than retained as derived plaintext.
- The isolated database restore used no network, published ports or host data
  directories. All collections, source payload hashes, chunks and vectors matched
  the authenticated comparison; only that temporary database was removed.
- The synthetic ingestion/worker/offline-chat workflow passed and removed only its
  own collection UUID. Three project-owned user timers were installed and enabled;
  the workflow timer also triggered from the user manager. Backup/restore were
  invoked explicitly; their future calendar triggers are not yet observed.
- The **qualified** publication path was exercised and correctly refused
  publication on full-runtime security findings. No findings were suppressed.
  Remaining failing image families: Caddy, PostgreSQL/pgvector, node exporter,
  distribution registry, Grafana and Tempo. Controller aliases refer to the same
  registry/Caddy families. Full vulnerability reports and CycloneDX SBOMs are
  private under `.local/evidence/runtime-security/`. A successful development
  publication does not override this gate.

These additions do not establish offsite recovery, independent key custody,
monitoring-volume restoration, sustained-load behavior, generated-answer quality,
optional model deployment in the VM, cloud/CI qualification or human paging.
The [operator guide](LOCAL-OBSERVABILITY.md) contains the repeatable commands and
schedule cleanup. Actual final monitoring receipts remain private under
`.local/lab/`; earlier counts above describe their historical profiles.

Final checks after the runtime update completed: **19 operational checks** passed,
including all **16 configured scrape targets**, three positive outside-guest
probes, rejected TLS hostname/bad-response probes, restricted exporter access,
resource counters and successful scheduled checks. A Redis-exporter outage left
the API ready, appeared in Grafana's Alertmanager inbox and delivered local firing
and resolution events. The **16 PDF/log/trace checks**, **11 TLS/auth/dependency
checks** and **4 Tempo-outage checks** passed again against the updated services.
The last alert-rule reload was validated with the deployed `promtool`.

## P1 runtime and answer improvements — 2026-11-04

The strict runtime remediation uses reviewed source builds for node exporter,
distribution registry, PostgreSQL/pgvector/gosu and Tempo 2.10.10. The controller
TLS/auth proxy now uses the already pinned Traefik image. Grafana 13.3.0 retains
its configured official signed Prometheus/Loki/Tempo backends; unused backends
were removed without disabling plugin signature validation. The CPU-only Ollama
0.40.14 image is source-built with a pinned llama.cpp revision and contains no GPU
runtime or model weights.

The full scan/SBOM inventory now passes every image except Grafana: **two HIGH,
zero CRITICAL** findings remain in the required signed Tempo datasource dependency
(`CVE-2026-21728`, `CVE-2026-28377`). Qualified publication was attempted and refused
before publication. No ignore rule, unfixed-finding exception or false clean
qualification was added. Development publication remains explicitly unqualified.

The original PostgreSQL volume was retained. An isolated Alpine-candidate restore
matched every knowledge collection, payload hash, chunk and vector. The live
logical migration then paused the app, authenticated a fresh encrypted backup,
restored into a new owned volume, compared the data before exposing the app and
verified API/worker readiness. Subsequent synthetic ingestion/search passed.
Grafana's pre-upgrade SQLite snapshot was backed up consistently and encrypted.
This is not monitoring-volume or physical offsite recovery qualification.

The retained guest disk now boots with **8192 MiB / four vCPUs**. The model service
has no published port or outbound network, uses a read-only public-model cache,
no cloud/GPU configuration and a 4.5 GiB/two-CPU resource limit. A missing
`manifests-v2` directory in the new runtime was prepared without making model data
writable. A stale SSH-tunnel PID record was retired without signalling an
unrelated process. A model check that overlapped a proxy reload was discarded;
the completed deployment was checked again afterward.

Executed after deployment completed:

- **Nine VM model checks passed**, including 768D semantic worker indexing,
  persisted model identity, source-excerpt answers/citations, reported generation
  usage, unsupported-question abstention, source-instruction canary and explicit
  external-provider refusal. Only the synthetic collection was deleted. An earlier
  interrupted fixture was cleaned only after its full payload set matched the
  public synthetic templates.
- **Sixteen PDF/log/trace checks passed** through the upgraded Grafana/Tempo/API.
- **Nineteen operational checks passed**, covering every configured scrape, probes,
  exporter restrictions, resource history, workflow freshness and Grafana inbox
  firing/resolution. The Redis exporter was restored after its bounded outage.
- **87 unit tests** and Ruff/JavaScript/diff checks passed at this stage.
- The native v2 answer regression passed all automatic checks in 16 cases; human
  support/relevance review remains pending. A second run against the actual VM
  runtime captures ordered evidence for the review worksheet separately.

API bundle `api-release-062fbb54585d` passed strict HIGH=0/CRITICAL=0 scanning with
SBOM/checksums/source material hashes. The public UI explains that knowledge mode
selects source excerpts; general chat is separate free-form output. These are local
HTTP/API checks, not a new browser visual acceptance, throughput claim or remote CI
result. Private receipts remain under `.local/lab/` and `.local/evidence/`.

The actual VM runtime evaluation (`ai-blueprint-models-bd46529240db`) subsequently
completed 16 cases and exposed one retrieved conflicting-evidence scenario with
three failed automatic checks. The model quoted a real sentence but assigned it
to the other passage; the v1 renderer correctly refused the response. A synthetic
raw-response reproduction isolated the cause. `verbatim-citations-v2` now repairs
that label only when the exact quote uniquely matches one supplied passage. Text
absent from the references, invalid indices and ambiguous correction candidates
still abstain. The captured failure passed a deterministic rendering retest;
subsequent live retest results are recorded separately. Earlier failed reports
remain intact. This is a citation-resolution fix, not a semantic-support approval.

The final citation regression increased the unit suite to **89 passed**. The API
bundle `api-release-c9e5c542142d` passed strict HIGH=0/CRITICAL=0 scanning. The
post-migration isolated restore path also passed with the Alpine runtime UID.

The repaired conflicting-source case then passed through the **real VM HTTPS
API**, including fresh semantic ingestion, retrieval and local generation. Fact
coverage was 1.0, both supporting sources were cited, citation bounds were valid
and the answer no longer incorrectly abstained. Its own synthetic collection was
removed. The original 16-case failure report was preserved; this is a targeted
post-fix retest, not a claim that a second full 16-case run was performed. The
private human worksheet now includes the question, ordered source passages and
both original/retested evidence. Every human decision remains pending.

The upgraded deployment also passed **11 TLS/auth/dependency checks**, **four
Tempo-outage checks**, and its isolated restore check. The final fresh full-runtime
inventory covers **20 image roles** and still blocks only Grafana (two HIGH, zero
CRITICAL); all other roles, including the final API and CPU Ollama, passed. Local
qualified publication was refused as intended. No remote CI, public release,
commit or push was performed.

## Exact-artifact security qualification — 2026-11-04

The signed Tempo datasource was inspected without executing its binary. Its
SHA-256 matches the official 13.4.2 archive and running guest; Grafana reports a
valid Grafana Labs signature. The dependency's source revision contains both
upstream CVE fixes. Source imports and the actual binary function table include
only Tempo protobuf packages, excluding both affected server packages. See the
[public evidence profile and gate behavior](TEMPO-SOURCE-ASSESSMENT.md).

A fresh full inventory of **20 image roles** passed: **raw HIGH=2, CRITICAL=0;
verified not affected=2; unresolved HIGH=0, CRITICAL=0**. Raw scanner JSON and
SBOMs remain intact. Qualified publication bound the summary checksum into the
local signed envelope; signature verification and negative-verification checks
passed. This is a local consumer applicability assessment and local development
signing identity, not an upstream VEX statement or public release.

All **109 unit tests passed**, including artifact/version/path/CVE mismatch,
additional findings, changed evidence profile and forged receipt rejection.
Targeted Ruff checks passed. No ignore rule, scanner suppression or plugin
signature bypass was added.

The controller and retained recovery VM needed restarting after the host session
restart. Stale controller network references were repaired by recreating only
its four owned containers, preserving mounted data. A real Ollama restart loop
exposed startup pruning of an empty directory inside the read-only model cache.
The guest now sets `OLLAMA_NOPRUNE=1`; the model cache and root filesystem remain
read-only. Deployment completed against the existing disk and database volume.

No remote CI, public registry publication, commit or push was performed. Human
answer relevance/completeness review remains pending.

After recovery, all 15 services were running and all 16 scrape targets were UP.
HTTPS workspace/Grafana/readiness probes passed. The signed running image
inventory matched the qualified envelope. Grafana reported the Tempo plugin
signature as valid, and its Tempo readiness proxy returned HTTP 200 after warmup.
All **16 PDF/log/trace acceptance checks passed**, including independent upload
traces, API-to-worker spans, parser failures and absence of document content in
logs/traces. Historical synthetic parse failures were retained.

All **nine local CPU model workflow checks passed** after recovery, including
semantic worker ingestion, source-cited generation, usage accounting, external
provider refusal, abstention and source-instruction rejection. The test collection
was removed. Human answer-quality acceptance remains pending.

A further explicit Ollama restart became healthy and still listed both seeded
models, confirming the immutable-cache startup fix survives restart. The runtime
summary SHA-256 matched the value bound into the signed qualification envelope.
