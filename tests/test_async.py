from __future__ import annotations

import asyncio
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


async def _identity(value: int) -> int:
    return value
