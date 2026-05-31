from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from relayna.metrics import RelaynaMetrics, create_metrics_router

from .events import (
    RedisStudioEventStore,
    StudioEventIngestService,
    StudioEventStream,
    StudioPullSyncWorker,
    create_studio_events_router,
)
from .failed_task_notifications import (
    FailedTaskEmailClient,
    FailedTaskEmailNotificationConfig,
    FailedTaskEmailNotificationService,
    FailedTaskEmailNotificationWorker,
    RedisFailedTaskEmailSettingsStore,
    create_failed_task_email_settings_router,
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
from .metrics import PrometheusMetricsProvider, StudioMetricsQueryService, create_studio_metrics_router
from .registry import (
    CapabilityFetcher,
    HttpCapabilityFetcher,
    RedisServiceRegistryStore,
    ServiceRegistryService,
    StudioOutboundUrlPolicy,
    create_service_registry_router,
)
from .search import (
    RedisStudioSearchStore,
    StudioRetentionWorker,
    StudioSearchService,
    create_studio_search_router,
)
from .traces import StudioTraceQueryService, TempoTraceProvider, create_studio_traces_router


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
    metrics_query_service: StudioMetricsQueryService
    trace_query_service: StudioTraceQueryService
    metrics: RelaynaMetrics
    outbound_policy: StudioOutboundUrlPolicy
    search_store: RedisStudioSearchStore
    search_service: StudioSearchService
    pull_sync_worker: StudioPullSyncWorker | None = None
    pull_sync_task: asyncio.Task[None] | None = None
    health_refresh_worker: StudioHealthRefreshWorker | None = None
    health_refresh_task: asyncio.Task[None] | None = None
    retention_worker: StudioRetentionWorker | None = None
    retention_task: asyncio.Task[None] | None = None
    failed_task_email_settings_store: RedisFailedTaskEmailSettingsStore | None = None
    failed_task_email_configured: bool = False
    failed_task_email_receivers: tuple[str, ...] = ()
    failed_task_email_worker: FailedTaskEmailNotificationWorker | None = None
    failed_task_email_task: asyncio.Task[None] | None = None


class _StudioLifespan:
    def __init__(
        self,
        *,
        redis_url: str,
        app_state_key: str,
        registry_prefix: str,
        capability_fetcher: CapabilityFetcher | None,
        capability_refresh_allowed_hosts: tuple[str, ...] | None,
        capability_refresh_allowed_networks: tuple[str, ...] | None,
        federation_client_factory,
        federation_timeout_seconds: float,
        event_store_prefix: str,
        event_store_ttl_seconds: int | None,
        event_history_maxlen: int,
        push_ingest_enabled: bool,
        pull_sync_interval_seconds: float | None,
        health_store_prefix: str,
        health_refresh_interval_seconds: float | None,
        capability_stale_after_seconds: int,
        observation_stale_after_seconds: int,
        worker_heartbeat_stale_after_seconds: int,
        task_search_index_prefix: str,
        task_index_ttl_seconds: int,
        retention_prune_interval_seconds: float | None,
        failed_task_email_enabled: bool,
        failed_task_email_service_url: str | None,
        failed_task_email_api_key: str | None,
        failed_task_email_receivers: tuple[str, ...],
        failed_task_email_interval_seconds: float,
        failed_task_email_timeout_seconds: float,
        failed_task_email_dedupe_ttl_seconds: int,
        failed_task_email_title_prefix: str,
        failed_task_email_batch_wait_seconds: int,
    ) -> None:
        self._redis_url = redis_url
        self._app_state_key = app_state_key
        self._registry_prefix = registry_prefix
        self._capability_fetcher = capability_fetcher
        self._capability_refresh_allowed_hosts = capability_refresh_allowed_hosts
        self._capability_refresh_allowed_networks = capability_refresh_allowed_networks
        self._federation_client_factory = federation_client_factory
        self._federation_timeout_seconds = federation_timeout_seconds
        self._event_store_prefix = event_store_prefix
        self._event_store_ttl_seconds = event_store_ttl_seconds
        self._event_history_maxlen = event_history_maxlen
        self._push_ingest_enabled = push_ingest_enabled
        self._pull_sync_interval_seconds = pull_sync_interval_seconds
        self._health_store_prefix = health_store_prefix
        self._health_refresh_interval_seconds = health_refresh_interval_seconds
        self._capability_stale_after_seconds = capability_stale_after_seconds
        self._observation_stale_after_seconds = observation_stale_after_seconds
        self._worker_heartbeat_stale_after_seconds = worker_heartbeat_stale_after_seconds
        self._task_search_index_prefix = task_search_index_prefix
        self._task_index_ttl_seconds = task_index_ttl_seconds
        self._retention_prune_interval_seconds = retention_prune_interval_seconds
        self._failed_task_email_enabled = failed_task_email_enabled
        self._failed_task_email_service_url = failed_task_email_service_url
        self._failed_task_email_api_key = failed_task_email_api_key
        self._failed_task_email_receivers = failed_task_email_receivers
        self._failed_task_email_interval_seconds = failed_task_email_interval_seconds
        self._failed_task_email_timeout_seconds = failed_task_email_timeout_seconds
        self._failed_task_email_dedupe_ttl_seconds = failed_task_email_dedupe_ttl_seconds
        self._failed_task_email_title_prefix = failed_task_email_title_prefix
        self._failed_task_email_batch_wait_seconds = failed_task_email_batch_wait_seconds
        self._runtime: StudioRuntime | None = None

    @property
    def app_state_key(self) -> str:
        return self._app_state_key

    def ensure_runtime(self) -> StudioRuntime:
        if self._runtime is None:
            redis = Redis.from_url(self._redis_url)
            metrics = RelaynaMetrics(service="relayna-studio")
            registry_store = RedisServiceRegistryStore(redis, prefix=self._registry_prefix)
            search_store = RedisStudioSearchStore(redis, prefix=self._task_search_index_prefix)
            outbound_policy = StudioOutboundUrlPolicy(
                allowed_hosts=self._capability_refresh_allowed_hosts,
                allowed_networks=self._capability_refresh_allowed_networks,
            )
            registry_service = ServiceRegistryService(
                store=registry_store,
                capability_fetcher=self._capability_fetcher,
                outbound_policy=outbound_policy,
            )
            http_client = self._federation_client_factory(self._federation_timeout_seconds)
            event_store = RedisStudioEventStore(
                redis,
                prefix=self._event_store_prefix,
                ttl_seconds=self._event_store_ttl_seconds,
                history_maxlen=self._event_history_maxlen,
            )
            health_store = RedisStudioHealthStore(redis, prefix=self._health_store_prefix)
            search_service = StudioSearchService(
                registry_service=registry_service,
                event_store=event_store,
                store=search_store,
                task_index_ttl_seconds=self._task_index_ttl_seconds,
                backfill_event_limit=max(1, self._event_history_maxlen),
            )
            registry_service.set_search_indexer(search_service)
            federation_service = StudioFederationService(
                registry_service=registry_service,
                http_client=http_client,
                outbound_policy=outbound_policy,
            )
            health_service = StudioHealthRefreshService(
                registry_service=registry_service,
                health_store=health_store,
                activity_reader=event_store,
                http_client=http_client,
                search_indexer=search_service,
                outbound_policy=outbound_policy,
                capability_stale_after_seconds=self._capability_stale_after_seconds,
                observation_stale_after_seconds=self._observation_stale_after_seconds,
                worker_heartbeat_stale_after_seconds=self._worker_heartbeat_stale_after_seconds,
            )
            log_query_service = StudioLogQueryService(
                registry_service=registry_service,
                providers={"loki": LokiLogProvider(http_client=http_client, outbound_policy=outbound_policy)},
            )
            metrics_query_service = StudioMetricsQueryService(
                registry_service=registry_service,
                providers={
                    "prometheus": PrometheusMetricsProvider(http_client=http_client, outbound_policy=outbound_policy)
                },
                federation_service=federation_service,
            )
            event_ingest_service = StudioEventIngestService(
                registry_service=registry_service,
                event_store=event_store,
                http_client=http_client,
                search_indexer=search_service,
                outbound_policy=outbound_policy,
            )
            trace_query_service = StudioTraceQueryService(
                registry_service=registry_service,
                providers={"tempo": TempoTraceProvider(http_client=http_client, outbound_policy=outbound_policy)},
                federation_service=federation_service,
                log_query_service=log_query_service,
                event_ingest_service=event_ingest_service,
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
            retention_worker = (
                StudioRetentionWorker(
                    search_service=search_service,
                    interval_seconds=self._retention_prune_interval_seconds,
                )
                if self._retention_prune_interval_seconds is not None
                else None
            )
            failed_task_email_settings_store = RedisFailedTaskEmailSettingsStore(
                redis,
                default_enabled=self._failed_task_email_enabled,
                default_batch_wait_seconds=self._failed_task_email_batch_wait_seconds,
            )
            failed_task_email_configured = self._failed_task_email_configured()
            failed_task_email_worker = self._build_failed_task_email_worker(
                redis=redis,
                http_client=http_client,
                federation_service=federation_service,
                outbound_policy=outbound_policy,
                settings_store=failed_task_email_settings_store,
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
                metrics_query_service=metrics_query_service,
                trace_query_service=trace_query_service,
                metrics=metrics,
                outbound_policy=outbound_policy,
                search_store=search_store,
                search_service=search_service,
                pull_sync_worker=pull_sync_worker,
                health_refresh_worker=health_refresh_worker,
                retention_worker=retention_worker,
                failed_task_email_settings_store=failed_task_email_settings_store,
                failed_task_email_configured=failed_task_email_configured,
                failed_task_email_receivers=self._failed_task_email_receivers,
                failed_task_email_worker=failed_task_email_worker,
            )
        return self._runtime

    def _failed_task_email_configured(self) -> bool:
        return bool(
            self._failed_task_email_service_url
            and self._failed_task_email_api_key
            and self._failed_task_email_receivers
        )

    def _build_failed_task_email_worker(
        self,
        *,
        redis: Redis,
        http_client: httpx.AsyncClient,
        federation_service: StudioFederationService,
        outbound_policy: StudioOutboundUrlPolicy,
        settings_store: RedisFailedTaskEmailSettingsStore,
    ) -> FailedTaskEmailNotificationWorker | None:
        configured = self._failed_task_email_configured()
        if self._failed_task_email_enabled and not self._failed_task_email_service_url:
            raise RuntimeError("Failed-task email service URL must be set when notification is enabled.")
        if self._failed_task_email_enabled and not self._failed_task_email_receivers:
            raise RuntimeError("Failed-task email receivers must be set when notification is enabled.")
        if self._failed_task_email_enabled and not self._failed_task_email_api_key:
            raise RuntimeError("Failed-task email API key must be set when notification is enabled.")
        if not configured:
            return None
        assert self._failed_task_email_service_url is not None
        assert self._failed_task_email_api_key is not None
        config = FailedTaskEmailNotificationConfig(
            service_url=self._failed_task_email_service_url,
            api_key=self._failed_task_email_api_key,
            receivers=self._failed_task_email_receivers,
            interval_seconds=self._failed_task_email_interval_seconds,
            timeout_seconds=self._failed_task_email_timeout_seconds,
            dedupe_ttl_seconds=self._failed_task_email_dedupe_ttl_seconds,
            title_prefix=self._failed_task_email_title_prefix,
            default_enabled=self._failed_task_email_enabled,
            default_batch_wait_seconds=self._failed_task_email_batch_wait_seconds,
        )
        email_client = FailedTaskEmailClient(
            http_client=http_client,
            service_url=config.service_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            outbound_policy=outbound_policy,
        )
        notification_service = FailedTaskEmailNotificationService(
            federation_service=federation_service,
            redis=redis,
            email_client=email_client,
            config=config,
            settings_store=settings_store,
        )
        return FailedTaskEmailNotificationWorker(
            notification_service=notification_service,
            interval_seconds=config.interval_seconds,
        )

    def __call__(self, app: FastAPI):
        @asynccontextmanager
        async def lifespan() -> AsyncIterator[None]:
            runtime = self.ensure_runtime()
            setattr(app.state, self._app_state_key, runtime)
            try:
                await runtime.search_service.initialize()
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
                if runtime.retention_worker is not None:
                    runtime.retention_task = asyncio.create_task(
                        runtime.retention_worker.run_forever(),
                        name="studio-retention-prune",
                    )
                if runtime.failed_task_email_worker is not None:
                    runtime.failed_task_email_task = asyncio.create_task(
                        runtime.failed_task_email_worker.run_forever(),
                        name="studio-failed-task-email-notifications",
                    )
                yield
            finally:
                if runtime.pull_sync_worker is not None:
                    runtime.pull_sync_worker.stop()
                if runtime.health_refresh_worker is not None:
                    runtime.health_refresh_worker.stop()
                if runtime.retention_worker is not None:
                    runtime.retention_worker.stop()
                if runtime.failed_task_email_worker is not None:
                    runtime.failed_task_email_worker.stop()
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
                if runtime.retention_task is not None:
                    try:
                        await asyncio.wait_for(asyncio.shield(runtime.retention_task), timeout=5.0)
                    except TimeoutError:
                        runtime.retention_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await runtime.retention_task
                    finally:
                        runtime.retention_task = None
                if runtime.failed_task_email_task is not None:
                    try:
                        await asyncio.wait_for(asyncio.shield(runtime.failed_task_email_task), timeout=5.0)
                    except TimeoutError:
                        runtime.failed_task_email_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await runtime.failed_task_email_task
                    finally:
                        runtime.failed_task_email_task = None
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
    push_ingest_enabled: bool = False,
    pull_sync_interval_seconds: float | None = 5.0,
    health_store_prefix: str = "studio:health",
    health_refresh_interval_seconds: float | None = 60.0,
    capability_stale_after_seconds: int = 180,
    observation_stale_after_seconds: int = 300,
    worker_heartbeat_stale_after_seconds: int = 90,
    task_search_index_prefix: str = "studio:search",
    task_index_ttl_seconds: int = 86400,
    retention_prune_interval_seconds: float | None = 60.0,
    failed_task_email_enabled: bool = False,
    failed_task_email_service_url: str | None = None,
    failed_task_email_api_key: str | None = None,
    failed_task_email_receivers: tuple[str, ...] = (),
    failed_task_email_interval_seconds: float = 30.0,
    failed_task_email_timeout_seconds: float = 5.0,
    failed_task_email_dedupe_ttl_seconds: int = 604800,
    failed_task_email_title_prefix: str = "[Relayna] Failed task",
    failed_task_email_batch_wait_seconds: int = 0,
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
        capability_refresh_allowed_hosts=capability_refresh_allowed_hosts,
        capability_refresh_allowed_networks=capability_refresh_allowed_networks,
        federation_client_factory=federation_client_factory or _default_async_client_factory,
        federation_timeout_seconds=federation_timeout_seconds,
        event_store_prefix=event_store_prefix,
        event_store_ttl_seconds=event_store_ttl_seconds,
        event_history_maxlen=event_history_maxlen,
        push_ingest_enabled=push_ingest_enabled,
        pull_sync_interval_seconds=pull_sync_interval_seconds,
        health_store_prefix=health_store_prefix,
        health_refresh_interval_seconds=health_refresh_interval_seconds,
        capability_stale_after_seconds=capability_stale_after_seconds,
        observation_stale_after_seconds=observation_stale_after_seconds,
        worker_heartbeat_stale_after_seconds=worker_heartbeat_stale_after_seconds,
        task_search_index_prefix=task_search_index_prefix,
        task_index_ttl_seconds=task_index_ttl_seconds,
        retention_prune_interval_seconds=retention_prune_interval_seconds,
        failed_task_email_enabled=failed_task_email_enabled,
        failed_task_email_service_url=failed_task_email_service_url,
        failed_task_email_api_key=failed_task_email_api_key,
        failed_task_email_receivers=failed_task_email_receivers,
        failed_task_email_interval_seconds=failed_task_email_interval_seconds,
        failed_task_email_timeout_seconds=failed_task_email_timeout_seconds,
        failed_task_email_dedupe_ttl_seconds=failed_task_email_dedupe_ttl_seconds,
        failed_task_email_title_prefix=failed_task_email_title_prefix,
        failed_task_email_batch_wait_seconds=failed_task_email_batch_wait_seconds,
    )
    runtime = lifespan_factory.ensure_runtime()
    app = FastAPI(title=title, lifespan=lifespan_factory)
    app.include_router(create_studio_search_router(search_service=runtime.search_service))
    app.include_router(
        create_service_registry_router(service_registry=runtime.registry_service, health_service=runtime.health_service)
    )
    app.include_router(create_studio_health_router(health_service=runtime.health_service))
    app.include_router(create_federation_router(federation_service=runtime.federation_service))
    app.include_router(
        create_studio_events_router(
            ingest_service=runtime.event_ingest_service,
            event_stream=runtime.event_stream,
            push_ingest_enabled=push_ingest_enabled,
        )
    )
    app.include_router(create_studio_logs_router(log_query_service=runtime.log_query_service))
    app.include_router(create_studio_metrics_router(metrics_query_service=runtime.metrics_query_service))
    app.include_router(create_studio_traces_router(trace_query_service=runtime.trace_query_service))
    if runtime.failed_task_email_settings_store is not None:
        app.include_router(
            create_failed_task_email_settings_router(
                configured=runtime.failed_task_email_configured,
                receivers=runtime.failed_task_email_receivers,
                settings_store=runtime.failed_task_email_settings_store,
            )
        )
    app.include_router(create_metrics_router(runtime.metrics))
    return app


def get_studio_runtime(app: FastAPI, *, app_state_key: str = "studio") -> StudioRuntime:
    runtime = getattr(app.state, app_state_key, None)
    if isinstance(runtime, StudioRuntime):
        return runtime
    raise RuntimeError(f"Studio runtime is not available on app.state.{app_state_key}.")


__all__ = ["StudioRuntime", "create_studio_app", "get_studio_runtime"]
