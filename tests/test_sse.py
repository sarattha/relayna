from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from relayna.observability import (
    SSEHistoryReplayed,
    SSEKeepaliveSent,
    SSELiveEventSent,
    SSEMalformedPubsubPayload,
    SSEResumeRequested,
    SSEStreamEnded,
    SSEStreamStarted,
)
from relayna.sse import SSEStatusStream


class FakePubSubIterator:
    def __init__(self, pubsub: "FakePubSub") -> None:
        self._pubsub = pubsub

    def __aiter__(self) -> FakePubSubIterator:
        return self

    async def __anext__(self) -> dict[str, Any]:
        while True:
            if self._pubsub.messages:
                return self._pubsub.messages.pop(0)
            await asyncio.Future()


class FakePubSub:
    def __init__(self, messages: list[dict[str, Any]] | None = None, *, supports_get_message: bool = False) -> None:
        self.messages = list(messages or [])
        self.supports_get_message = supports_get_message
        self.subscriptions: list[str] = []
        self.unsubscriptions: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscriptions.append(channel)

    def listen(self) -> AsyncIterator[dict[str, Any]]:
        return FakePubSubIterator(self)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscriptions.append(channel)

    async def close(self) -> None:
        self.closed = True


    async def get_message(self, *, timeout: float | None = None) -> dict[str, Any] | None:
        if not self.supports_get_message:
            raise AttributeError("get_message not supported")
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(timeout or 0)
        return None


