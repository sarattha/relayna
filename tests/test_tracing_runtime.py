from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from opentelemetry import baggage, propagate
from opentelemetry.context import Context
from opentelemetry.propagators.textmap import TextMapPropagator
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON
from opentelemetry.trace import Link, SpanContext, SpanKind, StatusCode, TraceFlags

from relayna.observability import active_trace_fields, extract_trace_context, inject_trace_headers, relayna_span
from relayna.observability import tracing as tracing_module

_VALID_TRACE_ID = int("11111111111111111111111111111111", 16)
_VALID_PARENT_SPAN_ID = int("2222222222222222", 16)
_VALID_TRACEPARENT = "00-11111111111111111111111111111111-2222222222222222-01"


def _provider(sampler: Any) -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=sampler)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_sampled_spans_preserve_parent_nested_link_attribute_and_error_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, exporter = _provider(ALWAYS_ON)
    monkeypatch.setattr(tracing_module, "_TRACER", provider.get_tracer("relayna"))
    linked_context = SpanContext(
        trace_id=int("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 16),
        span_id=int("bbbbbbbbbbbbbbbb", 16),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    app_tracer = provider.get_tracer("application")

    try:
        with app_tracer.start_as_current_span("application.linked", links=(Link(linked_context),)) as app_span:
            with relayna_span(
                "relayna.consumer.task_message",
                headers={"traceparent": _VALID_TRACEPARENT},
                attributes={"kept": "value", "dropped": None},
                kind=SpanKind.CONSUMER,
            ) as consumer_span:
                assert active_trace_fields()["trace_id"] == f"{_VALID_TRACE_ID:032x}"
                nested_headers = inject_trace_headers()
                with relayna_span("relayna.nested", headers=nested_headers) as nested_span:
                    assert active_trace_fields()["trace_id"] == f"{_VALID_TRACE_ID:032x}"
            assert consumer_span.get_span_context().trace_id == _VALID_TRACE_ID
            assert nested_span.get_span_context().trace_id == _VALID_TRACE_ID
            assert app_span.get_span_context().trace_id != _VALID_TRACE_ID

        with pytest.raises(ValueError, match="boom"):
            with relayna_span("relayna.error"):
                raise ValueError("boom")
    finally:
        provider.shutdown()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    consumer = spans["relayna.consumer.task_message"]
    nested = spans["relayna.nested"]
    linked = spans["application.linked"]
    error = spans["relayna.error"]
    assert consumer.kind is SpanKind.CONSUMER
    assert consumer.parent is not None
    assert consumer.parent.trace_id == _VALID_TRACE_ID
    assert consumer.parent.span_id == _VALID_PARENT_SPAN_ID
    assert consumer.attributes == {"kept": "value"}
    assert nested.parent is not None and nested.parent.span_id == consumer.context.span_id
    assert linked.links[0].context == linked_context
    assert error.status.status_code is StatusCode.ERROR
    assert [event.name for event in error.events] == ["exception"]


def test_unsampled_valid_missing_and_malformed_contexts_propagate_without_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, exporter = _provider(ALWAYS_OFF)
    monkeypatch.setattr(tracing_module, "_TRACER", provider.get_tracer("relayna"))
    contexts = []
    injected: dict[str, Any]
    try:
        for headers in (
            {"traceparent": _VALID_TRACEPARENT, "baggage": "tenant=blue"},
            {},
            {"traceparent": "malformed"},
        ):
            extracted = extract_trace_context(headers)
            if "baggage" in headers:
                assert baggage.get_baggage("tenant", context=extracted) == "blue"
            with relayna_span("relayna.unsampled", headers=headers) as span:
                contexts.append(span.get_span_context())
                injected = inject_trace_headers({"custom": "preserved"})
                assert injected["custom"] == "preserved"
                assert injected["traceparent"].endswith("-00")
    finally:
        provider.shutdown()

    assert contexts[0].trace_id == _VALID_TRACE_ID
    assert all(context.is_valid and not context.trace_flags.sampled for context in contexts)
    assert contexts[1].trace_id != _VALID_TRACE_ID
    assert contexts[2].trace_id != _VALID_TRACE_ID
    assert exporter.get_finished_spans() == ()


@pytest.mark.asyncio
async def test_async_context_isolation_and_cancellation_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, exporter = _provider(ALWAYS_ON)
    monkeypatch.setattr(tracing_module, "_TRACER", provider.get_tracer("relayna"))

    async def message(trace_digit: str) -> str:
        trace_id = trace_digit * 32
        header = f"00-{trace_id}-2222222222222222-01"
        with relayna_span("relayna.concurrent", headers={"traceparent": header}):
            await asyncio.sleep(0)
            return active_trace_fields()["trace_id"]

    async def cancelled() -> None:
        with relayna_span("relayna.cancelled"):
            await asyncio.sleep(0)
            raise asyncio.CancelledError

    try:
        assert await asyncio.gather(message("3"), message("4")) == ["3" * 32, "4" * 32]
        with pytest.raises(asyncio.CancelledError):
            await cancelled()
    finally:
        provider.shutdown()

    spans = exporter.get_finished_spans()
    concurrent = [span for span in spans if span.name == "relayna.concurrent"]
    cancelled_span = next(span for span in spans if span.name == "relayna.cancelled")
    assert {f"{span.context.trace_id:032x}" for span in concurrent} == {"3" * 32, "4" * 32}
    assert cancelled_span.status.status_code is StatusCode.UNSET
    assert cancelled_span.events == ()


class _CountingPropagator(TextMapPropagator):
    def __init__(self, delegate: TextMapPropagator) -> None:
        self.delegate = delegate
        self.extract_count = 0
        self.inject_count = 0

    def extract(self, carrier: Any, context: Context | None = None, getter: Any = None) -> Context:
        self.extract_count += 1
        return self.delegate.extract(carrier, context, getter=getter)

    def inject(self, carrier: Any, context: Context | None = None, setter: Any = None) -> None:
        self.inject_count += 1
        self.delegate.inject(carrier, context=context, setter=setter)

    @property
    def fields(self) -> set[str]:
        return self.delegate.fields


def test_dynamic_propagators_and_stateless_carrier_adapters_are_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    original = propagate.get_global_textmap()
    first = _CountingPropagator(original)
    second = _CountingPropagator(original)
    seen_getters: list[object] = []
    seen_setters: list[object] = []
    original_extract = tracing_module.propagate.extract
    original_inject = tracing_module.propagate.inject

    def counted_extract(*args: Any, **kwargs: Any) -> Context:
        seen_getters.append(kwargs["getter"])
        return original_extract(*args, **kwargs)

    def counted_inject(*args: Any, **kwargs: Any) -> None:
        seen_setters.append(kwargs["setter"])
        original_inject(*args, **kwargs)

    monkeypatch.setattr(tracing_module.propagate, "extract", counted_extract)
    monkeypatch.setattr(tracing_module.propagate, "inject", counted_inject)
    try:
        propagate.set_global_textmap(first)
        extract_trace_context({"traceparent": _VALID_TRACEPARENT})
        inject_trace_headers()
        propagate.set_global_textmap(second)
        extract_trace_context({"traceparent": _VALID_TRACEPARENT})
        inject_trace_headers()
    finally:
        propagate.set_global_textmap(original)

    assert (first.extract_count, first.inject_count) == (1, 1)
    assert (second.extract_count, second.inject_count) == (1, 1)
    assert seen_getters == [tracing_module._GETTER, tracing_module._GETTER]
    assert seen_setters == [tracing_module._SETTER, tracing_module._SETTER]


def test_relayna_span_uses_the_module_tracer_without_repeated_provider_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, exporter = _provider(ALWAYS_ON)
    monkeypatch.setattr(tracing_module, "_TRACER", provider.get_tracer("custom-relayna"))

    def forbidden_lookup(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError("relayna_span must not repeat tracer/provider lookup")

    monkeypatch.setattr(tracing_module.trace, "get_tracer", forbidden_lookup)
    try:
        with relayna_span("first"):
            pass
        with relayna_span("second"):
            pass
    finally:
        provider.shutdown()

    assert [span.name for span in exporter.get_finished_spans()] == ["first", "second"]


def test_module_tracer_binds_to_provider_configured_after_relayna_import() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    script = """
import json
from relayna.observability.tracing import relayna_span
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)
with relayna_span("late-configured"):
    pass
provider.shutdown()
print(json.dumps([span.name for span in exporter.get_finished_spans()]))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(repository_root)},
    )

    assert json.loads(completed.stdout) == ["late-configured"]
