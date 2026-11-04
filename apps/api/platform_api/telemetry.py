"""Manual, allowlisted tracing: no automatic request, SQL or exception capture."""

import logging
import os
from contextlib import contextmanager
from functools import wraps

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_provider = None
_propagator = TraceContextTextMapPropagator()
ATTRIBUTES = {
    "http.route",
    "http.method",
    "http.status_code",
    "job.id",
    "job.attempt",
    "operation.outcome",
    "provider.operation",
}


def configure(service):
    global _provider
    if _provider is not None:
        return
    # Avoid environment/host/process resource detectors and auto-instrumentation.
    _provider = TracerProvider(resource=Resource({"service.name": service}))
    endpoint = os.getenv("PLATFORM_TRACE_ENDPOINT")
    if endpoint:
        # Exporter failure diagnostics can contain response bodies; keep them out
        # of application logs. Missing traces are diagnosed via Tempo readiness.
        logger = logging.getLogger("opentelemetry")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        _provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint, timeout=0.5),
                max_queue_size=256,
                max_export_batch_size=32,
                schedule_delay_millis=1000,
                export_timeout_millis=1000,
            )
        )


def shutdown():
    global _provider
    if _provider is not None:
        _provider.force_flush(timeout_millis=1500)
        _provider.shutdown()
        _provider = None


def attributes(active, **values):
    for key, value in values.items():
        if key not in ATTRIBUTES:
            raise ValueError("Unapproved telemetry attribute")
        active.set_attribute(key, value)


def traceparent():
    carrier = {}
    _propagator.inject(carrier)
    return carrier.get("traceparent")


def annotate(**values):
    attributes(trace.get_current_span(), **values)


def trace_id():
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


@contextmanager
def span(name, *, parent=None, root=False, **values):
    tracer = _provider.get_tracer("platform") if _provider else trace.NoOpTracer()
    # Server ingress must not inherit context left by connection/body processing.
    context = (
        _propagator.extract({"traceparent": parent}, context=Context())
        if parent
        else Context()
        if root
        else None
    )
    with tracer.start_as_current_span(
        name,
        context=context,
        record_exception=False,
        set_status_on_exception=False,
    ) as active:
        attributes(active, **values)
        try:
            yield active
        except Exception:
            active.set_status(Status(StatusCode.ERROR))
            attributes(active, **{"operation.outcome": "error"})
            raise


def traced(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with span(name):
                return function(*args, **kwargs)

        return wrapped

    return decorate
