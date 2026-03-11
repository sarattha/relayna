from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any, Literal

from aio_pika.abc import AbstractChannel, AbstractQueue  # ty:ignore[unresolved-import]

from .contracts import normalize_event_aliases
from .rabbitmq import RelaynaRabbitClient

StreamOffset = Literal["first", "last"] | int


class StreamHistoryReader:
    """Short-lived status stream replay helper for debugging/history endpoints."""

    def __init__(
        self,
        *,
        rabbitmq: RelaynaRabbitClient,
        queue_arguments: dict[str, Any] | None = None,
        output_adapter: callable | None = None,
    ) -> None:
        self._rabbitmq = rabbitmq
        self._queue_arguments = queue_arguments
        self._output_adapter = output_adapter

    async def replay(
        self,
        *,
        task_id: str | None = None,
        start_offset: StreamOffset = "first",
        terminal_statuses: tuple[str, ...] = ("completed", "failed"),
        stop_on_terminal: bool = True,
        max_seconds: float | None = None,
        max_scan: int | None = None,
        require_stream: bool = True,
    ) -> list[dict[str, Any]]:
        queue_args = self._queue_arguments if self._queue_arguments is not None else self._rabbitmq.config.status_queue_arguments()
        if require_stream and queue_args.get("x-queue-type") != "stream":
            raise RuntimeError("History replay requires a stream queue")

        channel: AbstractChannel | None = None
        queue_name = await self._rabbitmq.ensure_status_queue()
        started = time.monotonic()
        events: list[dict[str, Any]] = []
        scanned = 0

        try:
            channel = await self._rabbitmq.acquire_channel(prefetch=1000)
            queue: AbstractQueue = await channel.declare_queue(queue_name, durable=True, arguments=queue_args or None)

            consume_args: dict[str, Any] = {}
            if isinstance(start_offset, str):
                consume_args["x-stream-offset"] = start_offset
            else:
                consume_args["x-stream-offset"] = int(start_offset)

            # Note: Timeout just in case user provides out of bounds max_scans or max_seconds
            async with queue.iterator(arguments=consume_args or None, timeout=30.0) as iterator:
                async for message in iterator:

                    if max_seconds is not None and (time.monotonic() - started) > max_seconds:
                        break

                    try:
                        payload = json.loads(message.body.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        await message.ack()
                        continue
                    await message.ack()

                    normalized = normalize_event_aliases(payload)
                    current_task_id = str(normalized.get("task_id", ""))
                    if task_id and current_task_id != task_id:
                        continue

                    if self._output_adapter is not None:
                        normalized = self._output_adapter(normalized)
                    events.append(normalized)

                    if max_scan is not None and len(events) >= max_scan:
                        break

                    status = str(normalized.get("status", "")).lower()
                    if task_id and stop_on_terminal and status in terminal_statuses:
                        break
            return events
        
        except TimeoutError:
            return events
        
        finally:
            if channel is not None:
                try:
                    await channel.close()
                except Exception:
                    pass
