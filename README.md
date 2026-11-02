# AI Platform Blueprint

A small self-hosted knowledge workspace with a browser interface, collection-scoped
retrieval, optional OpenAI chat and semantic embeddings, background ingestion,
monitoring, encrypted backups and isolated restore tests.

Import public web pages, PDF books, UTF-8 text or Markdown. Keep different areas
in separate collections. Search locally without a model key, or explicitly enable
external model processing for a collection. Public examples and CI remain synthetic;
real corpora, source manifests and API keys stay in local runtime storage.

## Start locally

Requirements: Linux, Docker Engine with Compose v2+, Python 3.12,
[uv](https://docs.astral.sh/uv/getting-started/installation/) and Make. Ports 8080,
3000 and 9090 must be available. Initial packages/images require internet access.
Default service memory limits total about 2.9 GiB; a restore test needs another
512 MiB plus host overhead. No GPU is required for this configuration.

```bash
make init
make up
```

Open **http://127.0.0.1:8080** and unlock the workspace using the value in
`.secrets/workspace_token`. Read it locally; do not paste it into chat, tickets or Git.

1. Create a collection for one coherent area of knowledge.
2. Select local lexical search or OpenAI semantic embeddings. The method is fixed
   for that collection; changing it requires a new collection and reimport.
3. Add a source in the Source library. Watch its background indexing status.
4. Ask a question in Knowledge mode and inspect the retrieved passages.

Local lexical search uses deterministic 128D word hashing; it is not semantic
model inference. General chat requires a configured OpenAI connection.

## Connect OpenAI privately

```bash
make configure-openai
make model-smoke      # optional real API call with synthetic input; consumes API credits
```

The hidden terminal prompt writes `.secrets/openai_api_key`, ignored by Git.
Reload the browser after first-time key setup to update connection availability.
The key is mounted only into the API/worker and is never returned to the browser.
The file is initially empty, so startup and synthetic tests work without paid calls.

Eligible Pro/Business subscriptions can claim API credits by linking an owned API
organization. Activation, balance and model access are account-specific; the app
reports actual token usage, not an invented credit balance.
[Official subscription benefit](https://developers.openai.com/api/docs/guides/chatgpt-api-credits).

Select OpenAI in the composer and consent to sending the question, recent
conversation and retrieved passages. A local-only collection blocks this path.
OpenAI embedding collections send extracted source text during indexing and queries
during retrieval. [Workspace and privacy details](docs/KNOWLEDGE-WORKSPACE.md).

## Runtime and operations

- Frontend: http://127.0.0.1:8080
- API reference: http://127.0.0.1:8080/docs
- Grafana: http://127.0.0.1:3000/d/platform-overview
- Prometheus alerts: http://127.0.0.1:9090/alerts

Grafana uses `admin` and the local `.secrets/grafana_password`. Existing secrets
are preserved by initialization; rotate credentials together with their services.

```bash
make lint test        # unit checks, no Docker or provider key required
make knowledge-test   # auth, ingestion, collection isolation, worker recovery, KB restore
make integration      # legacy retrieval, telemetry and encrypted restore
make failure-test     # intentional local DB outage, alert and recovery
make load-test        # small liveness concurrency smoke, no capacity claim
make backup
make restore-test
make scan             # locked Python runtime dependencies
make image-scan       # strict HIGH/CRITICAL gate for API image
make status
make logs
make down             # stop this project; preserve data volumes
```

The deployment script applies the additive KB migration before starting the new
application. It never deletes the existing database volume. PostgreSQL owns the
durable ingestion queue; Redis wakes the worker and carries heartbeat information.
The worker has bounded retries, per-job timeout and a recoverable lease.

Uploads and extracted passages live in PostgreSQL. Backup archives are encrypted
locally and restore into a separate temporary database without networking or live
volumes. The demo dump limit is 64 MiB; this is not an offsite backup strategy.

## Repository layout

```text
apps/api/                 API, collections, retrieval, provider and ingestion modules
apps/worker/              Background job supervisor
apps/demo-rag/frontend/   Same-origin browser workspace
infra/compose/            Active local deployment and additive SQL migration
infra/terraform/          Reserved M3 module; no resources implemented yet
infra/ansible/            Reserved M3 module; no playbooks implemented yet
observability/            Prometheus, alerts, Loki, Alloy and Grafana
observability/tempo/      Reserved tracing module; not started by Compose
scripts/                  Local setup, deployment, source import and recovery
tests/                    Unit, integration, failure and small load-smoke checks
docs/                     Uppercase Markdown documentation with Mermaid diagrams
```

[Documentation index](docs/README.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Roadmap](docs/ROADMAP.md) · [Validation](docs/VALIDATION.md)

This is a single-owner localhost application. Collections are context boundaries,
not independent multi-user security domains. The unauthenticated legacy synthetic
`/documents` and `/query` endpoints remain separate from workspace data. Public
hosting, multi-tenant identity, TLS, offsite recovery and distributed tracing remain
open work. The recorded [image scan](docs/SECURITY-SCAN.md) is not a production
security approval; its open findings must remain visible.

Contribution and commit conventions: [CONTRIBUTING.md](CONTRIBUTING.md).
Security reports: [SECURITY.md](SECURITY.md).

## License

License selection is pending. No open-source license is granted by this repository yet.
