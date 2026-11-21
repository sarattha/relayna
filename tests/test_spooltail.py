from __future__ import annotations

import asyncio
import os
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from relayna.consumer import RetryPolicy
from relayna.contracts import StatusEventEnvelope, TaskEnvelope
from relayna.spooltail import SpooltailClient, SpooltailTaskConsumer, SpooltailTaskContext
from relayna.spooltail import client as client_module


@pytest.mark.asyncio
async def test_missing_sdk_is_only_reported_at_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> None:
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr(client_module, "import_module", missing)
    client = SpooltailClient("localhost:7443")
    app = FastAPI(lifespan=client.lifespan)
    app.include_router(client.status_router())
    with pytest.raises(RuntimeError, match="Install the optional relayna-spooltail"):
        await client.initialize()


@pytest.mark.asyncio
async def test_missing_transitive_dependency_reports_the_actual_package(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> None:
        raise ModuleNotFoundError(name="grpc")

    monkeypatch.setattr(client_module, "import_module", missing)
    with pytest.raises(ModuleNotFoundError) as error:
        await SpooltailClient("localhost:7443").initialize()
    assert error.value.name == "grpc"


@pytest.mark.asyncio
async def test_failed_startup_closes_native_client_and_can_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    down = AsyncMock()
    down.ping.side_effect = ConnectionError
    up = AsyncMock()
    instances = iter((down, up))

    def make_client(*args, **kwargs):
        assert args == ("localhost:7443",)
        assert kwargs == {"tls": True, "root_certificates": b"pem", "timeout": 2}
        return next(instances)

    monkeypatch.setattr(client_module, "import_module", lambda _: SimpleNamespace(SpooltailClient=make_client))
    client = SpooltailClient("localhost:7443", root_certificates=b"pem", timeout=2)
    with pytest.raises(ConnectionError):
        await client.initialize()
    down.close.assert_awaited_once()
    with pytest.raises(RuntimeError, match="Initialize"):
        await client.get_latest("t")

    async with client:
        await client.initialize()
        up.ping.assert_awaited_once()
    await client.close()
    up.close.assert_awaited_once()


class _CursorGap(Exception):
    earliest_event_id = "earliest"


class _SubscriptionFailed(Exception):
    pass


class _StaleLease(Exception):
    pass


class _RpcError(Exception):
    def code(self) -> str:
        return self.args[0]


@pytest.fixture
def mock_connection(monkeypatch: pytest.MonkeyPatch):
    native = AsyncMock()
    actions = SimpleNamespace(ACK="ack", RENEW="renew", RETRY="retry", DEAD_LETTER="dead")
    sdk = SimpleNamespace(
        SpooltailClient=lambda *args, **kwargs: native,
        SettlementAction=actions,
        CursorGapError=_CursorGap,
        SubscriptionError=_SubscriptionFailed,
        StaleLeaseError=_StaleLease,
    )
    grpc = SimpleNamespace(
        aio=SimpleNamespace(AioRpcError=_RpcError),
        StatusCode=SimpleNamespace(UNAVAILABLE="unavailable", DEADLINE_EXCEEDED="deadline"),
    )
    monkeypatch.setattr(client_module, "import_module", {"relayna_spooltail": sdk, "grpc": grpc}.__getitem__)
    return native


def test_invalid_backend_configuration() -> None:
    with pytest.raises(ValueError, match="distinct task and status topics"):
        SpooltailClient("localhost:7443", task_topic="same", status_topic="same")


@pytest.mark.parametrize(
    "options",
    [
        {"group": ""},
        {"lease_ms": 0},
        {"idle_retry_seconds": 0},
        {"retry_policy": RetryPolicy(max_retries=-1)},
        {"retry_policy": RetryPolicy(delay_ms=3_600_001)},
        {"retry_policy": RetryPolicy(retry_priority_step=1)},
    ],
)
def test_invalid_worker_configuration(options) -> None:
    with pytest.raises(ValueError):
        SpooltailTaskConsumer(client=SpooltailClient("localhost:7443"), handler=AsyncMock(), **options)


@pytest.mark.asyncio
async def test_client_publishes_canonical_envelopes_and_reads_status(mock_connection: AsyncMock) -> None:
    async with SpooltailClient("localhost:7443") as client:
        await client.publish_task({"task_id": "t", "payload": {"n": 2}})
        task_topic, task = mock_connection.publish.await_args.args
        assert task_topic == "relayna.tasks"
        assert task["event_id"] == "task"
        assert task["payload"] == {"n": 2}
        await client.publish_status({"task_id": "t", "status": "processing"})
        status_topic, status = mock_connection.publish.await_args.args
        assert status_topic == "relayna.status"
        assert status["correlation_id"] == "t"
        mock_connection.latest.side_effect = [None, SimpleNamespace(event=status)]
        mock_connection.history.return_value = [SimpleNamespace(event=status)]
        assert await client.get_latest("t") is None
        assert await client.get_latest("t") == status
        assert await client.get_history("t", after="previous") == [status]
        mock_connection.history.assert_awaited_once_with("relayna.status", "t", after="previous")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [(_CursorGap(), b"event: gap"), (_SubscriptionFailed("broker error"), b"event: error")],
)
async def test_subscription_maps_broker_errors_to_sse(mock_connection: AsyncMock, error, expected: bytes) -> None:
    async def subscribe(topic, task_id, *, after):
        assert (topic, task_id, after) == ("relayna.status", "t", "previous")
        yield SimpleNamespace(kind="ready", data={})
        raise error

    mock_connection.subscribe = Mock(side_effect=subscribe)
    client = SpooltailClient("localhost:7443")
    app = FastAPI(lifespan=client.lifespan)
    app.include_router(client.status_router())
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        response = await http.get("/events/t", headers={"Last-Event-ID": "previous"})
        assert b"event: ready" in response.content
        assert expected in response.content


