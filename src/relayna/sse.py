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


def _event_id(data: Mapping[str, Any]) -> str | None:
    value = data.get("event_id")
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return None


def _sse_event(data: dict[str, Any], *, event_type: str = "status") -> bytes:
    lines: list[str] = []
    event_id = _event_id(data)
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return ("\n".join(lines) + "\n\n").encode()


def _sse_comment(comment: str) -> bytes:
    return f": {comment}\n\n".encode()


class SSEStatusStream:
    """History + realtime SSE stream built on Redis list + pubsub."""

    def __init__(
        self,
        *,
        store: RedisStatusStore,
        terminal_statuses: TerminalStatusSet | None = None,
        output_adapter: EventAdapter | None = None,
        keepalive_interval_seconds: float | None = 15.0,
    ) -> None:
        self._store = store
        self._terminal_statuses = terminal_statuses or TerminalStatusSet()
        self._output_adapter = output_adapter or _default_adapter
        self._keepalive_interval_seconds = keepalive_interval_seconds

    async def stream(self, task_id: str, *, last_event_id: str | None = None) -> AsyncIterator[bytes]:
        yield b"event: ready\ndata: {}\n\n"

        pubsub = self._store.redis.pubsub()
        channel_resolver = getattr(self._store, "channel_name", None)
        if callable(channel_resolver):
            channel = channel_resolver(task_id)
        else:
            channel = f"{self._store.prefix}:channel:{task_id}"

        try:
            await pubsub.subscribe(channel)
            history = await self._store.get_history(task_id)
            history_items = list(reversed(history))

            if last_event_id is not None:
                replay_start = 0
                for index, item in enumerate(history_items):
                    if _event_id(item) == last_event_id:
                        replay_start = index + 1
                        break
                history_items = history_items[replay_start:]

            seen_event_ids: set[str] = set()
            for item in history_items:
                data = self._output_adapter(dict(item))
                emitted_event_id = _event_id(data)
                if emitted_event_id is not None:
                    seen_event_ids.add(emitted_event_id)
                yield _sse_event(data)
                await asyncio.sleep(0)

                if self._terminal_statuses.is_terminal(data.get("status")):
                    return

            message_iterator = pubsub.listen().__aiter__()
            while True:
                try:
                    message = await self._next_pubsub_message(message_iterator)
                except StopAsyncIteration:
                    break
                if message is None:
                    yield _sse_comment("keepalive")
                    continue
                if message.get("type") != "message":
                    continue
                data_raw = message.get("data")
                if not data_raw:
                    continue
                try:
                    parsed = json.loads(data_raw)
                except json.JSONDecodeError:
                    continue

                data = self._output_adapter(dict(parsed))
                emitted_event_id = _event_id(data)
                if emitted_event_id is not None:
                    if emitted_event_id in seen_event_ids:
                        continue
                    seen_event_ids.add(emitted_event_id)

                yield _sse_event(data)

                if self._terminal_statuses.is_terminal(data.get("status")):
                    break
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    async def _next_pubsub_message(self, iterator: AsyncIterator[dict[str, Any]]) -> dict[str, Any] | None:
        if self._keepalive_interval_seconds is None:
            return await anext(iterator)
        try:
            return await asyncio.wait_for(anext(iterator), timeout=self._keepalive_interval_seconds)
        except asyncio.TimeoutError:
            return None


def document_output_adapter(data: Mapping[str, Any]) -> dict[str, Any]:
    return denormalize_document_aliases(data)
