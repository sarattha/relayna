from __future__ import annotations

import json
from typing import Any

import pytest

from relayna.contracts import ContractAliasConfig
from relayna.status.history import StreamHistoryReader
from relayna.topology import SharedTasksSharedStatusTopology


class Message:
    def __init__(self, payload: bytes) -> None:
        self.body = payload
        self.acked = False

    async def ack(self) -> None:
        self.acked = True


class Iterator:
    def __init__(self, messages: list[Message], *, fail_enter: bool = False) -> None:
        self.messages = messages
        self.fail_enter = fail_enter

    async def __aenter__(self) -> Iterator:
        if self.fail_enter:
            raise TimeoutError
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def __aiter__(self) -> Iterator:
        return self

    async def __anext__(self) -> Message:
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


class Queue:
    def __init__(self, iterator: Iterator) -> None:
        self._iterator = iterator
        self.iterator_calls: list[dict[str, Any]] = []

    def iterator(self, **kwargs: Any) -> Iterator:
        self.iterator_calls.append(kwargs)
        return self._iterator


class Channel:
    def __init__(self, queue: Queue, *, fail_close: bool = False) -> None:
        self.queue = queue
        self.fail_close = fail_close
        self.closed = False
        self.declarations: list[dict[str, Any]] = []

    async def declare_queue(self, name: str, **kwargs: Any) -> Queue:
        self.declarations.append({"name": name, **kwargs})
        return self.queue

    async def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close failed")


class Rabbit:
    def __init__(self, topology: SharedTasksSharedStatusTopology, channel: Channel) -> None:
        self.topology = topology
        self.channel = channel

    async def ensure_status_queue(self) -> str:
        return self.topology.status_queue

    async def acquire_channel(self, *, prefetch: int) -> Channel:
        assert prefetch == 1000
        return self.channel


def topology(*, streams: bool = True) -> SharedTasksSharedStatusTopology:
    return SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        status_use_streams=streams,
    )


@pytest.mark.asyncio
async def test_stream_history_replays_filters_aliases_and_stops_on_terminal() -> None:
    messages = [
        Message(b"{not-json"),
        Message(json.dumps({"attempt_id": "other", "state": "processing"}).encode()),
        Message(json.dumps({"attempt_id": "task-1", "state": "processing"}).encode()),
        Message(json.dumps({"attempt_id": "task-1", "state": "completed"}).encode()),
        Message(json.dumps({"attempt_id": "task-1", "state": "ignored"}).encode()),
    ]
    queue = Queue(Iterator(list(messages)))
    channel = Channel(queue)
    reader = StreamHistoryReader(
        rabbitmq=Rabbit(topology(), channel),  # type: ignore[arg-type]
        alias_config=ContractAliasConfig(field_aliases={"task_id": "attempt_id", "status": "state"}),
        output_adapter=lambda event: {**event, "adapted": True},
    )

    events = await reader.replay(task_id="task-1", start_offset=7)

    assert [event["state"] for event in events] == ["processing", "completed"]
    assert all(event["adapted"] is True for event in events)
    assert all(message.acked for message in messages[:4])
    assert messages[4].acked is False
    assert queue.iterator_calls[0]["arguments"] == {"x-stream-offset": 7}
    assert channel.closed is True


@pytest.mark.asyncio
async def test_stream_history_bounds_timeout_non_stream_and_close_failure() -> None:
    with pytest.raises(RuntimeError, match="requires a stream"):
        await StreamHistoryReader(
            rabbitmq=Rabbit(topology(streams=False), Channel(Queue(Iterator([])))),  # type: ignore[arg-type]
        ).replay()

    timeout_channel = Channel(Queue(Iterator([], fail_enter=True)), fail_close=True)
    reader = StreamHistoryReader(
        rabbitmq=Rabbit(topology(), timeout_channel),  # type: ignore[arg-type]
        idle_timeout_seconds=0,
    )
    assert await reader.replay(start_offset="last", max_seconds=0, require_stream=False) == []
    assert timeout_channel.closed is True

    message = Message(json.dumps({"task_id": "task-1", "status": "processing"}).encode())
    bounded_channel = Channel(Queue(Iterator([message])))
    bounded = StreamHistoryReader(rabbitmq=Rabbit(topology(), bounded_channel))  # type: ignore[arg-type]
    assert await bounded.replay(max_seconds=-1) == []

    scan_message = Message(json.dumps({"task_id": "task-1", "status": "processing"}).encode())
    scan_reader = StreamHistoryReader(
        rabbitmq=Rabbit(topology(), Channel(Queue(Iterator([scan_message])))),  # type: ignore[arg-type]
    )
    assert len(await scan_reader.replay(max_scan=1, stop_on_terminal=False)) == 1
