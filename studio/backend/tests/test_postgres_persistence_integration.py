from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from relayna_studio import app as studio_app_module
from relayna_studio import backfill as backfill_module
from relayna_studio.app import create_studio_app, get_studio_runtime
from relayna_studio.auth import (
    StudioEntraConfig,
    StudioMemberStatus,
    StudioRole,
    StudioUserUpdate,
    _LoginTransaction,
    _Session,
)
from relayna_studio.backfill import RedisStudioBackfill
from relayna_studio.database import (
    HybridStudioAuthStore,
    PostgresAdvisoryCoordinator,
    PostgresFailedTaskEmailSettingsStore,
    PostgresNotificationHistoryStore,
    PostgresOutboxRelay,
    PostgresServiceRegistryStore,
    PostgresStudioEventStore,
    PostgresStudioHealthStore,
    PostgresStudioSearchStore,
    StudioDatabase,
    StudioMutationAuditMiddleware,
    _dt,
    _json_model,
    _optional_text,
    audit_log,
    events,
    health_history,
    metadata,
    notification_deliveries,
    outbox,
    services,
    task_projections,
)
from relayna_studio.events import StudioEventEnvelope, StudioPullSyncWorker
from relayna_studio.failed_task_notifications import FailedTaskEmailNotificationWorker
from relayna_studio.health import (
    CapabilityHealthState,
    CapabilityHealthSummary,
    HttpStatusSummary,
    ObservationFreshnessState,
    ObservationFreshnessSummary,
    StudioHealthRefreshWorker,
    StudioHttpReachability,
    StudioOverallHealthStatus,
    StudioServiceHealthDocument,
    WorkerHealthState,
    WorkerHealthSummary,
)
from relayna_studio.registry import DuplicateServiceError, LokiLogConfig, ServiceNotFoundError, ServiceRecord
from relayna_studio.search import (
    StudioRetentionWorker,
    StudioSearchService,
    StudioServiceSearchDocument,
    StudioTaskSearchDocument,
)
from sqlalchemy import delete, func, select, text, update
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.exc import DBAPIError, IntegrityError

from relayna.observability import (
    RelaynaServiceEvent,
    ServiceEventSourceKind,
    StudioEventIngestMethod,
)

DATABASE_URL = os.getenv("RELAYNA_STUDIO_TEST_DATABASE_URL", "")
REDIS_URL = os.getenv("RELAYNA_STUDIO_TEST_REDIS_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not REDIS_URL,
    reason="Set RELAYNA_STUDIO_TEST_DATABASE_URL and RELAYNA_STUDIO_TEST_REDIS_URL for real integration tests.",
)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[StudioDatabase]:
    database = StudioDatabase(DATABASE_URL, pool_size=4, pool_max_overflow=4)
    await database.check_ready()
    await database.check_schema()
    table_names = ", ".join(f'"{table.name}"' for table in reversed(metadata.sorted_tables))
    async with database.engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    yield database
    await database.dispose()


@pytest_asyncio.fixture
async def redis() -> AsyncIterator[Redis]:
    redis = Redis.from_url(REDIS_URL)
    await redis.flushdb()
    yield redis
    await redis.flushdb()
    await redis.aclose()


def service_record(
    service_id: str = "payments-api", *, base_url: str = "https://payments.example.test"
) -> ServiceRecord:
    return ServiceRecord(
        service_id=service_id,
        name="Payments",
        base_url=base_url,
        environment="production",
        tags=["payments", "critical"],
        auth_mode="none",
    )


def event_envelope(
    *,
    cursor: str = "1-0",
    event_id: str | None = "event-1",
    timestamp: str | None = "2026-08-19T12:00:00Z",
    task_id: str = "task-1",
) -> StudioEventEnvelope:
    return StudioEventEnvelope(
        service_id="payments-api",
        ingest_method=StudioEventIngestMethod.PUSH,
        event=RelaynaServiceEvent(
            cursor=cursor,
            task_id=task_id,
            event_id=event_id,
            event_type="status.changed",
            source_kind=ServiceEventSourceKind.STATUS,
            timestamp=timestamp,
            correlation_id="corr-1",
            component="worker",
            payload={"status": "failed", "stage": "charge"},
        ),
    )


def health_document() -> StudioServiceHealthDocument:
    now = datetime.now(UTC)
    return StudioServiceHealthDocument(
        service_id="payments-api",
        registry_status="registered",
        http_status=HttpStatusSummary(state=StudioHttpReachability.REACHABLE, checked_at=now),
        capability_status=CapabilityHealthSummary(state=CapabilityHealthState.FRESH, checked_at=now),
        observation_freshness=ObservationFreshnessSummary(state=ObservationFreshnessState.FRESH, checked_at=now),
        worker_health=WorkerHealthSummary(state=WorkerHealthState.HEALTHY, checked_at=now),
        last_checked_at=now,
        overall_status=StudioOverallHealthStatus.HEALTHY,
    )


