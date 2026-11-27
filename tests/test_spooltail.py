from __future__ import annotations

import asyncio
import os
import sys
from contextlib import aclosing, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from relayna.consumer import RetryPolicy
from relayna.contracts import StatusEventEnvelope, TaskEnvelope
from relayna.spooltail import (
    SpooltailLocalProfile,
    SpooltailTaskConsumer,
    SpooltailTaskContext,
    create_spooltail_status_router,
    spooltail_task_event,
)


class _CursorGap(Exception):
    earliest_event_id = "earliest"


class _SubscriptionFailed(Exception):
    pass


class _StaleLease(Exception):
    pass


class _Conflict(Exception):
    pass


class _RpcError(Exception):
    def code(self) -> str:
        return self.args[0]


@pytest.fixture
def mock_connection(monkeypatch: pytest.MonkeyPatch):
    native = AsyncMock()
    actions = SimpleNamespace(ACK="ack", RENEW="renew", RETRY="retry", DEAD_LETTER="dead")
    sdk = SimpleNamespace(
        SettlementAction=actions,
        CursorGapError=_CursorGap,
        SubscriptionError=_SubscriptionFailed,
        StaleLeaseError=_StaleLease,
        ConflictError=_Conflict,
    )
    grpc = SimpleNamespace(
        aio=SimpleNamespace(AioRpcError=_RpcError),
        StatusCode=SimpleNamespace(UNAVAILABLE="unavailable", DEADLINE_EXCEEDED="deadline", INVALID_ARGUMENT="invalid"),
    )
    monkeypatch.setitem(sys.modules, "relayna_spooltail", sdk)
    monkeypatch.setitem(sys.modules, "grpc", grpc)
    monkeypatch.setitem(sys.modules, "grpc.aio", grpc.aio)
    return native


def test_invalid_backend_configuration() -> None:
    with pytest.raises(ValueError, match="distinct task and status topics"):
        SpooltailTaskConsumer(client=AsyncMock(), handler=AsyncMock(), task_topic="same", status_topic="same")


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
        SpooltailTaskConsumer(client=AsyncMock(), handler=AsyncMock(), **options)


def test_spooltail_task_event_serializes_the_relayna_envelope() -> None:
    assert spooltail_task_event({"task_id": "t", "payload": {"n": 2}})["payload"] == {"n": 2}
    event = TaskEnvelope(task_id="t")
    assert spooltail_task_event(event)["event_id"] == "task"
    assert spooltail_task_event(event) == spooltail_task_event(event)
    with pytest.raises(ValueError, match="does not support task priority"):
        spooltail_task_event(TaskEnvelope(task_id="t", priority=1))


@pytest.mark.asyncio
async def test_status_route_reads_from_the_caller_owned_client(mock_connection: AsyncMock) -> None:
    app = FastAPI()
    app.include_router(create_spooltail_status_router(get_client=lambda: mock_connection, status_topic="status-topic"))
    status = StatusEventEnvelope(task_id="t", status="completed", result={"n": 2}).as_transport_dict()
    mock_connection.latest.side_effect = [None, SimpleNamespace(event=status)]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/status/t")
    assert response.json() == {"task_id": "t", "event": status}
    assert mock_connection.latest.await_args.args == ("status-topic", "t")
    mock_connection.close.assert_not_called()


