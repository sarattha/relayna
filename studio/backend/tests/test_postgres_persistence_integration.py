from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, ConnectError, ConnectTimeout, MockTransport
from redis.asyncio import Redis
from relayna_studio import app as studio_app_module
from relayna_studio import backfill as backfill_module
from relayna_studio._chamber_connection import _ConnectionStore, _ConnectionUpdate
from relayna_studio._incident_groups import _dimensions, _group_id
from relayna_studio._incident_store import _IncidentStore
from relayna_studio._incidents import _create_incident_router
from relayna_studio.app import create_studio_app, get_studio_runtime
from relayna_studio.audit_context import reset_actor_user_id, set_actor_user_id
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
    _incident_history,
    _json_model,
    _optional_text,
    audit_log,
    events,
    health_history,
    metadata,
    notification_deliveries,
    operator_settings,
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
from relayna_studio.logs import LokiLogProvider, StudioLogQueryService, create_studio_logs_router
from relayna_studio.registry import (
    DuplicateServiceError,
    LokiLogConfig,
    ServiceNotFoundError,
    ServiceRecord,
    ServiceRegistryService,
    StudioOutboundUrlPolicy,
)
from relayna_studio.search import (
    StudioRetentionWorker,
    StudioSearchService,
    StudioServiceSearchDocument,
    StudioTaskSearchDocument,
)
from sqlalchemy import delete, func, select, text, update
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from relayna.api import AliasConfigSummary, CapabilityDocument, CapabilityServiceMetadata
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


