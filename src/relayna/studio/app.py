from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from .events import (
    RedisStudioEventStore,
    StudioEventIngestService,
    StudioEventStream,
    StudioPullSyncWorker,
    create_studio_events_router,
)
from .federation import (
    StudioFederationService,
    _default_async_client_factory,
    create_federation_router,
)
from .health import (
    RedisStudioHealthStore,
    StudioHealthRefreshService,
    StudioHealthRefreshWorker,
    create_studio_health_router,
)
from .logs import LokiLogProvider, StudioLogQueryService, create_studio_logs_router
from .registry import (
    CapabilityFetcher,
    HttpCapabilityFetcher,
    RedisServiceRegistryStore,
    ServiceRegistryService,
    create_service_registry_router,
)


@dataclass(slots=True)
class StudioRuntime:
    redis: Redis
    registry_store: RedisServiceRegistryStore
    registry_service: ServiceRegistryService
    http_client: httpx.AsyncClient
    federation_service: StudioFederationService
    event_store: RedisStudioEventStore
    event_ingest_service: StudioEventIngestService
    event_stream: StudioEventStream
    health_store: RedisStudioHealthStore
    health_service: StudioHealthRefreshService
    log_query_service: StudioLogQueryService
    pull_sync_worker: StudioPullSyncWorker | None = None
    pull_sync_task: asyncio.Task[None] | None = None
    health_refresh_worker: StudioHealthRefreshWorker | None = None
    health_refresh_task: asyncio.Task[None] | None = None


class _StudioLifespan:
    def __init__(
        self,
        *,
        redis_url: str,
        app_state_key: str,
        registry_prefix: str,
        capability_fetcher: CapabilityFetcher | None,
        federation_client_factory,
        federation_timeout_seconds: float,
        event_store_prefix: str,
        event_store_ttl_seconds: int | None,
        event_history_maxlen: int,
        pull_sync_interval_seconds: float | None,
        health_store_prefix: str,
        health_refresh_interval_seconds: float | None,
        capability_stale_after_seconds: int,
        observation_stale_after_seconds: int,
        worker_heartbeat_stale_after_seconds: int,
    ) -> None:
        self._redis_url = redis_url
        self._app_state_key = app_state_key
        self._registry_prefix = registry_prefix
        self._capability_fetcher = capability_fetcher
        self._federation_client_factory = federation_client_factory
        self._federation_timeout_seconds = federation_timeout_seconds
        self._event_store_prefix = event_store_prefix
        self._event_store_ttl_seconds = event_store_ttl_seconds
        self._event_history_maxlen = event_history_maxlen
        self._pull_sync_interval_seconds = pull_sync_interval_seconds
        self._health_store_prefix = health_store_prefix
        self._health_refresh_interval_seconds = health_refresh_interval_seconds
        self._capability_stale_after_seconds = capability_stale_after_seconds
        self._observation_stale_after_seconds = observation_stale_after_seconds
        self._worker_heartbeat_stale_after_seconds = worker_heartbeat_stale_after_seconds
        self._runtime: StudioRuntime | None = None

    @property
    def app_state_key(self) -> str:
        return self._app_state_key

    def ensure_runtime(self) -> StudioRuntime:
        if self._runtime is None:
            redis = Redis.from_url(self._redis_url)
            registry_store = RedisServiceRegistryStore(redis, prefix=self._registry_prefix)
            registry_service = ServiceRegistryService(
                store=registry_store,
                capability_fetcher=self._capability_fetcher,
            )
            http_client = self._federation_client_factory(self._federation_timeout_seconds)
            event_store = RedisStudioEventStore(
                redis,
                prefix=self._event_store_prefix,
                ttl_seconds=self._event_store_ttl_seconds,
                history_maxlen=self._event_history_maxlen,
            )
            health_store = RedisStudioHealthStore(redis, prefix=self._health_store_prefix)
            federation_service = StudioFederationService(
                registry_service=registry_service,
                http_client=http_client,
            )
            health_service = StudioHealthRefreshService(
                registry_service=registry_service,
                health_store=health_store,
                activity_reader=event_store,
                http_client=http_client,
                capability_stale_after_seconds=self._capability_stale_after_seconds,
                observation_stale_after_seconds=self._observation_stale_after_seconds,
                worker_heartbeat_stale_after_seconds=self._worker_heartbeat_stale_after_seconds,
            )
            log_query_service = StudioLogQueryService(
                registry_service=registry_service,
                providers={"loki": LokiLogProvider(http_client=http_client)},
            )
            event_ingest_service = StudioEventIngestService(
                registry_service=registry_service,
                event_store=event_store,
                http_client=http_client,
            )
            event_stream = StudioEventStream(event_store=event_store)
            pull_sync_worker = (
                StudioPullSyncWorker(
                    ingest_service=event_ingest_service,
                    interval_seconds=self._pull_sync_interval_seconds,
                )
                if self._pull_sync_interval_seconds is not None
                else None
            )
            health_refresh_worker = (
                StudioHealthRefreshWorker(
                    health_service=health_service,
                    interval_seconds=self._health_refresh_interval_seconds,
                )
                if self._health_refresh_interval_seconds is not None
                else None
            )
            self._runtime = StudioRuntime(
                redis=redis,
                registry_store=registry_store,
                registry_service=registry_service,
                http_client=http_client,
                federation_service=federation_service,
                event_store=event_store,
                event_ingest_service=event_ingest_service,
                event_stream=event_stream,
                health_store=health_store,
                health_service=health_service,
                log_query_service=log_query_service,
                pull_sync_worker=pull_sync_worker,
                health_refresh_worker=health_refresh_worker,
            )
        return self._runtime

    def __call__(self, app: FastAPI):
        @asynccontextmanager
        async def lifespan() -> AsyncIterator[None]:
            runtime = self.ensure_runtime()
            setattr(app.state, self._app_state_key, runtime)
            try:
                if runtime.pull_sync_worker is not None:
                    runtime.pull_sync_task = asyncio.create_task(
                        runtime.pull_sync_worker.run_forever(),
                        name="studio-event-pull-sync",
                    )
                if runtime.health_refresh_worker is not None:
                    runtime.health_refresh_task = asyncio.create_task(
                        runtime.health_refresh_worker.run_forever(),
                        name="studio-health-refresh",
                    )
                yield
            finally:
                if runtime.pull_sync_worker is not None:
                    runtime.pull_sync_worker.stop()
                if runtime.health_refresh_worker is not None:
                    runtime.health_refresh_worker.stop()
                if runtime.pull_sync_task is not None:
                    try:
                        await asyncio.wait_for(asyncio.shield(runtime.pull_sync_task), timeout=5.0)
                    except TimeoutError:
                        runtime.pull_sync_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await runtime.pull_sync_task
                    finally:
                        runtime.pull_sync_task = None
                if runtime.health_refresh_task is not None:
                    try:
                        await asyncio.wait_for(asyncio.shield(runtime.health_refresh_task), timeout=5.0)
                    except TimeoutError:
                        runtime.health_refresh_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await runtime.health_refresh_task
                    finally:
                        runtime.health_refresh_task = None
                await runtime.http_client.aclose()
                await runtime.redis.aclose()
                if hasattr(app.state, self._app_state_key):
                    delattr(app.state, self._app_state_key)

        return lifespan()