@pytest.mark.asyncio
async def test_spooltail_http_submit_compares_the_committed_work_event(mock_connection: AsyncMock) -> None:
    app = FastAPI()
    app.include_router(
        create_spooltail_status_router(get_client=lambda: mock_connection, task_topic="work", service="reference")
    )
    task = {"task_id": "task-a", "payload": {"value": 1}, "created_at": "2026-11-27T07:00:00+07:00"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post("/tasks", json=task)
        assert response.status_code == 202 and response.json() == {"task_id": "task-a"}
        topic, committed = mock_connection.publish.await_args.args
        assert topic == "work"
        assert committed["service"] == "reference"
        assert committed["created_at"] == "2026-11-27T00:00:00Z"
        assert committed["event_id"] == "task"
        mock_connection.publish.side_effect = _Conflict()
        mock_connection.latest.return_value = SimpleNamespace(event=committed)
        assert (await http.post("/tasks", json=task | {"service": "reference"})).status_code == 202
        without_timestamp = {key: value for key, value in task.items() if key != "created_at"}
        assert (await http.post("/tasks", json=without_timestamp)).status_code == 202
        assert (await http.post("/tasks", json=task | {"created_at": "2026-11-27T01:00:00Z"})).status_code == 409
        response = await http.post("/tasks", json=without_timestamp | {"payload": {"value": True}})
        assert response.status_code == 409 and response.json() == {
            "detail": "Task ID already has a different submission"
        }
        mock_connection.latest.assert_awaited_with("work", "task-a")
        mock_connection.latest.return_value = None
        assert (await http.post("/tasks", json=without_timestamp)).status_code == 409


def test_submission_requires_a_distinct_work_topic_and_service() -> None:
    with pytest.raises(ValueError, match="configured service"):
        create_spooltail_status_router(get_client=Mock(), task_topic="work")
    with pytest.raises(ValueError, match="distinct task topic"):
        create_spooltail_status_router(get_client=Mock(), task_topic="relayna.status", service="reference")


@pytest.mark.asyncio
async def test_spooltail_submit_validation_and_uncertain_broker_outcome(mock_connection: AsyncMock) -> None:
    app = FastAPI()
    app.include_router(
        create_spooltail_status_router(get_client=lambda: mock_connection, task_topic="work", service="reference")
    )
    valid = {"task_id": "task-a", "payload": {}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        for bad in (
            {"payload": {}},
            {"task_id": "task-a"},
            valid | {"task_id": "path/segment"},
            valid | {"event_id": "collision"},
            valid | {"service": "different"},
            valid | {"created_at": None},
            valid | {"created_at": 1764192000},
            valid | {"created_at": "2026-11-27T00:00:00"},
            valid | {"spec_version": "2.0"},
        ):
            assert (await http.post("/tasks", json=bad)).status_code == 422
        mock_connection.publish.assert_not_awaited()
        for error, code in ((_RpcError("invalid"), 422), (_RpcError("deadline"), 503), (ValueError(), 422)):
            mock_connection.publish.side_effect = error
            assert (await http.post("/tasks", json=valid)).status_code == code


@pytest.mark.asyncio
async def test_spooltail_history_exposes_broker_cursor_and_capability(mock_connection: AsyncMock) -> None:
    app = FastAPI()
    app.include_router(create_spooltail_status_router(get_client=lambda: mock_connection))
    event = {"task_id": "t", "event_id": "completed", "status": "completed"}
    mock_connection.history.side_effect = [[SimpleNamespace(event=event)], [], _CursorGap()]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/history/t")).json() == {"task_id": "t", "count": 1, "events": [event]}
        assert (await http.get("/history/t?after=completed")).json() == {"task_id": "t", "count": 0, "events": []}
        mock_connection.history.assert_awaited_with("relayna.status", "t", after="completed")
        gap = await http.get("/history/t?after=pruned")
        assert gap.status_code == 410 and gap.json() == {"detail": {"earliest_event_id": "earliest"}}
        capabilities = (await http.get("/relayna/capabilities")).json()
        assert capabilities["feature_flags"] == ["spooltail.task_history.v1", "spooltail.broker.v1"]
        assert "status.history" in capabilities["supported_routes"]
        assert (await http.post("/tasks", json={"task_id": "t", "payload": {}})).status_code == 404
        assert (await http.post("/tasks/t/cancel")).status_code == 404


@pytest.mark.asyncio
async def test_spooltail_cancellation_is_opt_in_and_writes_one_stable_intent(mock_connection: AsyncMock) -> None:
    for options in ({"cancel_topic": "control"}, {"task_topic": "work", "cancel_topic": "work"}):
        with pytest.raises(ValueError, match="distinct cancellation topic"):
            create_spooltail_status_router(get_client=lambda: mock_connection, **options)
    app = FastAPI()
    app.include_router(
        create_spooltail_status_router(
            get_client=lambda: mock_connection,
            task_topic="work",
            cancel_topic="control",
            service="reference",
            broker_version="0.0.6",
        )
    )
    mock_connection.stats.return_value = SimpleNamespace(payload_reads=2, recovered_frames=3)
    work = SimpleNamespace(event={"task_id": "task"})
    finished = SimpleNamespace(event={"status": "cancelled"})
    mock_connection.latest.side_effect = [None, None, finished, None, work, None, work, _RpcError("unavailable")]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        stats = await http.get("/relayna/broker")
        assert stats.json() == {"broker": "spooltail", "payload_reads": 2, "recovered_frames": 3, "version": "0.0.6"}
        assert "spooltail.cancel.v1" in (await http.get("/relayna/capabilities")).json()["feature_flags"]
        assert (await http.post("/tasks/task/cancel")).status_code == 404
        assert (await http.post("/tasks/task/cancel")).status_code == 409
        for _ in range(2):
            accepted = await http.post("/tasks/task/cancel")
            assert accepted.status_code == 202 and accepted.json() == {"task_id": "task"}
        assert (await http.post("/tasks/task/cancel")).status_code == 503
        assert mock_connection.publish.await_count == 2
        first, second = mock_connection.publish.await_args_list
        assert first == second and first.args == ("control", {"task_id": "task", "event_id": "cancel"})


@pytest.mark.asyncio
async def test_spooltail_cancellation_preempts_work_and_lets_active_profile_clean_up(
    mock_connection: AsyncMock,
) -> None:
    cleaned = asyncio.Event()

    async def handle(task, context):
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0.025)
            cleaned.set()

    lease = SimpleNamespace(generation=1, record=SimpleNamespace(event={"task_id": "t"}))
    intent = SimpleNamespace(event={"task_id": "t", "event_id": "cancel"})
    mock_connection.claim.return_value = lease
    worker = SpooltailTaskConsumer(
        client=mock_connection,
        profile=SpooltailLocalProfile(handle),
        cancel_topic="control",
        lease_ms=30,
        cancel_check_seconds=0.001,
    )
    mock_connection.latest.return_value = intent
    assert await worker.run_once()
    assert not cleaned.is_set()
    event = mock_connection.complete.await_args.args[2]
    assert event["status"] == "cancelled" and event["attempt_id"]

    async def complete(*args):
        assert cleaned.is_set()
        assert args[2]["status"] == "cancelled"
        return 1

    mock_connection.complete.side_effect = complete
    mock_connection.latest.side_effect = [None, None, intent]
    assert await asyncio.wait_for(worker.run_once(), timeout=1)
    mock_connection.settle.assert_awaited_with(lease, "renew", delay_ms=30)


def test_spooltail_worker_profile_and_cancellation_configuration() -> None:
    client = AsyncMock()
    profile = SpooltailLocalProfile(AsyncMock())
    for kwargs in (
        {},
        {"handler": AsyncMock(), "profile": profile},
        {"profile": profile, "cancel_topic": "relayna.tasks"},
        {"profile": profile, "cancel_check_seconds": 0},
    ):
        with pytest.raises(ValueError):
            SpooltailTaskConsumer(client=client, **kwargs)


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
    app = FastAPI()
    app.include_router(create_spooltail_status_router(get_client=lambda: mock_connection))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/events/t", headers={"Last-Event-ID": "previous"})
        assert b"event: ready" in response.content
        assert expected in response.content


@pytest.mark.asyncio
async def test_completion_replays_exact_request_after_ambiguous_rpc(mock_connection: AsyncMock) -> None:
    async def handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        await context.publish_status(StatusEventEnvelope(task_id="wrong", status="completed", result={"ok": True}))
        task.task_id = "changed"

    consumer = SpooltailTaskConsumer(client=mock_connection, handler=handler)
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

    worker = SpooltailTaskConsumer(
        client=mock_connection, handler=handler, retry_policy=RetryPolicy(max_retries=1, delay_ms=9)
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
    worker = SpooltailTaskConsumer(client=mock_connection, handler=handler)
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

    worker = SpooltailTaskConsumer(client=mock_connection, handler=handler, idle_retry_seconds=0.001)
    lease = SimpleNamespace(generation=2, record=SimpleNamespace(event={"task_id": "t"}))
    mock_connection.claim.side_effect = [_RpcError("unavailable"), _StaleLease(), None, lease]
    await asyncio.wait_for(worker.run_forever(), timeout=1)
    assert mock_connection.claim.await_count == 4
    assert mock_connection.complete.await_args.args[2]["status"] == "completed"

    worker = SpooltailTaskConsumer(client=mock_connection, handler=handler)
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

    worker = SpooltailTaskConsumer(client=mock_connection, handler=handler, lease_ms=30)
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

    worker = SpooltailTaskConsumer(client=mock_connection, handler=handler)
    mock_connection.claim.return_value = SimpleNamespace(generation=1, record=SimpleNamespace(event={"task_id": "t"}))
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
    from relayna_spooltail import SpooltailClient

    assert spooltail_target is not None
    scope = uuid4().hex
    task_topic, status_topic = f"{scope}.tasks", f"{scope}.status"
    attempts: list[int] = []

    async def handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        attempts.append(context.retry_attempt)
        assert context.correlation_id == "request"
        await context.publish_status(status="processing", meta={"attempt": context.retry_attempt})
        if context.retry_attempt == 0:
            raise RuntimeError("temporary failure")
        assert context.redelivered
        await context.publish_status(status="completed", result={"answer": task.payload["number"] * 2})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with SpooltailClient(spooltail_target, tls=False) as client:
            await client.ping()
            app.state.spooltail = client
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(
        create_spooltail_status_router(
            get_client=lambda: app.state.spooltail,
            status_topic=status_topic,
            task_topic=task_topic,
            service="reference",
        )
    )
    async with (
        asyncio.timeout(8),
        app.router.lifespan_context(app),
        SpooltailClient(spooltail_target, tls=False) as worker_client,
    ):
        api_client: SpooltailClient = app.state.spooltail
        consumer = SpooltailTaskConsumer(
            client=worker_client,
            handler=handler,
            task_topic=task_topic,
            status_topic=status_topic,
            retry_policy=RetryPolicy(max_retries=1, delay_ms=300),
        )
        task_id = "task-1"
        request = {"task_id": task_id, "correlation_id": "request", "payload": {"number": 21}}
        assert await api_client.latest(status_topic, task_id) is None
        second_app = FastAPI()
        second_app.include_router(
            create_spooltail_status_router(
                get_client=lambda: worker_client, status_topic=status_topic, task_topic=task_topic, service="reference"
            )
        )
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as first,
            AsyncClient(transport=ASGITransport(app=second_app), base_url="http://test") as second,
        ):
            retries = await asyncio.gather(
                *(http.post("/tasks", json=request) for http in (first, second, first, second))
            )
            assert all(item.status_code == 202 and item.json() == {"task_id": task_id} for item in retries)
            changed = await second.post("/tasks", json=request | {"payload": {"number": 22}})
            assert changed.status_code == 409
            assert (await first.get(f"/history/{task_id}")).json()["events"] == []

        work = await api_client.history(task_topic, task_id)
        assert len(work) == 1 and work[0].event["service"] == "reference"
        assert work[0].event["created_at"].endswith("Z")
        abandoned = await api_client.claim(task_topic, "relayna.workers", lease_ms=150)
        assert abandoned is not None and abandoned.record == work[0]
        assert not await consumer.run_once()
        await asyncio.sleep(0.2)
        assert await consumer.run_once()
        assert not await consumer.run_once()
        await asyncio.sleep(0.35)
        assert await consumer.run_once()
        assert not await consumer.run_once()
        assert attempts == [0, 1]

        history = [record.event for record in await api_client.history(status_topic, task_id)]
        assert [event["status"] for event in history] == ["processing", "processing", "completed"]
        assert history[0]["attempt_id"] != history[1]["attempt_id"] == history[2]["attempt_id"]
        cursor = history[0]["event_id"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get(f"/status/{task_id}")
            assert response.status_code == 200
            assert response.json() == {"task_id": task_id, "event": history[-1]}
            assert response.json()["event"]["result"] == {"answer": 42}
            replay = await http.get(f"/history/{task_id}", params={"after": cursor})
            assert replay.json() == {"task_id": task_id, "count": 2, "events": history[1:]}
            gap = await http.get(f"/history/{task_id}?after=absent")
            assert gap.status_code == 410 and gap.json() == {"detail": {"earliest_event_id": cursor}}
            assert (await http.post("/tasks", json=request)).status_code == 202
            assert (await api_client.history(task_topic, task_id)) == work
            response = await http.get(f"/events/{task_id}", headers={"Last-Event-ID": cursor}, timeout=3)
            assert response.text.count("event: status") == 2
            assert f"id: {cursor}" not in response.text
            assert f"id: {history[-1]['event_id']}" in response.text
            response = await http.get(f"/events/{task_id}", headers={"Last-Event-ID": "absent"}, timeout=3)
            assert "event: gap" in response.text
            assert cursor in response.text

        failing_worker = SpooltailTaskConsumer(
            client=worker_client,
            handler=AsyncMock(side_effect=ValueError("handler failed")),
            task_topic=task_topic,
            status_topic=status_topic,
        )
        await api_client.publish(task_topic, spooltail_task_event({"task_id": "failed-task"}))
        assert await failing_worker.run_once()
        assert not await failing_worker.run_once()
        failure = await api_client.latest(status_topic, "failed-task")
        assert failure is not None and failure.event["status"] == "failed"


@real_broker
@pytest.mark.asyncio
async def test_native_broker_renews_lease_and_streams_live_status() -> None:
    from relayna_spooltail import SpooltailClient

    assert spooltail_target is not None
    scope = uuid4().hex
    topics = {"task_topic": f"{scope}.tasks", "status_topic": f"{scope}.status"}
    release = asyncio.Event()
    processing = asyncio.Event()

    async def handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        await context.publish_status(status="processing")
        processing.set()
        await release.wait()

    async with SpooltailClient(spooltail_target, tls=False) as client:
        competitor = AsyncMock()
        worker = SpooltailTaskConsumer(client=client, handler=handler, lease_ms=400, **topics)
        other_worker = SpooltailTaskConsumer(client=client, handler=competitor, **topics)
        async with asyncio.timeout(5), aclosing(client.subscribe(topics["status_topic"], "long-task")) as stream:
            assert (await anext(stream)).kind == "ready"
            await client.publish(topics["task_topic"], spooltail_task_event({"task_id": "long-task"}))
            delivery = asyncio.create_task(worker.run_once())
            try:
                await processing.wait()
                assert (await anext(stream)).data["status"] == "processing"
                await asyncio.sleep(0.55)
                assert not await other_worker.run_once()
                competitor.assert_not_awaited()
                release.set()
                assert await delivery
                assert (await anext(stream)).data["status"] == "completed"
                with pytest.raises(StopAsyncIteration):
                    await anext(stream)
            finally:
                release.set()
                await delivery


@real_broker
@pytest.mark.skipif(os.environ.get("SPOOLTAIL_TEST_CANCEL") != "1", reason="requires a cancellation-capable broker")
@pytest.mark.asyncio
async def test_native_cancellation_interrupts_worker_and_replays_terminal_status() -> None:
    from relayna_spooltail import SpooltailClient

    assert spooltail_target
    key = uuid4().hex
    task_topic, status_topic, cancel_topic = (f"{key}.{kind}" for kind in ("task", "status", "cancel"))
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        await context.publish_status(status="processing")
        started.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    async with SpooltailClient(spooltail_target, tls=False) as native:
        worker = SpooltailTaskConsumer(
            client=native,
            handler=handler,
            task_topic=task_topic,
            status_topic=status_topic,
            cancel_topic=cancel_topic,
            cancel_check_seconds=0.01,
        )
        app = FastAPI()
        app.include_router(
            create_spooltail_status_router(
                get_client=lambda: native,
                task_topic=task_topic,
                status_topic=status_topic,
                cancel_topic=cancel_topic,
                service="reference",
            )
        )
        async with (
            asyncio.timeout(8),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
        ):
            assert (await http.post("/tasks/not-found/cancel")).status_code == 404
            assert (await http.post("/tasks", json={"task_id": "running", "payload": {}})).status_code == 202
            execution = asyncio.create_task(worker.run_once())
            try:
                await started.wait()
                assert (await http.post("/tasks/running/cancel")).status_code == 202
                assert await execution
            finally:
                if not execution.done():
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
            assert cleaned.is_set()
            history = (await http.get("/history/running")).json()["events"]
            assert [event["status"] for event in history] == ["processing", "cancelled"]
            assert history[0]["attempt_id"] == history[1]["attempt_id"]
            assert (await http.get("/status/running")).json()["event"] == history[1]
            replay = await http.get("/events/running", headers={"Last-Event-ID": history[0]["event_id"]})
            assert replay.text.count("event: status") == 1 and "cancelled" in replay.text
            assert (await http.post("/tasks/running/cancel")).status_code == 409
            assert len(await native.history(cancel_topic, "running")) == 1
            assert (await http.get("/relayna/broker")).json()["broker"] == "spooltail"
            assert not await worker.run_once()
