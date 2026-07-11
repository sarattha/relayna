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
        self.rejected_with: bool | None = None
        self.done = asyncio.Event()

    async def ack(self) -> None:
        self.acked = True
        self.done.set()
        if self._on_ack is not None:
            self._on_ack()

    async def reject(self, *, requeue: bool) -> None:
        self.rejected_with = requeue
        self.done.set()
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


class FailingIteratorQueue(FakeQueue):
    def __init__(self, exc: Exception) -> None:
        super().__init__([])
        self.exc = exc

    def iterator(self, arguments: dict[str, Any] | None = None) -> FakeIterator:
        self.iterator_calls.append(arguments)
        raise self.exc


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
async def test_status_hub_acks_non_mapping_json_payload_before_skipping() -> None:
    observed: list[object] = []
    topology = make_topology()
    hub: StatusHub | None = None

    async def sink(event: object) -> None:
        observed.append(event)

    def stop_hub() -> None:
        assert hub is not None
        hub.stop()

    message = FakeMessage(b"[]", on_ack=stop_hub)
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[FakeChannel(queue)])
    store = FakeStore()
    hub = StatusHub(rabbitmq=rabbit, store=store, observation_sink=sink)

    await hub.run_forever()

    assert message.acked is True
    malformed = [event for event in observed if isinstance(event, StatusHubMalformedMessage)]
    assert malformed
    assert malformed[0].reason == "payload_not_mapping"
    assert not any(isinstance(event, StatusHubLoopError) for event in observed)
    assert store.stored == []


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

    write_failed = next(event for event in observed if isinstance(event, StatusHubStoreWriteFailed))
    assert write_failed.exception_message == "redis down"
    assert message.acked is False
    assert message.rejected_with is True


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
    assert loop_errors[0].exception_message == "temporary failure"


@pytest.mark.asyncio
async def test_status_hub_fail_fast_errors_raise_before_iterator_starts() -> None:
    observed: list[object] = []
    topology = make_topology()
    queue = FailingIteratorQueue(RuntimeError("queue unavailable"))
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[FakeChannel(queue)])
    store = FakeStore()
    hub = StatusHub(
        rabbitmq=rabbit,
        store=store,
        observation_sink=observed.append,
        fail_fast_errors={RuntimeError},
    )

    with pytest.raises(RuntimeError, match="StatusHub failed to start consuming queue") as exc_info:
        await hub.run_forever()

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "queue unavailable"
    assert not any(isinstance(event, StatusHubLoopError) for event in observed)


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


@pytest.mark.asyncio
async def test_status_hub_prefetch_dispatches_different_tasks_concurrently() -> None:
    topology = make_topology()
    both_started = asyncio.Event()
    release = asyncio.Event()

    class BlockingStore(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0

        async def set_history(self, task_id: str, event: dict[str, Any]) -> None:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == 2:
                both_started.set()
            try:
                await release.wait()
                self.stored.append((task_id, dict(event)))
            finally:
                self.active -= 1

    messages = (
        FakeMessage(json.dumps({"task_id": "task-1", "status": "processing"}).encode("utf-8")),
        FakeMessage(json.dumps({"task_id": "task-2", "status": "processing"}).encode("utf-8")),
    )
    store = BlockingStore()
    queue = FakeQueue(list(messages))
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[FakeChannel(queue)])
    hub = StatusHub(rabbitmq=rabbit, store=store, prefetch=2)
    run_task = asyncio.create_task(hub.run_forever())
    try:
        await asyncio.wait_for(both_started.wait(), timeout=1)
        hub.stop()
        release.set()
        await asyncio.wait_for(run_task, timeout=1)
    finally:
        hub.stop()
        release.set()
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)

    assert store.max_active == 2
    assert all(message.acked for message in messages)
    assert hub._task_locks == {}


