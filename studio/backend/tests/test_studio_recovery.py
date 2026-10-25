from __future__ import annotations

import asyncio
import copy
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from relayna_studio._recovery import (
    _Control,
    _create_recovery_router,
    _CreateRequest,
    _PreviewRequest,
    _RecoveryService,
    _report,
)
from relayna_studio.database import StudioDatabase, _recovery_plans, audit_log
from relayna_studio.federation import StudioFederationError
from relayna_studio.registry import ServiceRecord
from sqlalchemy import select, text, update

DATABASE_URL = os.getenv("RELAYNA_STUDIO_TEST_DATABASE_URL", "")
real_database = pytest.mark.skipif(not DATABASE_URL, reason="Recovery concurrency tests require PostgreSQL.")


def now():
    return datetime.now(UTC).isoformat()


@pytest.fixture
def upstream():
    record = ServiceRecord(
        service_id="svc",
        name="Payments",
        environment="staging",
        base_url="http://payments.test",
        auth_mode="internal_network",
        status="healthy",
        capabilities={
            "supported_routes": ["failed_tasks.detail", "failed_tasks.retry", "status.latest", "workflow.topology"]
        },
    )
    failures = {
        f"f{i}": {
            "failure_id": f"f{i}",
            "task_id": f"original-{i}",
            "status": "DLQ",
            "source_queue_name": "payments",
            "payload_available": True,
            "raw_body_b64": "e30=",
            "body": {},
            "retry_status": "not_retried",
            "failed_at": "2026-01-01T00:00:00Z",
            "latest_status": {"status": "failed", "timestamp": "2026-01-01T00:00:00Z"},
        }
        for i in range(6)
    }
    statuses = {}

    async def detail(service, failure_id):
        return copy.deepcopy(failures[failure_id])

    async def send(service, failure_id, **kwargs):
        receipt = {
            "failure_id": failure_id,
            "retry_status": "retried",
            "retried_at": now(),
            "retried_task_id": failures[failure_id]["task_id"],
        }
        failures[failure_id].update(**receipt, retry_note=kwargs["payload"]["note"])
        return receipt

    async def status(service, task_id):
        return {
            "task_id": task_id,
            "event": statuses.get(task_id, {"status": "failed", "timestamp": "2026-01-01T00:00:00Z"}),
        }

    federation = SimpleNamespace(
        _get_proxyable_service=AsyncMock(return_value=record),
        _fetch_failed_task_detail=AsyncMock(side_effect=detail),
        _mutate_failed_task=AsyncMock(side_effect=send),
        _fetch_status=AsyncMock(side_effect=status),
        get_service_workflow_topology=AsyncMock(return_value={"graph": {"stages": [{"queue": "alternate"}]}}),
    )
    return SimpleNamespace(record=record, failures=failures, statuses=statuses, federation=federation)


@pytest_asyncio.fixture
async def database():
    if not DATABASE_URL:
        pytest.skip("Set RELAYNA_STUDIO_TEST_DATABASE_URL to a disposable PostgreSQL database.")
    db = StudioDatabase(DATABASE_URL)
    await db.check_schema()
    async with db.engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE studio_recovery_claims, studio_recovery_plans, studio_operator_audit_log CASCADE")
        )
    yield db
    await db.dispose()


def selection(count=3, **kwargs):
    return _PreviewRequest(
        service_id="svc", environment="staging", items=[{"failure_id": f"f{i}"} for i in range(count)], **kwargs
    )


async def create(service, count=3, **kwargs):
    payload = selection(count, **kwargs)
    preview = await service.preview(payload)
    request = _CreateRequest(**payload.model_dump(), preview_digest=preview["preview_digest"], request_id=uuid4())
    return await service.create(request, None), request


