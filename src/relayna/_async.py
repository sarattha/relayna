from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar, cast

_InputT = TypeVar("_InputT")
_OutputT = TypeVar("_OutputT")


async def map_bounded(
    items: Sequence[_InputT],
    worker: Callable[[_InputT], Awaitable[_OutputT]],
    *,
    concurrency: int,
) -> list[_OutputT]:
    """Map an async worker over items with bounded task creation and stable ordering."""

    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if not items:
        return []

    results: list[_OutputT | None] = [None] * len(items)
    next_index = 0

    async def run_worker() -> None:
        nonlocal next_index
        while next_index < len(items):
            index = next_index
            next_index += 1
            results[index] = await worker(items[index])

    tasks = [asyncio.create_task(run_worker()) for _ in range(min(concurrency, len(items)))]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return cast(list[_OutputT], results)


async def run_bounded_iterator(
    iterator: Any,
    *,
    concurrency: int,
    handler: Callable[[Any], Awaitable[None]],
    stop_event: asyncio.Event | None = None,
) -> None:
    """Dispatch iterator items concurrently while preserving a hard in-flight bound."""

    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    semaphore = asyncio.Semaphore(concurrency)
    in_flight: set[asyncio.Task[None]] = set()
    first_error: BaseException | None = None

    async def run_item(item: Any) -> None:
        try:
            await handler(item)
        finally:
            semaphore.release()

    def record_completion(task: asyncio.Task[None]) -> None:
        nonlocal first_error
        in_flight.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None and first_error is None:
            first_error = exc

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    try:
        while not stopped():
            await semaphore.acquire()
            if first_error is not None:
                semaphore.release()
                raise first_error
            if stopped():
                semaphore.release()
                break
            try:
                item = await anext(iterator)
            except StopAsyncIteration:
                semaphore.release()
                break
            except TimeoutError:
                semaphore.release()
                if in_flight:
                    await asyncio.sleep(0)
                    continue
                raise
            except BaseException:
                semaphore.release()
                raise
            task = asyncio.create_task(run_item(item))
            in_flight.add(task)
            task.add_done_callback(record_completion)
    except asyncio.CancelledError:
        for task in in_flight:
            task.cancel()
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        raise
    finally:
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        if first_error is not None:
            raise first_error


__all__ = ["map_bounded", "run_bounded_iterator"]
