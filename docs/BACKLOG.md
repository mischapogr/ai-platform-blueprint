# Capability backlog

Created: 2026-11-03. B01–B05 are implemented and locally qualified. B06 has live
evaluation evidence with human review pending. The owner selected fully local
infrastructure: B07/B08/B09 now have the local reference implementation described
in [LOCAL-LAB.md](LOCAL-LAB.md). Cloud deployment and physical offsite recovery
remain separate, unqualified scopes. No paid APIs or infrastructure were purchased.

The goal is a measurable, diagnosable knowledge workspace, followed by a
reproducible deployment. This backlog turns the capability audit into bounded
work packages and complements the milestones in [ROADMAP.md](ROADMAP.md).
Priorities reflect the current single-owner application, rather than a technology
scorecard. Estimates are rough focused engineering days, including validation and
documentation, excluding access delays and hardware procurement.

## Delivery order

| ID | Priority | Deliverable | Estimate | Dependencies | Roadmap |
|---|---|---|---|---|---|
| B01 | P0 | Reproducible retrieval benchmark — locally qualified | 2–4 days | None | M2 |
| B02 | P0 | Live semantic retrieval comparison — locally qualified | 1–3 days | B01/B05 | M2 |
| B03 | P0 | End-to-end request and ingestion traces — locally qualified | 3–5 days | None | M2 |
| B04 | P0 | Queue crash and lease qualification — locally qualified | 2–4 days | None | M2 |
| B05 | P1 | Local generation/embeddings — locally qualified | 3–6 days | B01 | M2 extension |
| B06 | P1 | Answer evaluation implemented; human review pending | 2–4 days | B01/B05 | M2 extension |
| B07 | P1 | Single-server deployment — local VM profile | 4–7 days | Local QEMU/Terraform/Ansible | M3 |
| B08 | P1 | Local artifact/signing/deployment and rollback | 3–5 days | Local OCI registry and development key | M4 |
| B09 | P1 | Fresh-VM recovery — local failure domain | 3–5 days | B07; local encrypted store/key escrow | M4 |
| B10 | P2 | Trusted CMS connector — planned | 2–4 days | Selected endpoint/read access | M2 extension |

B01 establishes the baseline before changing retrieval or chunking. B02/B05 now
provide a live local semantic comparison. B03/B04 add tracing and queue qualification.
B02 and B05 are alternative ways to obtain a semantic comparison; B05 does not
require buying external API access. The selected local deployment can be reproduced independently,
but does not establish retrieval or answer quality.

## B01 — Establish a retrieval benchmark

**Status:** Implemented and locally qualified on 2026-11-03. See
[metric definitions and baseline](RETRIEVAL-EVALUATION.md). CI is configured to run
the gate; a remote CI execution has not been verified.

**Problem:** Existing embedding and integration checks demonstrate functional
behavior, not retrieval quality on a labeled corpus.

**Starting points:** [retrieval.py](../apps/api/platform_api/retrieval.py),
[knowledge.py](../apps/api/platform_api/knowledge.py),
[ingestion.py](../apps/api/platform_api/ingestion.py),
[retrieval tests](../tests/unit/test_retrieval.py),
[knowledge integration](../tests/integration/check_knowledge.py).

Acceptance criteria:

- Add a versioned, synthetic corpus with approximately 40–60 questions, relevant
  source/text-span labels, paraphrases, distractors, unanswerable questions and
  cross-collection traps. Separate calibration cases from held-out regression cases.
- Use stable source/text-span identifiers; labels must survive regenerated chunk
  UUIDs and permit later chunking comparisons. Define how relevance maps to chunks.
- Exercise the application retrieval path against a disposable database. Report
  Recall@K and MRR at supported K values, plus the false-positive rate for
  unanswerable questions. Define denominators, ties and zero-result behavior.
- Record corpus version, code revision, embedding configuration, chunking settings
  and retrieval cutoff. Fix inputs and ordering for the offline baseline.
- Provide one documented command and a machine-readable report. Keep raw runs in
  ignored local evidence; publish only synthetic fixtures and reviewed baselines.
- Record initial measurements before choosing regression thresholds; enforce the
  selected thresholds in CI without external model calls. Collection leakage is
  always a failing result.

Out of scope: changing the retrieval algorithm or claiming semantic quality from
the deterministic hash baseline.

