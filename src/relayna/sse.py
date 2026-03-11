from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from .contracts import TerminalStatusSet, denormalize_document_aliases
from .status_store import RedisStatusStore

EventAdapter = Callable[[dict[str, Any]], dict[str, Any]]


def _default_adapter(data: dict[str, Any]) -> dict[str, Any]:
    return data


def _sse_event(data: dict[str, Any], *, event_type: str = "status") -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class SSEStatusStream:
    """History + realtime SSE stream built on Redis list + pubsub."""

    def __init__(
        self,
        *,
        store: RedisStatusStore,
        terminal_statuses: TerminalStatusSet | None = None,
        output_adapter: EventAdapter | None = None,
    ) -> None:
        self._store = store
        self._terminal_statuses = terminal_statuses or TerminalStatusSet()
        self._output_adapter = output_adapter or _default_adapter

    async def stream(self, task_id: str) -> AsyncIterator[bytes]:
        yield b"event: ready\ndata: {}\n\n"

        history = await self._store.get_history(task_id)

        for item in reversed(history):
            data = self._output_adapter(dict(item))
            yield _sse_event(data)
            await asyncio.sleep(0)

            if self._terminal_statuses.is_terminal(data["status"]):
                return

        pubsub = self._store.redis.pubsub()
        channel_resolver = getattr(self._store, "channel_name", None)
        if callable(channel_resolver):
            channel = channel_resolver(task_id)
        else:
            channel = f"{self._store.prefix}:channel:{task_id}"
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message is None or message.get("type") != "message":
                    continue
                data_raw = message.get("data")
                if not data_raw:
                    continue
                try:
                    parsed = json.loads(data_raw)
                except json.JSONDecodeError:
                    continue

                data = self._output_adapter(dict(parsed))
                yield _sse_event(data)

                status = data.get("status")
                if isinstance(status, str) and self._terminal_statuses.is_terminal(status):
                    break
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()


def document_output_adapter(data: Mapping[str, Any]) -> dict[str, Any]:
    return denormalize_document_aliases(data)
