from __future__ import annotations

import os
import re

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter

_SENSITIVE_ATTR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^http\.request\.header\.authorization$",
        r"authorization",
        r"password",
        r"secret",
        r"token",
        r"api[_-]?key",
        r"auth[_-]?header",
    ]
]


class RedactingSpanProcessor(SpanProcessor):
    def __init__(self, exporter: SpanExporter) -> None:
        self._inner = BatchSpanProcessor(exporter)

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        self._inner.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        raw = span.attributes
        if raw is None:
            self._inner.on_end(span)
            return
        attrs = dict(raw)
        redacted = False
        for key in list(attrs):
            for pattern in _SENSITIVE_ATTR_PATTERNS:
                if pattern.search(key):
                    attrs[key] = "[REDACTED]"
                    redacted = True
                    break
        if redacted:
            attrs["sieshka.redacted"] = "true"
        span._attributes = attrs
        self._inner.on_end(span)

    def shutdown(self) -> None:
        self._inner.shutdown()  # type: ignore[no-untyped-call]

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


def configure_otel() -> None:
    provider = TracerProvider()
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        exporter: SpanExporter = OTLPSpanExporter(endpoint=endpoint)
    else:
        exporter = ConsoleSpanExporter()
    provider.add_span_processor(RedactingSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
