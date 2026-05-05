from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.propagators.textmap import Getter, Setter
from opentelemetry.trace import Span, SpanKind

TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"
TRACE_HEADERS = frozenset({TRACEPARENT_HEADER, TRACESTATE_HEADER})


class _Getter(Getter[dict[str, Any]]):
    def get(self, carrier: dict[str, Any], key: str) -> list[str] | None:
        value = carrier.get(key)
        if value is None:
            return None
        return [str(value)]

    def keys(self, carrier: dict[str, Any]) -> list[str]:
        return [str(key) for key in carrier]


class _Setter(Setter[MutableMapping[str, Any]]):
    def set(self, carrier: MutableMapping[str, Any], key: str, value: str) -> None:
        carrier[key] = value


def active_trace_fields() -> dict[str, str]:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}
    return {
        "trace_id": f"{span_context.trace_id:032x}",
        "span_id": f"{span_context.span_id:016x}",
    }


def inject_trace_headers(headers: Mapping[str, Any] | None = None) -> dict[str, Any]:
    carrier: dict[str, Any] = dict(headers or {})
    propagate.inject(carrier, setter=_Setter())
    return carrier


def extract_trace_context(headers: Mapping[str, Any] | None):
    return propagate.extract(dict(headers or {}), getter=_Getter())


@contextmanager
def relayna_span(
    name: str,
    *,
    headers: Mapping[str, Any] | None = None,
    attributes: Mapping[str, Any] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
):
    tracer = trace.get_tracer("relayna")
    with tracer.start_as_current_span(
        name,
        context=extract_trace_context(headers),
        kind=kind,
        attributes={key: value for key, value in dict(attributes or {}).items() if value is not None},
    ) as span:
        yield span


def span_trace_fields(span: Span | None) -> dict[str, str]:
    if span is None:
        return {}
    span_context = span.get_span_context()
    if not span_context.is_valid:
        return {}
    return {
        "trace_id": f"{span_context.trace_id:032x}",
        "span_id": f"{span_context.span_id:016x}",
    }


__all__ = [
    "TRACEPARENT_HEADER",
    "TRACESTATE_HEADER",
    "TRACE_HEADERS",
    "active_trace_fields",
    "extract_trace_context",
    "inject_trace_headers",
    "relayna_span",
    "span_trace_fields",
]
