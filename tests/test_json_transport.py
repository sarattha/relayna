from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from relayna._transport_json import encode_transport_json, parse_transport_json
from relayna.consumer.context import _to_json_bytes as consumer_json_bytes
from relayna.contracts import (
    ContractAliasConfig,
    TaskEnvelope,
    ensure_status_event_id,
    normalize_contract_aliases,
)
from relayna.dlq.service import _retry_payload_bytes
from relayna.rabbitmq.client import _to_json_bytes as rabbit_json_bytes


class _NestedModel(BaseModel):
    value: str


def test_transport_encoder_uses_compact_utf8_and_preserves_accepted_numeric_domain() -> None:
    value = {
        "unicode": "สวัสดี",
        "huge": 2**100,
        "numbers": [float("nan"), float("inf"), float("-inf")],
        1: "one",
    }

    encoded = encode_transport_json(value)

    assert encoded == (
        b'{"unicode":"\xe0\xb8\xaa\xe0\xb8\xa7\xe0\xb8\xb1\xe0\xb8\xaa\xe0\xb8\x94\xe0\xb8\xb5",'
        b'"huge":1267650600228229401496703205376,"numbers":[NaN,Infinity,-Infinity],"1":"one"}'
    )
    parsed = parse_transport_json(encoded)
    assert parsed["unicode"] == "สวัสดี"
    assert parsed["huge"] == 2**100
    assert parsed["numbers"][0] != parsed["numbers"][0]
    assert parsed["numbers"][1] == float("inf")
    assert parsed["numbers"][2] == float("-inf")
    assert parsed["1"] == "one"


def test_transport_non_string_key_domain_is_explicit() -> None:
    assert encode_transport_json({1: "integer"}) == b'{"1":"integer"}'
    assert encode_transport_json({1.5: "float"}) == b'{"1.5":"float"}'
    assert encode_transport_json({True: "boolean"}) == b'{"true":"boolean"}'
    assert encode_transport_json({None: "none"}) == b'{"None":"none"}'
    assert encode_transport_json({("x", "y"): "tuple"}) == b'{"x,y":"tuple"}'


def test_transport_encoder_serializes_current_prepared_model_values() -> None:
    envelope = TaskEnvelope(
        task_id="prepared",
        payload={
            "created": datetime(2025, 1, 1, tzinfo=UTC),
            "identifier": UUID("12345678-1234-5678-1234-567812345678"),
            "model": _NestedModel(value="nested"),
        },
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    prepared = envelope.model_dump(mode="json", exclude_none=True)

    parsed = parse_transport_json(encode_transport_json(prepared))

    assert parsed["payload"] == {
        "created": "2025-01-01T00:00:00Z",
        "identifier": "12345678-1234-5678-1234-567812345678",
        "model": {"value": "nested"},
    }


def test_transport_wire_bytes_intentionally_break_released_whitespace_only() -> None:
    payload = {"task_id": "task-1", "payload": {"unicode": "สวัสดี", "value": 7}}
    released = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    production = encode_transport_json(payload)

    assert released == (
        b'{"task_id": "task-1", "payload": {"unicode": '
        b'"\xe0\xb8\xaa\xe0\xb8\xa7\xe0\xb8\xb1\xe0\xb8\xaa\xe0\xb8\x94\xe0\xb8\xb5", "value": 7}}'
    )
    assert production == (
        b'{"task_id":"task-1","payload":{"unicode":'
        b'"\xe0\xb8\xaa\xe0\xb8\xa7\xe0\xb8\xb1\xe0\xb8\xaa\xe0\xb8\x94\xe0\xb8\xb5","value":7}}'
    )
    assert parse_transport_json(released) == parse_transport_json(production)


def test_transport_parser_intentionally_rejects_invalid_utf8_instead_of_replacing() -> None:
    payload = b'{"task_id":"task-1","payload":{"text":"a\xffb"}}'

    released = json.loads(payload.decode("utf-8", errors="replace"))
    assert released["payload"]["text"] == "a\ufffdb"
    with pytest.raises(ValueError, match="invalid unicode code point"):
        parse_transport_json(payload)


def test_transport_parser_preserves_alias_normalization() -> None:
    configured = ContractAliasConfig(field_aliases={"task_id": "jobId"})
    document_payload = parse_transport_json(b'{"documentId":"document-1","payload":{}}')
    configured_payload = parse_transport_json(b'{"jobId":"job-1","payload":{}}')

    document = TaskEnvelope.model_validate(normalize_contract_aliases(document_payload, drop_aliases=True))
    job = TaskEnvelope.model_validate(normalize_contract_aliases(configured_payload, configured, drop_aliases=True))

    assert document.task_id == "document-1"
    assert job.task_id == "job-1"


def test_transport_parser_keeps_malformed_and_invalid_envelope_stages_distinct() -> None:
    with pytest.raises(ValueError):
        parse_transport_json(b'{"task_id":')

    parsed = parse_transport_json(b'{"payload":{"valid_json":true}}')
    with pytest.raises(ValidationError):
        TaskEnvelope.model_validate(parsed)


def test_all_scoped_outbound_helpers_use_the_production_transport_codec() -> None:
    payload = {"task_id": "task-1", "payload": {"value": 7}}
    expected = b'{"task_id":"task-1","payload":{"value":7}}'

    assert rabbit_json_bytes(payload) == expected
    assert consumer_json_bytes(payload) == expected


def test_dlq_override_uses_transport_codec_but_original_body_remains_byte_exact() -> None:
    from relayna.dlq import DLQRecord

    record = DLQRecord(
        dlq_id="dlq-1",
        queue_name="tasks.dlq",
        source_queue_name="tasks",
        retry_queue_name="tasks.retry",
        reason="handler_error",
        retry_attempt=1,
        max_retries=3,
        body='{"task_id": "original"}',
        body_encoding="json",
        raw_body_b64="eyJ0YXNrX2lkIjogIm9yaWdpbmFsIn0=",
    )

    assert _retry_payload_bytes(record, None) == b'{"task_id": "original"}'
    assert _retry_payload_bytes(record, {"task_id": "override"}) == b'{"task_id":"override"}'


def test_canonical_hash_input_remains_on_released_stdlib_bytes() -> None:
    event = {"task_id": "task-123", "status": "processing", "message": "Started."}

    result = ensure_status_event_id(event)

    assert result["event_id"] == "bcd054185ab592588ff9ccdbe1b4aeb58da1751bdb39ed731b1ed9e2f7236151"
