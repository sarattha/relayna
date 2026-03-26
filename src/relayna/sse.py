from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from .contracts import ContractAliasConfig, TerminalStatusSet, denormalize_document_aliases, public_output_aliases
from .observability import (
    ObservationSink,
    SSEHistoryReplayed,
    SSEKeepaliveSent,
    SSELiveEventSent,
    SSEMalformedPubsubPayload,
    SSEResumeRequested,
    SSEStreamEnded,
    SSEStreamStarted,
    emit_observation,
)
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
        observation_sink: ObservationSink | None = None,
        alias_config: ContractAliasConfig | None = None,
    ) -> None:
        self._store = store
        self._terminal_statuses = terminal_statuses or TerminalStatusSet()
        self._output_adapter = output_adapter or _default_adapter
        self._keepalive_interval_seconds = keepalive_interval_seconds
        self._observation_sink = observation_sink
        self._alias_config = alias_config

    async def stream(self, task_id: str, *, last_event_id: str | None = None) -> AsyncIterator[bytes]:
        yield b"event: ready\ndata: {}\n\n"
        await emit_observation(
            self._observation_sink,
            SSEStreamStarted(task_id=task_id, resume_requested=last_event_id is not None),
        )

        pubsub = self._store.redis.pubsub()
        channel_resolver = getattr(self._store, "channel_name", None)
        if callable(channel_resolver):
            channel = channel_resolver(task_id)
        else:
            channel = f"{self._store.prefix}:channel:{task_id}"

        sent_count = 0
        terminal_status: str | None = None
        try:
            await pubsub.subscribe(channel)
            history = await self._store.get_history(task_id)
            history_items = list(reversed(history))

            if last_event_id is not None:
                replay_start = 0
                history_match_found = False
                for index, item in enumerate(history_items):
                    if _event_id(item) == last_event_id:
                        replay_start = index + 1
                        history_match_found = True
                        break
                history_items = history_items[replay_start:]
                await emit_observation(
                    self._observation_sink,
                    SSEResumeRequested(
                        task_id=task_id,
                        last_event_id=last_event_id,
                        history_match_found=history_match_found,
                    ),
                )

            seen_event_ids: set[str] = set()
            replayed_count = 0
            for item in history_items:
                data = self._to_public_event(dict(item))
                emitted_event_id = _event_id(data)
                if emitted_event_id is not None:
                    seen_event_ids.add(emitted_event_id)
                yield _sse_event(data)
                sent_count += 1
                replayed_count += 1
                await emit_observation(
                    self._observation_sink,
                    SSELiveEventSent(
                        task_id=task_id,
                        event_id=emitted_event_id,
                        status=_status_value(data),
                        source="history",
                    ),
                )
                await asyncio.sleep(0)

                if self._terminal_statuses.is_terminal(data.get("status")):
                    await emit_observation(
                        self._observation_sink,
                        SSEHistoryReplayed(task_id=task_id, replayed_count=replayed_count),
                    )
                    terminal_status = _status_value(data)
                    return
            await emit_observation(
                self._observation_sink,
                SSEHistoryReplayed(task_id=task_id, replayed_count=replayed_count),
            )

            message_iterator = pubsub.listen().__aiter__()
            while True:
                try:
                    message = await self._next_pubsub_message(pubsub, message_iterator)
                except StopAsyncIteration:
                    break
                if message is None:
                    yield _sse_comment("keepalive")
                    await emit_observation(self._observation_sink, SSEKeepaliveSent(task_id=task_id))
                    continue
                if message.get("type") != "message":
                    continue
                data_raw = message.get("data")
                if not data_raw:
                    continue
                try:
                    parsed = json.loads(data_raw)
                except json.JSONDecodeError:
                    await emit_observation(self._observation_sink, SSEMalformedPubsubPayload(task_id=task_id))
                    continue

                data = self._to_public_event(dict(parsed))
                emitted_event_id = _event_id(data)
                if emitted_event_id is not None:
                    if emitted_event_id in seen_event_ids:
                        continue
                    seen_event_ids.add(emitted_event_id)

                yield _sse_event(data)
                sent_count += 1
                await emit_observation(
                    self._observation_sink,
                    SSELiveEventSent(
                        task_id=task_id,
                        event_id=emitted_event_id,
                        status=_status_value(data),
                        source="pubsub",
                    ),
                )

                if self._terminal_statuses.is_terminal(data.get("status")):
                    terminal_status = _status_value(data)
                    break
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            await emit_observation(
                self._observation_sink,
                SSEStreamEnded(task_id=task_id, terminal_status=terminal_status, sent_count=sent_count),
            )

    async def _next_pubsub_message(
        self,
        pubsub: Any,
        iterator: AsyncIterator[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if self._keepalive_interval_seconds is None:
            return await anext(iterator)

        get_message = getattr(pubsub, "get_message", None)
        if callable(get_message):
            try:
                return await get_message(timeout=self._keepalive_interval_seconds)
            except AttributeError:
                pass

        try:
            return await asyncio.wait_for(anext(iterator), timeout=self._keepalive_interval_seconds)
        except TimeoutError:
            return None

    def _to_public_event(self, data: dict[str, Any]) -> dict[str, Any]:
        adapted = self._output_adapter(dict(data))
        return public_output_aliases(adapted, self._alias_config)


def document_output_adapter(data: Mapping[str, Any]) -> dict[str, Any]:
    return denormalize_document_aliases(data)


def _status_value(data: Mapping[str, Any]) -> str | None:
    status = data.get("status")
    if isinstance(status, str):
        return status
    if status is None:
        return None
    return str(status)
