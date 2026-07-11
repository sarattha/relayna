from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import pytest

from relayna.dlq.broker import (
    RabbitMQManagementDLQInspector,
    _extract_correlation_id,
    _extract_dead_letter_time,
    _extract_reason,
    _extract_source_queue_name,
    _extract_task_id,
    _message_key,
    _normalize_headers,
    _normalize_properties,
    _normalized_string,
    _parse_datetime,
    broker_message_from_management_payload,
)


def test_broker_dlq_normalizers_cover_management_payload_variants() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert _parse_datetime(now) == now
    assert _parse_datetime(datetime(2026, 1, 1)) == now
    assert _parse_datetime(0) == datetime(1970, 1, 1, tzinfo=UTC)
    assert _parse_datetime("2026-01-01T00:00:00Z") == now
    assert _parse_datetime("2026-01-01T00:00:00") == now
    assert _parse_datetime("") is None
    assert _parse_datetime("invalid") is None
    assert _parse_datetime(object()) is None
    assert _normalize_headers(None) == {}
    assert _normalize_headers({"a": 1}) == {"a": 1}
    assert _normalize_properties(None) == {}
    assert _normalize_properties({"a": 1}) == {"a": 1}
    assert _normalized_string(None) is None
    assert _normalized_string("  ") is None
    assert _normalized_string(3) == "3"

    assert _message_key("queue", "body", {}, {"message_id": " id "}) == "id"
    assert _message_key("queue", "body", {"x-message-id": "header-id"}, {}) == "header-id"
    assert len(_message_key("queue", "body", {}, {"correlation_id": "corr"})) == 64

    headers = {
        "x-death": [
            "ignored",
            {"time": "2026-01-01T00:00:00Z", "queue": "source", "reason": "rejected"},
        ]
    }
    assert _extract_dead_letter_time(headers, {}) == now
    assert _extract_dead_letter_time({"x-first-death-time": now}, {}) == now
    assert _extract_dead_letter_time({}, {}) is None
    assert _extract_source_queue_name(headers) == "source"
    assert _extract_source_queue_name({"x-first-death-queue": "direct"}) == "direct"
    assert _extract_source_queue_name({}) is None
    assert _extract_reason(headers) == "rejected"
    assert _extract_reason({"x-relayna-failure-reason": "handler_error"}) == "handler_error"
    assert _extract_reason({}) is None
    assert _extract_task_id({"task_id": "task-header"}, {}) == "task-header"
    assert _extract_task_id({}, {"task_id": "task-body"}) == "task-body"
    assert _extract_task_id({}, []) is None
    assert _extract_correlation_id({}, {"correlation_id": "corr-property"}, {}) == "corr-property"
    assert _extract_correlation_id({}, {}, {"correlation_id": "corr-body"}) == "corr-body"
    assert _extract_correlation_id({}, {}, []) is None


def test_broker_message_from_management_payload_decodes_text_base64_bytes_and_empty() -> None:
    body = b'{"task_id":"task-1","correlation_id":"corr-body"}'
    common = {
        "properties": {
            "message_id": "message-1",
            "content_type": "application/json",
            "headers": {
                "x-first-death-queue": "tasks.queue",
                "x-first-death-reason": "rejected",
                "x-first-death-time": "2026-01-01T00:00:00Z",
            },
        },
        "redelivered": True,
    }
    encoded = broker_message_from_management_payload(
        "tasks.queue.dlq",
        {**common, "payload": base64.b64encode(body).decode("ascii"), "payload_encoding": "base64"},
    )
    assert encoded.message_key == "message-1"
    assert encoded.task_id == "task-1"
    assert encoded.correlation_id == "corr-body"
    assert encoded.source_queue_name == "tasks.queue"
    assert encoded.reason == "rejected"
    assert encoded.redelivered is True

    raw = broker_message_from_management_payload("queue", {"payload": body})
    text = broker_message_from_management_payload("queue", {"payload": "plain text"})
    empty = broker_message_from_management_payload("queue", {"payload": object()})
    assert raw.body["task_id"] == "task-1"
    assert text.body == "plain text"
    assert empty.raw_body_b64 == ""
    assert empty.redelivered is None


@pytest.mark.asyncio
async def test_management_inspector_posts_requeues_filters_invalid_items_and_closes() -> None:
    class Response:
        def __init__(self, payload: Any) -> None:
            self.payload = payload
            self.raise_calls = 0

        def raise_for_status(self) -> None:
            self.raise_calls += 1

        def json(self) -> Any:
            return self.payload

    class Client:
        def __init__(self, payload: Any) -> None:
            self.response = Response(payload)
            self.posts: list[tuple[str, dict[str, Any]]] = []
            self.closed = False

        async def post(self, url: str, **kwargs: Any) -> Response:
            self.posts.append((url, kwargs))
            return self.response

        async def aclose(self) -> None:
            self.closed = True

    clients: list[Client] = []

    def factory(_timeout: float) -> Client:
        client = Client(["ignored", {"payload": "hello"}])
        clients.append(client)
        return client

    inspector = RabbitMQManagementDLQInspector(
        base_url="http://rabbit/",
        username="guest",
        password="guest",
        vhost="/demo",
        client_factory=factory,  # type: ignore[arg-type]
    )
    messages = await inspector.list_messages(" queue/name ", limit=500)
    assert len(messages) == 1
    client = clients[0]
    assert client.closed is True
    assert client.posts[0][0].endswith("/api/queues/%2Fdemo/queue%2Fname/get")
    assert client.posts[0][1]["json"]["count"] == 200
    assert client.posts[0][1]["json"]["ackmode"] == "ack_requeue_true"

    with pytest.raises(ValueError, match="queue_name"):
        await inspector.list_messages(" ")

    invalid_client = Client({"not": "a list"})
    invalid = RabbitMQManagementDLQInspector(
        base_url="http://rabbit",
        username="guest",
        password="guest",
        client_factory=lambda _timeout: invalid_client,  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="invalid DLQ"):
        await invalid.list_messages("queue", limit=0)
    assert invalid_client.closed is True
