import json

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from platform_api import main, telemetry


@pytest.fixture
def captured(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({"service.name": "privacy-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_provider", provider)
    yield exporter
    provider.shutdown()


def test_exception_details_and_unknown_attributes_cannot_escape(captured):
    canary = "SYNTHETIC-PRIVATE-EXCEPTION-731"
    with pytest.raises(RuntimeError):
        with telemetry.span("qualification.error"):
            raise RuntimeError(canary)
    with pytest.raises(ValueError, match="Unapproved"):
        with telemetry.span("qualification.attributes") as active:
            telemetry.attributes(active, **{"prompt": canary})
    for active in captured.get_finished_spans():
        assert not active.events
        assert active.status.description is None
        assert canary not in active.to_json()


def test_only_traceparent_propagates_between_attempts(captured):
    with telemetry.span("queue.enqueue"):
        parent = telemetry.traceparent()
        trace_id = telemetry.trace_id()
    with telemetry.span("worker.attempt", parent=parent):
        assert telemetry.trace_id() == trace_id
    spans = captured.get_finished_spans()
    assert spans[1].parent.span_id == spans[0].context.span_id


def test_http_export_excludes_raw_paths_headers_and_bodies(captured, monkeypatch, tmp_path):
    canary = "SYNTHETIC-PRIVATE-HTTP-831"
    monkeypatch.setenv("REQUEST_LOG", str(tmp_path / "requests.jsonl"))
    with TestClient(main.app) as client:
        response = client.get(
            "/missing/" + canary,
            headers={
                "Authorization": canary,
                "baggage": "private=" + canary,
                "tracestate": "private=" + canary,
            },
            params={"token": canary},
        )
        assert response.status_code == 404
        assert len(response.headers["X-Trace-ID"]) == 32
    payload = json.dumps([s.to_json() for s in captured.get_finished_spans()])
    assert canary not in payload
    assert canary not in (tmp_path / "requests.jsonl").read_text()


def test_resource_environment_is_not_imported(monkeypatch):
    monkeypatch.setattr(telemetry, "_provider", None)
    monkeypatch.delenv("PLATFORM_TRACE_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "private.attribute=synthetic-secret")
    telemetry.configure("platform-test")
    try:
        assert dict(telemetry._provider.resource.attributes) == {"service.name": "platform-test"}
    finally:
        telemetry.shutdown()


def test_http_requests_start_independent_traces_despite_ambient_context(
    captured, monkeypatch, tmp_path
):
    monkeypatch.setenv("REQUEST_LOG", str(tmp_path / "requests.jsonl"))
    with TestClient(main.app) as client, telemetry.span("unrelated-background"):
        ambient = telemetry.trace_id()
        first = client.get("/missing").headers["X-Trace-ID"]
        second = client.post("/missing", content=b"synthetic").headers["X-Trace-ID"]
        assert len({ambient, first, second}) == 3
        remote = "1234567890abcdef1234567890abcdef"
        result = client.get(
            "/missing",
            headers={
                "traceparent": "00-" + remote + "-1234567890abcdef-01",
                "baggage": "private=synthetic-secret",
            },
        )
        assert result.headers["X-Trace-ID"] == remote
    http_spans = [s for s in captured.get_finished_spans() if s.name == "http.request"]
    assert http_spans[0].parent is None and http_spans[1].parent is None
    assert http_spans[2].parent.is_remote