def create_studio_app(
    *,
    redis_url: str,
    title: str = "Relayna Studio Backend",
    app_state_key: str = "studio",
    registry_prefix: str = "studio:services",
    capability_fetcher: CapabilityFetcher | None = None,
    capability_refresh_allowed_hosts: tuple[str, ...] | None = None,
    capability_refresh_allowed_networks: tuple[str, ...] | None = None,
    federation_client_factory=None,
    federation_timeout_seconds: float = 5.0,
    event_store_prefix: str = "studio:events",
    event_store_ttl_seconds: int | None = 86400,
    event_history_maxlen: int = 5000,
    pull_sync_interval_seconds: float | None = 5.0,
    health_store_prefix: str = "studio:health",
    health_refresh_interval_seconds: float | None = 60.0,
    capability_stale_after_seconds: int = 180,
    observation_stale_after_seconds: int = 300,
    worker_heartbeat_stale_after_seconds: int = 90,
) -> FastAPI:
    resolved_capability_fetcher = capability_fetcher or HttpCapabilityFetcher(
        allowed_hosts=capability_refresh_allowed_hosts,
        allowed_networks=capability_refresh_allowed_networks,
    )
    lifespan_factory = _StudioLifespan(
        redis_url=redis_url,
        app_state_key=app_state_key,
        registry_prefix=registry_prefix,
        capability_fetcher=resolved_capability_fetcher,
        federation_client_factory=federation_client_factory or _default_async_client_factory,
        federation_timeout_seconds=federation_timeout_seconds,
        event_store_prefix=event_store_prefix,
        event_store_ttl_seconds=event_store_ttl_seconds,
        event_history_maxlen=event_history_maxlen,
        pull_sync_interval_seconds=pull_sync_interval_seconds,
        health_store_prefix=health_store_prefix,
        health_refresh_interval_seconds=health_refresh_interval_seconds,
        capability_stale_after_seconds=capability_stale_after_seconds,
        observation_stale_after_seconds=observation_stale_after_seconds,
        worker_heartbeat_stale_after_seconds=worker_heartbeat_stale_after_seconds,
    )
    runtime = lifespan_factory.ensure_runtime()
    app = FastAPI(title=title, lifespan=lifespan_factory)
    app.include_router(
        create_service_registry_router(service_registry=runtime.registry_service, health_service=runtime.health_service)
    )
    app.include_router(create_studio_health_router(health_service=runtime.health_service))
    app.include_router(create_federation_router(federation_service=runtime.federation_service))
    app.include_router(
        create_studio_events_router(
            ingest_service=runtime.event_ingest_service,
            event_stream=runtime.event_stream,
        )
    )
    app.include_router(create_studio_logs_router(log_query_service=runtime.log_query_service))
    return app


def get_studio_runtime(app: FastAPI, *, app_state_key: str = "studio") -> StudioRuntime:
    runtime = getattr(app.state, app_state_key, None)
    if isinstance(runtime, StudioRuntime):
        return runtime
    raise RuntimeError(f"Studio runtime is not available on app.state.{app_state_key}.")


__all__ = ["StudioRuntime", "create_studio_app", "get_studio_runtime"]
