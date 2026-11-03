# Optional distributed tracing

The application uses manual OpenTelemetry spans and an optional Tempo development
service. Default Compose startup keeps Tempo disabled. Prometheus metrics and
request logs remain available independently.

```bash
PLATFORM_TRACING=1 make up
make tracing-test
```

The first command changes the normal local deployment; the second qualifies a
separate disposable PostgreSQL/Tempo environment and does not require changing the
workspace. Use the same flag for operations on the tracing deployment, for example
`PLATFORM_TRACING=1 make down`. That stops its containers and preserves application
volumes. Starting again without the flag selects the default deployment.

The overlay adds 384 MiB of service memory and 0.5 CPU, with a 128 MiB temporary
trace store. No Tempo or OTLP ports are published. API, worker and Tempo communicate
on the internal telemetry network. This is a development configuration: traces
are lost when Tempo's temporary storage is recreated; the 24-hour retention setting
does not constitute durable observability storage or a capacity guarantee.

## Find an operation

1. Enable tracing and open Grafana Explore; choose the provisioned Tempo datasource.
2. Copy the `X-Trace-ID` response header, or the `trace_id` in a sanitized request log.
3. Look up that trace ID. Ingestion includes `queue.enqueue`, `worker.attempt`,
   `ingestion.attempt`, `ingestion.process`, `db.transaction` and embedding adapter
   spans. Knowledge queries include retrieval and generation/embedding operations.
4. A queued job persists only its W3C traceparent. Each worker attempt has its job
   UUID, attempt number and bounded outcome. The child inherits the attempt context.
   Retries keep the enqueue trace ID and have separate attempt spans.

Job UUIDs can be matched against the source creation response or the spans' `job.id`.
Database spans describe transaction duration, not individual SQL statements.
Embedding adapter spans include offline hashing; they do not by themselves prove
that a remote model call occurred. Existing model metrics count actual provider
calls/tokens separately; worker spans describe ingestion attempts rather than HTTP
request latency.

When Tempo is disabled, the datasource is still provisioned and reports unavailable
if selected. Request correlation IDs can still be generated locally, but traces
are not exported without `PLATFORM_TRACE_ENDPOINT`.

## Privacy and failure behavior

Instrumentation is explicit, with an attribute allowlist. Raw paths, query strings,
URLs, titles, document text, prompts, generated answers, SQL parameters, model
credentials and authentication headers are excluded. Exceptions set an error
status without recording their messages, descriptions or stack traces. Propagation
uses traceparent only; baggage and tracestate are not persisted or forwarded.
Resource attributes contain the fixed service name, without host/environment
detectors. Do not enable automatic instrumentation or payload capture around this
boundary without a privacy review.

OTLP export is asynchronous with a bounded queue and short timeout. Export failure
does not fail application work. Exporter diagnostic bodies are suppressed to keep
remote response content out of application logs. Use Tempo readiness and trace
availability to diagnose export failure; absent spans are not successful evidence.

`make tracing-test` exercises actual OTLP export into Tempo, request-to-child parent
links, provider failure before network access, a failed/retried ingestion, a privacy
canary and application operation during a refused exporter connection. It uses
synthetic data and no model credentials. It does not qualify a live model provider
or tracing throughput under production load.

Reference versions qualified locally: OpenTelemetry Python 1.45.2 and Tempo 2.8.2.
Configuration follows the [Tempo receiver documentation](https://github.com/grafana/tempo/blob/v2.8.2/docs/sources/tempo/release-notes/v2-7.md)
and [OpenTelemetry Python SDK](https://github.com/open-telemetry/opentelemetry-python).
