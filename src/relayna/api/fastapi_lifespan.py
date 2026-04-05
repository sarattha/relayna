from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from ..contracts import ContractAliasConfig, TerminalStatusSet, public_output_aliases
from ..dlq import RedisDLQStore
from ..rabbitmq import RelaynaRabbitClient
from ..status.history import StreamHistoryReader
from ..status.hub import StatusHub
from ..status.store import RedisStatusStore
from ..status.stream import EventAdapter, SSEStatusStream
from ..topology import RelaynaTopology


class HistoryOutputAdapter(Protocol):
    def __call__(self, event: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class RelaynaRuntime:
    rabbitmq: RelaynaRabbitClient
    redis: Redis
    store: RedisStatusStore
    dlq_store: RedisDLQStore | None
    hub: StatusHub
    sse_stream: SSEStatusStream
    history_reader: StreamHistoryReader
    hub_task: asyncio.Task[None] | None = None


class _RelaynaLifespan:
    def __init__(
        self,
        *,
        topology: RelaynaTopology,
        redis_url: str,
        store_prefix: str,
        store_ttl_seconds: int | None,
        store_history_maxlen: int,
        rabbit_connection_name: str,
        hub_consume_arguments: dict[str, Any] | None,
        hub_sanitize_meta_keys: set[str] | None,
        hub_prefetch: int,
        sse_terminal_statuses: TerminalStatusSet | None,
        sse_output_adapter: EventAdapter | None,
        history_queue_arguments: dict[str, Any] | None,
        history_output_adapter: HistoryOutputAdapter | None,
        dlq_store_prefix: str | None,
        dlq_store_ttl_seconds: int | None,
        app_state_key: str,
        alias_config: ContractAliasConfig | None,
    ) -> None:
        self._topology = topology
        self._redis_url = redis_url
        self._store_prefix = store_prefix
        self._store_ttl_seconds = store_ttl_seconds
        self._store_history_maxlen = store_history_maxlen
        self._rabbit_connection_name = rabbit_connection_name
        self._hub_consume_arguments = hub_consume_arguments
        self._hub_sanitize_meta_keys = hub_sanitize_meta_keys
        self._hub_prefetch = hub_prefetch
        self._sse_terminal_statuses = sse_terminal_statuses
        self._sse_output_adapter = sse_output_adapter
        self._history_queue_arguments = history_queue_arguments
        self._history_output_adapter = history_output_adapter
        self._dlq_store_prefix = dlq_store_prefix
        self._dlq_store_ttl_seconds = dlq_store_ttl_seconds
        self._app_state_key = app_state_key
        self._alias_config = alias_config
        self._runtime: RelaynaRuntime | None = None

    @property
    def app_state_key(self) -> str:
        return self._app_state_key

    def ensure_runtime(self) -> RelaynaRuntime:
        if self._runtime is None:
            rabbit_kwargs: dict[str, Any] = {"connection_name": self._rabbit_connection_name}
            if self._alias_config is not None:
                rabbit_kwargs["alias_config"] = self._alias_config
            rabbitmq = RelaynaRabbitClient(self._topology, **rabbit_kwargs)
            redis = Redis.from_url(self._redis_url)
            store = RedisStatusStore(
                redis,
                prefix=self._store_prefix,
                ttl_seconds=self._store_ttl_seconds,
                history_maxlen=self._store_history_maxlen,
            )
            dlq_store = None
            if self._dlq_store_prefix is not None:
                dlq_store = RedisDLQStore(
                    redis,
                    prefix=self._dlq_store_prefix,
                    ttl_seconds=self._dlq_store_ttl_seconds,
                )
            hub_kwargs: dict[str, Any] = {
                "rabbitmq": rabbitmq,
                "store": store,
                "consume_arguments": self._hub_consume_arguments,
                "sanitize_meta_keys": self._hub_sanitize_meta_keys,
                "prefetch": self._hub_prefetch,
            }
            sse_kwargs: dict[str, Any] = {
                "store": store,
                "terminal_statuses": self._sse_terminal_statuses,
                "output_adapter": self._sse_output_adapter,
            }
            history_kwargs: dict[str, Any] = {
                "rabbitmq": rabbitmq,
                "queue_arguments": self._history_queue_arguments,
                "output_adapter": self._history_output_adapter,
            }
            if self._alias_config is not None:
                hub_kwargs["alias_config"] = self._alias_config
                sse_kwargs["alias_config"] = self._alias_config
                history_kwargs["alias_config"] = self._alias_config
            hub = StatusHub(**hub_kwargs)
            sse_stream = SSEStatusStream(**sse_kwargs)
            history_reader = StreamHistoryReader(**history_kwargs)
            self._runtime = RelaynaRuntime(
                rabbitmq=rabbitmq,
                redis=redis,
                store=store,
                dlq_store=dlq_store,
                hub=hub,
                sse_stream=sse_stream,
                history_reader=history_reader,
            )
        return self._runtime

    def __call__(self, app: FastAPI):
        @asynccontextmanager
        async def lifespan():
            runtime = self.ensure_runtime()
            setattr(app.state, self._app_state_key, runtime)
            try:
                await runtime.rabbitmq.initialize()
                runtime.hub_task = asyncio.create_task(runtime.hub.run_forever(), name="relayna-status-hub")
            except Exception:
                await _shutdown_runtime(runtime)
                if hasattr(app.state, self._app_state_key):
                    delattr(app.state, self._app_state_key)
                raise

            try:
                yield
            finally:
                await _shutdown_runtime(runtime)
                if hasattr(app.state, self._app_state_key):
                    delattr(app.state, self._app_state_key)

        return lifespan()


async def _shutdown_runtime(runtime: RelaynaRuntime) -> None:
    if runtime.hub_task is not None:
        runtime.hub.stop()
        try:
            await asyncio.wait_for(asyncio.shield(runtime.hub_task), timeout=5.0)
        except TimeoutError:
            runtime.hub_task.cancel()
            with suppress(asyncio.CancelledError):
                await runtime.hub_task
        finally:
            runtime.hub_task = None
    await runtime.rabbitmq.close()
    await runtime.redis.aclose()


def create_relayna_lifespan(
    *,
    topology: RelaynaTopology,
    redis_url: str,
    store_prefix: str = "relayna",
    store_ttl_seconds: int | None = 86400,
    store_history_maxlen: int = 50,
    rabbit_connection_name: str = "relayna-fastapi",
    hub_consume_arguments: dict[str, Any] | None = None,
    hub_sanitize_meta_keys: set[str] | None = None,
    hub_prefetch: int = 200,
    sse_terminal_statuses: TerminalStatusSet | None = None,
    sse_output_adapter: EventAdapter | None = None,
    history_queue_arguments: dict[str, Any] | None = None,
    history_output_adapter: HistoryOutputAdapter | None = None,
    dlq_store_prefix: str | None = None,
    dlq_store_ttl_seconds: int | None = None,
    app_state_key: str = "relayna",
    alias_config: ContractAliasConfig | None = None,
) -> _RelaynaLifespan:
    return _RelaynaLifespan(
        topology=topology,
        redis_url=redis_url,
        store_prefix=store_prefix,
        store_ttl_seconds=store_ttl_seconds,
        store_history_maxlen=store_history_maxlen,
        rabbit_connection_name=rabbit_connection_name,
        hub_consume_arguments=hub_consume_arguments,
        hub_sanitize_meta_keys=hub_sanitize_meta_keys,
        hub_prefetch=hub_prefetch,
        sse_terminal_statuses=sse_terminal_statuses,
        sse_output_adapter=sse_output_adapter,
        history_queue_arguments=history_queue_arguments,
        history_output_adapter=history_output_adapter,
        dlq_store_prefix=dlq_store_prefix,
        dlq_store_ttl_seconds=dlq_store_ttl_seconds,
        app_state_key=app_state_key,
        alias_config=alias_config,
    )


def get_relayna_runtime(app: FastAPI, *, app_state_key: str = "relayna") -> RelaynaRuntime:
    runtime = getattr(app.state, app_state_key, None)
    if isinstance(runtime, RelaynaRuntime):
        return runtime

    lifespan_context = getattr(app.router, "lifespan_context", None)
    if isinstance(lifespan_context, _RelaynaLifespan) and lifespan_context.app_state_key == app_state_key:
        runtime = lifespan_context.ensure_runtime()
        setattr(app.state, app_state_key, runtime)
        return runtime

    raise RuntimeError(
        f"Relayna runtime is not available on app.state.{app_state_key}. "
        "Attach create_relayna_lifespan(...) to FastAPI or initialize the runtime manually."
    )


def sse_response(stream: AsyncIterator[bytes]) -> StreamingResponse:
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)