@pytest.mark.asyncio
async def test_registry_constraints_soft_delete_projection_and_audit(database: StudioDatabase) -> None:
    store = PostgresServiceRegistryStore(database)
    record = service_record()
    assert await store.create(record) == record
    assert await store.get(record.service_id) == record
    assert await store.list_records() == [record]

    with pytest.raises(DuplicateServiceError):
        await store.create(record)
    with pytest.raises(DuplicateServiceError):
        await store.create(service_record("other", base_url=record.base_url))

    updated = record.model_copy(update={"name": "Payments v2"})
    assert await store.update(record.service_id, updated) == updated
    projection = PostgresStudioSearchStore(database)
    assert (await projection.get_service_document(record.service_id)).name == "Payments v2"  # type: ignore[union-attr]

    await store.delete(record.service_id)
    assert await store.get(record.service_id) is None
    assert await store.list_records() == []
    with pytest.raises(ServiceNotFoundError):
        await store.delete(record.service_id)
    with pytest.raises(ServiceNotFoundError):
        await store.update(record.service_id, updated)

    resurrected = record.model_copy(update={"name": "Payments restored"})
    assert await store.create(resurrected) == resurrected
    other = service_record("orders-api", base_url="https://orders.example.test")
    await store.create(other)
    with pytest.raises(DuplicateServiceError):
        await store.update(
            other.service_id,
            other.model_copy(update={"base_url": resurrected.base_url}),
        )
    configured = service_record("logs-api", base_url="https://logs.example.test").model_copy(
        update={
            "log_config": LokiLogConfig(
                base_url="https://loki.example.test",
                service_selector_labels={"service": "logs"},
            )
        }
    )
    await store.create(configured)
    assert (await store.get(configured.service_id)).log_config == configured.log_config  # type: ignore[union-attr]
    assert _dt(datetime(2026, 8, 19, 12, 0)).tzinfo is not None
    assert _json_model(configured.log_config) == configured.log_config.model_dump(mode="json")
    assert _json_model({"plain": True}) == {"plain": True}
    assert _optional_text(None) is None
    async with database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(audit_log)) == 6
    long_service = service_record(
        "service-" + "x" * 300,
        base_url="https://long-service.example.test",
    )
    assert await store.create(long_service) == long_service
    assert await store.get(long_service.service_id) == long_service

    task = StudioTaskSearchDocument(
        service_id=record.service_id,
        service_name=updated.name,
        environment=updated.environment,
        task_id="task-registry-metadata",
        detail_path=f"/studio/tasks/{record.service_id}/task-registry-metadata",
    )
    await projection.set_task_document(task)
    await PostgresStudioHealthStore(database).set_health(record.service_id, health_document())
    metadata_update = updated.model_copy(update={"name": "Payments v3", "environment": "staging"})
    await store.update(record.service_id, metadata_update)
    service_projection = await projection.get_service_document(record.service_id)
    task_projection = await projection.get_task_document(task.document_id)
    assert service_projection is not None and service_projection.health_status == "healthy"
    assert task_projection is not None
    assert (task_projection.service_name, task_projection.environment) == ("Payments v3", "staging")


@pytest.mark.asyncio
async def test_event_transactional_projection_dedupe_queries_retention_and_outbox(
    database: StudioDatabase, redis: Redis
) -> None:
    registry = PostgresServiceRegistryStore(database)
    await registry.create(service_record())
    store = PostgresStudioEventStore(database, redis, ttl_seconds=60, task_index_ttl_seconds=60)

    envelope = event_envelope()
    inserted = await asyncio.gather(*(store.insert_event(envelope) for _ in range(5)))
    assert inserted.count(True) == 1
    assert inserted.count(False) == 4
    assert not await store.insert_event(event_envelope(cursor="2-0", event_id="event-1"))
    assert await store.insert_event(event_envelope(cursor="3-0", event_id="event-2", timestamp="2026-08-19T11:59:00Z"))
    missing_service_event = event_envelope()
    missing_service_event.service_id = "missing-service"
    with pytest.raises(IntegrityError):
        await store.insert_event(missing_service_event)

    page = await store.list_service_events(
        "payments-api",
        task_id="task-1",
        source_kind=ServiceEventSourceKind.STATUS,
        event_type="status.changed",
        from_time="2026-08-19T11:00:00Z",
        to_time="2026-08-19T13:00:00Z",
        limit=1,
    )
    assert page.count == 1
    assert page.next_cursor is not None
    assert page.items[0].out_of_order is False
    next_page = await store.list_task_events("payments-api", "task-1", before=page.next_cursor, limit=10)
    assert next_page.count == 1
    assert next_page.items[0].out_of_order is True
    unknown_cursor_page = await store.list_task_events("payments-api", "task-1", before="unknown-dedupe-key", limit=1)
    assert unknown_cursor_page.items == page.items
    capped_store = PostgresStudioEventStore(
        database,
        redis,
        ttl_seconds=60,
        history_maxlen=1,
        task_index_ttl_seconds=60,
    )
    capped_service_page = await capped_store.list_service_events("payments-api", limit=10)
    capped_task_page = await capped_store.list_task_events("payments-api", "task-1", limit=10)
    assert [item.dedupe_key for item in capped_service_page.items] == [next_page.items[0].dedupe_key]
    assert capped_task_page.items == capped_service_page.items
    snapshot = await store.get_service_activity_snapshot("payments-api")
    assert snapshot.latest_status_event_at == "2026-08-19T12:00:00Z"
    assert snapshot.latest_ingested_at is not None

    await store.set_pull_cursor("payments-api", "cursor-3")
    assert await store.get_pull_cursor("payments-api") == "cursor-3"
    search = PostgresStudioSearchStore(database)
    task_ids = await search.list_task_document_ids_for_filter("correlation_id", "corr-1")
    assert len(task_ids) == 1
    task = await search.get_task_document(next(iter(task_ids)))
    assert task is not None and task.status == "failed" and task.stage == "charge"

    pubsub = redis.pubsub()
    await pubsub.subscribe(store.service_channel("payments-api"), store.task_channel("payments-api", "task-1"))
    relay = PostgresOutboxRelay(database, redis)
    assert await relay.relay_once() == 4
    messages: list[dict[str, Any]] = []
    for _ in range(20):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if message:
            messages.append(message)
        if len(messages) == 4:
            break
    assert len(messages) == 4
    assert json.loads(messages[0]["data"])["task_id"] == "task-1"
    await pubsub.aclose()

    async with database.transaction() as session:
        await session.execute(update(events).values(expires_at=datetime(2000, 1, 1, tzinfo=UTC)))
        await session.execute(update(outbox).values(delivered_at=datetime(2000, 1, 1, tzinfo=UTC)))
    assert await store.prune_expired() == 2
    async with database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(outbox)) == 0


