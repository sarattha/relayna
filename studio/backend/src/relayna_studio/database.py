from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    func,
    insert,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy import (
    cast as sql_cast,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from .audit_context import current_actor_user_id
from .auth import (
    StudioEntraConfig,
    StudioMember,
    StudioMemberStatus,
    StudioRole,
    StudioUserUpdate,
    _iso,
    _LoginTransaction,
    _now,
    _Session,
    _token_digest,
)
from .events import (
    StudioControlPlaneEvent,
    StudioEventEnvelope,
    StudioEventListResponse,
    StudioServiceActivitySnapshot,
    _event_dedupe_key,
    _parse_timestamp,
)
from .failed_task_notifications import FailedTaskEmailRuntimeSettings, _normalize_batch_wait_seconds
from .health import StudioServiceHealthDocument
from .registry import DuplicateServiceError, ServiceNotFoundError, ServiceRecord
from .search import StudioServiceSearchDocument, StudioTaskSearchDocument

LOGGER = logging.getLogger(__name__)
EXPECTED_SCHEMA_REVISION = "0001_studio_postgres"

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)
json_type = JSON().with_variant(JSONB(), "postgresql")

# Tables are declared explicitly rather than through an ORM so persistence
# methods expose the same Pydantic contracts already used by Studio routes.
services = Table(
    "studio_services",
    metadata,
    # Stable external identifier, not a generated database identity.
    # Text lengths are intentionally bounded for index safety.
    Column("service_id", String(255), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("base_url", Text, nullable=False),
    Column("environment", String(128), nullable=False),
    Column("tags", json_type, nullable=False, server_default=text("'[]'")),
    Column("auth_mode", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("capabilities", json_type),
    Column("last_seen_at", DateTime(timezone=True)),
    Column("log_config", json_type),
    Column("metrics_config", json_type),
    Column("trace_config", json_type),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
    Column("deleted_at", DateTime(timezone=True)),
)
Index("ix_studio_services_environment_status", services.c.environment, services.c.status)
Index("ix_studio_services_base_url", services.c.base_url)
Index(
    "uq_studio_services_active_environment_base_url",
    services.c.environment,
    services.c.base_url,
    unique=True,
    postgresql_where=services.c.deleted_at.is_(None),
)

members = Table(
    "studio_members",
    metadata,
    Column("user_id", String(600), primary_key=True),
    Column("tenant_id", String(255), nullable=False),
    Column("object_id", String(255), nullable=False),
    Column("email", String(320), nullable=False),
    Column("display_name", String(255), nullable=False),
    Column("role", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("last_sign_in_at", DateTime(timezone=True)),
    UniqueConstraint("tenant_id", "object_id", name="uq_studio_members_tenant_object"),
    CheckConstraint("role IN ('admin', 'readonly')", name="member_role"),
    CheckConstraint("status IN ('pending', 'active', 'blocked')", name="member_status"),
)
Index("ix_studio_members_status_role", members.c.status, members.c.role)
Index("ix_studio_members_email", members.c.email)

operator_settings = Table(
    "studio_operator_settings",
    metadata,
    Column("setting_key", String(255), primary_key=True),
    Column("value", json_type, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_by", String(600)),
)

events = Table(
    "studio_events",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("service_id", String(255), ForeignKey("studio_services.service_id", ondelete="CASCADE"), nullable=False),
    Column("ingest_method", String(32), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("dedupe_key", Text, nullable=False, unique=True),
    Column("out_of_order", Boolean, nullable=False, server_default=text("false")),
    Column("task_id", String(255), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("source_kind", String(64), nullable=False),
    Column("component", String(255)),
    Column("event_timestamp", DateTime(timezone=True)),
    Column("event_timestamp_text", Text),
    Column("event_id", String(255)),
    Column("correlation_id", String(255)),
    Column("parent_task_id", String(255)),
    Column("status", String(128)),
    Column("stage", String(128)),
    Column("payload", json_type, nullable=False),
    Column("expires_at", DateTime(timezone=True)),
)
Index("ix_studio_events_service_time", events.c.service_id, events.c.event_timestamp.desc(), events.c.id.desc())
Index("ix_studio_events_service_task_time", events.c.service_id, events.c.task_id, events.c.id.desc())
Index(
    "ix_studio_events_service_effective_time",
    events.c.service_id,
    func.coalesce(events.c.event_timestamp, events.c.ingested_at).desc(),
    events.c.id.desc(),
)
Index(
    "ix_studio_events_task_effective_time",
    events.c.service_id,
    events.c.task_id,
    func.coalesce(events.c.event_timestamp, events.c.ingested_at).desc(),
    events.c.id.desc(),
)
Index("ix_studio_events_correlation_time", events.c.correlation_id, events.c.event_timestamp.desc())
Index("ix_studio_events_status_stage_time", events.c.status, events.c.stage, events.c.event_timestamp.desc())
Index("ix_studio_events_retention", events.c.expires_at, postgresql_where=events.c.expires_at.is_not(None))
Index(
    "uq_studio_events_service_source_event_id",
    events.c.service_id,
    events.c.source_kind,
    events.c.event_id,
    unique=True,
    postgresql_where=events.c.event_id.is_not(None),
)

task_projections = Table(
    "studio_task_search_projections",
    metadata,
    Column("service_id", String(255), ForeignKey("studio_services.service_id", ondelete="CASCADE"), primary_key=True),
    Column("task_id", String(255), primary_key=True),
    Column("service_name", String(255), nullable=False),
    Column("environment", String(128), nullable=False),
    Column("correlation_id", String(255)),
    Column("status", String(128)),
    Column("stage", String(128)),
    Column("first_seen_at", DateTime(timezone=True)),
    Column("last_seen_at", DateTime(timezone=True)),
    Column("latest_event_type", String(128)),
    Column("latest_event_at", DateTime(timezone=True)),
    Column("latest_ingested_at", DateTime(timezone=True)),
    Column("detail_path", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True)),
    Column("source", String(32)),
)
Index("ix_studio_tasks_service_last_seen", task_projections.c.service_id, task_projections.c.last_seen_at.desc())
Index("ix_studio_tasks_task", task_projections.c.task_id)
Index("ix_studio_tasks_correlation", task_projections.c.correlation_id)
Index(
    "ix_studio_tasks_status_stage_time",
    task_projections.c.status,
    task_projections.c.stage,
    task_projections.c.last_seen_at.desc(),
)
Index(
    "ix_studio_tasks_retention",
    task_projections.c.expires_at,
    postgresql_where=task_projections.c.expires_at.is_not(None),
)

service_projections = Table(
    "studio_service_search_projections",
    metadata,
    Column("service_id", String(255), ForeignKey("studio_services.service_id", ondelete="CASCADE"), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("environment", String(128), nullable=False),
    Column("tags", json_type, nullable=False),
    Column("status", String(32), nullable=False),
    Column("health_status", String(32)),
    Column("base_url", Text, nullable=False),
    Column("auth_mode", String(64), nullable=False),
    Column("last_seen_at", DateTime(timezone=True)),
)
Index(
    "ix_studio_service_search_filters",
    service_projections.c.environment,
    service_projections.c.status,
    service_projections.c.health_status,
)
Index("ix_studio_service_search_tags_gin", service_projections.c.tags, postgresql_using="gin")

pull_cursors = Table(
    "studio_pull_ingestion_cursors",
    metadata,
    Column("service_id", String(255), ForeignKey("studio_services.service_id", ondelete="CASCADE"), primary_key=True),
    Column("cursor", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

health_current = Table(
    "studio_service_health_current",
    metadata,
    Column("service_id", String(255), ForeignKey("studio_services.service_id", ondelete="CASCADE"), primary_key=True),
    Column("overall_status", String(32), nullable=False),
    Column("last_checked_at", DateTime(timezone=True)),
    Column("document", json_type, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_studio_health_current_status", health_current.c.overall_status, health_current.c.updated_at.desc())

health_history = Table(
    "studio_service_health_history",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("service_id", String(255), ForeignKey("studio_services.service_id", ondelete="CASCADE"), nullable=False),
    Column("overall_status", String(32), nullable=False),
    Column("checked_at", DateTime(timezone=True), nullable=False),
    Column("document", json_type, nullable=False),
)
Index("ix_studio_health_history_service_time", health_history.c.service_id, health_history.c.checked_at.desc())
Index("ix_studio_health_history_status_time", health_history.c.overall_status, health_history.c.checked_at.desc())
Index("ix_studio_health_history_retention", health_history.c.checked_at)

notification_deliveries = Table(
    "studio_notification_deliveries",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("service_id", String(255), nullable=False),
    Column("failure_id", String(255), nullable=False),
    Column("state", String(32), nullable=False),
    Column("payload", json_type),
    Column("attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("last_error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("delivered_at", DateTime(timezone=True)),
    Column("dedupe_expires_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("service_id", "failure_id", name="uq_studio_notification_service_failure"),
)
Index("ix_studio_notification_state_created", notification_deliveries.c.state, notification_deliveries.c.created_at)
Index("ix_studio_notification_retention", notification_deliveries.c.dedupe_expires_at)

notification_batches = Table(
    "studio_notification_batches",
    metadata,
    Column("batch_key", String(255), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("items", json_type, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

audit_log = Table(
    "studio_operator_audit_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor_user_id", String(600)),
    Column("action", String(128), nullable=False),
    Column("target_type", String(128), nullable=False),
    Column("target_id", Text),
    Column("details", json_type, nullable=False, server_default=text("'{}'")),
)
Index("ix_studio_audit_time", audit_log.c.occurred_at.desc())
Index("ix_studio_audit_actor_time", audit_log.c.actor_user_id, audit_log.c.occurred_at.desc())
Index("ix_studio_audit_target_time", audit_log.c.target_type, audit_log.c.target_id, audit_log.c.occurred_at.desc())
Index("ix_studio_audit_action_time", audit_log.c.action, audit_log.c.occurred_at.desc())

outbox = Table(
    "studio_outbox",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("channel", Text, nullable=False),
    Column("payload", json_type, nullable=False),
    Column("dedupe_key", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("delivered_at", DateTime(timezone=True)),
    Column("attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("last_error", Text),
    UniqueConstraint("channel", "dedupe_key", name="uq_studio_outbox_channel_dedupe"),
)
Index("ix_studio_outbox_pending", outbox.c.available_at, outbox.c.id, postgresql_where=outbox.c.delivered_at.is_(None))
Index(
    "ix_studio_outbox_delivered_retention",
    outbox.c.delivered_at,
    postgresql_where=outbox.c.delivered_at.is_not(None),
)

migration_imports = Table(
    "studio_redis_imports",
    metadata,
    Column("source_fingerprint", String(64), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("counts", json_type, nullable=False),
    Column("checksum", String(64)),
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso_or_none(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def _dt(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return _parse_timestamp(value)


def _json_model(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


class StudioDatabase:
    def __init__(self, url: str, *, pool_size: int = 10, pool_max_overflow: int = 20) -> None:
        if not url.startswith(("postgresql+asyncpg://", "postgresql://")):
            raise RuntimeError("RELAYNA_STUDIO_DATABASE_URL must be a PostgreSQL URL.")
        async_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        self.url = async_url
        self.engine: AsyncEngine = create_async_engine(
            async_url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=pool_max_overflow,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions.begin() as session:
            yield session

    async def check_ready(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def check_schema(self) -> None:
        async with self.engine.connect() as connection:
            try:
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            except Exception as exc:
                raise RuntimeError("Studio PostgreSQL schema is missing; run 'alembic upgrade head'.") from exc
        if revision != EXPECTED_SCHEMA_REVISION:
            raise RuntimeError(
                f"Studio PostgreSQL schema is at {revision or 'no revision'}; expected {EXPECTED_SCHEMA_REVISION}."
            )

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def append_audit(
        self,
        *,
        action: str,
        target_type: str,
        target_id: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        async with self.transaction() as session:
            await _append_audit(
                session,
                action=action,
                target_type=target_type,
                target_id=target_id,
                details=details,
            )


async def _append_audit(
    session: AsyncSession,
    *,
    action: str,
    target_type: str,
    target_id: str | None,
    details: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
) -> None:
    await session.execute(
        insert(audit_log).values(
            actor_user_id=actor_user_id if actor_user_id is not None else current_actor_user_id(),
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
        )
    )


def _service_values(record: ServiceRecord) -> dict[str, Any]:
    return {
        "service_id": record.service_id,
        "name": record.name,
        "base_url": record.base_url,
        "environment": record.environment,
        "tags": list(record.tags),
        "auth_mode": record.auth_mode,
        "status": str(record.status),
        "capabilities": record.capabilities,
        "last_seen_at": record.last_seen_at,
        "log_config": _json_model(record.log_config),
        "metrics_config": _json_model(record.metrics_config),
        "trace_config": _json_model(record.trace_config),
        "updated_at": _utcnow(),
        "deleted_at": None,
    }


def _service_from_row(row: RowMapping) -> ServiceRecord:
    return ServiceRecord.model_validate(
        {
            "service_id": row["service_id"],
            "name": row["name"],
            "base_url": row["base_url"],
            "environment": row["environment"],
            "tags": row["tags"] or [],
            "auth_mode": row["auth_mode"],
            "status": row["status"],
            "capabilities": row["capabilities"],
            "last_seen_at": row["last_seen_at"],
            "log_config": row["log_config"],
            "metrics_config": row["metrics_config"],
            "trace_config": row["trace_config"],
        }
    )


async def _upsert_service_projection(session: AsyncSession, record: ServiceRecord) -> None:
    current_health = await session.scalar(
        select(service_projections.c.health_status).where(service_projections.c.service_id == record.service_id)
    )
    values = {
        "service_id": record.service_id,
        "name": record.name,
        "environment": record.environment,
        "tags": list(record.tags),
        "status": str(record.status),
        "health_status": current_health,
        "base_url": record.base_url,
        "auth_mode": record.auth_mode,
        "last_seen_at": record.last_seen_at,
    }
    await session.execute(
        pg_insert(service_projections)
        .values(**values)
        .on_conflict_do_update(index_elements=[service_projections.c.service_id], set_=values)
    )


class PostgresServiceRegistryStore:
    def __init__(self, database: StudioDatabase) -> None:
        self.database = database

    async def create(self, record: ServiceRecord) -> ServiceRecord:
        try:
            async with self.database.transaction() as session:
                existing = (
                    await session.execute(
                        select(services.c.deleted_at)
                        .where(services.c.service_id == record.service_id)
                        .with_for_update()
                    )
                ).one_or_none()
                if existing is not None and existing.deleted_at is None:
                    raise DuplicateServiceError(f"Service '{record.service_id}' is already registered.")
                if existing is None:
                    await session.execute(insert(services).values(**_service_values(record)))
                else:
                    await session.execute(
                        update(services)
                        .where(services.c.service_id == record.service_id)
                        .values(**_service_values(record))
                    )
                await _upsert_service_projection(session, record)
                await _append_audit(
                    session,
                    action="service.create",
                    target_type="service",
                    target_id=record.service_id,
                    details={"environment": record.environment, "base_url": record.base_url},
                )
        except IntegrityError as exc:
            if "environment" in str(exc.orig).lower() or "base_url" in str(exc.orig).lower():
                raise DuplicateServiceError(
                    f"A service is already registered for environment '{record.environment}' "
                    f"and base_url '{record.base_url}'."
                ) from exc
            raise DuplicateServiceError(f"Service '{record.service_id}' is already registered.") from exc
        return record

    async def list_records(self) -> list[ServiceRecord]:
        async with self.database.sessions() as session:
            result = await session.execute(
                select(services)
                .where(services.c.deleted_at.is_(None))
                .order_by(services.c.environment, func.lower(services.c.name), services.c.service_id)
            )
            return [_service_from_row(row) for row in result.mappings()]

    async def get(self, service_id: str) -> ServiceRecord | None:
        async with self.database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(services).where(services.c.service_id == service_id, services.c.deleted_at.is_(None))
                    )
                )
                .mappings()
                .one_or_none()
            )
            return _service_from_row(row) if row else None

    async def update(self, service_id: str, record: ServiceRecord) -> ServiceRecord:
        try:
            async with self.database.transaction() as session:
                result = await session.execute(
                    update(services)
                    .where(services.c.service_id == service_id, services.c.deleted_at.is_(None))
                    .values(**_service_values(record))
                )
                if getattr(result, "rowcount", 0) == 0:
                    raise ServiceNotFoundError(f"Service '{service_id}' was not found.")
                await _upsert_service_projection(session, record)
                await _append_audit(
                    session,
                    action="service.update",
                    target_type="service",
                    target_id=service_id,
                    details={
                        "environment": record.environment,
                        "base_url": record.base_url,
                        "status": str(record.status),
                    },
                )
        except IntegrityError as exc:
            raise DuplicateServiceError(
                f"A service is already registered for environment '{record.environment}' "
                f"and base_url '{record.base_url}'."
            ) from exc
        return record

    async def delete(self, service_id: str) -> None:
        async with self.database.transaction() as session:
            result = await session.execute(
                update(services)
                .where(services.c.service_id == service_id, services.c.deleted_at.is_(None))
                .values(deleted_at=_utcnow(), updated_at=_utcnow())
            )
            if getattr(result, "rowcount", 0) == 0:
                raise ServiceNotFoundError(f"Service '{service_id}' was not found.")
            await session.execute(delete(service_projections).where(service_projections.c.service_id == service_id))
            await session.execute(delete(task_projections).where(task_projections.c.service_id == service_id))
            await _append_audit(session, action="service.delete", target_type="service", target_id=service_id)


def _event_from_row(row: RowMapping) -> StudioControlPlaneEvent:
    return StudioControlPlaneEvent.model_validate(
        {
            "service_id": row["service_id"],
            "ingest_method": row["ingest_method"],
            "ingested_at": _iso_or_none(row["ingested_at"]),
            "dedupe_key": row["dedupe_key"],
            "out_of_order": row["out_of_order"],
            "task_id": row["task_id"],
            "event_type": row["event_type"],
            "source_kind": row["source_kind"],
            "component": row["component"],
            "timestamp": row["event_timestamp_text"],
            "event_id": row["event_id"],
            "correlation_id": row["correlation_id"],
            "parent_task_id": row["parent_task_id"],
            "payload": row["payload"] or {},
        }
    )


async def _upsert_task_projection(
    session: AsyncSession,
    event: StudioControlPlaneEvent,
    *,
    service_name: str,
    environment: str,
    ttl_seconds: int,
) -> None:
    row = (
        (
            await session.execute(
                select(task_projections).where(
                    task_projections.c.service_id == event.service_id,
                    task_projections.c.task_id == event.task_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    event_at = _dt(event.timestamp) or _dt(event.ingested_at) or _utcnow()
    ingested_at = _dt(event.ingested_at) or _utcnow()
    first_seen = min(row["first_seen_at"], event_at) if row and row["first_seen_at"] else event_at
    last_seen = max(row["last_seen_at"], event_at) if row and row["last_seen_at"] else event_at
    current_latest = row["latest_event_at"] if row else None
    use_event_as_latest = current_latest is None or event_at >= current_latest
    values = {
        "service_id": event.service_id,
        "task_id": event.task_id,
        "service_name": service_name,
        "environment": environment,
        "correlation_id": event.correlation_id or (row["correlation_id"] if row else None),
        "status": _optional_text(event.payload.get("status")) or (row["status"] if row else None),
        "stage": _optional_text(event.payload.get("stage")) or (row["stage"] if row else None),
        "first_seen_at": first_seen,
        "last_seen_at": last_seen,
        "latest_event_type": event.event_type if use_event_as_latest or row is None else row["latest_event_type"],
        "latest_event_at": event_at if use_event_as_latest else current_latest,
        "latest_ingested_at": max(row["latest_ingested_at"], ingested_at)
        if row and row["latest_ingested_at"]
        else ingested_at,
        "detail_path": f"/studio/tasks/{event.service_id}/{event.task_id}",
        "expires_at": _utcnow() + timedelta(seconds=ttl_seconds),
        "source": row["source"] if row else None,
    }
    await session.execute(
        pg_insert(task_projections)
        .values(**values)
        .on_conflict_do_update(index_elements=[task_projections.c.service_id, task_projections.c.task_id], set_=values)
    )


class PostgresStudioEventStore:
    def __init__(
        self,
        database: StudioDatabase,
        redis: Redis,
        *,
        prefix: str = "studio:events",
        ttl_seconds: int | None = 86400,
        history_maxlen: int = 5000,
        task_index_ttl_seconds: int = 86400,
    ) -> None:
        self.database = database
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.history_maxlen = history_maxlen
        self.task_index_ttl_seconds = task_index_ttl_seconds

    def service_channel(self, service_id: str) -> str:
        return f"{self.prefix}:channel:service:{service_id}"

    def task_channel(self, service_id: str, task_id: str) -> str:
        return f"{self.prefix}:channel:task:{service_id}:{task_id}"

    async def insert_event(self, envelope: StudioEventEnvelope) -> bool:
        event = envelope.event
        dedupe_key = _event_dedupe_key(envelope.service_id, event)
        timestamp = _parse_timestamp(event.timestamp)
        now = _utcnow()
        expires_at = now + timedelta(seconds=self.ttl_seconds) if self.ttl_seconds else None
        payload = event.payload or {}
        try:
            async with self.database.transaction() as session:
                lock_id = int.from_bytes(
                    hashlib.sha256(f"event:{envelope.service_id}:{event.task_id}".encode()).digest()[:8],
                    "big",
                    signed=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(lock_id)))
                latest = await session.scalar(
                    select(func.max(events.c.event_timestamp)).where(
                        events.c.service_id == envelope.service_id,
                        events.c.task_id == event.task_id,
                    )
                )
                normalized = StudioControlPlaneEvent(
                    service_id=envelope.service_id,
                    ingest_method=envelope.ingest_method,
                    ingested_at=now.isoformat(),
                    dedupe_key=dedupe_key,
                    out_of_order=bool(timestamp is not None and latest is not None and timestamp < latest),
                    task_id=event.task_id,
                    event_type=event.event_type,
                    source_kind=event.source_kind,
                    component=event.component,
                    timestamp=event.timestamp,
                    event_id=event.event_id,
                    correlation_id=event.correlation_id,
                    parent_task_id=event.parent_task_id,
                    payload=payload,
                )
                await session.execute(
                    insert(events).values(
                        service_id=envelope.service_id,
                        ingest_method=str(envelope.ingest_method),
                        ingested_at=now,
                        dedupe_key=dedupe_key,
                        out_of_order=normalized.out_of_order,
                        task_id=event.task_id,
                        event_type=event.event_type,
                        source_kind=str(event.source_kind),
                        component=event.component,
                        event_timestamp=timestamp,
                        event_timestamp_text=event.timestamp,
                        event_id=event.event_id,
                        correlation_id=event.correlation_id,
                        parent_task_id=event.parent_task_id,
                        status=_optional_text(payload.get("status")),
                        stage=_optional_text(payload.get("stage")),
                        payload=payload,
                        expires_at=expires_at,
                    )
                )
                service_row = (
                    await session.execute(
                        select(services.c.name, services.c.environment).where(
                            services.c.service_id == envelope.service_id
                        )
                    )
                ).one()
                await _upsert_task_projection(
                    session,
                    normalized,
                    service_name=service_row.name,
                    environment=service_row.environment,
                    ttl_seconds=self.task_index_ttl_seconds,
                )
                serialized = normalized.model_dump(mode="json")
                await session.execute(
                    pg_insert(outbox)
                    .values(
                        [
                            {
                                "channel": self.service_channel(envelope.service_id),
                                "payload": serialized,
                                "dedupe_key": dedupe_key,
                            },
                            {
                                "channel": self.task_channel(envelope.service_id, event.task_id),
                                "payload": serialized,
                                "dedupe_key": dedupe_key,
                            },
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=[outbox.c.channel, outbox.c.dedupe_key])
                )
            return True
        except IntegrityError as exc:
            if "dedupe" in str(exc.orig).lower() or "unique" in str(exc.orig).lower():
                return False
            raise

    async def list_service_events(
        self,
        service_id: str,
        *,
        task_id: str | None = None,
        source_kind: Any | None = None,
        event_type: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> StudioEventListResponse:
        filters: list[Any] = [events.c.service_id == service_id]
        if task_id is not None:
            filters.append(events.c.task_id == task_id)
        if source_kind is not None:
            filters.append(events.c.source_kind == str(source_kind))
        if event_type is not None:
            filters.append(events.c.event_type == event_type)
        event_time = func.coalesce(events.c.event_timestamp, events.c.ingested_at)
        if from_time:
            filters.append(event_time >= cast(datetime, _parse_timestamp(from_time)))
        if to_time:
            filters.append(event_time <= cast(datetime, _parse_timestamp(to_time)))
        return await self._list(filters, before=before, limit=limit)

    async def list_task_events(
        self, service_id: str, task_id: str, *, before: str | None = None, limit: int = 100
    ) -> StudioEventListResponse:
        return await self.list_service_events(service_id, task_id=task_id, before=before, limit=limit)

    async def _list(
        self,
        filters: list[Any],
        *,
        before: str | None,
        limit: int,
    ) -> StudioEventListResponse:
        event_time = func.coalesce(events.c.event_timestamp, events.c.ingested_at)
        async with self.database.sessions() as session:
            if before:
                anchor = (
                    (
                        await session.execute(
                            select(
                                event_time.label("event_time"),
                                events.c.ingested_at,
                                events.c.dedupe_key,
                            ).where(events.c.dedupe_key == before)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                # Match the legacy Redis pager: an unknown cursor starts at the
                # first page instead of returning an empty result.
                if anchor is not None:
                    filters.append(
                        tuple_(event_time, events.c.ingested_at, events.c.dedupe_key)
                        < tuple_(anchor["event_time"], anchor["ingested_at"], anchor["dedupe_key"])
                    )
            rows = (
                (
                    await session.execute(
                        select(events)
                        .where(*filters)
                        .order_by(event_time.desc(), events.c.ingested_at.desc(), events.c.dedupe_key.desc())
                        .limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
        visible = rows[:limit]
        items = [_event_from_row(row) for row in visible]
        return StudioEventListResponse(
            count=len(items), items=items, next_cursor=items[-1].dedupe_key if len(rows) > limit and items else None
        )

    async def get_pull_cursor(self, service_id: str) -> str | None:
        async with self.database.sessions() as session:
            return await session.scalar(select(pull_cursors.c.cursor).where(pull_cursors.c.service_id == service_id))

    async def set_pull_cursor(self, service_id: str, cursor: str) -> None:
        async with self.database.transaction() as session:
            await session.execute(
                pg_insert(pull_cursors)
                .values(service_id=service_id, cursor=cursor, updated_at=_utcnow())
                .on_conflict_do_update(
                    index_elements=[pull_cursors.c.service_id],
                    set_={"cursor": cursor, "updated_at": _utcnow()},
                )
            )

    async def get_service_activity_snapshot(self, service_id: str) -> StudioServiceActivitySnapshot:
        async with self.database.sessions() as session:
            row = (
                await session.execute(
                    select(
                        func.max(events.c.event_timestamp).filter(events.c.source_kind == "status").label("status_at"),
                        func.max(events.c.event_timestamp)
                        .filter(events.c.source_kind == "observation")
                        .label("observation_at"),
                        func.max(events.c.ingested_at).label("ingested_at"),
                    ).where(events.c.service_id == service_id)
                )
            ).one()
        return StudioServiceActivitySnapshot(
            service_id=service_id,
            latest_status_event_at=_iso_or_none(row.status_at),
            latest_observation_event_at=_iso_or_none(row.observation_at),
            latest_ingested_at=_iso_or_none(row.ingested_at),
        )

    async def prune_expired(self) -> int:
        now = _utcnow()
        async with self.database.transaction() as session:
            result = await session.execute(delete(events).where(events.c.expires_at <= now))
            await session.execute(
                delete(notification_deliveries).where(notification_deliveries.c.dedupe_expires_at <= now)
            )
            await session.execute(delete(notification_batches).where(notification_batches.c.expires_at <= now))
            await session.execute(
                delete(outbox).where(
                    outbox.c.delivered_at.is_not(None),
                    outbox.c.delivered_at <= now - timedelta(days=7),
                )
            )
            await session.execute(delete(health_history).where(health_history.c.checked_at <= now - timedelta(days=30)))
            return int(getattr(result, "rowcount", 0) or 0)


class PostgresStudioSearchStore:
    def __init__(self, database: StudioDatabase) -> None:
        self.database = database

    async def task_index_is_empty(self) -> bool:
        async with self.database.sessions() as session:
            return not bool(await session.scalar(select(task_projections.c.service_id).limit(1)))

    async def get_task_document(self, document_id: str) -> StudioTaskSearchDocument | None:
        from .search import _decode_cursor

        try:
            identity = _decode_cursor(document_id)
        except ValueError:
            return None
        async with self.database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(task_projections).where(
                            task_projections.c.service_id == identity.get("service_id"),
                            task_projections.c.task_id == identity.get("task_id"),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _task_from_row(row) if row else None

    async def set_task_document(self, document: StudioTaskSearchDocument) -> None:
        values = _task_values(document)
        async with self.database.transaction() as session:
            await session.execute(
                pg_insert(task_projections)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[task_projections.c.service_id, task_projections.c.task_id], set_=values
                )
            )

    async def delete_task_document(self, document_id: str) -> None:
        document = await self.get_task_document(document_id)
        if document is None:
            return
        async with self.database.transaction() as session:
            await session.execute(
                delete(task_projections).where(
                    task_projections.c.service_id == document.service_id,
                    task_projections.c.task_id == document.task_id,
                )
            )

    async def list_task_document_ids(self) -> set[str]:
        return await self._task_ids()

    async def list_task_document_ids_for_service(self, service_id: str) -> set[str]:
        return await self._task_ids(task_projections.c.service_id == service_id)

    async def list_task_document_ids_for_filter(self, field: str, value: str) -> set[str]:
        allowed = {
            "service_id": task_projections.c.service_id,
            "task_id": task_projections.c.task_id,
            "correlation_id": task_projections.c.correlation_id,
            "status": task_projections.c.status,
            "stage": task_projections.c.stage,
        }
        column = allowed.get(field)
        return await self._task_ids(column == value) if column is not None else set()

    async def _task_ids(self, *filters: Any) -> set[str]:
        from .search import _task_document_id

        async with self.database.sessions() as session:
            rows = (
                await session.execute(select(task_projections.c.service_id, task_projections.c.task_id).where(*filters))
            ).all()
        return {_task_document_id(row.service_id, row.task_id) for row in rows}

    async def get_service_document(self, service_id: str) -> StudioServiceSearchDocument | None:
        async with self.database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(service_projections).where(service_projections.c.service_id == service_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _service_projection_from_row(row) if row else None

    async def set_service_document(self, document: StudioServiceSearchDocument) -> None:
        values = _service_projection_values(document)
        async with self.database.transaction() as session:
            await session.execute(
                pg_insert(service_projections)
                .values(**values)
                .on_conflict_do_update(index_elements=[service_projections.c.service_id], set_=values)
            )

    async def delete_service_document(self, service_id: str) -> None:
        async with self.database.transaction() as session:
            await session.execute(delete(service_projections).where(service_projections.c.service_id == service_id))

    async def list_service_document_ids(self) -> set[str]:
        return await self._service_ids()

    async def list_service_document_ids_for_filter(self, field: str, value: str) -> set[str]:
        allowed = {
            "environment": service_projections.c.environment,
            "status": service_projections.c.status,
            "health": service_projections.c.health_status,
        }
        if field == "tag":
            predicate = service_projections.c.tags.op("@>")(sql_cast([value], JSONB))
        else:
            column = allowed.get(field)
            if column is None:
                return set()
            predicate = column == value
        return await self._service_ids(predicate)

    async def list_service_document_ids_for_token(self, token: str) -> set[str]:
        pattern = f"%{token.lower()}%"
        return await self._service_ids(
            or_(
                func.lower(service_projections.c.name).like(pattern),
                func.lower(service_projections.c.service_id).like(pattern),
                func.lower(service_projections.c.environment).like(pattern),
                func.lower(service_projections.c.base_url).like(pattern),
            )
        )

    async def _service_ids(self, *filters: Any) -> set[str]:
        async with self.database.sessions() as session:
            return set((await session.scalars(select(service_projections.c.service_id).where(*filters))).all())


def _task_values(document: StudioTaskSearchDocument) -> dict[str, Any]:
    return {
        "service_id": document.service_id,
        "task_id": document.task_id,
        "service_name": document.service_name,
        "environment": document.environment,
        "correlation_id": document.correlation_id,
        "status": document.status,
        "stage": document.stage,
        "first_seen_at": _dt(document.first_seen_at),
        "last_seen_at": _dt(document.last_seen_at),
        "latest_event_type": document.latest_event_type,
        "latest_event_at": _dt(document.latest_event_at),
        "latest_ingested_at": _dt(document.latest_ingested_at),
        "detail_path": document.detail_path,
        "expires_at": _dt(document.expires_at),
        "source": document.source,
    }


def _task_from_row(row: RowMapping) -> StudioTaskSearchDocument:
    return StudioTaskSearchDocument(
        service_id=row["service_id"],
        service_name=row["service_name"],
        environment=row["environment"],
        task_id=row["task_id"],
        correlation_id=row["correlation_id"],
        status=row["status"],
        stage=row["stage"],
        first_seen_at=_iso_or_none(row["first_seen_at"]),
        last_seen_at=_iso_or_none(row["last_seen_at"]),
        latest_event_type=row["latest_event_type"],
        latest_event_at=_iso_or_none(row["latest_event_at"]),
        latest_ingested_at=_iso_or_none(row["latest_ingested_at"]),
        detail_path=row["detail_path"],
        expires_at=_iso_or_none(row["expires_at"]),
        source=row["source"],
    )


def _service_projection_values(document: StudioServiceSearchDocument) -> dict[str, Any]:
    return {
        "service_id": document.service_id,
        "name": document.name,
        "environment": document.environment,
        "tags": document.tags,
        "status": document.status,
        "health_status": document.health_status,
        "base_url": document.base_url,
        "auth_mode": document.auth_mode,
        "last_seen_at": _dt(document.last_seen_at),
    }


def _service_projection_from_row(row: RowMapping) -> StudioServiceSearchDocument:
    return StudioServiceSearchDocument(
        service_id=row["service_id"],
        name=row["name"],
        environment=row["environment"],
        tags=row["tags"] or [],
        status=row["status"],
        health_status=row["health_status"],
        base_url=row["base_url"],
        auth_mode=row["auth_mode"],
        last_seen_at=_iso_or_none(row["last_seen_at"]),
    )


class PostgresStudioHealthStore:
    def __init__(self, database: StudioDatabase) -> None:
        self.database = database

    async def get_health(self, service_id: str) -> StudioServiceHealthDocument | None:
        async with self.database.sessions() as session:
            payload = await session.scalar(
                select(health_current.c.document).where(health_current.c.service_id == service_id)
            )
        return StudioServiceHealthDocument.model_validate(payload) if payload else None

    async def set_health(self, service_id: str, document: StudioServiceHealthDocument) -> StudioServiceHealthDocument:
        payload = document.model_dump(mode="json")
        checked_at = document.last_checked_at or _utcnow()
        values = {
            "service_id": service_id,
            "overall_status": str(document.overall_status),
            "last_checked_at": document.last_checked_at,
            "document": payload,
            "updated_at": _utcnow(),
        }
        async with self.database.transaction() as session:
            await session.execute(
                pg_insert(health_current)
                .values(**values)
                .on_conflict_do_update(index_elements=[health_current.c.service_id], set_=values)
            )
            await session.execute(
                insert(health_history).values(
                    service_id=service_id,
                    overall_status=str(document.overall_status),
                    checked_at=checked_at,
                    document=payload,
                )
            )
            await session.execute(
                update(service_projections)
                .where(service_projections.c.service_id == service_id)
                .values(health_status=str(document.overall_status))
            )
        return document


def _member_from_row(row: RowMapping) -> StudioMember:
    return StudioMember(
        user_id=row["user_id"],
        tenant_id=row["tenant_id"],
        object_id=row["object_id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        status=row["status"],
        created_at=_iso_or_none(row["created_at"]) or "",
        updated_at=_iso_or_none(row["updated_at"]) or "",
        last_sign_in_at=_iso_or_none(row["last_sign_in_at"]),
    )


class HybridStudioAuthStore:
    """PostgreSQL members plus Redis-only OIDC transactions and sessions."""

    def __init__(self, database: StudioDatabase, redis: Redis, *, prefix: str) -> None:
        self.database = database
        self.redis = redis
        self.prefix = prefix.rstrip(":")

    async def initialize(self, config: StudioEntraConfig) -> None:
        async with self.database.sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(members)
                .where(members.c.role == StudioRole.ADMIN.value, members.c.status == StudioMemberStatus.ACTIVE.value)
            )
        if not count and not config.admin_emails:
            raise RuntimeError(
                "Studio has no active administrator; configure RELAYNA_STUDIO_ENTRA_ADMIN_EMAILS and "
                "RELAYNA_STUDIO_ENTRA_ADMIN_OBJECT_IDS for bootstrap."
            )

    async def upsert_login(self, claims: dict[str, Any], config: StudioEntraConfig) -> StudioMember:
        object_id = str(claims["oid"]).strip().lower()
        tenant_id = str(claims["tid"]).strip().lower()
        user_id = f"{tenant_id}:{object_id}"
        email = str(claims["email"]).strip().lower()
        now = _now()
        async with self.database.transaction() as session:
            existing_row = (
                (await session.execute(select(members).where(members.c.user_id == user_id).with_for_update()))
                .mappings()
                .one_or_none()
            )
            if existing_row:
                current = _member_from_row(existing_row)
                role = current.role.value
                status = current.status.value
                created_at = _dt(current.created_at) or now
            else:
                bootstrap = email in config.admin_emails and object_id in config.admin_object_ids
                role = StudioRole.ADMIN.value if bootstrap else StudioRole.READONLY.value
                status = StudioMemberStatus.ACTIVE.value if bootstrap else StudioMemberStatus.PENDING.value
                created_at = now
            values = {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "object_id": object_id,
                "email": email,
                "display_name": str(claims.get("name") or email),
                "role": role,
                "status": status,
                "created_at": created_at,
                "updated_at": now,
                "last_sign_in_at": now,
            }
            await session.execute(
                pg_insert(members)
                .values(**values)
                .on_conflict_do_update(index_elements=[members.c.user_id], set_=values)
            )
            if not existing_row:
                await _append_audit(
                    session,
                    actor_user_id=user_id,
                    action="member.create",
                    target_type="member",
                    target_id=user_id,
                    details={"role": role, "status": status},
                )
        return StudioMember(
            user_id=user_id,
            tenant_id=tenant_id,
            object_id=object_id,
            email=email,
            display_name=values["display_name"],
            role=StudioRole(role),
            status=StudioMemberStatus(status),
            created_at=_iso(created_at),
            updated_at=_iso(now),
            last_sign_in_at=_iso(now),
        )

    async def get_member(self, user_id: str) -> StudioMember | None:
        async with self.database.sessions() as session:
            row = (
                (await session.execute(select(members).where(members.c.user_id == user_id.lower())))
                .mappings()
                .one_or_none()
            )
        return _member_from_row(row) if row else None

    async def list_members(self) -> list[StudioMember]:
        async with self.database.sessions() as session:
            rows = (
                (await session.execute(select(members).order_by(members.c.email, members.c.object_id))).mappings().all()
            )
        return [_member_from_row(row) for row in rows]

    async def update_member(
        self, user_id: str, update_request: StudioUserUpdate, *, actor_user_id: str
    ) -> StudioMember:
        async with self.database.transaction() as session:
            lock_id = int.from_bytes(hashlib.sha256(b"studio-active-admins").digest()[:8], "big", signed=True)
            await session.execute(select(func.pg_advisory_xact_lock(lock_id)))
            row = (
                (await session.execute(select(members).where(members.c.user_id == user_id).with_for_update()))
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise KeyError(user_id)
            current = _member_from_row(row)
            next_role = update_request.role or current.role
            next_status = update_request.status or current.status
            if user_id == actor_user_id and (
                next_role is not StudioRole.ADMIN or next_status is not StudioMemberStatus.ACTIVE
            ):
                raise ValueError("Administrators cannot demote or block themselves.")
            if (
                current.role is StudioRole.ADMIN
                and current.status is StudioMemberStatus.ACTIVE
                and (next_role is not StudioRole.ADMIN or next_status is not StudioMemberStatus.ACTIVE)
            ):
                active_admin_count = await session.scalar(
                    select(func.count())
                    .select_from(members)
                    .where(
                        members.c.role == StudioRole.ADMIN.value,
                        members.c.status == StudioMemberStatus.ACTIVE.value,
                    )
                )
                if active_admin_count is not None and active_admin_count <= 1:
                    raise ValueError("At least one active administrator must remain.")
            now = _utcnow()
            await session.execute(
                update(members)
                .where(members.c.user_id == user_id)
                .values(role=next_role.value, status=next_status.value, updated_at=now)
            )
            await _append_audit(
                session,
                actor_user_id=actor_user_id,
                action="member.update",
                target_type="member",
                target_id=user_id,
                details={
                    "previous_role": current.role.value,
                    "previous_status": current.status.value,
                    "role": next_role.value,
                    "status": next_status.value,
                },
            )
        return current.model_copy(update={"role": next_role, "status": next_status, "updated_at": _iso(now)})

    async def save_login(self, raw_token: str, transaction: _LoginTransaction, ttl: int) -> None:
        await self.redis.set(f"{self.prefix}:login:{_token_digest(raw_token)}", transaction.dump(), ex=ttl, nx=True)

    async def consume_login(self, raw_token: str) -> _LoginTransaction | None:
        raw = await self.redis.getdel(f"{self.prefix}:login:{_token_digest(raw_token)}")
        return _LoginTransaction.load(raw) if raw else None

    async def save_session(self, raw_token: str, session: _Session, ttl: int) -> None:
        await self.redis.set(f"{self.prefix}:session:{_token_digest(raw_token)}", session.dump(), ex=ttl)

    async def get_session(self, raw_token: str) -> _Session | None:
        raw = await self.redis.get(f"{self.prefix}:session:{_token_digest(raw_token)}")
        return _Session.load(raw) if raw else None

    async def delete_session(self, raw_token: str) -> None:
        await self.redis.delete(f"{self.prefix}:session:{_token_digest(raw_token)}")


class PostgresFailedTaskEmailSettingsStore:
    KEY = "failed_task_email"

    def __init__(
        self,
        database: StudioDatabase,
        *,
        default_enabled: bool = False,
        default_batch_wait_seconds: int = 0,
    ) -> None:
        self.database = database
        self.default_enabled = default_enabled
        self.default_batch_wait_seconds = _normalize_batch_wait_seconds(default_batch_wait_seconds)

    async def get(self) -> FailedTaskEmailRuntimeSettings:
        async with self.database.sessions() as session:
            value = await session.scalar(
                select(operator_settings.c.value).where(operator_settings.c.setting_key == self.KEY)
            )
        if not isinstance(value, dict):
            return FailedTaskEmailRuntimeSettings(
                enabled=self.default_enabled, batch_wait_seconds=self.default_batch_wait_seconds
            )
        return FailedTaskEmailRuntimeSettings(
            enabled=bool(value.get("enabled", self.default_enabled)),
            batch_wait_seconds=_normalize_batch_wait_seconds(
                value.get("batch_wait_seconds", self.default_batch_wait_seconds)
            ),
        )

    async def update(
        self, *, enabled: bool | None = None, batch_wait_seconds: int | None = None
    ) -> FailedTaskEmailRuntimeSettings:
        current = await self.get()
        next_settings = FailedTaskEmailRuntimeSettings(
            enabled=current.enabled if enabled is None else enabled,
            batch_wait_seconds=current.batch_wait_seconds
            if batch_wait_seconds is None
            else _normalize_batch_wait_seconds(batch_wait_seconds),
        )
        value = {"enabled": next_settings.enabled, "batch_wait_seconds": next_settings.batch_wait_seconds}
        async with self.database.transaction() as session:
            await session.execute(
                pg_insert(operator_settings)
                .values(
                    setting_key=self.KEY,
                    value=value,
                    updated_at=_utcnow(),
                    updated_by=current_actor_user_id(),
                )
                .on_conflict_do_update(
                    index_elements=[operator_settings.c.setting_key],
                    set_={"value": value, "updated_at": _utcnow(), "updated_by": current_actor_user_id()},
                )
            )
            await _append_audit(
                session,
                action="settings.failed_task_email.update",
                target_type="operator_setting",
                target_id=self.KEY,
                details=value,
            )
        return next_settings


class PostgresNotificationHistoryStore:
    def __init__(self, database: StudioDatabase, *, dedupe_ttl_seconds: int) -> None:
        self.database = database
        self.dedupe_ttl_seconds = dedupe_ttl_seconds

    async def is_notified(self, service_id: str, failure_id: str) -> bool:
        async with self.database.sessions() as session:
            return bool(
                await session.scalar(
                    select(notification_deliveries.c.id).where(
                        notification_deliveries.c.service_id == service_id,
                        notification_deliveries.c.failure_id == failure_id,
                        notification_deliveries.c.state == "delivered",
                        notification_deliveries.c.dedupe_expires_at > _utcnow(),
                    )
                )
            )

    async def mark_notified(self, service_id: str, failure_id: str, payload: dict[str, Any]) -> None:
        now = _utcnow()
        values = {
            "service_id": service_id,
            "failure_id": failure_id,
            "state": "delivered",
            "payload": payload,
            "attempt_count": 1,
            "last_error": None,
            "delivered_at": now,
            "dedupe_expires_at": now + timedelta(seconds=self.dedupe_ttl_seconds),
        }
        async with self.database.transaction() as session:
            await session.execute(
                pg_insert(notification_deliveries)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[notification_deliveries.c.service_id, notification_deliveries.c.failure_id],
                    set_=values,
                )
            )
            await _append_audit(
                session,
                actor_user_id="studio-notification-worker",
                action="notification.delivered",
                target_type="failed_task",
                target_id=f"{service_id}:{failure_id}",
            )

    async def load_pending(self) -> dict[str, Any]:
        async with self.database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(notification_batches).where(notification_batches.c.batch_key == "failed_task_email")
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["expires_at"] <= _utcnow():
            return {"started_at": _utcnow().isoformat(), "items": []}
        return {"started_at": row["started_at"].isoformat(), "items": row["items"] or []}

    async def save_pending(self, pending: dict[str, Any]) -> None:
        started_at = _dt(str(pending.get("started_at") or "")) or _utcnow()
        values = {
            "batch_key": "failed_task_email",
            "started_at": started_at,
            "items": pending.get("items", []),
            "expires_at": _utcnow() + timedelta(seconds=self.dedupe_ttl_seconds),
        }
        async with self.database.transaction() as session:
            await session.execute(
                pg_insert(notification_batches)
                .values(**values)
                .on_conflict_do_update(index_elements=[notification_batches.c.batch_key], set_=values)
            )

    async def clear_pending(self) -> None:
        async with self.database.transaction() as session:
            await session.execute(
                delete(notification_batches).where(notification_batches.c.batch_key == "failed_task_email")
            )


class PostgresOutboxRelay:
    def __init__(
        self,
        database: StudioDatabase,
        redis: Redis,
        *,
        interval_seconds: float = 0.25,
        batch_size: int = 100,
    ) -> None:
        self.database = database
        self.redis = redis
        self.interval_seconds = interval_seconds
        self.batch_size = batch_size
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def relay_once(self) -> int:
        delivered = 0
        async with self.database.transaction() as session:
            rows = (
                (
                    await session.execute(
                        select(outbox)
                        .where(outbox.c.delivered_at.is_(None), outbox.c.available_at <= _utcnow())
                        .order_by(outbox.c.id)
                        .limit(self.batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                try:
                    await self.redis.publish(row["channel"], json.dumps(row["payload"], separators=(",", ":")))
                except Exception as exc:
                    attempts = int(row["attempt_count"] or 0) + 1
                    retry_delay = min(60, 2 ** min(attempts, 6))
                    await session.execute(
                        update(outbox)
                        .where(outbox.c.id == row["id"])
                        .values(
                            attempt_count=attempts,
                            last_error=str(exc)[:2000],
                            available_at=_utcnow() + timedelta(seconds=retry_delay),
                        )
                    )
                    continue
                await session.execute(
                    update(outbox)
                    .where(outbox.c.id == row["id"])
                    .values(delivered_at=_utcnow(), attempt_count=int(row["attempt_count"] or 0) + 1, last_error=None)
                )
                delivered += 1
        return delivered

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.relay_once()
            except Exception:
                LOGGER.exception("Studio outbox relay iteration failed.")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue


class PostgresAdvisoryCoordinator:
    """Connection-scoped advisory locks for multi-replica periodic workers."""

    def __init__(self, database: StudioDatabase) -> None:
        self.database = database

    @asynccontextmanager
    async def try_lock(self, name: str) -> AsyncIterator[bool]:
        lock_id = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
        async with self.database.engine.connect() as connection:
            acquired = bool(await connection.scalar(select(func.pg_try_advisory_lock(lock_id))))
            try:
                yield acquired
            finally:
                if acquired:
                    await connection.execute(select(func.pg_advisory_unlock(lock_id)))


class AuditEntry(BaseModel):
    id: int
    occurred_at: datetime
    actor_user_id: str | None = None
    action: str
    target_type: str
    target_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AuditListResponse(BaseModel):
    count: int
    items: list[AuditEntry]


_FAILED_TASK_MUTATION = re.compile(
    r"^/studio/failed-tasks/(?P<service_id>[^/]+)/(?P<failure_id>[^/]+)"
    r"(?:/(?P<operation>mark-investigated|mark-uninvestigated|retry))?$"
)


class StudioMutationAuditMiddleware(BaseHTTPMiddleware):
    """Audit request and outcome around synchronous upstream mutations."""

    def __init__(self, app: Any, *, database: StudioDatabase) -> None:
        super().__init__(app)
        self.database = database

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        match = _FAILED_TASK_MUTATION.fullmatch(request.url.path)
        if match is None or request.method not in {"POST", "DELETE"}:
            return await call_next(request)
        operation = match.group("operation") or "delete"
        operation_id = str(uuid.uuid4())
        target_id = f"{match.group('service_id')}:{match.group('failure_id')}"
        base_details = {
            "operation_id": operation_id,
            "method": request.method,
            "path": request.url.path,
        }
        await self.database.append_audit(
            action=f"failed_task.{operation}.requested",
            target_type="failed_task",
            target_id=target_id,
            details=base_details,
        )
        try:
            response = await call_next(request)
        except Exception as exc:
            await self.database.append_audit(
                action=f"failed_task.{operation}.failed",
                target_type="failed_task",
                target_id=target_id,
                details={**base_details, "error_type": type(exc).__name__},
            )
            raise
        outcome = "succeeded" if response.status_code < 400 else "failed"
        await self.database.append_audit(
            action=f"failed_task.{operation}.{outcome}",
            target_type="failed_task",
            target_id=target_id,
            details={**base_details, "status_code": response.status_code},
        )
        return response


def create_studio_audit_router(database: StudioDatabase, *, prefix: str = "/studio/admin") -> APIRouter:
    router = APIRouter()

    @router.get(f"{prefix}/audit", response_model=AuditListResponse)
    async def list_audit_entries(
        actor_user_id: str | None = Query(default=None),
        action: str | None = Query(default=None),
        target_type: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> AuditListResponse:
        filters: list[Any] = []
        if actor_user_id:
            filters.append(audit_log.c.actor_user_id == actor_user_id)
        if action:
            filters.append(audit_log.c.action == action)
        if target_type:
            filters.append(audit_log.c.target_type == target_type)
        async with database.sessions() as session:
            rows = (
                (await session.execute(select(audit_log).where(*filters).order_by(audit_log.c.id.desc()).limit(limit)))
                .mappings()
                .all()
            )
        items = [AuditEntry.model_validate(dict(row)) for row in rows]
        return AuditListResponse(count=len(items), items=items)

    return router


def create_studio_probe_router(database: StudioDatabase | None, redis: Redis) -> APIRouter:
    router = APIRouter()

    @router.get("/livez", include_in_schema=False)
    @router.get("/healthz", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz", include_in_schema=False)
    async def readiness() -> dict[str, str]:
        if database is not None:
            await database.check_ready()
            await database.check_schema()
        await cast(Awaitable[bool], redis.ping())
        return {"status": "ready"}

    return router


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


__all__ = [
    "AuditEntry",
    "AuditListResponse",
    "EXPECTED_SCHEMA_REVISION",
    "HybridStudioAuthStore",
    "PostgresAdvisoryCoordinator",
    "PostgresFailedTaskEmailSettingsStore",
    "PostgresNotificationHistoryStore",
    "PostgresOutboxRelay",
    "PostgresServiceRegistryStore",
    "PostgresStudioEventStore",
    "PostgresStudioHealthStore",
    "PostgresStudioSearchStore",
    "StudioDatabase",
    "StudioMutationAuditMiddleware",
    "create_studio_audit_router",
    "create_studio_probe_router",
    "metadata",
]