class FakeRedis:
    def __init__(self, pubsub: FakePubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> FakePubSub:
        return self._pubsub


class FakeStore:
    def __init__(
        self,
        *,
        history: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        prefix: str = "relayna",
        supports_get_message: bool = False,
    ) -> None:
        self._history = list(history or [])
        self.prefix = prefix
        self._pubsub = FakePubSub(messages, supports_get_message=supports_get_message)
        self.redis = FakeRedis(self._pubsub)

    async def get_history(self, task_id: str) -> list[dict[str, Any]]:
        return list(self._history)

    def channel_name(self, task_id: str) -> str:
        return f"{self.prefix}:channel:{task_id}"

    @property
    def pubsub(self) -> FakePubSub:
        return self._pubsub


async def collect_chunks(stream: AsyncIterator[bytes], count: int) -> list[str]:
    chunks: list[str] = []
    try:
        for _ in range(count):
            chunks.append((await asyncio.wait_for(anext(stream), timeout=0.1)).decode())
    finally:
        await stream.aclose()
    return chunks


def status_message(task_id: str, status: str, *, event_id: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"type": "message", "data": json.dumps({"task_id": task_id, "status": status, "event_id": event_id})}
    if event_id is None:
        data["data"] = json.dumps({"task_id": task_id, "status": status})
    return data


@pytest.mark.asyncio
async def test_status_frames_include_id_when_event_id_present() -> None:
    store = FakeStore(history=[{"task_id": "task-123", "status": "completed", "event_id": "evt-1"}])
    stream = SSEStatusStream(store=store, keepalive_interval_seconds=None)

    chunks = await collect_chunks(stream.stream("task-123"), 2)

    assert chunks[0] == "event: ready\ndata: {}\n\n"
    assert chunks[1] == (
        'id: evt-1\n'
        'event: status\n'
        'data: {"task_id": "task-123", "status": "completed", "event_id": "evt-1"}\n\n'
    )


@pytest.mark.asyncio
async def test_status_frames_omit_id_when_event_id_missing() -> None:
    store = FakeStore(history=[{"task_id": "task-123", "status": "completed"}])
    stream = SSEStatusStream(store=store, keepalive_interval_seconds=None)

    chunks = await collect_chunks(stream.stream("task-123"), 2)

    assert chunks[1] == 'event: status\ndata: {"task_id": "task-123", "status": "completed"}\n\n'


@pytest.mark.asyncio
async def test_keepalive_emits_comment_after_idle_interval() -> None:
    store = FakeStore()
    stream = SSEStatusStream(store=store, keepalive_interval_seconds=0.01)

    chunks = await collect_chunks(stream.stream("task-123"), 2)

    assert chunks == ["event: ready\ndata: {}\n\n", ": keepalive\n\n"]


@pytest.mark.asyncio
async def test_keepalive_can_be_disabled() -> None:
    store = FakeStore(messages=[status_message("task-123", "completed", event_id="evt-1")])
    stream = SSEStatusStream(store=store, keepalive_interval_seconds=None)

    chunks = await collect_chunks(stream.stream("task-123"), 2)

    assert chunks[1].startswith("id: evt-1\n")
    assert ": keepalive" not in "".join(chunks)


@pytest.mark.asyncio
async def test_resume_replays_only_history_after_matching_last_event_id() -> None:
    store = FakeStore(
        history=[
            {"task_id": "task-123", "status": "completed", "event_id": "evt-3"},
            {"task_id": "task-123", "status": "processing", "event_id": "evt-2"},
            {"task_id": "task-123", "status": "queued", "event_id": "evt-1"},
        ]
    )
    stream = SSEStatusStream(store=store, keepalive_interval_seconds=None)

    chunks = await collect_chunks(stream.stream("task-123", last_event_id="evt-1"), 3)

    assert "evt-1" not in "".join(chunks[1:])
    assert chunks[1].startswith("id: evt-2\n")
    assert chunks[2].startswith("id: evt-3\n")


@pytest.mark.asyncio
async def test_unknown_last_event_id_replays_full_history() -> None:
    store = FakeStore(
        history=[
            {"task_id": "task-123", "status": "processing", "event_id": "evt-2"},
            {"task_id": "task-123", "status": "queued", "event_id": "evt-1"},
        ]
    )
    stream = SSEStatusStream(store=store, keepalive_interval_seconds=None)

    chunks = await collect_chunks(stream.stream("task-123", last_event_id="missing"), 3)

    assert chunks[1].startswith("id: evt-1\n")
    assert chunks[2].startswith("id: evt-2\n")


@pytest.mark.asyncio
async def test_terminal_replay_stops_without_consuming_live_messages() -> None:
    store = FakeStore(
        history=[{"task_id": "task-123", "status": "completed", "event_id": "evt-1"}],
        messages=[status_message("task-123", "completed", event_id="evt-2")],
    )
    stream = SSEStatusStream(store=store, keepalive_interval_seconds=None)

    chunks = await collect_chunks(stream.stream("task-123"), 2)

    assert chunks[1].startswith("id: evt-1\n")
    assert store.pubsub.unsubscriptions == ["relayna:channel:task-123"]
    assert store.pubsub.closed is True


@pytest.mark.asyncio
async def test_duplicate_pubsub_event_is_skipped_after_history_replay() -> None:
    store = FakeStore(
        history=[
            {"task_id": "task-123", "status": "processing", "event_id": "evt-2"},
            {"task_id": "task-123", "status": "queued", "event_id": "evt-1"},
        ],
        messages=[
            status_message("task-123", "processing", event_id="evt-2"),
            status_message("task-123", "completed", event_id="evt-3"),
        ],
    )
    stream = SSEStatusStream(store=store, keepalive_interval_seconds=None)

    chunks = await collect_chunks(stream.stream("task-123"), 4)

    combined = "".join(chunks)
    assert combined.count("id: evt-2\n") == 1
    assert chunks[3].startswith("id: evt-3\n")


@pytest.mark.asyncio
async def test_live_events_continue_after_history_replay() -> None:
    store = FakeStore(
        history=[{"task_id": "task-123", "status": "queued", "event_id": "evt-1"}],
        messages=[status_message("task-123", "completed", event_id="evt-2")],
    )
    stream = SSEStatusStream(store=store, keepalive_interval_seconds=None)

    chunks = await collect_chunks(stream.stream("task-123"), 3)

    assert chunks[1].startswith("id: evt-1\n")
    assert chunks[2].startswith("id: evt-2\n")


@pytest.mark.asyncio
async def test_malformed_pubsub_payloads_are_ignored() -> None:
    store = FakeStore(
        messages=[
            {"type": "message", "data": "{not-json"},
            status_message("task-123", "completed", event_id="evt-1"),
        ]
    )
    stream = SSEStatusStream(store=store, keepalive_interval_seconds=None)

    chunks = await collect_chunks(stream.stream("task-123"), 2)

    assert chunks[1].startswith("id: evt-1\n")


@pytest.mark.asyncio
async def test_sse_emits_observations_for_start_resume_replay_and_end() -> None:
    store = FakeStore(
        history=[
            {"task_id": "task-123", "status": "processing", "event_id": "evt-2"},
            {"task_id": "task-123", "status": "queued", "event_id": "evt-1"},
        ]
    )
    observed: list[object] = []

    async def sink(event: object) -> None:
        observed.append(event)

    stream = SSEStatusStream(store=store, keepalive_interval_seconds=0.01, observation_sink=sink)

    await collect_chunks(stream.stream("task-123", last_event_id="evt-1"), 3)

    assert isinstance(observed[0], SSEStreamStarted)
    assert observed[0].resume_requested is True
    assert isinstance(observed[1], SSEResumeRequested)
    assert observed[1].history_match_found is True
    assert isinstance(observed[2], SSELiveEventSent)
    assert observed[2].source == "history"
    assert isinstance(observed[3], SSEHistoryReplayed)
    assert observed[3].replayed_count == 1
    assert isinstance(observed[4], SSEStreamEnded)
    assert observed[4].sent_count == 1


@pytest.mark.asyncio
async def test_sse_emits_keepalive_observations() -> None:
    store = FakeStore()
    observed: list[object] = []

    async def sink(event: object) -> None:
        observed.append(event)

    stream = SSEStatusStream(store=store, keepalive_interval_seconds=0.01, observation_sink=sink)

    await collect_chunks(stream.stream("task-123"), 3)

    assert any(isinstance(event, SSEKeepaliveSent) for event in observed)


@pytest.mark.asyncio
async def test_sse_emits_malformed_pubsub_observations() -> None:
    store = FakeStore(
        messages=[
            {"type": "message", "data": "{not-json"},
            status_message("task-123", "completed", event_id="evt-1"),
        ]
    )
    observed: list[object] = []

    async def sink(event: object) -> None:
        observed.append(event)

    stream = SSEStatusStream(store=store, keepalive_interval_seconds=0.01, observation_sink=sink)
    iterator = stream.stream("task-123")

    await asyncio.wait_for(anext(iterator), timeout=0.1)
    await asyncio.wait_for(anext(iterator), timeout=0.1)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(iterator), timeout=0.1)

    assert any(isinstance(event, SSEMalformedPubsubPayload) for event in observed)
    live_events = [event for event in observed if isinstance(event, SSELiveEventSent)]
    assert live_events[-1].source == "pubsub"


@pytest.mark.asyncio
async def test_keepalive_and_messages_use_get_message_when_available() -> None:
    store = FakeStore(
        messages=[status_message("task-123", "completed", event_id="evt-1")],
        supports_get_message=True,
    )
    stream = SSEStatusStream(store=store, keepalive_interval_seconds=0.01)

    chunks = await collect_chunks(stream.stream("task-123"), 3)

    assert chunks[0] == "event: ready\ndata: {}\n\n"
    assert chunks[1].startswith("id: evt-1\n")
    assert chunks[2] == ": keepalive\n\n"


@pytest.mark.asyncio
async def test_sse_sink_failures_do_not_break_stream_delivery() -> None:
    store = FakeStore(messages=[status_message("task-123", "completed", event_id="evt-1")])

    async def sink(event: object) -> None:
        raise RuntimeError("sink failed")

    stream = SSEStatusStream(store=store, keepalive_interval_seconds=None, observation_sink=sink)

    chunks = await collect_chunks(stream.stream("task-123"), 2)

    assert chunks[1].startswith("id: evt-1\n")
