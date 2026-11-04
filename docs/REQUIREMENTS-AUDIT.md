# Platform requirements and operator evidence

Audit date: 2026-11-04. Scope: the local application, optional model profile and
Terraform/Ansible QEMU deployment. This is a capability/evidence review. A container
being present or running is insufficient to establish a successful user workflow.
Historical receipts retain their actual execution dates; changes since a receipt
are not silently treated as requalified. Public documents contain generic platform
requirements and synthetic examples only.

## Current requirements matrix

| Requirement | Implementation and evidence | Remaining boundary |
|---|---|---|
| Local application and containers | API, worker, PostgreSQL/pgvector, Redis and TLS proxy run in the VM; resource limits and non-root/read-only controls are configured | Sustained load, GPU operation and a public hosting profile are unqualified |
| UI/API ingestion | Text/Markdown and worker indexing were previously qualified; the PDF report exposed incorrect router precedence before API entry | The 2026-11-04 fix adds explicit router priority, proxy temporary buffering, visible HTTP errors and large-PDF acceptance |
| Public website ingestion | Bounded public HTTPS importer, robots policy, DNS/IP checks and source references | Five-page snapshots; no JavaScript rendering, full-site synchronization or trusted private CMS connector. Homepage/article extraction quality still needs broader fixtures |
| Semantic retrieval | Optional Ollama embeddings and measured synthetic retrieval benchmark; fixed local identity/dimensions | The VM now also has an optional CPU-only Ollama profile with semantic ingestion and local source-excerpt answers checked through HTTPS. Existing lexical collections retain their vectors. Broader semantic/no-match quality still needs work |
| Generated answers | Optional local/remote provider boundary, usage accounting and answer evaluation | V2 validates exact source excerpts/citations and abstains on invalid or empty evidence. Sixteen automatic checks passed; human relevance/completeness review remains pending |
| Request/worker tracing | OpenTelemetry context through API, durable job, worker and child; privacy allowlist and Tempo | Live upload testing exposed ambient trace inheritance. The fix and new regression require independent HTTP roots and exactly the intended job in each trace |
| Metrics/logging UI | Grafana, Loki, Alloy and Tempo existed in the workstation Compose profile but were missing from the initial VM profile | VM integration, provisioning, actual log/trace retrieval and desired-versus-running scrape checks are now part of the monitoring acceptance command |
| Monitoring coverage | Aggregate-only PostgreSQL/Redis exporters, outside-guest verified HTTPS/body/TLS probes, guest container memory/CPU/throttle/OOM history, workflow/recovery freshness and monitoring self-scrapes | All remain in one workstation failure domain; sustained load and alerting during workstation loss are unqualified |
| Human troubleshooting | Operator dashboard, status/error map, normalized request logs, trace links, read-only diagnostic command and runbooks | Local Grafana/Alertmanager inbox selected and connected. Unattended paging/escalation is outside the selected local-only scope |
| Alerts | Prometheus/Alertmanager route database outage firing/resolution to an independent local receiver | Receiver is on the same workstation. A Tempo outage delivered firing/resolution while the app kept working. The local inbox is selected; exporter firing/resolution and continued API readiness are tested separately. No off-host paging claim |
| CI and qualification | Locked dependencies, unit/lint, isolated retrieval/queue/tracing suites, integration/restore/outage and strict API scan | New GitHub CI run remains unverified until pushed/executed; optional model and VM tests are local opt-in |
| Releases | Scanned API archive, API SBOM, source checksums, local-key signatures, pinned/mirrored runtime images and pre-deployment verification | Strict full-runtime scan/SBOM and qualified-publication gate implemented; unresolved HIGH/CRITICAL findings block qualification; exact source applicability assessments retain raw findings. Public release identity and externally attested provenance remain open |
| Upgrade/rollback | Populated-data upgrade and compatible rollback; unsupported embedding-provider rollback refused | Direct application qualification is separate from zero-downtime/public traffic qualification |
| Recovery | Encrypted independent local store, separate key escrow, three-object retention, fresh-disk VM recovery and measured synthetic RPO/RTO | Project-owned user timers, failure/freshness alerts and authenticated isolated database restore comparison implemented. Independent key custody and physical offsite remain open. Monitoring storage is not covered by the database restore receipt |
| Infrastructure | Functional local Terraform/QEMU resource and guest-only Ansible configuration | Cloud provider modules, remote state, internet firewall/access policy and public DNS are unimplemented |
| Identity/isolation | Owner session, origin policy, collection processing policy and local-only provider refusal | Collections are not multi-tenant authorization boundaries; OIDC/RBAC/RLS remain deferred |
| Kubernetes/GitOps/gateway | Deliberately deferred until scheduling/scaling/environment requirements justify them | No Kubernetes, Argo CD, multi-provider routing/quota gateway or distributed serving claim |

## What the PDF incident exposed

1. A longer general workspace rule implicitly outranked the explicit upload rule.
   Requests therefore hit the 64 KiB general body limit instead of the upload limit.
   The API, parser and worker never received those rejected files.
2. The UI replaced non-JSON gateway errors with a generic message, hiding HTTP 413.
3. The proxy's read-only filesystem also lacked a writable spill location for
   requests over the buffering middleware's 1 MiB memory threshold.
4. Earlier small text/file tests did not exercise these boundaries through the
   actual VM TLS proxy. The new regression tests include 99 KiB and 1.5 MiB PDFs,
   the application/proxy limits and an intentionally invalid PDF.