@pytest.mark.asyncio
async def test_event_ordering_and_long_task_identifiers_preserve_redis_contract(
    database: StudioDatabase, redis: Redis
) -> None:
    await PostgresServiceRegistryStore(database).create(service_record())
    store = PostgresStudioEventStore(database, redis, ttl_seconds=60, task_index_ttl_seconds=60)
    valid = event_envelope(cursor="valid", event_id="valid", timestamp="2020-01-01T00:00:00Z")
    timestamp_less = event_envelope(cursor="missing", event_id="missing", timestamp=None)
    assert await store.insert_event(valid)
    assert await store.insert_event(timestamp_less)

    page = await store.list_task_events("payments-api", "task-1", limit=1)
    assert [item.event_id for item in page.items] == ["valid"]
    assert page.next_cursor == page.items[0].dedupe_key
    next_page = await store.list_task_events("payments-api", "task-1", before=page.next_cursor, limit=1)
    assert [item.event_id for item in next_page.items] == ["missing"]

    long_task_id = "task-" + "x" * 300
    long_parent_id = "parent-" + "y" * 300
    long_task = event_envelope(cursor="long", event_id="long", task_id=long_task_id)
    long_task.event.parent_task_id = long_parent_id
    assert await store.insert_event(long_task)
    search = PostgresStudioSearchStore(database)
    document_ids = await search.list_task_document_ids_for_filter("task_id", long_task_id)
    assert len(document_ids) == 1
    document = await search.get_task_document(next(iter(document_ids)))
    assert document is not None and document.task_id == long_task_id
    async with database.sessions() as session:
        parent_task_id = await session.scalar(select(events.c.parent_task_id).where(events.c.event_id == "long"))
    assert parent_task_id == long_parent_id


