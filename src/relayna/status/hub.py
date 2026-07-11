from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from aio_pika.abc import AbstractChannel, AbstractQueue

from .._async import run_bounded_iterator
from ..contracts import ContractAliasConfig, normalize_contract_aliases
from ..observability import (
    ObservationSink,
    StatusHubLoopError,
    StatusHubMalformedMessage,
    StatusHubStarted,
    StatusHubStoredEvent,
    StatusHubStoreWriteFailed,
    emit_observation,
)
from ..rabbitmq import RelaynaRabbitClient
from .store import RedisStatusStore


@dataclass(slots=True)
class _TaskLockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class StatusHub:
    """Consumes shared status queue/stream and writes normalized events to Redis."""

    def __init__(
        self,
        *,
        rabbitmq: RelaynaRabbitClient,
        store: RedisStatusStore,
        consume_arguments: dict[str, Any] | None = None,
        sanitize_meta_keys: set[str] | None = None,
        prefetch: int = 200,
        observation_sink: ObservationSink | None = None,
        alias_config: ContractAliasConfig | None = None,
        fail_fast_errors: Iterable[type[Exception]] | None = None,
    ) -> None:
        self._rabbitmq = rabbitmq
        self._store = store
        self._consume_arguments = consume_arguments or {}
        self._sanitize_meta_keys = sanitize_meta_keys or {"auth_token"}
        self._prefetch = prefetch
        self._observation_sink = observation_sink
        self._alias_config = alias_config
        self._fail_fast_errors = tuple(fail_fast_errors or ())
        self._stop = asyncio.Event()
        self._task_locks: dict[str, _TaskLockEntry] = {}

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        queue_name = await self._rabbitmq.ensure_status_queue()
        topology = self._rabbitmq.topology
        consume_args = dict(self._consume_arguments)
        default_stream_args = topology.status_stream_consume_arguments()
        if "x-stream-offset" not in consume_args and "x-stream-offset" in default_stream_args:
            consume_args.update(default_stream_args)
        await emit_observation(self._observation_sink, StatusHubStarted(queue_name=queue_name))
        started_consuming = False

        while not self._stop.is_set():
            channel: AbstractChannel | None = None
            try:
                channel = await self._rabbitmq.acquire_channel(prefetch=self._prefetch)
                queue: AbstractQueue = await channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments=topology.status_queue_arguments() or None,
                )
                async with queue.iterator(arguments=consume_args or None) as iterator:
                    started_consuming = True
                    if self._prefetch <= 1:
                        async for message in iterator:
                            await self._handle_message(message)
                            if self._stop.is_set():
                                break
                    else:
                        await run_bounded_iterator(
                            iterator,
                            concurrency=self._prefetch,
                            handler=self._handle_message,
                            stop_event=self._stop,
                        )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stop.is_set():
                    return
                if not started_consuming and self._fail_fast_errors and isinstance(exc, self._fail_fast_errors):
                    raise RuntimeError(f"StatusHub failed to start consuming queue: {exc}") from exc
                await emit_observation(
                    self._observation_sink,
                    StatusHubLoopError(
                        exception_type=type(exc).__name__,
                        exception_message=str(exc),
                        retry_delay_seconds=2.0,
                    ),
                )
                await asyncio.sleep(2)
            finally:
                if channel is not None:
                    try:
                        await channel.close()
                    except Exception:
                        pass

    async def _handle_message(self, message: Any) -> None:
        try:
            payload = json.loads(message.body.decode("utf-8", errors="replace"))
        except Exception:
            await message.ack()
            await emit_observation(
                self._observation_sink,
                StatusHubMalformedMessage(reason="malformed_json"),
            )
            return

        if not isinstance(payload, Mapping):
            await message.ack()
            await emit_observation(
                self._observation_sink,
                StatusHubMalformedMessage(reason="payload_not_mapping"),
            )
            return
        try:
            data = normalize_contract_aliases(payload, self._alias_config, drop_aliases=True)
        except Exception:
            await message.ack()
            await emit_observation(
                self._observation_sink,
                StatusHubMalformedMessage(reason="alias_normalization_failed"),
            )
            return
        meta = data.get("meta")
        if isinstance(meta, Mapping):
            sanitized_meta = dict(meta)
            for key in self._sanitize_meta_keys:
                sanitized_meta.pop(key, None)
            data["meta"] = sanitized_meta

        task_id = str(data.get("task_id", "")).strip()
        if not task_id:
            await message.ack()
            return

        try:
            await self._store_ordered(task_id, data)
        except Exception as exc:
            await emit_observation(
                self._observation_sink,
                StatusHubStoreWriteFailed(
                    task_id=task_id,
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                ),
            )
            await message.reject(requeue=True)
            return

        await message.ack()
        await emit_observation(
            self._observation_sink,
            StatusHubStoredEvent(
                task_id=task_id,
                event_id=_event_id(data),
                status=_status_value(data),
            ),
        )

    async def _store_ordered(self, task_id: str, data: dict[str, Any]) -> None:
        entry = self._task_locks.get(task_id)
        if entry is None:
            entry = _TaskLockEntry()
            self._task_locks[task_id] = entry
        entry.users += 1
        try:
            async with entry.lock:
                await self._store.set_history(task_id, data)
        finally:
            entry.users -= 1
            if entry.users == 0 and self._task_locks.get(task_id) is entry:
                del self._task_locks[task_id]


def _event_id(data: Mapping[str, Any]) -> str | None:
    value = data.get("event_id")
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return None


def _status_value(data: Mapping[str, Any]) -> str | None:
    status = data.get("status")
    if isinstance(status, str):
        return status
    if status is None:
        return None
    return str(status)


__all__ = ["StatusHub"]
