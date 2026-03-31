from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import APIRouter, FastAPI, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse, StreamingResponse
from redis.asyncio import Redis

from .contracts import ContractAliasConfig, TerminalStatusSet, public_output_aliases
from .dlq import DLQRecordState, DLQReplayConflict, DLQService, RedisDLQStore
from .history import StreamHistoryReader, StreamOffset
from .rabbitmq import RelaynaRabbitClient
from .sse import EventAdapter, SSEStatusStream
from .status_hub import StatusHub
from .status_store import RedisStatusStore
from .topology import RelaynaTopology


class HistoryOutputAdapter(Protocol):
    def __call__(self, event: dict[str, Any]) -> dict[str, Any]: ...


HISTORY_START_OFFSET_QUERY = Query(default="first")


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


def create_status_router(
    *,
    sse_stream: SSEStatusStream,
    history_reader: StreamHistoryReader | None = None,
    latest_status_store: RedisStatusStore | None = None,
    latest_output_adapter: EventAdapter | None = None,
    events_path: str = "/events/{task_id}",
    history_path: str = "/history",
    status_path: str = "/status/{task_id}",
    require_stream: bool = True,
    alias_config: ContractAliasConfig | None = None,
) -> APIRouter:
    router = APIRouter()
    task_http_name = _http_field_name("task_id", alias_config)
    resolved_events_path = events_path.replace("{task_id}", "{" + task_http_name + "}")
    resolved_status_path = status_path.replace("{task_id}", "{" + task_http_name + "}")
    task_response_name = _payload_field_name("task_id", alias_config)

    @router.get(resolved_events_path)
    async def events(
        task_id: str = Path(alias=task_http_name),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        return sse_response(sse_stream.stream(task_id, last_event_id=last_event_id))

    if latest_status_store is not None:
        output_adapter = latest_output_adapter or (lambda data: data)

        @router.get(resolved_status_path)
        async def latest_status(task_id: str = Path(alias=task_http_name)) -> JSONResponse:
            event = await latest_status_store.get_latest(task_id)
            if event is None:
                raise HTTPException(status_code=404, detail=f"No status found for {task_response_name} '{task_id}'.")
            return JSONResponse(
                {
                    task_response_name: task_id,
                    "event": public_output_aliases(output_adapter(dict(event)), alias_config),
                }
            )

    if history_reader is not None:

        @router.get(history_path)
        async def history(
            task_id: str | None = Query(default=None, alias=task_http_name),
            start_offset: StreamOffset = HISTORY_START_OFFSET_QUERY,
            max_seconds: float | None = Query(default=None),
            max_scan: int | None = Query(default=None),
        ) -> JSONResponse:
            try:
                if all(param is None for param in [max_seconds, max_scan, task_id]):
                    raise HTTPException(
                        status_code=422,
                        detail="Set either max_seconds, max_scan, or task_id to limit the scope of the history query.",
                    )

                events = await history_reader.replay(
                    task_id=task_id,
                    start_offset=start_offset,
                    max_seconds=max_seconds,
                    max_scan=max_scan,
                    require_stream=require_stream,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            payload: dict[str, Any] = {
                "count": len(events),
                "events": [public_output_aliases(dict(event), alias_config) for event in events],
            }
            if task_id is not None:
                payload[task_response_name] = task_id
            return JSONResponse(payload)

    return router


def create_dlq_router(
    *,
    dlq_service: DLQService,
    queues_path: str = "/dlq/queues",
    broker_queues_path: str = "/broker/dlq/queues",
    messages_path: str = "/dlq/messages",
    message_path: str = "/dlq/messages/{dlq_id}",
    replay_path: str = "/dlq/messages/{dlq_id}/replay",
    broker_dlq_queue_names: Sequence[str] | None = None,
    alias_config: ContractAliasConfig | None = None,
) -> APIRouter:
    router = APIRouter()
    task_http_name = _http_field_name("task_id", alias_config)

    @router.get(queues_path)
    async def queues() -> JSONResponse:
        items = await dlq_service.get_queue_summaries()
        return JSONResponse({"queues": [item.model_dump(mode="json") for item in items]})

    if broker_dlq_queue_names is not None:

        @router.get(broker_queues_path)
        async def broker_queues() -> JSONResponse:
            items = await dlq_service.get_broker_queue_summaries(broker_dlq_queue_names)
            return JSONResponse({"queues": [item.model_dump(mode="json") for item in items]})

    @router.get(messages_path)
    async def messages(
        queue_name: str | None = None,
        task_id: str | None = Query(default=None, alias=task_http_name),
        reason: str | None = None,
        source_queue_name: str | None = None,
        state: DLQRecordState | None = None,
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        payload = await dlq_service.list_messages(
            queue_name=queue_name,
            task_id=task_id,
            reason=reason,
            source_queue_name=source_queue_name,
            state=state,
            cursor=cursor,
            limit=limit,
        )
        return JSONResponse(_public_dlq_payload(payload.model_dump(mode="json"), alias_config))

    @router.post(replay_path)
    async def replay(dlq_id: str, force: bool = Query(default=False)) -> JSONResponse:
        try:
            result = await dlq_service.replay_message(dlq_id, force=force)
        except DLQReplayConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail=f"No DLQ message found for dlq_id '{dlq_id}'.")
        return JSONResponse(result.model_dump(mode="json"))

    @router.get(message_path)
    async def message_detail(dlq_id: str) -> JSONResponse:
        result = await dlq_service.get_message_detail(dlq_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No DLQ message found for dlq_id '{dlq_id}'.")
        return JSONResponse(_public_dlq_payload(result.model_dump(mode="json"), alias_config))

    return router


def _http_field_name(field_name: str, alias_config: ContractAliasConfig | None) -> str:
    if alias_config is None:
        return field_name
    return alias_config.http_alias_for(field_name) or field_name


def _payload_field_name(field_name: str, alias_config: ContractAliasConfig | None) -> str:
    if alias_config is None:
        return field_name
    return alias_config.payload_alias_for(field_name) or field_name


def _public_dlq_payload(payload: Any, alias_config: ContractAliasConfig | None) -> Any:
    if isinstance(payload, list):
        return [_public_dlq_payload(item, alias_config) for item in payload]
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
        updated["items"] = [_public_dlq_payload(item, alias_config) for item in updated["items"]]
    return updated