@pytest.mark.asyncio
async def test_postgres_search_bulk_loads_and_deletes_task_projections(database: StudioDatabase) -> None:
    await PostgresServiceRegistryStore(database).create(service_record())
    store = PostgresStudioSearchStore(database)
    documents = [
        StudioTaskSearchDocument(
            service_id="payments-api",
            service_name="Payments",
            environment="production",
            task_id=f"task-{index}",
            detail_path=f"/studio/tasks/payments-api/task-{index}",
        )
        for index in range(3)
    ]
    for document in documents:
        await store.set_task_document(document)
    service = StudioSearchService(
        registry_service=cast(Any, None),
        event_store=cast(Any, None),
        store=store,
    )
    statements: list[str] = []

    def record_statement(_conn: Any, _cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    sqlalchemy_event.listen(database.engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        loaded = await service._load_task_documents([*[document.document_id for document in documents], "invalid"])
    finally:
        sqlalchemy_event.remove(database.engine.sync_engine, "before_cursor_execute", record_statement)
    assert {document.task_id for document in loaded} == {document.task_id for document in documents}
    assert sum(statement.lstrip().upper().startswith("SELECT") for statement in statements) == 1

    await store.delete_task_documents(["invalid"])
    await store.delete_task_documents([document.document_id for document in documents])
    assert await store.list_task_document_ids() == set()


@pytest.mark.asyncio
async def test_search_health_members_settings_notifications_and_coordination(
    database: StudioDatabase, redis: Redis
) -> None:
    registry = PostgresServiceRegistryStore(database)
    await registry.create(service_record())
    search = PostgresStudioSearchStore(database)
    assert await search.task_index_is_empty()
    service_doc = StudioServiceSearchDocument(
        service_id="payments-api",
        name="Payments Search",
        environment="production",
        tags=["critical"],
        status="registered",
        health_status=None,
        base_url="https://payments.example.test",
        auth_mode="none",
    )
    await search.set_service_document(service_doc)
    assert await search.get_service_document("payments-api") == service_doc
    assert await search.get_service_documents([]) == {}
    assert await search.get_service_documents(["payments-api", "missing"]) == {"payments-api": service_doc}
    assert await search.list_service_document_ids() == {"payments-api"}
    assert await search.list_service_document_ids_for_filter("environment", "production") == {"payments-api"}
    assert await search.list_service_document_ids_for_filter("tag", "critical") == {"payments-api"}
    assert await search.list_service_document_ids_for_token("pay") == {"payments-api"}
    assert await search.list_service_document_ids_for_filter("invalid", "x") == set()

    task_doc = StudioTaskSearchDocument(
        service_id="payments-api",
        service_name="Payments Search",
        environment="production",
        task_id="task-2",
        correlation_id="corr-2",
        status="failed",
        stage="settle",
        first_seen_at="2026-08-19T12:00:00Z",
        last_seen_at="2026-08-19T12:01:00Z",
        detail_path="/studio/tasks/payments-api/task-2",
    )
    await search.set_task_document(task_doc)
    ids = await search.list_task_document_ids()
    assert len(ids) == 1
    document_id = next(iter(ids))
    assert await search.get_task_document(document_id) == task_doc
    assert await search.get_task_document("bad") is None
    assert await search.list_task_document_ids_for_service("payments-api") == ids
    assert await search.list_task_document_ids_for_filter("status", "failed") == ids
    assert await search.list_task_document_ids_for_filter("invalid", "x") == set()
    await search.delete_task_document(document_id)
    await search.delete_task_document(document_id)
    await search.delete_service_document("payments-api")
    assert await search.get_service_document("payments-api") is None

    health_store = PostgresStudioHealthStore(database)
    document = health_document()
    assert await health_store.get_health("payments-api") is None
    assert await health_store.set_health("payments-api", document) == document
    assert await health_store.get_health("payments-api") == document
    await health_store.set_health("payments-api", document)
    async with database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(health_history)) == 2

    config = StudioEntraConfig(
        application_id="app",
        tenant_id="tenant",
        issuer="http://127.0.0.1/tenant/v2.0",
        discovery_url="http://127.0.0.1/.well-known/openid-configuration",
        redirect_uri="http://127.0.0.1/callback",
        private_key_path="/tmp/key",
        certificate_path="/tmp/cert",
        admin_emails=("admin@example.test",),
        admin_object_ids=("admin-1",),
    )
    auth = HybridStudioAuthStore(database, redis, prefix="studio:auth")
    empty_config = StudioEntraConfig(
        application_id="app",
        tenant_id="tenant",
        issuer="http://127.0.0.1/tenant/v2.0",
        discovery_url="http://127.0.0.1/.well-known/openid-configuration",
        redirect_uri="http://127.0.0.1/callback",
        private_key_path="/tmp/key",
        certificate_path="/tmp/cert",
    )
    with pytest.raises(RuntimeError, match="no active administrator"):
        await auth.initialize(empty_config)
    await auth.initialize(config)
    admin = await auth.upsert_login(
        {"oid": "admin-1", "tid": "tenant", "email": "admin@example.test", "name": "Admin"}, config
    )
    pending = await auth.upsert_login(
        {"oid": "user-1", "tid": "tenant", "email": "user@example.test", "name": "User"}, config
    )
    assert admin.role is StudioRole.ADMIN and admin.status is StudioMemberStatus.ACTIVE
    assert pending.status is StudioMemberStatus.PENDING
    signed_in_again = await auth.upsert_login(
        {"oid": "admin-1", "tid": "tenant", "email": "admin2@example.test", "name": "Admin Two"},
        config,
    )
    assert signed_in_again.email == "admin2@example.test"
    assert await auth.get_member(admin.user_id) == signed_in_again
    assert await auth.get_member("missing") is None
    assert [item.user_id for item in await auth.list_members()] == [admin.user_id, pending.user_id]
    active = await auth.update_member(
        pending.user_id,
        StudioUserUpdate(status=StudioMemberStatus.ACTIVE),
        actor_user_id=admin.user_id,
    )
    assert active.status is StudioMemberStatus.ACTIVE
    with pytest.raises(ValueError, match="demote or block themselves"):
        await auth.update_member(
            admin.user_id,
            StudioUserUpdate(role=StudioRole.READONLY),
            actor_user_id=admin.user_id,
        )
    with pytest.raises(KeyError):
        await auth.update_member("missing", StudioUserUpdate(), actor_user_id=admin.user_id)
    second_admin = await auth.upsert_login(
        {"oid": "admin-2", "tid": "tenant", "email": "second@example.test", "name": "Second"},
        StudioEntraConfig(
            application_id="app",
            tenant_id="tenant",
            issuer="http://127.0.0.1/tenant/v2.0",
            discovery_url="http://127.0.0.1/.well-known/openid-configuration",
            redirect_uri="http://127.0.0.1/callback",
            private_key_path="/tmp/key",
            certificate_path="/tmp/cert",
            admin_emails=("second@example.test",),
            admin_object_ids=("admin-2",),
        ),
    )
    await auth.update_member(
        second_admin.user_id,
        StudioUserUpdate(role=StudioRole.READONLY),
        actor_user_id=admin.user_id,
    )
    with pytest.raises(ValueError, match="At least one active administrator"):
        await auth.update_member(
            admin.user_id,
            StudioUserUpdate(role=StudioRole.READONLY),
            actor_user_id=pending.user_id,
        )

    transaction = _LoginTransaction(state="state", nonce="nonce", code_verifier="verifier", return_to="/")
    await auth.save_login("login", transaction, 60)
    assert await auth.consume_login("login") == transaction
    assert await auth.consume_login("login") is None
    session_value = _Session(user_id=admin.user_id, csrf_token="csrf")
    await auth.save_session("session", session_value, 60)
    assert await auth.get_session("session") == session_value
    await auth.delete_session("session")
    assert await auth.get_session("session") is None

    settings = PostgresFailedTaskEmailSettingsStore(database, default_batch_wait_seconds=5)
    assert (await settings.get()).batch_wait_seconds == 5
    updated_settings = await settings.update(enabled=True, batch_wait_seconds=9999999)
    assert updated_settings.batch_wait_seconds == 604800
    assert await settings.get() == updated_settings
    history = PostgresNotificationHistoryStore(database, dedupe_ttl_seconds=3600)
    assert not await history.is_notified("payments-api", "failure-1")
    await history.mark_notified("payments-api", "failure-1", {"task_id": "task-2"})
    assert await history.is_notified("payments-api", "failure-1")
    pending_batch = {"started_at": datetime.now(UTC).isoformat(), "items": [{"failure_id": "failure-2"}]}
    await history.save_pending(pending_batch)
    assert (await history.load_pending())["items"] == pending_batch["items"]
    await history.clear_pending()
    assert (await history.load_pending())["items"] == []
    await history.save_pending(pending_batch)
    async with database.transaction() as session:
        await session.execute(text("UPDATE studio_notification_batches SET expires_at = '2000-01-01'"))
    assert (await history.load_pending())["items"] == []

    coordinator = PostgresAdvisoryCoordinator(database)
    entered = asyncio.Event()

    async def first() -> bool:
        async with coordinator.try_lock("same-job") as acquired:
            assert acquired
            entered.set()
            await asyncio.sleep(0.1)
            return acquired

    async def second() -> bool:
        await entered.wait()
        async with coordinator.try_lock("same-job") as acquired:
            return acquired

    assert await asyncio.gather(first(), second()) == [True, False]


@pytest.mark.asyncio
async def test_outbox_failure_recovery_and_audit_immutability(database: StudioDatabase, redis: Redis) -> None:
    await PostgresServiceRegistryStore(database).create(service_record())
    store = PostgresStudioEventStore(database, redis)
    assert await store.insert_event(event_envelope())

    class FailingPublisher:
        async def publish(self, *_args: Any) -> None:
            raise ConnectionError("redis unavailable")

    failing = PostgresOutboxRelay(database, cast(Any, FailingPublisher()))
    assert await failing.relay_once() == 0
    async with database.transaction() as session:
        await session.execute(update(outbox).values(available_at=datetime.now(UTC)))
    assert await PostgresOutboxRelay(database, redis).relay_once() == 2
    assert await PostgresOutboxRelay(database, redis).relay_once() == 0

    failing_loop = PostgresOutboxRelay(database, redis, interval_seconds=0.01)
    failing_loop.relay_once = AsyncMock(side_effect=RuntimeError("temporary"))  # type: ignore[method-assign]
    loop_task = asyncio.create_task(failing_loop.run_forever())
    await asyncio.sleep(0.03)
    failing_loop.stop()
    await loop_task

    async with database.transaction() as session:
        entry_id = await session.scalar(select(audit_log.c.id).limit(1))
    with pytest.raises(DBAPIError, match="append-only"):
        async with database.transaction() as session:
            await session.execute(update(audit_log).where(audit_log.c.id == entry_id).values(action="tampered"))
    with pytest.raises(DBAPIError, match="append-only"):
        async with database.transaction() as session:
            await session.execute(delete(audit_log).where(audit_log.c.id == entry_id))


@pytest.mark.asyncio
async def test_upstream_failed_task_mutations_record_requested_and_outcome(database: StudioDatabase) -> None:
    app = FastAPI()
    app.add_middleware(StudioMutationAuditMiddleware, database=database)

    @app.post("/studio/failed-tasks/{service_id}/{failure_id}/retry")
    async def retry(service_id: str, failure_id: str) -> dict[str, str]:
        return {"service_id": service_id, "failure_id": failure_id}

    @app.delete("/studio/failed-tasks/{service_id}/{failure_id}", status_code=502)
    async def remove(service_id: str, failure_id: str) -> dict[str, str]:
        return {"service_id": service_id, "failure_id": failure_id}

    @app.post("/studio/failed-tasks/{service_id}/{failure_id}/mark-investigated")
    async def investigate(service_id: str, failure_id: str) -> dict[str, str]:
        raise RuntimeError(f"upstream failed for {service_id}:{failure_id}")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://studio.test") as client:
        assert (await client.post("/studio/failed-tasks/svc/failure/retry")).status_code == 200
        assert (await client.delete("/studio/failed-tasks/svc/failure")).status_code == 502
        with pytest.raises(RuntimeError, match="upstream failed"):
            await client.post("/studio/failed-tasks/svc/failure/mark-investigated")

    async with database.sessions() as session:
        rows = (
            (
                await session.execute(
                    select(audit_log.c.action, audit_log.c.target_id, audit_log.c.details).order_by(audit_log.c.id)
                )
            )
            .mappings()
            .all()
        )
    assert [row["action"] for row in rows] == [
        "failed_task.retry.requested",
        "failed_task.retry.succeeded",
        "failed_task.delete.requested",
        "failed_task.delete.failed",
        "failed_task.mark-investigated.requested",
        "failed_task.mark-investigated.failed",
    ]
    assert {row["target_id"] for row in rows} == {"svc:failure"}
    assert rows[0]["details"]["operation_id"] == rows[1]["details"]["operation_id"]


@pytest.mark.asyncio
async def test_redis_backfill_is_idempotent_validated_and_detects_source_changes(
    database: StudioDatabase, redis: Redis
) -> None:
    record = service_record()
    await redis.sadd("studio:services:all", record.service_id)
    await redis.set(f"studio:services:by-id:{record.service_id}", record.model_dump_json())
    colon_record = service_record("team:payments", base_url="https://team-payments.example.test")
    await redis.sadd("studio:services:all", colon_record.service_id)
    await redis.set(f"studio:services:by-id:{colon_record.service_id}", colon_record.model_dump_json())
    member = {
        "user_id": "tenant:admin-1",
        "tenant_id": "tenant",
        "object_id": "admin-1",
        "email": "admin@example.test",
        "display_name": "Admin",
        "role": "admin",
        "status": "active",
        "created_at": "2026-08-19T12:00:00Z",
        "updated_at": "2026-08-19T12:00:00Z",
    }
    await redis.sadd("studio:auth:members", member["user_id"])
    await redis.set(f"studio:auth:member:{member['user_id']}", json.dumps(member))
    event = event_envelope().event
    control = {
        "service_id": record.service_id,
        "ingest_method": "push",
        "ingested_at": "2026-08-19T12:00:01Z",
        "dedupe_key": "payments-api:status:event-1",
        "out_of_order": False,
        "task_id": event.task_id,
        "event_type": event.event_type,
        "source_kind": "status",
        "component": event.component,
        "timestamp": event.timestamp,
        "event_id": event.event_id,
        "correlation_id": event.correlation_id,
        "payload": event.payload,
    }
    event_key = "studio:events:event:payments-api:status:event-1"
    await redis.set(event_key, json.dumps(control), ex=300)
    await redis.rpush("studio:events:service:payments-api:history", "payments-api:status:event-1")
    await redis.set("studio:events:pull-cursor:payments-api", "cursor-1")
    task = StudioTaskSearchDocument(
        service_id="payments-api",
        service_name="Payments",
        environment="production",
        task_id="task-1",
        status="failed",
        stage="charge",
        detail_path="/studio/tasks/payments-api/task-1",
    )
    await redis.set(f"studio:search:task:doc:{task.document_id}", task.model_dump_json())
    service = StudioServiceSearchDocument(
        service_id="payments-api",
        name="Payments",
        environment="production",
        tags=["payments"],
        status="registered",
        base_url=record.base_url,
        auth_mode="none",
    )
    await redis.set("studio:search:service:doc:payments-api", service.model_dump_json())
    await redis.set("studio:health:payments-api", health_document().model_dump_json())
    await redis.set("studio:failed_task_email:settings", json.dumps({"enabled": True, "batch_wait_seconds": 30}))
    await redis.set("studio:failed_task_email:notified:payments-api:failure-1", "2026-08-19T12:00:00Z")
    await redis.set(
        "studio:failed_task_email:notified:team:payments:failure:2",
        "2026-08-19T12:00:00Z",
    )
    await redis.set(
        "studio:failed_task_email:pending",
        json.dumps({"started_at": "2026-08-19T12:00:00Z", "items": [{"failure_id": "failure-2"}]}),
    )
    await redis.set("relayna:history:sdk-task", "sdk-runtime-state")

    backfill = RedisStudioBackfill(redis=redis, database=database, redis_url=REDIS_URL)
    snapshot = await backfill.snapshot()
    assert ("team:payments", "failure:2", "2026-08-19T12:00:00Z") in snapshot.notification_deliveries
    validation = await backfill.run(validate_only=True)
    assert validation["status"] == "validated" and validation["counts"]["invalid"] == 0
    imported = await backfill.run()
    assert imported["status"] == "imported"
    assert (await backfill.run())["status"] == "already_imported"
    assert await redis.exists("studio:services:by-id:payments-api")
    assert await redis.get("relayna:history:sdk-task") == b"sdk-runtime-state"
    async with database.sessions() as session:
        imported_expiry = await session.scalar(
            select(events.c.expires_at).where(events.c.dedupe_key == control["dedupe_key"])
        )
        colon_delivery = (
            await session.execute(
                select(notification_deliveries.c.service_id, notification_deliveries.c.failure_id).where(
                    notification_deliveries.c.service_id == "team:payments"
                )
            )
        ).one()
    assert colon_delivery == ("team:payments", "failure:2")
    assert imported_expiry is not None
    remaining_expiry = imported_expiry - datetime.now(UTC)
    assert timedelta(seconds=250) < remaining_expiry <= timedelta(seconds=300)

    await redis.set("studio:events:pull-cursor:payments-api", "changed-after-import")
    with pytest.raises(RuntimeError, match="source changed"):
        await backfill.run()


@pytest.mark.asyncio
async def test_redis_backfill_rejects_invalid_and_retains_deleted_service_history(
    database: StudioDatabase, redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    await redis.sadd("studio:services:all", "missing-service")
    duplicate_service = service_record("duplicate-service", base_url="https://duplicate.example.test")
    await redis.sadd("studio:services:all", "duplicate-a", "duplicate-b")
    await redis.set("studio:services:by-id:duplicate-a", duplicate_service.model_dump_json())
    await redis.set("studio:services:by-id:duplicate-b", duplicate_service.model_dump_json())
    await redis.sadd("studio:auth:members", "tenant:broken")
    await redis.set("studio:auth:member:tenant:broken", "not-json")
    await redis.set("studio:events:event:broken", "not-json")
    valid_event = {
        "service_id": "missing-service",
        "ingest_method": "push",
        "ingested_at": "2026-08-19T12:00:00Z",
        "dedupe_key": "missing:status:valid",
        "out_of_order": False,
        **event_envelope().model_dump(mode="json")["event"],
    }
    await redis.set("studio:events:event:valid", json.dumps(valid_event))
    await redis.rpush("studio:events:service:broken:history", "missing-dedupe")
    await redis.set("studio:health:broken", "not-json")
    await redis.set("studio:failed_task_email:notified:broken", "now")
    await redis.set("studio:failed_task_email:settings", "not-json")
    await redis.set("studio:failed_task_email:pending", "[]")
    backfill = RedisStudioBackfill(redis=redis, database=database, redis_url=REDIS_URL)
    original_ttl = redis.ttl

    async def expired_during_snapshot(_key: str) -> int:
        return -2

    monkeypatch.setattr(redis, "ttl", expired_during_snapshot)
    snapshot = await backfill.snapshot()
    monkeypatch.setattr(redis, "ttl", original_ttl)
    assert len(snapshot.invalid) >= 7
    with pytest.raises(RuntimeError, match="malformed records"):
        await backfill.run()
    allowed = await backfill.run(validate_only=True, allow_invalid=True)
    assert allowed["counts"]["invalid"] >= 7

    await redis.flushdb()
    orphan_event = event_envelope().model_dump(mode="json")
    orphan_event = {
        "service_id": "missing-service",
        "ingest_method": "push",
        "ingested_at": "2026-08-19T12:00:00Z",
        "dedupe_key": "missing:status:event-1",
        "out_of_order": False,
        **orphan_event["event"],
    }
    await redis.set("studio:events:event:orphan", json.dumps(orphan_event))
    await redis.set("studio:health:missing-service", health_document().model_dump_json())
    task = StudioTaskSearchDocument(
        service_id="missing-service",
        service_name="Missing",
        environment="test",
        task_id="task-1",
        detail_path="/studio/tasks/missing-service/task-1",
    )
    await redis.set(f"studio:search:task:doc:{task.document_id}", task.model_dump_json())
    service = StudioServiceSearchDocument(
        service_id="missing-service",
        name="Missing",
        environment="test",
        status="registered",
        base_url="https://missing.example.test",
        auth_mode="none",
    )
    await redis.set("studio:search:service:doc:missing-service", service.model_dump_json())
    imported = await RedisStudioBackfill(redis=redis, database=database, redis_url=REDIS_URL).run()
    assert imported["status"] == "imported"
    assert imported["counts"]["tombstone_services"] == 1
    async with database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(events)) == 1
        assert await session.scalar(select(func.count()).select_from(task_projections)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(services).where(services.c.deleted_at.is_not(None)))
            == 1
        )
    args = backfill_module._parser().parse_args(
        ["--redis-url", REDIS_URL, "--database-url", DATABASE_URL, "--validate-only", "--allow-invalid"]
    )
    assert await backfill_module._main_async(args) == 0