## B02 — Qualify semantic retrieval on the same benchmark

**Status:** Locally qualified with real nomic-embed-text embeddings on CPU. The
same 48-question corpus and unchanged cutoff yield regression Recall@5 0.921053
versus 0.815789 offline, with no collection leakage. Semantic retrieval returns
passages for all ten unanswerable questions. See [local models](LOCAL-MODELS.md)
and the [synthetic measurements](../tests/evaluation/local-baseline.json).

**Problem:** Mocked provider calls do not establish live embedding compatibility
or relevance quality.

**Starting points:** [models.py](../apps/api/platform_api/models.py),
[model smoke](../scripts/model-smoke.py),
[ingestion job](../apps/api/platform_api/ingest_job.py).

Acceptance criteria:

- Run B01 against at least one real embedding model with matching corpus,
  questions and retrieval settings. Use separate disposable collections for each
  embedding configuration; retain the offline comparison.
- Record model identity, dimensions, provider, dataset version and actual reported
  token usage where available. Keep estimates separate from measured usage/cost.
- Report relevance and latency results, failures and sample count. Preserve a
  disappointing result rather than changing the evaluation to imply improvement.
- Require explicit invocation for paid/provider-backed runs; never make default
  CI depend on credentials or model access. A B05 local model may supply this run.

## B03 — Trace requests and asynchronous ingestion

**Status:** Implemented and locally qualified on 2026-11-03. See the
[tracing guide](../observability/tempo/README.md). The disposable qualification
uses actual Tempo export, cross-process parent checks, retry attempts, a privacy
canary and a refused exporter connection. Remote CI and production load remain unverified.

**Problem:** Request logs and provider metrics exist, but cannot explain a single
operation across API, database, queue, worker child process and provider.

**Starting points:** [API middleware](../apps/api/platform_api/main.py),
[jobs.py](../apps/api/platform_api/jobs.py), [worker](../apps/worker/main.py),
[ingestion job](../apps/api/platform_api/ingest_job.py),
[models.py](../apps/api/platform_api/models.py),
[tracing configuration](../observability/tempo/README.md).

Acceptance criteria:

- Add an OpenTelemetry/Tempo development configuration and documented resource
  budget. Preserve operation when telemetry export is unavailable.
- Correlate a knowledge request with retrieval and provider work. Persist trace
  context with queued jobs and propagate it into the worker child process;
  represent retries as separate attempts with explicit correlation.
- Capture bounded operation names, durations, outcomes and retry/timeout state.
  Exclude source URLs, SQL parameters, document text, prompts, answers, secrets
  and authentication headers from exported telemetry.
- Demonstrate successful ingestion and querying, provider failure, worker retry
  and exporter outage using synthetic input. Inspect captured telemetry for leaks.
- Document how to find the trace from request/job identifiers and distinguish
  API metrics from worker/provider measurements.

## B04 — Qualify queue crash boundaries

**Status:** Implemented and locally qualified on 2026-11-03. Fresh attempt tokens
fence final persistence and failure writes. See the
[ingestion runbook](runbooks/KNOWLEDGE-INGESTION.md). Thirteen disposable scenarios
cover process crashes, ownership, retry, deletion and Redis loss. Timeout handling
uses an injected timeout exception; full wall-clock timeout behavior is not qualified.

**Problem:** A worker stop/start check does not cover every lease and transaction
boundary or overlapping execution after a lease expires.

**Starting points:** [worker](../apps/worker/main.py),
[ingestion job](../apps/api/platform_api/ingest_job.py),
[queue operations](../apps/api/platform_api/jobs.py),
[knowledge integration](../tests/integration/check_knowledge.py).

Acceptance criteria:

- In a disposable stack, deterministically interrupt execution after claim,
  during processing and around the final persistence transaction.
- Test expired-lease reclamation, retry exhaustion, non-retryable failures,
  Redis unavailability, source deletion during processing and a stale attempt
  completing after another attempt claims the job.
- Assert consistent source/job status, bounded attempts, no partial or duplicate
  active index and no source recreation after deletion. Add ownership/fencing
  protection if the stale-attempt scenario demonstrates a race.
- Separate logical index idempotency from external billing: a retried provider
  request can incur another charge even when database writes are atomic.
