from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from .federation import (
    StudioFederationService,
    _default_async_client_factory,
    create_federation_router,
)
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
    ) -> None:
        self._redis_url = redis_url
        self._app_state_key = app_state_key
        self._registry_prefix = registry_prefix
        self._capability_fetcher = capability_fetcher
        self._federation_client_factory = federation_client_factory
        self._federation_timeout_seconds = federation_timeout_seconds
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
            federation_service = StudioFederationService(
                registry_service=registry_service,
                http_client=http_client,
            )
            self._runtime = StudioRuntime(
                redis=redis,
                registry_store=registry_store,
                registry_service=registry_service,
                http_client=http_client,
                federation_service=federation_service,
            )
        return self._runtime

    def __call__(self, app: FastAPI):
        @asynccontextmanager
        async def lifespan() -> AsyncIterator[None]:
            runtime = self.ensure_runtime()
            setattr(app.state, self._app_state_key, runtime)
            try:
                yield
            finally:
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
    )
    runtime = lifespan_factory.ensure_runtime()
    app = FastAPI(title=title, lifespan=lifespan_factory)
    app.include_router(create_service_registry_router(service_registry=runtime.registry_service))
    app.include_router(create_federation_router(federation_service=runtime.federation_service))
    return app


def get_studio_runtime(app: FastAPI, *, app_state_key: str = "studio") -> StudioRuntime:
    runtime = getattr(app.state, app_state_key, None)
    if isinstance(runtime, StudioRuntime):
        return runtime
    raise RuntimeError(f"Studio runtime is not available on app.state.{app_state_key}.")


__all__ = ["StudioRuntime", "create_studio_app", "get_studio_runtime"]