@pytest.mark.asyncio
async def test_all_periodic_workers_run_under_database_coordination(database: StudioDatabase) -> None:
    coordinator = PostgresAdvisoryCoordinator(database)
    pull_service = AsyncMock()
    health_service = AsyncMock()
    search_service = AsyncMock()
    search_service.prune_expired_task_documents.return_value = 0
    event_store = AsyncMock()
    event_store.prune_expired.return_value = 0
    notification_service = AsyncMock()
    notification_service.notify_new_failed_tasks.return_value = 0
    workers = [
        StudioPullSyncWorker(pull_service, interval_seconds=0.01, coordinator=coordinator),
        StudioHealthRefreshWorker(health_service, interval_seconds=0.01, coordinator=coordinator),
        StudioRetentionWorker(
            search_service,
            interval_seconds=0.01,
            coordinator=coordinator,
            event_store=event_store,
        ),
        FailedTaskEmailNotificationWorker(
            notification_service,
            interval_seconds=0.01,
            coordinator=coordinator,
        ),
    ]
    tasks = [asyncio.create_task(worker.run_forever()) for worker in workers]
    await asyncio.sleep(0.08)
    for worker in workers:
        worker.stop()
    await asyncio.gather(*tasks)
    pull_service.sync_registered_services.assert_awaited()
    health_service.refresh_all_services.assert_awaited()
    search_service.prune_expired_task_documents.assert_awaited()
    event_store.prune_expired.assert_awaited()
    notification_service.notify_new_failed_tasks.assert_awaited()