- Publish exact reproduction commands and bounded diagnostics without user data.

## B05 — Add a local model provider

**Status:** Implemented and locally qualified with Ollama 0.23.2, 768D
nomic-embed-text and llama3.2:3b generation. The authenticated API/worker flow
succeeds for a local-only collection and blocks external generation. Contract
checks cover missing usage, malformed vectors, context limits, timeout, identity
drift and retagging during inference. The non-root image passed startup smoke;
a complete Compose/browser model workflow remains unverified.
See [LOCAL-MODELS.md](LOCAL-MODELS.md).

**Problem:** Provider behavior is centralized but currently tied to one external
service. Local lexical search does not provide local model inference.

**Starting points:** [models.py](../apps/api/platform_api/models.py),
[collection and chat configuration](../apps/api/platform_api/knowledge.py),
[Compose](../infra/compose/compose.yaml),
[workspace guide](KNOWLEDGE-WORKSPACE.md).

Acceptance criteria:

- Define explicit generation and embedding capabilities behind the existing
  provider boundary. Select one local runtime after checking available hardware;
  treat API compatibility as something to test.
- Execute real local generation and embeddings, including ingestion and querying.
  Keep this runtime optional and preserve the default model-free workflow.
- Record endpoint, model identity and embedding dimensions; require a new index
  when embedding compatibility changes. Keep local/external processing policy
  explicit and prevent silent fallback to an external service.
- Validate timeouts, unavailable models, malformed vectors and context limits.
  Keep usage fields honest when a provider does not report tokens.
- Run B01 for the local embedding model and document hardware, memory, latency
  and result quality. GPU serving remains a separate option, not an acceptance
  requirement for this item.

## B06 — Evaluate answer quality separately

**Status:** Implemented and executed: eight synthetic cases each run with fixed
and retrieved evidence, versioned prompts, per-case outputs and a review rubric.
Observed failures include obeying a source instruction and fabricated citations
in a direct empty-evidence test. Literal/wording checks also produce false failures.
Human evidence-support review remains pending; there is no automated groundedness
approval. See [ANSWER-EVALUATION.md](ANSWER-EVALUATION.md).

**Problem:** Finding relevant passages does not establish factual support for a
generated answer, correct citations or appropriate abstention.

**Starting points:** [generation prompt](../apps/api/platform_api/models.py),
[chat flow](../apps/api/platform_api/knowledge.py), B01 corpus and labels.

Acceptance criteria:

- Version the generation prompt and evaluation configuration. Separate retrieval
  failures from generation failures using both fixed passages and retrieved input.
- Label expected supporting evidence, citation targets and unanswerable cases;
  include conflicting sources and source-embedded instructions.
- Define a reviewable rubric for evidence support, citation correctness and
  abstention. Distinguish deterministic checks, human review and optional model
  judging; record judge identity/configuration and limitations when used.
- Report per-case failures and aggregate results with sample count. Keep live
  generation opt-in; synthetic mocked responses establish contract behavior only.

## B07 — Deploy reproducibly to one server

**Status:** Local scope implemented: Terraform creates a disposable QEMU guest;
Ansible installs runtime packages and deploys signed digest-pinned images, TLS,
authentication, rate limits, host metrics and alerts. The synthetic acceptance
checks are recorded in `.local/lab/acceptance.json`. No cloud provider module,
public DNS, production firewall/access policy or remote test host is qualified.

**Starting points:** [Terraform boundary](../infra/terraform/README.md),
[Ansible boundary](../infra/ansible/README.md),
[deployment script](../scripts/deploy.sh), [threat model](THREAT-MODEL.md).

Acceptance criteria:

- Provision one authorized test server, firewall and storage; define private
  state storage, inventory and secret handling before applying infrastructure.
- Configure host access, updates, application deployment, TLS, authentication and
  rate limits appropriate to remote exposure. Do not expose the legacy synthetic
  endpoints or management ports without an explicit access policy.
- Rebuild from documented inputs and verify TLS, denied unauthenticated access,
  external alert delivery, disk alerts and resource budgets.
- Record cleanup and ongoing cost ownership. Infrastructure implementation requires
  current provider/tool documentation; this backlog creates no remote resources.

## B08 — Qualify release artifacts and rollback

