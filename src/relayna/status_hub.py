from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from aio_pika.abc import AbstractChannel, AbstractQueue

from .contracts import normalize_event_aliases
from .rabbitmq import RelaynaRabbitClient
from .status_store import RedisStatusStore


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
    ) -> None:
        self._rabbitmq = rabbitmq
        self._store = store
        self._consume_arguments = consume_arguments or {}
        self._sanitize_meta_keys = sanitize_meta_keys or {"auth_token"}
        self._prefetch = prefetch
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        queue_name = await self._rabbitmq.ensure_status_queue()
        config = self._rabbitmq.config
        consume_args = dict(self._consume_arguments)
        if config.status_use_streams and "x-stream-offset" not in consume_args:
            consume_args["x-stream-offset"] = config.status_stream_initial_offset

        while not self._stop.is_set():
            channel: AbstractChannel | None = None
            try:
                channel = await self._rabbitmq.acquire_channel(prefetch=self._prefetch)
                queue: AbstractQueue = await channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments=config.status_queue_arguments() or None,
                )
                async with queue.iterator(arguments=consume_args or None) as iterator:
                    async for message in iterator:
                        try:
                            payload = json.loads(message.body.decode("utf-8", errors="replace"))
                        except Exception:
                            await message.ack()
                            continue
                        await message.ack()

                        data = normalize_event_aliases(payload)
                        meta = data.get("meta")
                        if isinstance(meta, Mapping):
                            sanitized_meta = dict(meta)
                            for key in self._sanitize_meta_keys:
                                sanitized_meta.pop(key, None)
                            data["meta"] = sanitized_meta

                        task_id = str(data.get("task_id", "")).strip()
                        if not task_id:
                            continue
                        try:
                            await self._store.set_history(task_id, data)
                        except Exception:
                            # Redis writes are best effort.
                            continue
                        
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._stop.is_set():
                    return
                await asyncio.sleep(2)
            finally:
                if channel is not None:
                    try:
                        await channel.close()
                    except Exception:
                        pass