def http_field_name(field_name: str, alias_config: ContractAliasConfig | None) -> str:
    if alias_config is None:
        return field_name
    return alias_config.http_alias_for(field_name) or field_name


def payload_field_name(field_name: str, alias_config: ContractAliasConfig | None) -> str:
    if alias_config is None:
        return field_name
    return alias_config.payload_alias_for(field_name) or field_name


def public_dlq_payload(payload: Any, alias_config: ContractAliasConfig | None) -> Any:
    if isinstance(payload, list):
        return [public_dlq_payload(item, alias_config) for item in payload]
    if not isinstance(payload, dict):
        return payload

    updated = dict(payload)
    if "task_id" in updated:
        updated = public_output_aliases(updated, alias_config)
    if "body" in updated and isinstance(updated["body"], dict):
        updated["body"] = public_output_aliases(updated["body"], alias_config)
    if "latest_status" in updated and isinstance(updated["latest_status"], dict):
        updated["latest_status"] = public_output_aliases(updated["latest_status"], alias_config)
    if "status_history" in updated and isinstance(updated["status_history"], list):
        updated["status_history"] = [
            public_output_aliases(item, alias_config) if isinstance(item, dict) else item
            for item in updated["status_history"]
        ]
    if "items" in updated and isinstance(updated["items"], list):
        updated["items"] = [public_dlq_payload(item, alias_config) for item in updated["items"]]
    return updated


__all__ = [
    "HistoryOutputAdapter",
    "RelaynaRuntime",
    "create_relayna_lifespan",
    "get_relayna_runtime",
    "http_field_name",
    "payload_field_name",
    "public_dlq_payload",
    "sse_response",
]