async def pump(service, plan_id, advance=True):
    async with service.store.database.transaction() as session:
        if advance:
            doc = await session.scalar(select(_recovery_plans.c.document).where(_recovery_plans.c.plan_id == plan_id))
            doc = copy.deepcopy(doc)
            doc["next_dispatch_at"] = "2020-01-01T00:00:00Z"
            await session.execute(
                update(_recovery_plans).where(_recovery_plans.c.plan_id == plan_id).values(document=doc)
            )
        await session.execute(
            update(_recovery_plans)
            .where(_recovery_plans.c.plan_id == plan_id)
            .values(next_poll_at=datetime.now(UTC) - timedelta(days=1))
        )
    await service.tick()
    return await service.store.get(plan_id)


@pytest.mark.asyncio
async def test_preview_validates_exact_pin_payload_and_target_permissions(upstream):
    service = _RecoveryService(None, upstream.federation)
    result = await service.preview(selection())
    assert result["eligible"] and [i["failure_id"] for i in result["items"]] == ["f0", "f1", "f2"]
    assert result["items"][0]["target_queue"] == "payments"
    upstream.failures["f1"]["payload_available"] = False
    result = await service.preview(selection())
    assert not result["eligible"] and "missing" in result["items"][1]["errors"][0]
    target = _PreviewRequest(
        service_id="svc",
        environment="staging",
        items=[{"failure_id": "f0", "target_queue": "forbidden", "override_payload": {"task_id": "new"}}],
    )
    assert not (await service.preview(target))["eligible"]
    target.items[0].target_queue = "alternate"
    assert (await service.preview(target))["eligible"]
    upstream.record.environment = "production"
    with pytest.raises(HTTPException, match="409"):
        await service.preview(selection())


@pytest.mark.asyncio
async def test_preview_partial_read_failure_and_unavailable_runtime(upstream):
    service = _RecoveryService(None, upstream.federation)
    upstream.federation._fetch_failed_task_detail.side_effect = StudioFederationError(
        status_code=504, detail="Timeout", code="upstream_timeout"
    )
    result = await service.preview(selection())
    assert not result["eligible"] and all(i["errors"] == ["Timeout"] for i in result["items"])
    app = FastAPI()
    app.include_router(_create_recovery_router(None))
    with TestClient(app) as client:
        assert client.get("/studio/recovery/plans").status_code == 503
        assert client.post("/studio/recovery/preview", json=selection().model_dump()).status_code == 503


def test_selection_rejects_duplicates_limits_and_unknown_configuration():
    for changes in ({"items": [{"failure_id": "same"}] * 2}, {"interval_seconds": 0}, {"items": []}, {"unknown": True}):
        with pytest.raises(ValidationError):
            _PreviewRequest.model_validate({**selection().model_dump(), **changes})


@real_database
@pytest.mark.asyncio
async def test_create_is_idempotent_reserves_cohort_and_requires_same_preview(database, upstream):
    service = _RecoveryService(database, upstream.federation)
    plan, request = await create(service)
    assert (await service.create(request, None))["plan_id"] == plan["plan_id"]
    with pytest.raises(HTTPException) as conflict:
        await create(service)
    assert conflict.value.status_code == 409
    with pytest.raises(HTTPException):
        await service.create(request.model_copy(update={"note": "different"}), None)
    fresh = selection(4)
    preview = await service.preview(fresh)
    upstream.failures["f3"]["raw_body_b64"] = "changed"
    with pytest.raises(HTTPException, match="409"):
        await service.create(
            _CreateRequest(**fresh.model_dump(), request_id=uuid4(), preview_digest=preview["preview_digest"]), None
        )
    listing = await service.store.list("staging", 0)
    assert listing["count"] == 1 and listing["items"][0]["report"]["requested"] == 0
    assert not (await service.store.list("production", 0))["items"]
    assert "request_hash" not in listing["items"][0]