@pytest.mark.asyncio
async def test_chamber_connection_is_encrypted_durable_and_audited(database, monkeypatch):
    monkeypatch.setenv("RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    policy = StudioOutboundUrlPolicy(allowed_hosts=[".internal"])
    store = _ConnectionStore(database, "http://deployment.internal", "deployment-token", policy)
    await store.update(_ConnectionUpdate(mode="ui", url="http://chamber.internal", token="synthetic-private-token"))
    restarted = _ConnectionStore(database, "http://deployment.internal", "deployment-token", policy)
    snapshot = await restarted.current()
    assert restarted.token(snapshot) == "synthetic-private-token"
    async with database.sessions() as session:
        stored = await session.scalar(select(operator_settings.c.value))
        audit = (await session.execute(select(audit_log))).mappings().all()
    assert "synthetic-private-token" not in json.dumps(stored)
    assert "synthetic-private-token" not in json.dumps([dict(row) for row in audit], default=str)
    assert audit[-1]["action"] == "settings.chamber.update"
    await restarted.update(_ConnectionUpdate(mode="deployment"))
    assert (await restarted.current())["url"] == "http://deployment.internal"
    assert restarted.token(snapshot) == "synthetic-private-token"


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
        assert runtime.recovery_service is not None
        assert runtime.recovery_task is not None and not runtime.recovery_task.done()
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

    assert runtime.recovery_task is None

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
        with pytest.raises(RuntimeError, match="expected 0003_recovery"):
            await database.check_schema()
        async with database.engine.begin() as connection:
            await connection.execute(text("UPDATE alembic_version SET version_num = '0003_recovery'"))
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


@pytest.mark.asyncio
async def test_load_test_actions_audit_actor_and_outcome(
    database: StudioDatabase, redis: Redis, monkeypatch, tmp_path
) -> None:
    from pathlib import Path
    from types import SimpleNamespace

    import httpx
    from relayna_studio.audit_context import reset_actor_user_id, set_actor_user_id
    from relayna_studio.load_testing import _create_load_testing_router

    profiles = Path(__file__).resolve().parents[3] / "docs/examples/studio-chamber-profiles.json"
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_PROFILES_PATH", str(profiles))
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_URL", "http://chamber.internal")
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_TOKEN", "test-token")
    registry = SimpleNamespace(
        get_service=AsyncMock(return_value=SimpleNamespace(environment="staging", status="healthy"))
    )

    def upstream(request):
        if request.url.path.endswith("/plans"):
            return httpx.Response(200, json={"run_id": "plan"})
        if request.url.path.endswith("/cancel"):
            return httpx.Response(503)
        return httpx.Response(200, json={"job_id": "job", "state": "queued"})

    app = FastAPI()
    app.include_router(
        _create_load_testing_router(registry, redis, httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    )
    app.add_middleware(StudioMutationAuditMiddleware, database=database)
    actor = set_actor_user_id("load-test-admin")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://studio.test") as client:
            base = "/studio/services/translation-staging/load-tests"
            response = await client.post(
                f"{base}/plans",
                json={
                    "profile_id": "translate-text",
                    "inputs": {"text": "Hello", "language_target": "Thai"},
                    "vus": 1,
                    "iterations": 1,
                    "duration_seconds": 30,
                },
            )
            assert response.status_code == 201
            identity = response.json()["id"]
            assert (await client.post(f"{base}/{identity}/start")).status_code == 202
            assert (await client.post(f"{base}/{identity}/cancel")).status_code == 502
            assert (await client.get(base)).status_code == 200
    finally:
        reset_actor_user_id(actor)
    async with database.sessions() as session:
        rows = (await session.execute(select(audit_log).order_by(audit_log.c.id))).mappings().all()
    assert [row["action"] for row in rows] == [
        "load_test.plan.requested",
        "load_test.plan.succeeded",
        "load_test.start.requested",
        "load_test.start.succeeded",
        "load_test.cancel.requested",
        "load_test.cancel.failed",
    ]
    assert {row["actor_user_id"] for row in rows} == {"load-test-admin"}
    assert rows[0]["target_id"] == "translation-staging"
    assert rows[2]["target_id"] == f"translation-staging:{identity}"
    assert rows[4]["details"]["operation_id"] == rows[5]["details"]["operation_id"]
    assert rows[5]["details"]["status_code"] == 502
    assert all("Hello" not in json.dumps(row["details"]) for row in rows)


@pytest.mark.asyncio
async def test_imported_profiles_are_durable_scoped_and_idempotent(database):
    from fastapi import HTTPException
    from relayna_studio._profile_import import _ProfileStore

    await PostgresServiceRegistryStore(database).create(service_record())
    store = _ProfileStore(database)
    profile = {"id": "chamber-test", "name": "Imported", "max_vus": 8}
    await store.save("payments-api", "production", profile)
    await store.save("payments-api", "production", profile)
    assert await _ProfileStore(database).get_profiles("payments-api", "production") == [profile]
    assert await store.get_profiles("payments-api", "staging") == []
    with pytest.raises(HTTPException) as error:
        await store.save("payments-api", "production", {**profile, "max_vus": 9})
    assert error.value.status_code == 409
    await store.remove("payments-api", "staging", "chamber-test")
    assert await store.get_profiles("payments-api", "production") == [profile]
    await store.remove("payments-api", "production", "chamber-test")
    assert await store.get_profiles("payments-api", "production") == []


@pytest.mark.asyncio
async def test_profile_mutations_audit_without_request_values(database):
    from relayna_studio.audit_context import reset_actor_user_id, set_actor_user_id

    app = FastAPI()
    path = "/studio/services/payments-api/load-tests/profile-import"

    @app.post(path)
    async def save_profile():
        return {"id": "profile"}

    @app.delete(path + "/{profile_id}")
    async def remove_profile(profile_id: str):
        return {"removed": True}

    app.add_middleware(StudioMutationAuditMiddleware, database=database)
    actor = set_actor_user_id("profile-admin")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://studio.test") as client:
            assert (await client.post(path, json={"token": "never-log"})).status_code == 200
            assert (await client.delete(path + "/profile")).status_code == 200
    finally:
        reset_actor_user_id(actor)
    async with database.sessions() as session:
        rows = (await session.execute(select(audit_log).order_by(audit_log.c.id))).mappings().all()
    assert [row["action"] for row in rows] == [
        "load_profile.import.requested",
        "load_profile.import.succeeded",
        "load_profile.remove.requested",
        "load_profile.remove.succeeded",
    ]
    assert {row["actor_user_id"] for row in rows} == {"profile-admin"}
    assert "never-log" not in str(rows)


@pytest.mark.asyncio
async def test_service_deletion_revokes_profiles_before_id_reuse(database):
    from fastapi import HTTPException
    from relayna_studio._profile_import import _ProfileStore

    registry = PostgresServiceRegistryStore(database)
    original = service_record()
    other = service_record("other", base_url="https://other.example.test")
    await registry.create(original)
    await registry.create(other)
    store = _ProfileStore(database)
    profile = {"id": "chamber-old-target", "name": "Old approved target"}
    await store.save(original.service_id, original.environment, profile)
    await store.save(other.service_id, other.environment, profile)
    await registry.delete(original.service_id)
    assert await store.get_profiles(original.service_id, original.environment) == []
    with pytest.raises(HTTPException) as stale:
        await store.save(original.service_id, original.environment, profile)
    assert stale.value.status_code == 409
    await registry.create(original)
    assert await store.get_profiles(original.service_id, original.environment) == []
    assert await store.get_profiles(other.service_id, other.environment) == [profile]
    # A new explicit import after registration is supported.
    await store.save(original.service_id, original.environment, profile)
    assert await store.get_profiles(original.service_id, original.environment) == [profile]


@pytest.mark.asyncio
async def test_chamber_mutations_audit_actor_and_outcome_without_credentials(database):
    from relayna_studio.audit_context import reset_actor_user_id, set_actor_user_id

    app = FastAPI()
    path = "/studio/services/payments-api/load-tests/chamber/connection"

    @app.put(path)
    async def update_connection():
        return {"source": "ui"}

    app.add_middleware(StudioMutationAuditMiddleware, database=database)
    actor = set_actor_user_id("connection-admin")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://studio.test") as client:
            assert (await client.put(path, json={"token": "never-log-this-credential"})).status_code == 200
    finally:
        reset_actor_user_id(actor)
    async with database.sessions() as session:
        rows = (await session.execute(select(audit_log).order_by(audit_log.c.id))).mappings().all()
    assert [row["action"] for row in rows] == ["chamber.connection.requested", "chamber.connection.succeeded"]
    assert {row["actor_user_id"] for row in rows} == {"connection-admin"}
    assert {row["target_id"] for row in rows} == {"payments-api"}
    assert "never-log-this-credential" not in str(rows)


@pytest.mark.asyncio
async def test_incident_inbox_live_counts_durable_triage_split_merge_and_conflicts(database: StudioDatabase) -> None:
    registry = PostgresServiceRegistryStore(database)
    await registry.create(service_record())
    failures = [
        {
            "failure_id": str(i),
            "task_id": f"task-{i}",
            "queue_name": "payment-capture",
            "error_type": "TimeoutError",
            "error_message": f"Capture request {i} timed out",
            "failed_at": "2026-10-25T10:42:00Z",
        }
        for i in range(380)
    ]
    failures.append(
        {
            "failure_id": "other",
            "task_id": "other",
            "queue_name": "payment-capture",
            "error_type": "ConnectionError",
            "error_message": "Connection reset",
            "failed_at": None,
        }
    )

    async def read(**query: Any) -> dict[str, Any]:
        offset = int(query["cursor"] or 0)
        return {
            "items": failures[offset : offset + 50],
            "next_cursor": str(offset + 50) if offset + 50 < len(failures) else None,
        }

    federation = SimpleNamespace(
        registry_service=SimpleNamespace(list_services=registry.list_records),
        list_failed_tasks=AsyncMock(side_effect=read),
        search_max_concurrency=2,
    )
    app = FastAPI()
    app.include_router(_create_incident_router(federation, database))
    actor = set_actor_user_id("tenant:operator")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            result = (await client.get("/studio/incidents?limit=1")).json()
            assert result["complete"] is True
            assert result["total_incidents"] == 2
            assert result["next_offset"] == 1
            assert result["observed_failures"] == 381
            incident = result["items"][0]
            assert incident["failure_count"] == 380
            iid = incident["incident_id"]
            path = f"/studio/incidents/{iid}"
            detail = (await client.get(path)).json()
            assert len(detail["evidence"]) == 380
            assert detail["affected_tasks"] == 380
            assert (
                await client.post(
                    path, json={"revision": 0, "owner": "Payments", "status": "acknowledged", "note": "Investigating"}
                )
            ).status_code == 200
            assert (await _IncidentStore(database).get(iid))["owner"] == "Payments"
            assert (await client.post(path, json={"revision": 0, "status": "resolved"})).status_code == 409
            assert (await client.post(path, json={"revision": 1, "status": "resolved"})).status_code == 200
            failures.append({**failures[0], "failure_id": "new", "task_id": "new"})
            active = (await client.get("/studio/incidents")).json()
            assert all(item["incident_id"] != iid for item in active["items"])
            retained = (await client.get(path)).json()
            assert retained["status"] == "resolved" and retained["failure_count"] == 381
            assert (await client.post(path, json={"revision": 2, "status": "open"})).status_code == 200
            assert (
                await client.post(f"{path}/split", json={"revision": 3, "failure_ids": ["missing"]})
            ).status_code == 409
            split = await client.post(
                f"{path}/split", json={"revision": 3, "failure_ids": ["0", "1"], "note": "Separate deployment"}
            )
            assert split.status_code == 200
            split_id = split.json()["incident_id"]
            split_detail = (await client.get(f"/studio/incidents/{split_id}")).json()
            assert split_detail["failure_count"] == 2
            assert split_detail["owner"] is None and split_detail["status"] == "open"
            assert (await client.get(path)).json()["failure_count"] == 379
            assert (
                await client.post(
                    f"/studio/incidents/{split_id}/merge", json={"revision": 0, "target_id": iid, "target_revision": 4}
                )
            ).status_code == 200
            merged = (await client.get(path)).json()
            assert merged["failure_count"] == 381 and merged["owner"] == "Payments"
            assert merged["revision"] == 5
            source = (await client.get(f"/studio/incidents/{split_id}")).json()
            assert source["merged_into"] == iid and source["history"][0]["action"] == "merged"
            assert all(item["actor_user_id"] == "tenant:operator" for item in merged["history"])
            edits = await asyncio.gather(
                client.post(path, json={"revision": 5, "owner": "A"}),
                client.post(path, json={"revision": 5, "owner": "B"}),
            )
            assert sorted(response.status_code for response in edits) == [200, 409]
            await registry.delete("payments-api")
            absent = (await client.get(path)).json()
            assert absent["failure_count"] == 0 and absent["history"]
            assert absent["owner"] in {"A", "B"}
    finally:
        reset_actor_user_id(actor)
    async with database.sessions() as session:
        audit = (await session.execute(select(audit_log).where(audit_log.c.target_type == "incident"))).mappings().all()
    assert len(audit) == 8
    with pytest.raises(DBAPIError, match="append-only"):
        async with database.transaction() as session:
            await session.execute(update(_incident_history).values(action="rewritten"))


@pytest.mark.asyncio
async def test_incident_transactions_rollback_and_merge_scope(database: StudioDatabase, monkeypatch) -> None:
    from fastapi import HTTPException

    registry = PostgresServiceRegistryStore(database)
    await registry.create(service_record())
    await registry.create(service_record("other", base_url="https://other.example.test"))
    store = _IncidentStore(database)
    dimensions = [
        _dimensions("production", sid, {"failure_id": "one", "error_message": "Timeout", "queue_name": queue})
        for sid, queue in [("payments-api", "one"), ("payments-api", "two"), ("other", "one")]
    ]
    ids = [_group_id(dims) for dims in dimensions]
    await store.snapshot(dict(zip(ids, dimensions, strict=True)), environment=None, service_id=None)
    with pytest.raises(HTTPException) as wrong_scope:
        await store.merge(ids[0], 0, ids[2], 0, "")
    assert wrong_scope.value.status_code == 422
    with pytest.raises(HTTPException) as self_merge:
        await store.merge(ids[0], 0, ids[0], 0, "")
    assert self_merge.value.status_code == 422
    await store.merge(ids[0], 0, ids[1], 0, "same service")
    with pytest.raises(HTTPException) as cycle:
        await store.merge(ids[1], 1, ids[0], 1, "")
    assert cycle.value.status_code == 409

    async def fail_record(*args, **kwargs):
        raise RuntimeError("Audit unavailable")

    monkeypatch.setattr(store, "_record", fail_record)
    with pytest.raises(RuntimeError, match="Audit unavailable"):
        await store.change(ids[1], 1, {"owner": "Uncommitted"}, "test rollback")
    target = await store.get(ids[1])
    assert target["owner"] is None and target["revision"] == 1
    assert len(await store.history(ids[1])) == 1


@pytest.mark.asyncio
async def test_incident_read_snapshot_remains_consistent_during_split(database: StudioDatabase) -> None:
    await PostgresServiceRegistryStore(database).create(service_record())
    dims = _dimensions("production", "payments-api", {"failure_id": "one", "error_message": "Timeout"})
    iid = _group_id(dims)
    store = _IncidentStore(database)
    await store.snapshot({iid: dims}, environment=None, service_id=None)
    states_read = asyncio.Event()
    release_read = asyncio.Event()

    class PausingSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            if str(statement).startswith("SELECT studio_incidents.incident_id"):
                states_read.set()
                await release_read.wait()
            return result

    reader_database = StudioDatabase(DATABASE_URL)
    reader_database.sessions = async_sessionmaker(reader_database.engine, class_=PausingSession, expire_on_commit=False)
    reader = _IncidentStore(reader_database)
    read_task = asyncio.create_task(reader.snapshot({}, environment=None, service_id=None))
    try:
        await asyncio.wait_for(states_read.wait(), 2)
        split_task = asyncio.create_task(store.split(iid, 0, ["one"], {"one": iid, "two": iid}, "Separate"))
        # Allow an unlocked writer to commit between the two snapshot SELECTs.
        await asyncio.wait({split_task}, timeout=0.1)
        release_read.set()
        states, overrides = await asyncio.wait_for(read_task, 2)
        await asyncio.wait_for(split_task, 2)
        assert set(overrides.values()) <= set(states)
        subsequent_states, subsequent_overrides = await store.snapshot({}, environment=None, service_id=None)
        assert set(subsequent_overrides.values()) <= set(subsequent_states)
        assert subsequent_overrides[("production", "payments-api", "one")] != iid
    finally:
        release_read.set()
        await reader_database.dispose()


@pytest.mark.asyncio
async def test_doctor_reads_retained_events_and_bounded_outbox_age(database: StudioDatabase, redis: Redis) -> None:
    from types import SimpleNamespace

    from relayna_studio._doctor import _Doctor
    from relayna_studio.database import PostgresStudioEventStore, outbox

    store = PostgresStudioEventStore(database, redis, history_maxlen=3)
    registry = PostgresServiceRegistryStore(database)
    await registry.create(service_record())
    await store.insert_event(event_envelope())
    runtime = SimpleNamespace(
        database=database,
        redis=redis,
        event_store=store,
        failed_task_email_configured=False,
        health_refresh_worker=None,
        health_refresh_task=None,
        pull_sync_worker=None,
        pull_sync_task=None,
        retention_worker=None,
        retention_task=None,
    )
    doctor = _Doctor(runtime)
    sample = await doctor._recent("payments-api")
    assert len(sample) == 1 and sample[0].service_id == "payments-api"
    assert sample[0].task_id == "task-1"
    report = await doctor.studio()
    checks = {item.source: item for item in report.checks}
    assert checks["readiness"].state == "ok"
    assert checks["outbox"].value is not None
    async with database.transaction() as session:
        await session.execute(delete(outbox))
        await session.execute(
            outbox.insert(),
            [
                {
                    "channel": "test",
                    "dedupe_key": str(i),
                    "payload": {},
                    "created_at": datetime.now(UTC) - timedelta(minutes=2),
                    "available_at": datetime.now(UTC),
                }
                for i in range(105)
            ],
        )
    report = await doctor.studio()
    check = next(item for item in report.checks if item.source == "outbox")
    assert check.state == "stale" and check.value >= 120
    assert "100 pending rows sampled (more exist)" in check.detail
    async with database.transaction() as session:
        await session.execute(delete(outbox))
    check = next(item for item in (await doctor.studio()).checks if item.source == "outbox")
    assert check.state == "empty" and check.value is None


@contextmanager
def _contention_queries(database: StudioDatabase):
    queries: list[tuple[str, int]] = []

    def executed(_connection, cursor, statement, _parameters, _context, _executemany):
        queries.append((statement, cursor.rowcount))

    def committed(_connection):
        queries.append(("COMMIT", 0))

    sqlalchemy_event.listen(database.engine.sync_engine, "after_cursor_execute", executed)
    sqlalchemy_event.listen(database.engine.sync_engine, "commit", committed)
    try:
        yield queries
    finally:
        sqlalchemy_event.remove(database.engine.sync_engine, "after_cursor_execute", executed)
        sqlalchemy_event.remove(database.engine.sync_engine, "commit", committed)


async def _contention_search(database: StudioDatabase, redis: Redis):
    registry_store = PostgresServiceRegistryStore(database)
    await registry_store.create(service_record())
    registry = ServiceRegistryService(store=registry_store)
    store = PostgresStudioSearchStore(database)
    search = StudioSearchService(
        registry_service=registry, store=store, event_store=PostgresStudioEventStore(database, redis)
    )
    return registry, store, search


def _contention_task(task_id: str, *, expires_at: datetime | None = None) -> StudioTaskSearchDocument:
    return StudioTaskSearchDocument(
        service_id="payments-api",
        service_name="Payments",
        environment="production",
        task_id=task_id,
        detail_path=f"/studio/tasks/payments-api/{task_id}",
        expires_at=expires_at.isoformat() if expires_at else None,
    )


async def _contention_versions(database: StudioDatabase) -> dict[str, str]:
    async with database.sessions() as session:
        rows = (await session.execute(text("SELECT task_id, xmin::text FROM studio_task_search_projections"))).all()
    return dict(rows)


@pytest.mark.asyncio
async def test_contention_capability_refresh_skips_task_writes_and_locked_rows(database: StudioDatabase, redis: Redis):
    registry, store, _ = await _contention_search(database, redis)
    task = _contention_task("locked-task")
    await store.set_task_document(task)
    before = await _contention_versions(database)
    capabilities = CapabilityDocument(
        relayna_version="1.13.0",
        topology_kind="shared_tasks_shared_status",
        alias_config_summary=AliasConfigSummary(),
        supported_routes=["status.latest"],
        service_metadata=CapabilityServiceMetadata(
            capability_path="/relayna/capabilities",
            discovery_source="live",
            compatibility="capabilities_v1",
        ),
    )
    async with database.transaction() as session:
        await session.execute(
            select(task_projections).where(task_projections.c.task_id == task.task_id).with_for_update()
        )
        with _contention_queries(database) as queries:
            refreshed = await asyncio.wait_for(
                registry.store_capability_document("payments-api", capabilities, mark_healthy=True),
                timeout=2,
            )
    assert refreshed.capabilities == capabilities.model_dump(mode="json")
    assert refreshed.last_seen_at is not None
    assert not any(sql.startswith("UPDATE studio_task_search_projections") for sql, _ in queries)
    assert await _contention_versions(database) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes", [{"name": "Renamed"}, {"environment": "staging"}, {"name": "Renamed", "environment": "staging"}]
)
async def test_contention_real_metadata_edits_still_propagate(database: StudioDatabase, redis: Redis, changes):
    _, store, _ = await _contention_search(database, redis)
    registry = PostgresServiceRegistryStore(database)
    record = await registry.get("payments-api")
    assert record is not None
    task = _contention_task("metadata", expires_at=datetime.now(UTC) + timedelta(days=1))
    await store.set_task_document(task)
    stored_before = await store.get_task_document(task.document_id)
    assert stored_before is not None
    before = await _contention_versions(database)
    updated = record.model_copy(update=changes)
    await registry.update(record.service_id, updated)
    after = await _contention_versions(database)
    assert after != before
    document = await store.get_task_document(task.document_id)
    assert document == stored_before.model_copy(
        update={"service_name": updated.name, "environment": updated.environment}
    )
    with _contention_queries(database) as queries:
        await registry.update(record.service_id, updated)
    assert not any(sql.startswith("UPDATE studio_task_search_projections") for sql, _ in queries)
    assert await _contention_versions(database) == after


@pytest.mark.asyncio
async def test_contention_retention_does_not_read_every_live_task(database: StudioDatabase, redis: Redis):
    _, _, search = await _contention_search(database, redis)
    async with database.transaction() as session:
        await session.execute(
            text("""
            INSERT INTO studio_task_search_projections
                (service_id, task_id, service_name, environment, detail_path, expires_at)
            SELECT 'payments-api', 'live-' || n, 'Payments', 'production', '/task/' || n, now() + interval '1 day'
            FROM generate_series(1, 100) n
        """)
        )
    with _contention_queries(database) as queries:
        assert await search.prune_expired_task_documents() == 0
    assert len([sql for sql, _ in queries if sql != "COMMIT"]) <= 2
    async with database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(task_projections)) == 100


