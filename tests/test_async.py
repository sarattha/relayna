from __future__ import annotations

import asyncio
import contextvars
import gc
import weakref
from typing import Any

import pytest

from relayna._async import map_bounded, run_bounded_iterator


class _Iterator:
    def __init__(self, items: list[Any], *, terminal_error: BaseException | None = None) -> None:
        self.items = items
        self.terminal_error = terminal_error

    async def __anext__(self) -> Any:
        if self.items:
            return self.items.pop(0)
        if self.terminal_error is not None:
            raise self.terminal_error
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_map_bounded_preserves_order_and_bound() -> None:
    active = 0
    max_active = 0
    release = asyncio.Event()
    two_started = asyncio.Event()

    async def worker(value: int) -> int:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            two_started.set()
        try:
            await release.wait()
            return value * 2
        finally:
            active -= 1

    task = asyncio.create_task(map_bounded([1, 2, 3], worker, concurrency=2))
    await asyncio.wait_for(two_started.wait(), timeout=1)
    release.set()

    assert await task == [2, 4, 6]
    assert max_active == 2
    assert await map_bounded([], worker, concurrency=1) == []


@pytest.mark.asyncio
async def test_map_bounded_rejects_invalid_concurrency_and_cancels_peers() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        await map_bounded([1], _identity, concurrency=0)

    peer_cancelled = asyncio.Event()

    async def worker(value: int) -> int:
        if value == 1:
            await asyncio.sleep(0)
            raise RuntimeError("boom")
        try:
            await asyncio.Event().wait()
        finally:
            peer_cancelled.set()
        return value

    with pytest.raises(RuntimeError, match="boom"):
        await map_bounded([1, 2], worker, concurrency=2)
    assert peer_cancelled.is_set()


@pytest.mark.asyncio
async def test_run_bounded_iterator_handles_exhaustion_timeout_and_errors() -> None:
    handled: list[int] = []

    async def handler(value: int) -> None:
        handled.append(value)

    await run_bounded_iterator(_Iterator([1, 2]), concurrency=2, handler=handler)
    assert handled == [1, 2]

    with pytest.raises(TimeoutError):
        await run_bounded_iterator(
            _Iterator([], terminal_error=TimeoutError()),
            concurrency=1,
            handler=handler,
        )
    with pytest.raises(ValueError, match="concurrency"):
        await run_bounded_iterator(_Iterator([]), concurrency=0, handler=handler)
    with pytest.raises(RuntimeError, match="iterator failed"):
        await run_bounded_iterator(
            _Iterator([], terminal_error=RuntimeError("iterator failed")),
            concurrency=1,
            handler=handler,
        )


@pytest.mark.asyncio
async def test_run_bounded_iterator_propagates_handler_failure_and_cancellation() -> None:
    release = asyncio.Event()
    started = asyncio.Event()

    async def failing_handler(value: int) -> None:
        if value == 1:
            started.set()
            await release.wait()
            raise RuntimeError("handler failed")

    task = asyncio.create_task(
        run_bounded_iterator(
            _Iterator([1]),
            concurrency=2,
            handler=failing_handler,
        )
    )
    await started.wait()
    release.set()
    with pytest.raises(RuntimeError, match="handler failed"):
        await task

    cancelled = asyncio.Event()

    async def blocking_handler(value: int) -> None:
        del value
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(
        run_bounded_iterator(
            _Iterator([1]),
            concurrency=1,
            handler=blocking_handler,
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_run_bounded_iterator_cleans_high_cardinality_tasks() -> None:
    class Item:
        pass

    items = [Item() for _ in range(2_048)]
    item_refs = [weakref.ref(item) for item in items]
    task_refs: list[weakref.ReferenceType[asyncio.Task[None]]] = []
    active = 0
    peak_active = 0

    async def handler(item: Item) -> None:
        nonlocal active, peak_active
        del item
        task = asyncio.current_task()
        assert task is not None
        task_refs.append(weakref.ref(task))
        active += 1
        peak_active = max(peak_active, active)
        try:
            await asyncio.sleep(0)
        finally:
            active -= 1

    await run_bounded_iterator(_Iterator(items), concurrency=32, handler=handler)

    assert peak_active == 32
    assert active == 0
    items.clear()
    await asyncio.sleep(0)
    gc.collect()
    assert all(reference() is None for reference in item_refs)
    assert all(reference() is None for reference in task_refs)


@pytest.mark.asyncio
async def test_run_bounded_iterator_preserves_fairness_and_context_isolation() -> None:
    message_context = contextvars.ContextVar("message_context", default="outside")
    fairness_ticks = 0
    handled: list[tuple[int, str]] = []

    class ContextIterator(_Iterator):
        async def __anext__(self) -> Any:
            value = await super().__anext__()
            message_context.set(f"message-{value}")
            return value

    async def fairness_probe() -> None:
        nonlocal fairness_ticks
        for _ in range(20):
            await asyncio.sleep(0)
            fairness_ticks += 1

    async def handler(value: int) -> None:
        initial = message_context.get()
        message_context.set(f"handler-{value}")
        await asyncio.sleep(0)
        handled.append((value, initial))

    await asyncio.gather(
        run_bounded_iterator(ContextIterator(list(range(128))), concurrency=8, handler=handler),
        fairness_probe(),
    )

    assert fairness_ticks == 20
    assert handled == [(value, f"message-{value}") for value in range(128)]
    assert message_context.get() == "outside"


@pytest.mark.asyncio
async def test_run_bounded_iterator_cancellation_while_waiting_for_capacity_cleans_child() -> None:
    first_started = asyncio.Event()
    child_cancelled = asyncio.Event()
    iterator = _Iterator([1, 2])

    async def handler(value: int) -> None:
        assert value == 1
        first_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            child_cancelled.set()

    dispatch = asyncio.create_task(run_bounded_iterator(iterator, concurrency=1, handler=handler))
    await first_started.wait()
    dispatch.cancel()

    with pytest.raises(asyncio.CancelledError):
        await dispatch
    assert child_cancelled.is_set()
    assert iterator.items == [2]


@pytest.mark.asyncio
async def test_run_bounded_iterator_retrieves_child_exception() -> None:
    loop = asyncio.get_running_loop()
    unobserved: list[dict[str, Any]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unobserved.append(context))

    async def handler(value: int) -> None:
        if value == 7:
            raise RuntimeError("expected child failure")
        await asyncio.sleep(0)

    try:
        with pytest.raises(RuntimeError, match="expected child failure"):
            await run_bounded_iterator(_Iterator(list(range(32))), concurrency=8, handler=handler)
        await asyncio.sleep(0)
        gc.collect()
        assert unobserved == []
    finally:
        loop.set_exception_handler(previous_handler)


async def _identity(value: int) -> int:
    return value