@pytest.mark.asyncio
async def test_completion_replays_exact_request_after_ambiguous_rpc(mock_connection: AsyncMock) -> None:
    async def handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        await context.publish_status(StatusEventEnvelope(task_id="wrong", status="completed", result={"ok": True}))
        task.task_id = "changed"

    async with SpooltailClient("localhost:7443") as client:
        consumer = SpooltailTaskConsumer(client=client, handler=handler)
        mock_connection.claim.return_value = SimpleNamespace(
            generation=1, record=SimpleNamespace(event={"task_id": "t", "correlation_id": "request"})
        )
        mock_connection.complete.side_effect = [_RpcError("deadline"), 42]
        assert await consumer.run_once()
        first, second = mock_connection.complete.await_args_list
        assert first == second
        assert first.args[2]["task_id"] == "t"
        assert first.args[2]["correlation_id"] == "request"
        assert first.args[2]["result"] == {"ok": True}
        mock_connection.settle.assert_not_awaited()

        mock_connection.complete.side_effect = _RpcError("invalid argument")
        with pytest.raises(_RpcError, match="invalid argument"):
            await consumer.run_once()


@pytest.mark.asyncio
async def test_failed_handler_discards_pending_terminal_and_delays_retry(mock_connection: AsyncMock) -> None:
    async def handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        await context.publish_status(status="completed", result={"partial": True})
        raise ValueError("private handler details")

    async with SpooltailClient("localhost:7443") as client:
        worker = SpooltailTaskConsumer(
            client=client, handler=handler, retry_policy=RetryPolicy(max_retries=1, delay_ms=9)
        )
        record = SimpleNamespace(event={"task_id": "t"})
        first = SimpleNamespace(generation=1, record=record)
        second = SimpleNamespace(generation=3, record=record)
        mock_connection.claim.side_effect = [first, second, None]
        assert await worker.run_once()
        mock_connection.settle.assert_awaited_once_with(first, "retry", delay_ms=9)
        mock_connection.publish.assert_not_awaited()
        mock_connection.complete.assert_not_awaited()
        assert await worker.run_once()
        payload = mock_connection.complete.await_args.args[2]
        assert payload["status"] == "failed"
        assert "result" not in payload
        assert payload["message"] == "Task handler failed"
        assert not await worker.run_once()


