from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from collections.abc import AsyncIterator
from typing import Any, Protocol

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from redis.asyncio import Redis

from .config import RelaynaTopologyConfig
from .contracts import TerminalStatusSet
from .history import StreamHistoryReader, StreamOffset
from .rabbitmq import RelaynaRabbitClient
from .sse import EventAdapter, SSEStatusStream
from .status_hub import StatusHub
from .status_store import RedisStatusStore


class HistoryOutputAdapter(Protocol):
    def __call__(self, event: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class RelaynaRuntime:
    rabbitmq: RelaynaRabbitClient
    redis: Redis
    store: RedisStatusStore
    hub: StatusHub
    sse_stream: SSEStatusStream
    history_reader: StreamHistoryReader
    hub_task: asyncio.Task[None] | None = None


class _RelaynaLifespan:
    def __init__(
        self,
        *,
        topology_config: RelaynaTopologyConfig,
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
        app_state_key: str,
    ) -> None:
        self._topology_config = topology_config
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
        self._app_state_key = app_state_key
        self._runtime: RelaynaRuntime | None = None

    @property
    def app_state_key(self) -> str:
        return self._app_state_key

    def ensure_runtime(self) -> RelaynaRuntime:
        if self._runtime is None:
            rabbitmq = RelaynaRabbitClient(
                self._topology_config,
                connection_name=self._rabbit_connection_name,
            )
            redis = Redis.from_url(self._redis_url)
            store = RedisStatusStore(
                redis,
                prefix=self._store_prefix,
                ttl_seconds=self._store_ttl_seconds,
                history_maxlen=self._store_history_maxlen,
            )
            hub = StatusHub(
                rabbitmq=rabbitmq,
                store=store,
                consume_arguments=self._hub_consume_arguments,
                sanitize_meta_keys=self._hub_sanitize_meta_keys,
                prefetch=self._hub_prefetch,
            )
            sse_stream = SSEStatusStream(
                store=store,
                terminal_statuses=self._sse_terminal_statuses,
                output_adapter=self._sse_output_adapter,
            )
            history_reader = StreamHistoryReader(
                rabbitmq=rabbitmq,
                queue_arguments=self._history_queue_arguments,
                output_adapter=self._history_output_adapter,
            )
            self._runtime = RelaynaRuntime(
                rabbitmq=rabbitmq,
                redis=redis,
                store=store,
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
    topology_config: RelaynaTopologyConfig,
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
    app_state_key: str = "relayna",
) -> _RelaynaLifespan:
    return _RelaynaLifespan(
        topology_config=topology_config,
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
        app_state_key=app_state_key,
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
) -> APIRouter:
    router = APIRouter()

    @router.get(events_path)
    async def events(task_id: str, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")) -> StreamingResponse:
        return sse_response(sse_stream.stream(task_id, last_event_id=last_event_id))

    if latest_status_store is not None:
        output_adapter = latest_output_adapter or (lambda data: data)

        @router.get(status_path)
        async def latest_status(task_id: str) -> JSONResponse:
            event = await latest_status_store.get_latest(task_id)
            if event is None:
                raise HTTPException(status_code=404, detail=f"No status found for task_id '{task_id}'.")
            return JSONResponse({"task_id": task_id, "event": output_adapter(dict(event))})

    if history_reader is not None:

        @router.get(history_path)
        async def history(
            task_id: str | None = None,
            start_offset: StreamOffset = Query(default="first"),
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
            return JSONResponse({"task_id": task_id, "count": len(events), "events": events})

    return router
