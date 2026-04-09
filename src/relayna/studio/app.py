from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from redis.asyncio import Redis

from .registry import (
    CapabilityFetcher,
    RedisServiceRegistryStore,
    ServiceRegistryService,
    create_service_registry_router,
)


@dataclass(slots=True)
class StudioRuntime:
    redis: Redis
    registry_store: RedisServiceRegistryStore
    registry_service: ServiceRegistryService


class _StudioLifespan:
    def __init__(
        self,
        *,
        redis_url: str,
        app_state_key: str,
        registry_prefix: str,
        capability_fetcher: CapabilityFetcher | None,
    ) -> None:
        self._redis_url = redis_url
        self._app_state_key = app_state_key
        self._registry_prefix = registry_prefix
        self._capability_fetcher = capability_fetcher
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
            self._runtime = StudioRuntime(
                redis=redis,
                registry_store=registry_store,
                registry_service=registry_service,
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
) -> FastAPI:
    lifespan_factory = _StudioLifespan(
        redis_url=redis_url,
        app_state_key=app_state_key,
        registry_prefix=registry_prefix,
        capability_fetcher=capability_fetcher,
    )
    runtime = lifespan_factory.ensure_runtime()
    app = FastAPI(title=title, lifespan=lifespan_factory)
    app.include_router(create_service_registry_router(service_registry=runtime.registry_service))
    return app


def get_studio_runtime(app: FastAPI, *, app_state_key: str = "studio") -> StudioRuntime:
    runtime = getattr(app.state, app_state_key, None)
    if isinstance(runtime, StudioRuntime):
        return runtime
    raise RuntimeError(f"Studio runtime is not available on app.state.{app_state_key}.")


__all__ = ["StudioRuntime", "create_studio_app", "get_studio_runtime"]
