from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from relayna.observability import (
    StatusHubLoopError,
    StatusHubMalformedMessage,
    StatusHubStarted,
    StatusHubStoredEvent,
    StatusHubStoreWriteFailed,
)
from relayna.status import StatusHub
from relayna.topology import SharedTasksSharedStatusTopology


class FakeMessage:
    def __init__(self, body: bytes, *, on_ack: Callable[[], None] | None = None) -> None:
        self.body = body
        self._on_ack = on_ack
        self.acked = False

    async def ack(self) -> None:
        self.acked = True
        if self._on_ack is not None:
            self._on_ack()


class FakeIterator:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self._messages = messages

    async def __aenter__(self) -> FakeIterator:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def __aiter__(self) -> FakeIterator:
        return self

    async def __anext__(self) -> FakeMessage:
        if self._messages:
            return self._messages.pop(0)
        raise StopAsyncIteration


class FakeQueue:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self._messages = messages
        self.iterator_calls: list[dict[str, Any] | None] = []

    def iterator(self, arguments: dict[str, Any] | None = None) -> FakeIterator:
        self.iterator_calls.append(arguments)
        return FakeIterator(self._messages)


class FakeChannel:
    def __init__(self, queue: FakeQueue) -> None:
        self.queue = queue
        self.declare_queue_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    async def declare_queue(self, name: str, *, durable: bool, arguments: dict[str, Any] | None = None) -> FakeQueue:
        self.declare_queue_calls.append({"name": name, "durable": durable, "arguments": arguments})
        return self.queue

    async def close(self) -> None:
        self.close_calls += 1


class FakeRabbitClient:
    def __init__(
        self, *, topology: SharedTasksSharedStatusTopology, acquire_results: list[FakeChannel | Exception]
    ) -> None:
        self.topology = topology
        self.acquire_results = list(acquire_results)
        self.ensure_status_queue_calls = 0

    async def ensure_status_queue(self) -> str:
        self.ensure_status_queue_calls += 1
        return self.topology.status_queue

    async def acquire_channel(self, prefetch: int = 200) -> FakeChannel:
        if not self.acquire_results:
            raise RuntimeError("no more channels")
        result = self.acquire_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeStore:
    def __init__(self, *, fail: bool = False, on_store: Callable[[], None] | None = None) -> None:
        self.fail = fail
        self.on_store = on_store
        self.stored: list[tuple[str, dict[str, Any]]] = []

    async def set_history(self, task_id: str, event: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("redis down")
        self.stored.append((task_id, dict(event)))
        if self.on_store is not None:
            self.on_store()


def make_topology() -> SharedTasksSharedStatusTopology:
    return SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )


@pytest.mark.asyncio
async def test_status_hub_emits_started_and_stored_event_observations() -> None:
    observed: list[object] = []
    topology = make_topology()
    stop_event = asyncio.Event()
    hub: StatusHub | None = None

    async def sink(event: object) -> None:
        observed.append(event)

    def stop_hub() -> None:
        assert hub is not None
        hub.stop()
        stop_event.set()

    message = FakeMessage(
        json.dumps({"task_id": "task-123", "status": "completed", "event_id": "evt-1"}).encode("utf-8")
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[FakeChannel(queue)])
    store = FakeStore(on_store=stop_hub)
    hub = StatusHub(rabbitmq=rabbit, store=store, observation_sink=sink)

    await hub.run_forever()

    assert isinstance(observed[0], StatusHubStarted)
    assert isinstance(observed[1], StatusHubStoredEvent)
    assert observed[1].task_id == "task-123"
    assert observed[1].event_id == "evt-1"


@pytest.mark.asyncio
async def test_status_hub_emits_malformed_message_observation() -> None:
    observed: list[object] = []
    topology = make_topology()
    hub: StatusHub | None = None

    async def sink(event: object) -> None:
        observed.append(event)

    def stop_hub() -> None:
        assert hub is not None
        hub.stop()

    message = FakeMessage(b"{not-json", on_ack=stop_hub)
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[FakeChannel(queue)])
    store = FakeStore()
    hub = StatusHub(rabbitmq=rabbit, store=store, observation_sink=sink)

    await hub.run_forever()

    assert any(isinstance(event, StatusHubMalformedMessage) for event in observed)


@pytest.mark.asyncio
async def test_status_hub_emits_store_write_failed_observation() -> None:
    observed: list[object] = []
    topology = make_topology()
    hub: StatusHub | None = None

    async def sink(event: object) -> None:
        observed.append(event)

    def stop_hub() -> None:
        assert hub is not None
        hub.stop()

    message = FakeMessage(
        json.dumps({"task_id": "task-123", "status": "processing"}).encode("utf-8"),
        on_ack=stop_hub,
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[FakeChannel(queue)])
    store = FakeStore(fail=True)
    hub = StatusHub(rabbitmq=rabbit, store=store, observation_sink=sink)

    await hub.run_forever()

    assert any(isinstance(event, StatusHubStoreWriteFailed) for event in observed)


@pytest.mark.asyncio
async def test_status_hub_emits_loop_error_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[object] = []
    topology = make_topology()
    message = FakeMessage(json.dumps({"task_id": "task-123", "status": "completed"}).encode("utf-8"))
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[RuntimeError("temporary failure"), channel])
    hub: StatusHub | None = None
    original_sleep = asyncio.sleep

    async def sink(event: object) -> None:
        observed.append(event)

    def stop_hub() -> None:
        assert hub is not None
        hub.stop()

    store = FakeStore(on_store=stop_hub)
    hub = StatusHub(rabbitmq=rabbit, store=store, observation_sink=sink)

    async def fake_sleep(delay: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr("relayna.status.hub.asyncio.sleep", fake_sleep)

    await hub.run_forever()

    loop_errors = [event for event in observed if isinstance(event, StatusHubLoopError)]
    assert loop_errors
    assert loop_errors[0].retry_delay_seconds == 2.0


@pytest.mark.asyncio
async def test_status_hub_sink_failures_do_not_break_storage() -> None:
    topology = make_topology()
    hub: StatusHub | None = None

    async def sink(event: object) -> None:
        raise RuntimeError("sink failed")

    def stop_hub() -> None:
        assert hub is not None
        hub.stop()

    message = FakeMessage(
        json.dumps({"task_id": "task-123", "status": "completed"}).encode("utf-8"),
        on_ack=stop_hub,
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[FakeChannel(queue)])
    store = FakeStore()
    hub = StatusHub(rabbitmq=rabbit, store=store, observation_sink=sink)

    await hub.run_forever()

    assert store.stored == [("task-123", {"task_id": "task-123", "status": "completed"})]