**Status:** Local API/worker bundle, strict scan, CycloneDX SBOM, source
provenance/checksums and populated-data upgrade/rollback are qualified. The local
OCI registry publishes the verified archive; a development Cosign key signs the
image envelope and deployment checks it. Altered payloads/untrusted keys are
rejected. Runtime images are pinned/mirrored; their full scan/SBOM policy and
public release trust remain open. See [RELEASE-QUALIFICATION.md](RELEASE-QUALIFICATION.md).

**Starting points:** [CI](../.github/workflows/ci.yml),
[API image](../apps/api/Dockerfile), [Compose](../infra/compose/compose.yaml),
[deployment script](../scripts/deploy.sh).

Acceptance criteria:

- Identify releasable images by digest and generate an SBOM and provenance.
  Define signing identity and verify artifacts before deployment.
- Extend digest pinning to runtime dependencies with an explicit update path;
  preserve strict dependency/image scanning and visible failures.
- Test an application upgrade and rollback with populated synthetic data,
  including migration compatibility and a documented stop/restore path for
  incompatible schemas. Record the interruption; make no zero-downtime claim.
- Separate artifact build/qualification from authorized publication/deployment.

## B09 — Recover offsite backups on a fresh host

**Status:** A local encrypted backup store, independent controller key escrow,
three-object retention and fresh-QEMU-disk recovery drill are implemented. The
source VM/disk is removed, runtime credentials regenerated, recovered content and
vectors compared, and chat/new ingestion checked. Actual measured receipt:
`.local/lab/recovery-receipt.json`. This is one physical workstation. Physical
offsite storage, scheduled backups/failure escalation, independent key custody and
production RPO/RTO remain unqualified.

**Starting points:** [backups.py](../scripts/backups.py),
[restore test](../scripts/restore-test.sh), [DR guide](DISASTER-RECOVERY.md).

Acceptance criteria:

- Define an authorized offsite destination, encryption/key recovery, retention,
  scheduling, failure alerts and recovery ownership.
- Restore onto a fresh host without the original volumes, local secret directory
  or application host. Verify collections, source payload hashes, chunks and vectors.
- Measure RPO from the last recoverable backup and RTO through validated service
  recovery. Include retrieval and ingestion checks after restore.
- Exercise a missing/corrupted backup and key-recovery procedure; record results
  separately from the existing isolated local restore checks.

## B10 — Trusted CMS connector (planned)

P2, roughly 2–4 focused days; depends on an explicitly selected CMS endpoint and
read access. Public rendered HTTPS pages already use the generic importer. A
WordPress REST connector is not implemented and no existing CMS was accessed.

- Pin an explicit trusted endpoint/port and processing policy in private local
  configuration; preserve public-importer SSRF protections. Never forward
  credentials across origins or accept arbitrary UI-supplied private URLs.
- Support public posts/pages first, bounded pagination and stable source IDs;
  authenticated content needs a dedicated read-only credential and TLS trust.
- Track source links/modified times, make re-import idempotent, and define
  deletion/update behavior and ingestion limits before automatic synchronization.
- Qualify against a disposable WordPress fixture: import, worker completion,
  collection-scoped UI chat/search, update/re-import and credential-safe failure.
  JavaScript-rendered applications require a separate explicitly bounded adapter.

## Deferred scope

- **Hybrid retrieval, reranking and alternative chunking:** select experiments
  using B01 failures and compare against a held-out baseline before adoption.
- **Kubernetes/GitOps:** optional deployment track after a concrete scheduling,
  scaling or environment-management need is established. It is not required for
  the single-server path; define cluster/data ownership and rollback first.
- **OIDC/RBAC/tenant isolation:** a separate product/security change when multiple
  users are required. Collections currently remain single-owner context boundaries.
- **Gateway routing, quotas and fallback:** add when multiple providers or users
  create a demonstrated requirement; enforce processing policy on every route.
- **Isolated tool execution:** retains its existing M5 scope and independent
  review requirement; none of the work above introduces arbitrary tool execution.

## Completion evidence

An item is complete only when its acceptance criteria are checked, reproduction
commands and limitations are documented, and the evidence identifies the tested
revision/configuration. Record actual checks in [VALIDATION.md](VALIDATION.md).
Keep implementation, local execution, CI execution and remote recovery results
distinct. Update status and estimates as work reveals new constraints.