@real_database
@pytest.mark.asyncio
async def test_canary_pauses_then_releases_remaining_and_reports_real_outcomes(database, upstream):
    service = _RecoveryService(database, upstream.federation)
    plan, _ = await create(service)
    plan = await service.control(plan["plan_id"], _Control(action="batch", version=0, count=2), None)
    plan = await pump(service, plan["plan_id"])
    plan = await pump(service, plan["plan_id"])
    assert plan["state"] == "paused"
    assert _report(plan)["report"]["accepted"] == 2
    assert _report(plan)["report"]["observed_success"] == 0
    await pump(service, plan["plan_id"])
    assert upstream.federation._mutate_failed_task.await_count == 2
    with pytest.raises(HTTPException):
        await service.control(plan["plan_id"], _Control(action="resume", version=plan["version"]), None)
    upstream.statuses["original-0"] = {"status": "completed", "timestamp": now()}
    upstream.statuses["original-1"] = {"status": "failed", "timestamp": now()}
    plan = await pump(service, plan["plan_id"])
    report = _report(plan)["report"]
    assert report["observed_success"] == 1 and report["observed_failure"] == report["repeated_failures"] == 1
    assert report["cohort_recovery_seconds"] is None
    await service.control(plan["plan_id"], _Control(action="batch", version=plan["version"], count=200), None)
    plan = await pump(service, plan["plan_id"])
    assert plan["state"] == "completed" and upstream.federation._mutate_failed_task.await_count == 3
    assert _report(plan)["report"]["awaiting_outcome"] == 1
    async with database.transaction() as session:
        actions = list(await session.scalars(select(audit_log.c.action)))
    assert "recovery.dispatch.requested" in actions and "recovery.observed" in actions


@real_database
@pytest.mark.asyncio
async def test_multiple_instances_claim_once_and_share_rate_deadline(database, upstream):
    first, second = _RecoveryService(database, upstream.federation), _RecoveryService(database, upstream.federation)
    plan, _ = await create(first)
    plan = await first.control(plan["plan_id"], _Control(action="batch", version=0), None)
    await asyncio.gather(first._dispatch(plan), second._dispatch(plan))
    assert upstream.federation._mutate_failed_task.await_count == 1
    await second._dispatch(await second.store.get(plan["plan_id"]))
    assert upstream.federation._mutate_failed_task.await_count == 1
    await pump(second, plan["plan_id"])
    assert upstream.federation._mutate_failed_task.await_count == 2


@real_database
@pytest.mark.asyncio
async def test_pause_wins_preflight_race_and_stale_control_does_not_dispatch(database, upstream):
    service = _RecoveryService(database, upstream.federation)
    plan, _ = await create(service)
    plan = await service.control(plan["plan_id"], _Control(action="batch", version=0), None)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._inspect

    async def inspect(*args):
        result = await original(*args)
        entered.set()
        await release.wait()
        return result

    service._inspect = inspect
    sending = asyncio.create_task(service._dispatch(plan))
    await entered.wait()
    paused = await service.control(plan["plan_id"], _Control(action="pause", version=plan["version"]), None)
    release.set()
    await sending
    upstream.federation._mutate_failed_task.assert_not_awaited()
    with pytest.raises(HTTPException):
        await service.control(plan["plan_id"], _Control(action="resume", version=plan["version"]), None)
    assert (await service.control(plan["plan_id"], _Control(action="resume", version=paused["version"]), None))[
        "state"
    ] == "running"