def test_real_app_lifecycle_probes_audit_and_restart_persistence() -> None:
    app = create_studio_app(
        redis_url=REDIS_URL,
        database_url=DATABASE_URL,
        pull_sync_interval_seconds=0.05,
        health_refresh_interval_seconds=0.05,
        retention_prune_interval_seconds=0.05,
        outbox_relay_interval_seconds=0.05,
        failed_task_email_service_url="https://email.example.test/send",
        failed_task_email_api_key="key",
        failed_task_email_receivers=("ops@example.test",),
        failed_task_email_interval_seconds=0.05,
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        runtime = get_studio_runtime(app)
        assert runtime.event_ingest_service.search_indexer is None
        assert runtime.registry_service._search_indexer is None
        assert runtime.health_service.search_indexer is None
        assert client.get("/livez").json() == {"status": "ok"}
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").json() == {"status": "ready"}
        response = client.post(
            "/studio/services",
            json={
                "service_id": "restart-api",
                "name": "Restart",
                "base_url": "https://restart.example.test",
                "environment": "test",
                "tags": [],
                "auth_mode": "none",
            },
        )
        assert response.status_code == 201
        audit = client.get("/studio/admin/audit", params={"action": "service.create", "target_type": "service"})
        assert audit.status_code == 200 and audit.json()["count"] == 1
        actor_audit = client.get("/studio/admin/audit", params={"actor_user_id": "nobody"})
        assert actor_audit.json()["count"] == 0

    restarted = create_studio_app(
        redis_url=REDIS_URL,
        database_url=DATABASE_URL,
        pull_sync_interval_seconds=None,
        health_refresh_interval_seconds=None,
        retention_prune_interval_seconds=None,
    )
    with TestClient(restarted) as client:
        response = client.get("/studio/services/restart-api")
        assert response.status_code == 200 and response.json()["name"] == "Restart"


def test_lifespan_cancels_stuck_background_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_studio_app(
        redis_url=REDIS_URL,
        pull_sync_interval_seconds=None,
        health_refresh_interval_seconds=None,
        retention_prune_interval_seconds=None,
    )
    from fastapi.testclient import TestClient

    class Worker:
        def stop(self) -> None:
            return None

    async def install_stuck_tasks() -> None:
        runtime = get_studio_runtime(app)

        async def stuck() -> None:
            await asyncio.Event().wait()

        runtime.pull_sync_worker = cast(Any, Worker())
        runtime.health_refresh_worker = cast(Any, Worker())
        runtime.retention_worker = cast(Any, Worker())
        runtime.failed_task_email_worker = cast(Any, Worker())
        runtime.outbox_relay = cast(Any, Worker())
        runtime.pull_sync_task = asyncio.create_task(stuck())
        runtime.health_refresh_task = asyncio.create_task(stuck())
        runtime.retention_task = asyncio.create_task(stuck())
        runtime.failed_task_email_task = asyncio.create_task(stuck())
        runtime.outbox_relay_task = asyncio.create_task(stuck())

    original_wait_for = studio_app_module.asyncio.wait_for

    async def immediate_timeout(*_args: Any, **_kwargs: Any) -> None:
        raise TimeoutError

    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(install_stuck_tasks)
        monkeypatch.setattr(studio_app_module.asyncio, "wait_for", immediate_timeout)
    monkeypatch.setattr(studio_app_module.asyncio, "wait_for", original_wait_for)


@pytest.mark.asyncio
async def test_schema_has_required_constraints_indexes_and_audit_trigger(database: StudioDatabase) -> None:
    async with database.engine.connect() as connection:
        indexes = set(
            await connection.scalars(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() AND tablename LIKE 'studio_%'"
                )
            )
        )
        constraints = set(
            await connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint WHERE connamespace = "
                    "(SELECT oid FROM pg_namespace WHERE nspname = current_schema())"
                )
            )
        )
        trigger_count = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'studio_operator_audit_log_append_only' "
                "AND NOT tgisinternal"
            )
        )
        service_id_type = await connection.scalar(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'studio_services' AND column_name = 'service_id'"
            )
        )
        task_id_types = set(
            await connection.scalars(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND column_name = 'task_id' "
                    "AND table_name IN ('studio_events', 'studio_task_search_projections')"
                )
            )
        )
        parent_task_id_type = await connection.scalar(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'studio_events' AND column_name = 'parent_task_id'"
            )
        )
    assert {
        "uq_studio_services_active_environment_base_url",
        "uq_studio_events_service_source_event_id",
        "ix_studio_events_service_task_time",
        "ix_studio_events_service_effective_time",
        "ix_studio_events_task_effective_time",
        "ix_studio_events_correlation_time",
        "ix_studio_events_status_stage_time",
        "ix_studio_tasks_status_stage_time",
        "ix_studio_outbox_pending",
        "ix_studio_outbox_delivered_retention",
        "ix_studio_audit_target_time",
        "ix_studio_audit_action_time",
        "ix_studio_health_history_retention",
        "ix_studio_service_search_tags_gin",
    } <= indexes
    assert {
        "fk_studio_events_service_id_studio_services",
        "uq_studio_events_dedupe_key",
        "ck_studio_members_member_role",
        "ck_studio_members_member_status",
    } <= constraints
    assert trigger_count == 1
    assert service_id_type == "text"
    assert task_id_types == {"text"}
    assert parent_task_id_type == "text"