@pytest.mark.asyncio
async def test_contention_retention_batches_preserve_live_and_non_expiring_tasks(
    database: StudioDatabase, redis: Redis, monkeypatch
):
    _, store, search = await _contention_search(database, redis)
    monkeypatch.setattr(store, "_retention_batch_size", 3, raising=False)
    cutoff = datetime.now(UTC)
    for index in range(7):
        await store.set_task_document(
            _contention_task(f"expired-{index}", expires_at=cutoff if index == 0 else cutoff - timedelta(days=1))
        )
    for task in [
        _contention_task("no-expiry"),
        _contention_task("live", expires_at=datetime.now(UTC) + timedelta(days=1)),
    ]:
        await store.set_task_document(task)
    monkeypatch.setattr("relayna_studio.database._utcnow", lambda: cutoff)
    with _contention_queries(database) as queries:
        assert await search.prune_expired_task_documents() == 7
    deletes = [count for sql, count in queries if sql.startswith("DELETE FROM studio_task_search_projections")]
    assert sum(deletes) == 7 and max(deletes) <= 3
    assert len(deletes) == 3
    assert sum(sql == "COMMIT" for sql, _ in queries) == 3
    async with database.sessions() as session:
        assert set((await session.scalars(select(task_projections.c.task_id))).all()) == {"no-expiry", "live"}