@real_database
@pytest.mark.asyncio
async def test_stop_during_inflight_send_preserves_receipt_without_dispatching_more(database, upstream):
    service = _RecoveryService(database, upstream.federation)
    plan, _ = await create(service)
    plan = await service.control(plan["plan_id"], _Control(action="batch", version=0), None)
    entered, release = asyncio.Event(), asyncio.Event()
    original = upstream.federation._mutate_failed_task.side_effect

    async def sending(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    upstream.federation._mutate_failed_task.side_effect = sending
    task = asyncio.create_task(service._dispatch(plan))
    await entered.wait()
    await service.control(plan["plan_id"], _Control(action="stop", version=plan["version"]), None)
    release.set()
    await task
    plan = await pump(service, plan["plan_id"])
    assert plan["state"] == "stopped" and plan["items"][0]["dispatch_state"] == "accepted"
    assert upstream.federation._mutate_failed_task.await_count == 1


@real_database
@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [True, False])
async def test_timeout_reconciles_exact_receipt_or_keeps_unknown_without_resend(database, upstream, receipt):
    service = _RecoveryService(database, upstream.federation)
    plan, _ = await create(service, 1)
    plan = await service.control(plan["plan_id"], _Control(action="batch", version=0), None)
    original = upstream.federation._mutate_failed_task.side_effect

    async def timeout(*args, **kwargs):
        if receipt:
            await original(*args, **kwargs)
        raise StudioFederationError(status_code=504, detail="Timeout after publication", code="upstream_timeout")

    upstream.federation._mutate_failed_task.side_effect = timeout
    await service._dispatch(plan)
    restarted = _RecoveryService(database, upstream.federation)
    plan = await pump(restarted, plan["plan_id"])
    assert plan["items"][0]["dispatch_state"] == ("accepted" if receipt else "unknown")
    if not receipt:
        upstream.failures["f0"].update(retry_note="another operator", retried_at=now(), retried_task_id="other")
        plan = await pump(restarted, plan["plan_id"])
        assert plan["items"][0]["dispatch_state"] == "unknown"
    await pump(restarted, plan["plan_id"])
    assert upstream.federation._mutate_failed_task.await_count == 1


@real_database
@pytest.mark.asyncio
async def test_process_crash_after_acceptance_reconciles_committed_intent(database, upstream):
    service = _RecoveryService(database, upstream.federation)
    plan, _ = await create(service, 1)
    plan = await service.control(plan["plan_id"], _Control(action="batch", version=0), None)
    original = upstream.federation._mutate_failed_task.side_effect

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError

    upstream.federation._mutate_failed_task.side_effect = crash
    with pytest.raises(asyncio.CancelledError):
        await service._dispatch(plan)
    plan = await service.store.get(plan["plan_id"])
    assert plan["items"][0]["dispatch_state"] == "dispatching"
    await service._item_change(
        plan["plan_id"], "f0", {"requested_at": (datetime.now(UTC) - timedelta(minutes=2)).isoformat()}, "test.clock"
    )
    upstream.statuses["original-0"] = {"status": "completed", "timestamp": now()}
    plan = await pump(_RecoveryService(database, upstream.federation), plan["plan_id"])
    assert plan["items"][0]["outcome"] == "succeeded"
    assert _report(plan)["report"]["cohort_recovery_seconds"] is not None
    assert upstream.federation._mutate_failed_task.await_count == 1


@real_database
@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["environment", "payload", "role", "queue"])
async def test_dispatch_revalidates_and_blocks_changed_permission_or_eligibility(database, upstream, change):
    service = _RecoveryService(database, upstream.federation)
    payload = selection(1)
    if change == "queue":
        payload.items[0].target_queue = "alternate"
    preview = await service.preview(payload)
    plan = await service.create(
        _CreateRequest(**payload.model_dump(), request_id=uuid4(), preview_digest=preview["preview_digest"]), None
    )
    plan = await service.control(plan["plan_id"], _Control(action="batch", version=0), None)
    if change == "environment":
        upstream.record.environment = "production"
    elif change == "payload":
        upstream.failures["f0"]["payload_available"] = False
    elif change == "queue":
        upstream.federation.get_service_workflow_topology.return_value = {"stages": []}
    else:

        def revoke(doc, _):
            doc["execution_actor"] = "revoked"
            return True

        plan = await service.store.change(plan["plan_id"], revoke, "test.revoke")
    await service._dispatch(plan)
    result = await service.store.get(plan["plan_id"])
    assert result["state"] == "paused" and result["items"][0]["dispatch_state"] == (
        "pending" if change == "role" else "blocked"
    )
    upstream.federation._mutate_failed_task.assert_not_awaited()