5. Monitoring services in one deployment do not observe another deployment by
   implication. The VM now has its own log collection, provisioned Grafana sources
   and trace export. Reloading bind-mounted configuration is explicitly handled.
6. Merely finding worker span names was too weak: multiple jobs could have appeared
   in one inherited trace. The strengthened acceptance requires independent upload
   traces and the matching job ID, in addition to privacy checks.

The operator walkthrough is in [LOCAL-OBSERVABILITY.md](LOCAL-OBSERVABILITY.md).
The 2026-11-04 live run passed 16 upload/monitoring checks, 4 trace-backend outage
checks and 11 application/dependency/alert checks. All 12 VM services were running
and all nine scrape targets UP; 67 unit tests passed. The deliberately failed
synthetic PDF fixtures remain visible to demonstrate troubleshooting.

Runtime receipts remain private under `.local/lab/`, and reproducible commands and
executed checks belong in [VALIDATION.md](VALIDATION.md). The original private PDF
was not read, copied into fixtures, logged or used for qualification.

## Highest-value next requirements

| Priority | Requirement | Acceptance criterion |
|---|---|---|
| P0 — checked locally | Close operator visibility gaps | All expected scrapes UP; a real synthetic upload visible in Loki and an independent Tempo trace; proxy rejection and parser failure distinguishable |
| P1 — checked locally | Outside-the-guest HTTP/TLS probe | Detect broken routing, bad response and expiring certificate even when internal scrapes are green |
| P1 — checked locally | Safe database/cache diagnostics | Least-privilege exporters; panels for connections/locks, memory/evictions and latency; no SQL text/key/content leakage |
| P1 — checked locally | Workflow freshness and recovery freshness | Last successful synthetic ingestion/search and restore timestamps; failure alerts and documented cleanup/schedule ownership |
| P1 — checked locally | Runtime supply-chain coverage | Explicit scan/SBOM/update policy for each pinned runtime image, with strict failures and reviewed exceptions if any |
| P1 | Reviewed answer quality | Human evidence/citation decisions on held-out cases before a public generated-answer experience |
| P2 — checked locally | Per-container resource history | Explain OOM/restarts/throttling with a constrained collector and documented host-access scope |
| P2 — local inbox selected | Actionable human notification | Chosen destination, severity, user impact, runbook and tested firing/resolution |
| P2 — checked locally | Optional model VM profile | Explicit local-model deployment, resources and inference checks; separate from the already qualified native model experiment |
| P2 | Trusted CMS connector | Explicit endpoint/read policy, bounded synchronization and idempotent updates; private SSRF boundary retained |

Existing Alloy can later centralize OTLP collection and export health. A second
collector, continuous profiler, cluster or orchestration system should follow a
specific diagnostic/operational need rather than inflate the component count.

## Follow-up implementation

Outside-guest HTTPS/TLS/body checks, restricted PostgreSQL/Redis exporters,
container resource history, local Grafana alerts, scheduled workflow/backup and
isolated database restore checks now have runnable implementations. Job failures
retain the previous successful timestamp and trigger freshness/failure alerts.
User timers are local session services; there is no unattended/off-host availability
claim. The database restore compares an authenticated shared-snapshot archive and
keeps both archive and comparison encrypted.

The full-runtime SBOM/scan and qualified-publication gate is implemented and was
exercised: the initial run refused publication on unassessed HIGH/CRITICAL findings.
The P1 follow-up reduced the blockers to two HIGH findings in Grafana's required
signed Tempo plugin dependency; all other runtime images, including CPU Ollama,
passed the strict scan. That initial blocked run is retained as historical
evidence. The subsequent exact-artifact assessment and fresh full inventory
passed local qualification; see [Tempo assessment](TEMPO-SOURCE-ASSESSMENT.md).
See [dated validation](VALIDATION.md) for executed checks and the
[operator guide](LOCAL-OBSERVABILITY.md) for commands and remaining boundaries.

## P1 follow-up status

The CPU model profile is running on the retained 8 GiB/four-vCPU guest and passed
nine real HTTPS workflow checks. All 15 services were running, all 16 scrape
targets UP, and 19 operator checks plus PDF/log/trace and dependency-outage checks
passed. PostgreSQL was logically migrated to a clean Alpine-based image with the
original volume retained. The initial runtime scan blocked on two HIGH findings in the required signed
Grafana Tempo plugin dependency. The subsequent
[exact-artifact assessment](TEMPO-SOURCE-ASSESSMENT.md) verifies that its affected
server packages are absent and both upstream fixes are already present. Raw
findings remain visible; changed artifacts and additional findings still block.

The actual VM 16-case answer run exposed one conflicting-source citation-label
error. Exact unique-span citation resolution fixed it, and a targeted full HTTPS
ingestion/retrieval/generation retest passed. Invented text and ambiguous matches
remain rejected. Human relevance/context/completeness decisions are pending in
the private evidence worksheet. Broader semantic retrieval quality and held-out
adversarial evaluation remain separate from these small synthetic checks.

The follow-up full 20-role runtime gate passed on 2026-11-04: raw HIGH=2,
CRITICAL=0, exact-artifact not affected=2, unresolved=0. Qualified local
publication and signature/negative-verification checks passed. The two raw
findings were retained, not erased. Human answer review is still open.