@pytest.mark.asyncio
async def test_validation_and_delivery_budget(mock_connection: AsyncMock) -> None:
    handler = AsyncMock()
    async with SpooltailClient("localhost:7443") as client:
        with pytest.raises(ValueError, match="does not support task priority"):
            await client.publish_task(TaskEnvelope(task_id="t", priority=1))
        worker = SpooltailTaskConsumer(client=client, handler=handler)
        malformed = SimpleNamespace(generation=1, record=SimpleNamespace(event={"task_id": "t", "payload": 4}))
        prioritized = SimpleNamespace(generation=1, record=SimpleNamespace(event={"task_id": "t", "priority": 2}))
        exhausted = SimpleNamespace(generation=3, record=SimpleNamespace(event={"task_id": "t"}))
        mock_connection.claim.side_effect = [malformed, prioritized, exhausted]
        assert await worker.run_once()
        mock_connection.settle.assert_awaited_once_with(malformed, "dead")
        assert await worker.run_once()
        mock_connection.settle.assert_awaited_with(prioritized, "dead")
        assert await worker.run_once()
        assert mock_connection.complete.await_args.args[2]["status"] == "failed"
        handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_loop_continues_after_transient_failure_and_stops_after_task(mock_connection: AsyncMock) -> None:
    async def handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        with pytest.raises(ValueError, match="Pass either"):
            await context.publish_status()
        await context.publish_status(status="processing")
        assert context.max_retries == 0
        worker.stop()

    async with SpooltailClient("localhost:7443") as client:
        worker = SpooltailTaskConsumer(client=client, handler=handler, idle_retry_seconds=0.001)
        lease = SimpleNamespace(generation=2, record=SimpleNamespace(event={"task_id": "t"}))
        mock_connection.claim.side_effect = [_RpcError("unavailable"), _StaleLease(), None, lease]
        await asyncio.wait_for(worker.run_forever(), timeout=1)
        assert mock_connection.claim.await_count == 4
        assert mock_connection.complete.await_args.args[2]["status"] == "completed"

        worker = SpooltailTaskConsumer(client=client, handler=handler)
        mock_connection.claim.side_effect = _RpcError("invalid argument")
        with pytest.raises(_RpcError, match="invalid argument"):
            await worker.run_forever()


@pytest.mark.asyncio
async def test_failed_renewal_cancels_handler_without_settling_stale_lease(mock_connection: AsyncMock) -> None:
    cancelled = asyncio.Event()

    async def handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        await context.publish_status(status="completed")
        with pytest.raises(ValueError, match="pending terminal"):
            await context.publish_status(status="failed")
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async with SpooltailClient("localhost:7443") as client:
        worker = SpooltailTaskConsumer(client=client, handler=handler, lease_ms=30)
        lease = SimpleNamespace(generation=1, record=SimpleNamespace(event={"task_id": "t"}))
        mock_connection.claim.return_value = lease
        mock_connection.settle.side_effect = _StaleLease()
        with pytest.raises(_StaleLease):
            await asyncio.wait_for(worker.run_once(), timeout=1)
        assert cancelled.is_set()
        mock_connection.settle.assert_awaited_once_with(lease, "renew", delay_ms=30)
        mock_connection.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_status_publish_failure_preserves_lease_recovery(mock_connection: AsyncMock) -> None:
    async def handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        await context.publish_status(status="processing")

    async with SpooltailClient("localhost:7443") as client:
        worker = SpooltailTaskConsumer(client=client, handler=handler)
        mock_connection.claim.return_value = SimpleNamespace(
            generation=1, record=SimpleNamespace(event={"task_id": "t"})
        )
        mock_connection.publish.side_effect = _RpcError("unavailable")
        with pytest.raises(_RpcError, match="unavailable"):
            await worker.run_once()
        mock_connection.complete.assert_not_awaited()
        mock_connection.settle.assert_not_awaited()


spooltail_target = os.environ.get("SPOOLTAIL_TEST_TARGET")
real_broker = pytest.mark.skipif(not spooltail_target, reason="set SPOOLTAIL_TEST_TARGET to a local Spooltail broker")