@real_database
@pytest.mark.asyncio
async def test_missing_replacement_id_and_unavailable_evidence_remain_unknown(database, upstream):
    service = _RecoveryService(database, upstream.federation)
    plan, _ = await create(service, 1)
    plan = await service.control(plan["plan_id"], _Control(action="batch", version=0), None)
    await service._dispatch(plan)
    await service._item_change(plan["plan_id"], "f0", {"replacement_task_id": None}, "test.receipt")
    plan = await pump(service, plan["plan_id"])
    assert plan["items"][0]["outcome"] == "unknown"
    await service._item_change(
        plan["plan_id"],
        "f0",
        {"replacement_task_id": "original-0", "accepted_at": "2026-01-01T00:00:00Z"},
        "test.receipt",
    )
    upstream.federation._fetch_status.side_effect = StudioFederationError(
        status_code=503, detail="Service unavailable", code="upstream_response_error"
    )
    plan = await pump(service, plan["plan_id"])
    assert plan["items"][0]["outcome"] == "unknown"
    assert "24 hours" in plan["items"][0]["message"]


@real_database
@pytest.mark.asyncio
async def test_http_contract_authorization_and_pinned_proxy_end_to_end(database, upstream):
    import httpx
    from relayna_studio._operator_auth import _MEMBER
    from relayna_studio.audit_context import reset_actor_user_id, set_actor_user_id
    from relayna_studio.federation import StudioFederationService

    async def handler(request):
        failure = upstream.failures["f0"]
        if request.method == "GET":
            return httpx.Response(200, json=failure)
        payload = __import__("json").loads(request.content)
        assert request.url.host == "payments.test" and request.url.path == "/failed-tasks/f0/retry"
        assert payload["target_queue"] == "payments" and payload["retried_by"] == _MEMBER.email
        return httpx.Response(
            200,
            json={"failure_id": "f0", "retry_status": "retried", "retried_at": now(), "retried_task_id": "replacement"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream_client:
        federation = StudioFederationService(
            registry_service=SimpleNamespace(get_service=AsyncMock(return_value=upstream.record)),
            http_client=upstream_client,
        )
        service = _RecoveryService(database, federation)
        app = FastAPI()
        actor = _MEMBER

        @app.middleware("http")
        async def auth(request, next_call):
            request.state.studio_member = actor
            token = set_actor_user_id(actor.user_id)
            try:
                return await next_call(request)
            finally:
                reset_actor_user_id(token)

        app.include_router(_create_recovery_router(service))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            payload = selection(1).model_dump()
            preview = (await client.post("/studio/recovery/preview", json=payload)).json()
            assert preview["eligible"]
            response = await client.post(
                "/studio/recovery/plans",
                json={**payload, "preview_digest": preview["preview_digest"], "request_id": str(uuid4())},
            )
            assert response.status_code == 200
            plan = response.json()
            assert "fingerprint" not in plan["items"][0]
            await client.post(
                f"/studio/recovery/plans/{plan['plan_id']}/control", json={"action": "batch", "version": 0, "count": 1}
            )
            await pump(service, plan["plan_id"])
            detail = (await client.get(f"/studio/recovery/plans/{plan['plan_id']}")).json()
            assert detail["report"]["accepted"] == 1
            assert detail["items"][0]["replacement_task_id"] == "replacement"
            actor = actor.model_copy(update={"role": "readonly"})
            assert (await client.post("/studio/recovery/preview", json=payload)).status_code == 403
            assert (await client.get(f"/studio/recovery/plans/{plan['plan_id']}")).status_code == 200
            assert (
                await client.post(
                    f"/studio/recovery/plans/{plan['plan_id']}/control", json={"action": "stop", "version": 1}
                )
            ).status_code == 403
        async with database.transaction() as session:
            audit = (
                (await session.execute(select(audit_log).where(audit_log.c.action == "recovery.dispatch.requested")))
                .mappings()
                .one()
            )
        assert audit["actor_user_id"] == _MEMBER.user_id


@real_database
@pytest.mark.asyncio
async def test_transient_preflight_can_resume_without_reclassifying_an_unsent_item(database, upstream):
    service = _RecoveryService(database, upstream.federation)
    plan, _ = await create(service, 1)
    plan = await service.control(plan["plan_id"], _Control(action="batch", version=0), None)
    detail = upstream.federation._fetch_failed_task_detail.side_effect
    upstream.federation._fetch_failed_task_detail.side_effect = StudioFederationError(
        status_code=504, detail="Read timed out", code="upstream_timeout", retryable=True
    )
    await service._dispatch(plan)
    paused = await service.store.get(plan["plan_id"])
    assert paused["state"] == "paused" and paused["items"][0]["dispatch_state"] == "pending"
    assert paused["items"][0]["requested_at"] is None
    upstream.federation._fetch_failed_task_detail.side_effect = detail
    resumed = await service.control(plan["plan_id"], _Control(action="resume", version=paused["version"]), None)
    await service._dispatch(resumed)
    assert upstream.federation._mutate_failed_task.await_count == 1


@real_database
@pytest.mark.asyncio
async def test_simultaneous_creation_uses_one_durable_request_identity(database, upstream):
    service = _RecoveryService(database, upstream.federation)
    payload = selection(1)
    preview = await service.preview(payload)
    request = _CreateRequest(**payload.model_dump(), request_id=uuid4(), preview_digest=preview["preview_digest"])
    first, second = await asyncio.gather(service.create(request, None), service.create(request, None))
    assert first["plan_id"] == second["plan_id"]
    assert (await service.store.list(None, 0))["count"] == 1
    upstream.federation._mutate_failed_task.assert_not_awaited()


@real_database
@pytest.mark.asyncio
async def test_invalid_acceptance_and_wrong_task_evidence_do_not_claim_recovery(database, upstream):
    service = _RecoveryService(database, upstream.federation)
    plan, _ = await create(service, 1)
    plan = await service.control(plan["plan_id"], _Control(action="batch", version=0), None)
    upstream.federation._mutate_failed_task.side_effect = None
    upstream.federation._mutate_failed_task.return_value = {"failure_id": "f0", "retry_status": "retried"}
    await service._dispatch(plan)
    plan = await service.store.get(plan["plan_id"])
    assert plan["items"][0]["dispatch_state"] == "unknown"
    # Even an exact marker is insufficient without a retried receipt.
    upstream.failures["f0"].update(retry_note=plan["items"][0]["receipt_marker"], retried_at=now())
    plan = await pump(service, plan["plan_id"])
    assert plan["items"][0]["dispatch_state"] == "unknown"
    upstream.failures["f0"].update(retry_status="retried", retried_task_id="original-0")
    upstream.federation._fetch_status.side_effect = None
    upstream.federation._fetch_status.return_value = {
        "task_id": "wrong-task",
        "event": {"status": "completed", "timestamp": now()},
    }
    plan = await pump(service, plan["plan_id"])
    assert plan["items"][0]["dispatch_state"] == "accepted"
    assert plan["items"][0]["outcome"] == "pending"
    upstream.federation._fetch_status.return_value = {
        "task_id": "original-0",
        "event": {"task_id": "wrong-task", "status": "completed", "timestamp": now()},
    }
    plan = await pump(service, plan["plan_id"])
    assert plan["items"][0]["outcome"] == "pending"
    assert "different task" in plan["items"][0]["message"]
    upstream.federation._fetch_status.return_value = {
        "task_id": "original-0",
        "event": {"status": "completed", "timestamp": now()},
    }
    plan = await pump(service, plan["plan_id"])
    assert plan["items"][0]["outcome"] == "succeeded"
    await service._item_change(plan["plan_id"], "f0", {"outcome": "pending"}, "test.stale")
    assert (await service.store.get(plan["plan_id"]))["items"][0]["outcome"] == "succeeded"


@pytest.mark.asyncio
async def test_background_worker_survives_tick_failure_and_can_be_cancelled(upstream, caplog):
    service = _RecoveryService(None, upstream.federation)
    progressed = asyncio.Event()

    async def tick():
        if not progressed.is_set():
            progressed.set()
            raise RuntimeError("Temporary database outage")

    service.tick = AsyncMock(side_effect=tick)
    task = asyncio.create_task(service.run())
    try:
        await asyncio.wait_for(progressed.wait(), timeout=1)
        await asyncio.sleep(0.25)
        assert service.tick.await_count >= 2
        assert "never resent" in caplog.text
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