@pytest.mark.asyncio
async def test_status_hub_serializes_writes_for_the_same_task() -> None:
    topology = make_topology()
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class OrderedStore(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0

        async def set_history(self, task_id: str, event: dict[str, Any]) -> None:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                if event["status"] == "first":
                    first_started.set()
                    await release_first.wait()
                self.stored.append((task_id, dict(event)))
            finally:
                self.active -= 1

    messages = (
        FakeMessage(json.dumps({"task_id": "task-1", "status": "first"}).encode("utf-8")),
        FakeMessage(json.dumps({"task_id": "task-1", "status": "second"}).encode("utf-8")),
    )
    store = OrderedStore()
    queue = FakeQueue(list(messages))
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[FakeChannel(queue)])
    hub = StatusHub(rabbitmq=rabbit, store=store, prefetch=2)
    run_task = asyncio.create_task(hub.run_forever())
    try:
        await asyncio.wait_for(first_started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert store.max_active == 1
        hub.stop()
        release_first.set()
        await asyncio.wait_for(messages[1].done.wait(), timeout=1)
        await asyncio.wait_for(run_task, timeout=1)
    finally:
        hub.stop()
        release_first.set()
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)

    assert [event["status"] for _, event in store.stored] == ["first", "second"]
    assert store.max_active == 1


@pytest.mark.asyncio
async def test_status_hub_prefetch_one_uses_sequential_path_and_sanitizes_meta() -> None:
    topology = make_topology()
    hub: StatusHub | None = None

    def stop() -> None:
        assert hub is not None
        hub.stop()

    message = FakeMessage(
        json.dumps(
            {
                "task_id": "task-1",
                "status": 7,
                "event_id": " ",
                "meta": {"auth_token": "secret", "keep": True},
            }
        ).encode()
    )
    store = FakeStore(on_store=stop)
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[FakeChannel(FakeQueue([message]))])
    observations: list[object] = []
    hub = StatusHub(rabbitmq=rabbit, store=store, prefetch=1, observation_sink=observations.append)

    await hub.run_forever()

    assert store.stored[0][1]["meta"] == {"keep": True}
    stored = next(event for event in observations if isinstance(event, StatusHubStoredEvent))
    assert stored.event_id is None
    assert stored.status == "7"


@pytest.mark.asyncio
async def test_status_hub_handles_missing_task_and_alias_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = StatusHub(
        rabbitmq=FakeRabbitClient(topology=make_topology(), acquire_results=[]),
        store=FakeStore(),
    )
    missing = FakeMessage(json.dumps({"status": "done"}).encode())
    await hub._handle_message(missing)
    assert missing.acked is True

    def fail_alias(*args: object, **kwargs: object) -> dict[str, Any]:
        raise ValueError("alias failed")

    monkeypatch.setattr("relayna.status.hub.normalize_contract_aliases", fail_alias)
    alias_message = FakeMessage(json.dumps({"task_id": "task"}).encode())
    await hub._handle_message(alias_message)
    assert alias_message.acked is True


@pytest.mark.asyncio
async def test_status_hub_propagates_cancellation_returns_when_stopped_and_tolerates_close_failure() -> None:
    class CancellingRabbit(FakeRabbitClient):
        async def acquire_channel(self, prefetch: int = 200) -> FakeChannel:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await StatusHub(
            rabbitmq=CancellingRabbit(topology=make_topology(), acquire_results=[]),
            store=FakeStore(),
        ).run_forever()

    stopped_hub: StatusHub | None = None

    class StoppingRabbit(FakeRabbitClient):
        async def acquire_channel(self, prefetch: int = 200) -> FakeChannel:
            assert stopped_hub is not None
            stopped_hub.stop()
            raise RuntimeError("stopped")

    stopped_hub = StatusHub(
        rabbitmq=StoppingRabbit(topology=make_topology(), acquire_results=[]),
        store=FakeStore(),
    )
    await stopped_hub.run_forever()

    class CloseFailChannel(FakeChannel):
        async def close(self) -> None:
            await super().close()
            raise RuntimeError("close failed")

    close_hub: StatusHub | None = None

    def stop_close_hub() -> None:
        assert close_hub is not None
        close_hub.stop()

    message = FakeMessage(json.dumps({"task_id": "task", "status": "done"}).encode())
    close_hub = StatusHub(
        rabbitmq=FakeRabbitClient(
            topology=make_topology(),
            acquire_results=[CloseFailChannel(FakeQueue([message]))],
        ),
        store=FakeStore(on_store=stop_close_hub),
    )
    await close_hub.run_forever()