@pytest.mark.asyncio
async def test_database_configuration_and_schema_failures() -> None:
    with pytest.raises(RuntimeError, match="PostgreSQL URL"):
        StudioDatabase("sqlite+aiosqlite:///tmp/studio.db")
    database = StudioDatabase(DATABASE_URL)
    try:
        await database.check_ready()
        await database.check_schema()
        async with database.engine.begin() as connection:
            await connection.execute(text("UPDATE alembic_version SET version_num = 'wrong'"))
        with pytest.raises(RuntimeError, match="expected 0001_studio_postgres"):
            await database.check_schema()
        async with database.engine.begin() as connection:
            await connection.execute(text("UPDATE alembic_version SET version_num = '0001_studio_postgres'"))
            await connection.execute(text("ALTER TABLE alembic_version RENAME TO alembic_version_hidden"))
        try:
            with pytest.raises(RuntimeError, match="schema is missing"):
                await database.check_schema()
        finally:
            async with database.engine.begin() as connection:
                await connection.execute(text("ALTER TABLE alembic_version_hidden RENAME TO alembic_version"))
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_database_task_search_keyset_survives_deleted_boundary(database: StudioDatabase) -> None:
    from relayna_studio.search import StudioTaskSearchQuery

    await PostgresServiceRegistryStore(database).create(service_record())
    store = PostgresStudioSearchStore(database)
    for index in range(6):
        await store.set_task_document(
            StudioTaskSearchDocument(
                service_id="payments-api",
                service_name="Payments",
                environment="production",
                task_id=f"keyset-{index}",
                status="failed" if index != 4 else "running",
                last_seen_at="2026-09-12T10:00:00Z",
                detail_path=f"/studio/tasks/payments-api/keyset-{index}",
                expires_at="2000-01-01T00:00:00Z" if index == 5 else None,
            )
        )
    query = StudioTaskSearchQuery(service_id="payments-api", status="failed", limit=2)
    first, cursor = await store._search_task_page(query)
    assert [item.task_id for item in first] == ["keyset-3", "keyset-2"]
    assert cursor
    await store.delete_task_documents([first[-1].document_id])
    second, end = await store._search_task_page(query.model_copy(update={"cursor": cursor}))
    assert [item.task_id for item in second] == ["keyset-1", "keyset-0"]
    assert end is None
