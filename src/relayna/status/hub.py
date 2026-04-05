from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from aio_pika.abc import AbstractChannel, AbstractQueue

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
    ) -> None:
        self._rabbitmq = rabbitmq
        self._store = store
        self._consume_arguments = consume_arguments or {}
        self._sanitize_meta_keys = sanitize_meta_keys or {"auth_token"}
        self._prefetch = prefetch
        self._observation_sink = observation_sink
        self._alias_config = alias_config
        self._stop = asyncio.Event()

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
                    async for message in iterator:
                        try:
                            payload = json.loads(message.body.decode("utf-8", errors="replace"))
                        except Exception:
                            await message.ack()
                            await emit_observation(
                                self._observation_sink,
                                StatusHubMalformedMessage(reason="malformed_json"),
                            )
                            continue

                        data = normalize_contract_aliases(payload, self._alias_config, drop_aliases=True)
                        meta = data.get("meta")
                        if isinstance(meta, Mapping):
                            sanitized_meta = dict(meta)
                            for key in self._sanitize_meta_keys:
                                sanitized_meta.pop(key, None)
                            data["meta"] = sanitized_meta

                        task_id = str(data.get("task_id", "")).strip()
                        await message.ack()
                        if not task_id:
                            continue
                        try:
                            await self._store.set_history(task_id, data)
                            await emit_observation(
                                self._observation_sink,
                                StatusHubStoredEvent(
                                    task_id=task_id,
                                    event_id=_event_id(data),
                                    status=_status_value(data),
                                ),
                            )
                        except Exception as exc:
                            await emit_observation(
                                self._observation_sink,
                                StatusHubStoreWriteFailed(
                                    task_id=task_id,
                                    exception_type=type(exc).__name__,
                                ),
                            )
                            continue

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stop.is_set():
                    return
                await emit_observation(
                    self._observation_sink,
                    StatusHubLoopError(exception_type=type(exc).__name__, retry_delay_seconds=2.0),
                )
                await asyncio.sleep(2)
            finally:
                if channel is not None:
                    try:
                        await channel.close()
                    except Exception:
                        pass


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
