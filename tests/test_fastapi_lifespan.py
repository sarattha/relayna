from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI

from relayna.config import RelaynaTopologyConfig
from relayna.fastapi import create_status_router
import relayna.fastapi as relayna_fastapi


class FakeRabbitClient:
    instances: list["FakeRabbitClient"] = []
    fail_initialize = False

    def __init__(self, config: RelaynaTopologyConfig, *, connection_name: str) -> None:
        self.config = config
        self.connection_name = connection_name
        self.initialize_calls = 0
        self.close_calls = 0
        FakeRabbitClient.instances.append(self)

    async def initialize(self) -> None:
        self.initialize_calls += 1
        if self.fail_initialize:
            raise RuntimeError("rabbit init failed")

    async def close(self) -> None:
        self.close_calls += 1


class FakeRedis:
    instances: list["FakeRedis"] = []

    def __init__(self, url: str) -> None:
        self.url = url
        self.close_calls = 0
        FakeRedis.instances.append(self)

    @classmethod
    def from_url(cls, url: str) -> "FakeRedis":
        return cls(url)

    async def aclose(self) -> None:
        self.close_calls += 1


class FakeStore:
    fail_init = False
    instances: list["FakeStore"] = []

    def __init__(
        self,
        redis: FakeRedis,
        *,
        prefix: str,
        ttl_seconds: int | None,
        history_maxlen: int,
    ) -> None:
        if self.fail_init:
            raise RuntimeError("store init failed")
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.history_maxlen = history_maxlen
        FakeStore.instances.append(self)


class FakeHub:
    instances: list["FakeHub"] = []

    def __init__(
        self,
        *,
        rabbitmq: FakeRabbitClient,
        store: FakeStore,
        consume_arguments: dict[str, object] | None = None,
        sanitize_meta_keys: set[str] | None = None,
        prefetch: int = 200,
    ) -> None:
        self.rabbitmq = rabbitmq
        self.store = store
        self.consume_arguments = consume_arguments
        self.sanitize_meta_keys = sanitize_meta_keys
        self.prefetch = prefetch
        self.stop_calls = 0
        self.run_calls = 0
        self._stopped = asyncio.Event()
        FakeHub.instances.append(self)

    def stop(self) -> None:
        self.stop_calls += 1
        self._stopped.set()

    async def run_forever(self) -> None:
        self.run_calls += 1
        await self._stopped.wait()


@pytest.fixture(autouse=True)
def patch_fastapi_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeRabbitClient.instances.clear()
    FakeRabbitClient.fail_initialize = False
    FakeRedis.instances.clear()
    FakeStore.instances.clear()
    FakeStore.fail_init = False
    FakeHub.instances.clear()
    monkeypatch.setattr(relayna_fastapi, "RelaynaRabbitClient", FakeRabbitClient)
    monkeypatch.setattr(relayna_fastapi, "Redis", FakeRedis)
    monkeypatch.setattr(relayna_fastapi, "RedisStatusStore", FakeStore)
    monkeypatch.setattr(relayna_fastapi, "StatusHub", FakeHub)


@pytest.fixture
def topology_config() -> RelaynaTopologyConfig:
    return RelaynaTopologyConfig(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )


@pytest.mark.asyncio
async def test_lifespan_startup_and_shutdown_manage_runtime(topology_config: RelaynaTopologyConfig) -> None:
    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology_config=topology_config,
            redis_url="redis://localhost:6379/0",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)
    app.include_router(create_status_router(sse_stream=runtime.sse_stream, history_reader=runtime.history_reader))

    assert runtime.hub_task is None

    async with app.router.lifespan_context(app):
        stored_runtime = relayna_fastapi.get_relayna_runtime(app)
        assert stored_runtime is runtime
        assert runtime.rabbitmq.initialize_calls == 1
        assert runtime.hub_task is not None
        await asyncio.sleep(0)
        assert runtime.hub.run_calls == 1

    assert runtime.hub.stop_calls == 1
    assert runtime.rabbitmq.close_calls == 1
    assert runtime.redis.close_calls == 1
    assert not hasattr(app.state, "relayna")
    paths = {route.path for route in app.routes}
    assert "/events/{task_id}" in paths
    assert "/history" in paths


@pytest.mark.asyncio
async def test_get_relayna_runtime_raises_when_missing() -> None:
    app = FastAPI()

    with pytest.raises(RuntimeError, match="Relayna runtime is not available"):
        relayna_fastapi.get_relayna_runtime(app)


@pytest.mark.asyncio
async def test_startup_failure_after_rabbit_init_cleans_up_resources(
    topology_config: RelaynaTopologyConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_create_task(coro: object, *, name: str | None = None) -> object:
        del name
        coro.close()
        raise RuntimeError("task creation failed")

    monkeypatch.setattr(relayna_fastapi.asyncio, "create_task", fail_create_task)

    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology_config=topology_config,
            redis_url="redis://localhost:6379/0",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)

    with pytest.raises(RuntimeError, match="task creation failed"):
        async with app.router.lifespan_context(app):
            raise AssertionError("unreachable")

    assert runtime.rabbitmq.initialize_calls == 1
    assert runtime.rabbitmq.close_calls == 1
    assert runtime.redis.close_calls == 1
    assert not hasattr(app.state, "relayna")


@pytest.mark.asyncio
async def test_startup_failure_after_redis_creation_closes_redis(topology_config: RelaynaTopologyConfig) -> None:
    FakeRabbitClient.fail_initialize = True

    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology_config=topology_config,
            redis_url="redis://localhost:6379/0",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)

    with pytest.raises(RuntimeError, match="rabbit init failed"):
        async with app.router.lifespan_context(app):
            raise AssertionError("unreachable")

    assert runtime.rabbitmq.close_calls == 1
    assert runtime.redis.close_calls == 1
    assert not hasattr(app.state, "relayna")


@pytest.mark.asyncio
async def test_runtime_is_available_under_custom_state_key(topology_config: RelaynaTopologyConfig) -> None:
    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology_config=topology_config,
            redis_url="redis://localhost:6379/0",
            app_state_key="custom_runtime",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app, app_state_key="custom_runtime")

    assert runtime.store.prefix == "relayna"
    assert getattr(app.state, "custom_runtime") is runtime