@real_broker
@pytest.mark.asyncio
async def test_native_broker_task_retry_result_history_and_status_routes() -> None:
    assert spooltail_target is not None
    scope = uuid4().hex
    settings = {"task_topic": f"{scope}.tasks", "status_topic": f"{scope}.status", "tls": False}
    attempts: list[int] = []

    async def handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        attempts.append(context.retry_attempt)
        assert context.correlation_id == "request"
        await context.publish_status(status="processing", meta={"attempt": context.retry_attempt})
        if context.retry_attempt == 0:
            raise RuntimeError("temporary failure")
        assert context.redelivered
        await context.publish_status(status="completed", result={"answer": task.payload["number"] * 2})

    api_client = SpooltailClient(spooltail_target, **settings)
    app = FastAPI(lifespan=api_client.lifespan)
    app.include_router(api_client.status_router())
    async with (
        asyncio.timeout(8),
        app.router.lifespan_context(app),
        SpooltailClient(spooltail_target, **settings) as worker_client,
    ):
        consumer = SpooltailTaskConsumer(
            client=worker_client, handler=handler, retry_policy=RetryPolicy(max_retries=1, delay_ms=300)
        )
        task = TaskEnvelope(task_id="task-1", correlation_id="request", payload={"number": 21})
        assert await api_client.get_latest(task.task_id) is None
        await api_client.publish_task(task)
        await api_client.publish_task(task)
        assert await consumer.run_once()
        assert not await consumer.run_once()
        await asyncio.sleep(0.35)
        assert await consumer.run_once()
        assert not await consumer.run_once()
        assert attempts == [0, 1]

        history = await api_client.get_history(task.task_id)
        assert [event["status"] for event in history] == ["processing", "processing", "completed"]
        cursor = history[0]["event_id"]
        assert await api_client.get_history(task.task_id, after=cursor) == history[1:]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get(f"/status/{task.task_id}")
            assert response.status_code == 200
            assert response.json() == {"task_id": task.task_id, "event": history[-1]}
            assert response.json()["event"]["result"] == {"answer": 42}
            response = await http.get(f"/events/{task.task_id}", headers={"Last-Event-ID": cursor}, timeout=3)
            assert response.text.count("event: status") == 2
            assert f"id: {cursor}" not in response.text
            assert f"id: {history[-1]['event_id']}" in response.text
            response = await http.get(f"/events/{task.task_id}", headers={"Last-Event-ID": "absent"}, timeout=3)
            assert "event: gap" in response.text
            assert cursor in response.text

        failing_worker = SpooltailTaskConsumer(
            client=worker_client, handler=AsyncMock(side_effect=ValueError("handler failed"))
        )
        await api_client.publish_task({"task_id": "failed-task"})
        assert await failing_worker.run_once()
        assert not await failing_worker.run_once()
        failure = await api_client.get_latest("failed-task")
        assert failure is not None and failure["status"] == "failed"


@real_broker
@pytest.mark.asyncio
async def test_native_broker_renews_lease_and_streams_live_status() -> None:
    assert spooltail_target is not None
    scope = uuid4().hex
    settings = {"task_topic": f"{scope}.tasks", "status_topic": f"{scope}.status", "tls": False}
    release = asyncio.Event()
    processing = asyncio.Event()

    async def handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        await context.publish_status(status="processing")
        processing.set()
        await release.wait()

    async with SpooltailClient(spooltail_target, **settings) as client:
        competitor = AsyncMock()
        worker = SpooltailTaskConsumer(client=client, handler=handler, lease_ms=400)
        other_worker = SpooltailTaskConsumer(client=client, handler=competitor)
        async with asyncio.timeout(5), aclosing(client.stream("long-task")) as stream:
            assert b"event: ready" in await anext(stream)
            await client.publish_task({"task_id": "long-task"})
            delivery = asyncio.create_task(worker.run_once())
            try:
                await processing.wait()
                assert b"processing" in await anext(stream)
                await asyncio.sleep(0.55)
                assert not await other_worker.run_once()
                competitor.assert_not_awaited()
                release.set()
                assert await delivery
                assert b"completed" in await anext(stream)
                with pytest.raises(StopAsyncIteration):
                    await anext(stream)
            finally:
                release.set()
                await delivery