@pytest.mark.asyncio
@pytest.mark.parametrize("extend_expiration", [False, True])
async def test_contention_retention_skips_locked_rows_and_rechecks_expiry(
    database: StudioDatabase, redis: Redis, extend_expiration
):
    _, store, search = await _contention_search(database, redis)
    expired = datetime.now(UTC) - timedelta(days=1)
    locked = _contention_task("locked", expires_at=expired)
    await store.set_task_document(locked)
    await store.set_task_document(_contention_task("available", expires_at=expired))
    async with database.transaction() as session:
        await session.execute(
            select(task_projections).where(task_projections.c.task_id == locked.task_id).with_for_update()
        )
        if extend_expiration:
            await session.execute(
                update(task_projections)
                .where(task_projections.c.task_id == locked.task_id)
                .values(expires_at=datetime.now(UTC) + timedelta(days=1))
            )
        assert await asyncio.wait_for(search.prune_expired_task_documents(), timeout=2) == 1
    assert await search.prune_expired_task_documents() == (0 if extend_expiration else 1)
    assert (await store.get_task_document(locked.document_id) is not None) == extend_expiration


@pytest.mark.asyncio
async def test_contention_coordinator_holds_lock_without_open_transaction(database: StudioDatabase):
    coordinator = PostgresAdvisoryCoordinator(database)
    async with coordinator.try_lock("contention-job") as acquired:
        assert acquired
        async with coordinator.try_lock("contention-job") as competing:
            assert not competing
            async with database.sessions() as session:
                rows = (
                    await session.execute(
                        text("""
                    SELECT state, xact_start FROM pg_stat_activity
                    WHERE datname = current_database() AND pid <> pg_backend_pid()
                      AND query LIKE 'SELECT pg_try_advisory_lock%'
                """)
                    )
                ).all()
            assert len(rows) == 2
            assert all(row.state == "idle" and row.xact_start is None for row in rows)
    async with coordinator.try_lock("contention-job") as acquired_again:
        assert acquired_again
    # Returning an AUTOCOMMIT connection must restore transactional behavior
    # for ordinary writes that later borrow it from the same pool.
    with pytest.raises(RuntimeError, match="rollback"):
        async with database.transaction() as session:
            await session.execute(audit_log.insert().values(action="rollback-probe", target_type="test"))
            raise RuntimeError("rollback")
    async with database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(audit_log).where(audit_log.c.action == "rollback-probe")
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_mode", ["exception", "cancel", "unlock_failure"])
async def test_contention_coordinator_releases_session_lock_on_every_exit(database: StudioDatabase, exit_mode):
    coordinator = PostgresAdvisoryCoordinator(database)
    entered = asyncio.Event()

    async def work():
        async with coordinator.try_lock("contention-exit") as acquired:
            assert acquired
            entered.set()
            if exit_mode == "cancel":
                await asyncio.Event().wait()
            if exit_mode == "exception":
                raise RuntimeError("worker failed")

    def fail_unlock(_connection, _cursor, statement, _parameters, _context, _executemany):
        if "pg_advisory_unlock(" in statement:
            raise RuntimeError("unlock failed")

    if exit_mode == "unlock_failure":
        sqlalchemy_event.listen(database.engine.sync_engine, "before_cursor_execute", fail_unlock)
    task = asyncio.create_task(work())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        if exit_mode == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if exit_mode == "cancel" else RuntimeError):
            await task
    finally:
        if exit_mode == "unlock_failure":
            sqlalchemy_event.remove(database.engine.sync_engine, "before_cursor_execute", fail_unlock)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with database.sessions() as session:
        count = await session.scalar(
            text("""
            SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
            WHERE a.datname = current_database() AND l.locktype = 'advisory'
        """)
        )
    assert count == 0
    assert database.engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_contention_coordinator_discards_uncertain_lock_acquisition(database: StudioDatabase):
    def fail_after_acquire(_connection, _cursor, statement, _parameters, _context, _executemany):
        if "pg_try_advisory_lock(" in statement:
            raise RuntimeError("acquire response lost")

    sqlalchemy_event.listen(database.engine.sync_engine, "after_cursor_execute", fail_after_acquire)
    try:
        with pytest.raises(RuntimeError, match="acquire response lost"):
            async with PostgresAdvisoryCoordinator(database).try_lock("contention-acquire"):
                pytest.fail("Worker must not run when lock acquisition fails")
    finally:
        sqlalchemy_event.remove(database.engine.sync_engine, "after_cursor_execute", fail_after_acquire)
    async with database.sessions() as session:
        assert (
            await session.scalar(
                text("""
                SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
                WHERE a.datname = current_database() AND l.locktype = 'advisory'
            """)
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ConnectError, ConnectTimeout])
async def test_contention_loki_failures_release_registry_connection(database: StudioDatabase, redis: Redis, failure):
    registry, _, _ = await _contention_search(database, redis)
    record = await registry.get_service("payments-api")
    await PostgresServiceRegistryStore(database).update(
        record.service_id,
        record.model_copy(
            update={
                "log_config": LokiLogConfig(
                    base_url="https://loki.example.test", service_selector_labels={"app": "payments"}
                )
            }
        ),
    )

    async def unavailable(request):
        assert database.engine.pool.checkedout() == 0
        raise failure("Loki unavailable", request=request)

    async with AsyncClient(transport=MockTransport(unavailable)) as outbound:
        provider = LokiLogProvider(
            http_client=outbound, outbound_policy=StudioOutboundUrlPolicy(allowed_hosts=["loki.example.test"])
        )
        app = FastAPI()
        app.include_router(
            create_studio_logs_router(
                log_query_service=StudioLogQueryService(registry_service=registry, providers={"loki": provider})
            )
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://studio.test") as client:
            for _ in range(3):
                assert (await client.get("/studio/tasks/payments-api/task-1/logs")).status_code == 502
                assert database.engine.pool.checkedout() == 0
