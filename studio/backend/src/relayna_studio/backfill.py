from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

from redis.asyncio import Redis
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .auth import StudioMember
from .database import (
    StudioDatabase,
    _dt,
    _optional_text,
    _service_projection_values,
    _service_values,
    _task_values,
    audit_log,
    events,
    health_current,
    members,
    migration_imports,
    notification_batches,
    notification_deliveries,
    operator_settings,
    pull_cursors,
    service_projections,
    services,
    task_projections,
)
from .events import StudioControlPlaneEvent
from .failed_task_notifications import _normalize_batch_wait_seconds
from .health import StudioServiceHealthDocument
from .registry import ServiceRecord
from .search import StudioServiceSearchDocument, StudioTaskSearchDocument


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _decode(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _safe_source_identity(redis_url: str) -> str:
    parsed = urlsplit(redis_url)
    host = parsed.hostname or "unknown"
    port = parsed.port or 6379
    database = parsed.path.strip("/") or "0"
    return f"{parsed.scheme}://{host}:{port}/{database}"


@dataclass(slots=True, frozen=True)
class BackfillPrefixes:
    registry: str = "studio:services"
    events: str = "studio:events"
    health: str = "studio:health"
    search: str = "studio:search"
    auth: str = "studio:auth"
    notifications: str = "studio:failed_task_email"


@dataclass(slots=True)
class RedisBackfillSnapshot:
    services: list[ServiceRecord]
    members: list[StudioMember]
    events: list[StudioControlPlaneEvent]
    pull_cursors: dict[str, str]
    health: dict[str, StudioServiceHealthDocument]
    task_projections: list[StudioTaskSearchDocument]
    service_projections: list[StudioServiceSearchDocument]
    notification_settings: dict[str, Any] | None
    notification_deliveries: list[tuple[str, str, str]]
    notification_batch: dict[str, Any] | None
    invalid: list[str]

    def tombstone_service_ids(self) -> set[str]:
        active = {item.service_id for item in self.services}
        referenced = (
            {item.service_id for item in self.events}
            | set(self.pull_cursors)
            | set(self.health)
            | {item.service_id for item in self.task_projections}
        )
        return referenced - active

    def counts(self) -> dict[str, int]:
        return {
            "services": len(self.services),
            "tombstone_services": len(self.tombstone_service_ids()),
            "members": len(self.members),
            "events": len(self.events),
            "pull_cursors": len(self.pull_cursors),
            "health": len(self.health),
            "task_projections": len(self.task_projections),
            "service_projections": len(self.service_projections),
            "notification_deliveries": len(self.notification_deliveries),
            "notification_settings": int(self.notification_settings is not None),
            "notification_batch": int(self.notification_batch is not None),
            "invalid": len(self.invalid),
        }

    def checksum(self) -> str:
        payload = {
            "services": [item.model_dump(mode="json") for item in sorted(self.services, key=lambda x: x.service_id)],
            "members": [item.model_dump(mode="json") for item in sorted(self.members, key=lambda x: x.user_id)],
            "events": [item.model_dump(mode="json") for item in sorted(self.events, key=lambda x: x.dedupe_key)],
            "pull_cursors": dict(sorted(self.pull_cursors.items())),
            "health": {key: value.model_dump(mode="json") for key, value in sorted(self.health.items())},
            "task_projections": [
                item.model_dump(mode="json")
                for item in sorted(self.task_projections, key=lambda x: (x.service_id, x.task_id))
            ],
            "service_projections": [
                item.model_dump(mode="json") for item in sorted(self.service_projections, key=lambda x: x.service_id)
            ],
            "notification_settings": self.notification_settings,
            "notification_deliveries": sorted(self.notification_deliveries),
            "notification_batch": self.notification_batch,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class RedisStudioBackfill:
    def __init__(
        self,
        *,
        redis: Redis,
        database: StudioDatabase,
        redis_url: str,
        prefixes: BackfillPrefixes | None = None,
        notification_dedupe_ttl_seconds: int = 604800,
    ) -> None:
        self.redis = redis
        self.database = database
        self.redis_url = redis_url
        self.prefixes = prefixes or BackfillPrefixes()
        self.notification_dedupe_ttl_seconds = notification_dedupe_ttl_seconds

    @property
    def source_fingerprint(self) -> str:
        source = {
            "redis": _safe_source_identity(self.redis_url),
            "prefixes": {
                "registry": self.prefixes.registry,
                "events": self.prefixes.events,
                "health": self.prefixes.health,
                "search": self.prefixes.search,
                "auth": self.prefixes.auth,
                "notifications": self.prefixes.notifications,
            },
        }
        return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()

    async def snapshot(self) -> RedisBackfillSnapshot:
        invalid: list[str] = []
        service_items = await self._models_from_set(
            f"{self.prefixes.registry}:all",
            lambda item: f"{self.prefixes.registry}:by-id:{item}",
            ServiceRecord,
            invalid,
        )
        member_items = await self._models_from_set(
            f"{self.prefixes.auth}:members",
            lambda item: f"{self.prefixes.auth}:member:{item.lower()}",
            StudioMember,
            invalid,
        )
        event_items = await self._scan_models(f"{self.prefixes.events}:event:*", StudioControlPlaneEvent, invalid)
        task_items = await self._scan_models(f"{self.prefixes.search}:task:doc:*", StudioTaskSearchDocument, invalid)
        service_projection_items = await self._scan_models(
            f"{self.prefixes.search}:service:doc:*", StudioServiceSearchDocument, invalid
        )
        self._validate_snapshot_invariants(service_items, member_items, event_items, invalid)
        await self._validate_event_history(invalid)

        cursors: dict[str, str] = {}
        cursor_prefix = f"{self.prefixes.events}:pull-cursor:"
        async for raw_key in self.redis.scan_iter(match=f"{cursor_prefix}*"):
            key = _decode(raw_key)
            value = await self.redis.get(key)
            if value is not None:
                cursors[key.removeprefix(cursor_prefix)] = _decode(value)

        health_items: dict[str, StudioServiceHealthDocument] = {}
        health_prefix = f"{self.prefixes.health}:"
        async for raw_key in self.redis.scan_iter(match=f"{health_prefix}*"):
            key = _decode(raw_key)
            payload = await self.redis.get(key)
            if payload is None:
                continue
            try:
                health_items[key.removeprefix(health_prefix)] = StudioServiceHealthDocument.model_validate_json(payload)
            except Exception:
                invalid.append(key)

        settings = await self._json_value(f"{self.prefixes.notifications}:settings", invalid)
        pending = await self._json_value(f"{self.prefixes.notifications}:pending", invalid)
        notified_prefix = f"{self.prefixes.notifications}:notified:"
        deliveries: list[tuple[str, str, str]] = []
        async for raw_key in self.redis.scan_iter(match=f"{notified_prefix}*"):
            key = _decode(raw_key)
            identity = key.removeprefix(notified_prefix)
            if ":" not in identity:
                invalid.append(key)
                continue
            service_id, failure_id = identity.split(":", 1)
            value = await self.redis.get(key)
            deliveries.append((service_id, failure_id, _decode(value) if value else _utcnow().isoformat()))

        return RedisBackfillSnapshot(
            services=service_items,
            members=member_items,
            events=event_items,
            pull_cursors=cursors,
            health=health_items,
            task_projections=task_items,
            service_projections=service_projection_items,
            notification_settings=settings,
            notification_deliveries=deliveries,
            notification_batch=pending,
            invalid=sorted(invalid),
        )

    async def run(self, *, validate_only: bool = False, allow_invalid: bool = False) -> dict[str, Any]:
        await self.database.check_ready()
        await self.database.check_schema()
        snapshot = await self.snapshot()
        if snapshot.invalid and not allow_invalid:
            raise RuntimeError("Redis backfill validation found malformed records: " + ", ".join(snapshot.invalid[:20]))
        checksum = snapshot.checksum()
        counts = snapshot.counts()
        if validate_only:
            return {
                "status": "validated",
                "source_fingerprint": self.source_fingerprint,
                "checksum": checksum,
                "counts": counts,
                "invalid_keys": snapshot.invalid,
            }

        async with self.database.transaction() as session:
            prior = (
                (
                    await session.execute(
                        select(migration_imports).where(
                            migration_imports.c.source_fingerprint == self.source_fingerprint
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if prior and prior["completed_at"] is not None:
                if prior["checksum"] != checksum:
                    raise RuntimeError(
                        "Redis source changed after its completed import; stop writers and restore the "
                        "cutover snapshot."
                    )
                return {
                    "status": "already_imported",
                    "source_fingerprint": self.source_fingerprint,
                    "checksum": checksum,
                    "counts": counts,
                    "invalid_keys": snapshot.invalid,
                }
            await session.execute(
                pg_insert(migration_imports)
                .values(
                    source_fingerprint=self.source_fingerprint,
                    started_at=_utcnow(),
                    counts=counts,
                )
                .on_conflict_do_update(
                    index_elements=[migration_imports.c.source_fingerprint],
                    set_={"started_at": _utcnow(), "counts": counts, "completed_at": None, "checksum": None},
                )
            )
            for record in snapshot.services:
                values = _service_values(record)
                await session.execute(
                    pg_insert(services)
                    .values(**values)
                    .on_conflict_do_update(index_elements=[services.c.service_id], set_=values)
                )
            active_service_ids = {record.service_id for record in snapshot.services}
            tombstone_service_ids = snapshot.tombstone_service_ids()
            for service_id in tombstone_service_ids:
                await session.execute(
                    pg_insert(services)
                    .values(
                        service_id=service_id,
                        name=service_id,
                        base_url=f"legacy-redis://{hashlib.sha256(service_id.encode()).hexdigest()}",
                        environment="legacy",
                        tags=[],
                        auth_mode="none",
                        status="unavailable",
                        deleted_at=_utcnow(),
                    )
                    .on_conflict_do_nothing(index_elements=[services.c.service_id])
                )
            service_ids = active_service_ids | tombstone_service_ids
            for member in snapshot.members:
                values = {
                    "user_id": member.user_id,
                    "tenant_id": member.tenant_id,
                    "object_id": member.object_id,
                    "email": member.email,
                    "display_name": member.display_name,
                    "role": member.role.value,
                    "status": member.status.value,
                    "created_at": _dt(member.created_at) or _utcnow(),
                    "updated_at": _dt(member.updated_at) or _utcnow(),
                    "last_sign_in_at": _dt(member.last_sign_in_at),
                }
                await session.execute(
                    pg_insert(members)
                    .values(**values)
                    .on_conflict_do_update(index_elements=[members.c.user_id], set_=values)
                )
            for item in snapshot.events:
                if item.service_id not in service_ids:
                    continue
                payload = item.payload or {}
                await session.execute(
                    pg_insert(events)
                    .values(
                        service_id=item.service_id,
                        ingest_method=str(item.ingest_method),
                        ingested_at=_dt(item.ingested_at) or _utcnow(),
                        dedupe_key=item.dedupe_key,
                        out_of_order=item.out_of_order,
                        task_id=item.task_id,
                        event_type=item.event_type,
                        source_kind=str(item.source_kind),
                        component=item.component,
                        event_timestamp=_dt(item.timestamp),
                        event_timestamp_text=item.timestamp,
                        event_id=item.event_id,
                        correlation_id=item.correlation_id,
                        parent_task_id=item.parent_task_id,
                        status=_optional_text(payload.get("status")),
                        stage=_optional_text(payload.get("stage")),
                        payload=payload,
                        expires_at=None,
                    )
                    .on_conflict_do_nothing(index_elements=[events.c.dedupe_key])
                )
            for service_id, cursor in snapshot.pull_cursors.items():
                if service_id in service_ids:
                    await session.execute(
                        pg_insert(pull_cursors)
                        .values(service_id=service_id, cursor=cursor, updated_at=_utcnow())
                        .on_conflict_do_update(
                            index_elements=[pull_cursors.c.service_id],
                            set_={"cursor": cursor, "updated_at": _utcnow()},
                        )
                    )
            for service_id, document in snapshot.health.items():
                if service_id not in service_ids:
                    continue
                values = {
                    "service_id": service_id,
                    "overall_status": str(document.overall_status),
                    "last_checked_at": document.last_checked_at,
                    "document": document.model_dump(mode="json"),
                    "updated_at": _utcnow(),
                }
                await session.execute(
                    pg_insert(health_current)
                    .values(**values)
                    .on_conflict_do_update(index_elements=[health_current.c.service_id], set_=values)
                )
            for document in snapshot.task_projections:
                if document.service_id in service_ids:
                    values = _task_values(document)
                    await session.execute(
                        pg_insert(task_projections)
                        .values(**values)
                        .on_conflict_do_update(
                            index_elements=[task_projections.c.service_id, task_projections.c.task_id], set_=values
                        )
                    )
            for document in snapshot.service_projections:
                if document.service_id in active_service_ids:
                    values = _service_projection_values(document)
                    await session.execute(
                        pg_insert(service_projections)
                        .values(**values)
                        .on_conflict_do_update(index_elements=[service_projections.c.service_id], set_=values)
                    )
            if snapshot.notification_settings is not None:
                value = {
                    "enabled": bool(snapshot.notification_settings.get("enabled", False)),
                    "batch_wait_seconds": _normalize_batch_wait_seconds(
                        snapshot.notification_settings.get("batch_wait_seconds", 0)
                    ),
                }
                await session.execute(
                    pg_insert(operator_settings)
                    .values(setting_key="failed_task_email", value=value, updated_at=_utcnow())
                    .on_conflict_do_update(
                        index_elements=[operator_settings.c.setting_key],
                        set_={"value": value, "updated_at": _utcnow()},
                    )
                )
            now = _utcnow()
            for service_id, failure_id, delivered_at in snapshot.notification_deliveries:
                delivered = _dt(delivered_at) or now
                values = {
                    "service_id": service_id,
                    "failure_id": failure_id,
                    "state": "delivered",
                    "attempt_count": 1,
                    "delivered_at": delivered,
                    "dedupe_expires_at": now + timedelta(seconds=self.notification_dedupe_ttl_seconds),
                }
                await session.execute(
                    pg_insert(notification_deliveries)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=[notification_deliveries.c.service_id, notification_deliveries.c.failure_id],
                        set_=values,
                    )
                )
            if snapshot.notification_batch is not None:
                started_at = _dt(str(snapshot.notification_batch.get("started_at") or "")) or now
                values = {
                    "batch_key": "failed_task_email",
                    "started_at": started_at,
                    "items": snapshot.notification_batch.get("items", []),
                    "expires_at": now + timedelta(seconds=self.notification_dedupe_ttl_seconds),
                }
                await session.execute(
                    pg_insert(notification_batches)
                    .values(**values)
                    .on_conflict_do_update(index_elements=[notification_batches.c.batch_key], set_=values)
                )
            await session.execute(
                insert(audit_log).values(
                    actor_user_id="studio-redis-backfill",
                    action="migration.redis_backfill",
                    target_type="database",
                    target_id=self.source_fingerprint,
                    details={"counts": counts, "checksum": checksum},
                )
            )
            await session.execute(
                update(migration_imports)
                .where(migration_imports.c.source_fingerprint == self.source_fingerprint)
                .values(completed_at=_utcnow(), counts=counts, checksum=checksum)
            )
        return {
            "status": "imported",
            "source_fingerprint": self.source_fingerprint,
            "checksum": checksum,
            "counts": counts,
            "invalid_keys": snapshot.invalid,
        }

    async def _models_from_set(self, set_key: str, key_builder: Any, model: Any, invalid: list[str]) -> list[Any]:
        members_raw = await cast(Awaitable[set[str | bytes]], self.redis.smembers(set_key))
        items: list[Any] = []
        for raw_identity in members_raw:
            identity = _decode(raw_identity)
            key = key_builder(identity)
            payload = await self.redis.get(key)
            if payload is None:
                invalid.append(key)
                continue
            try:
                items.append(model.model_validate_json(payload))
            except Exception:
                invalid.append(key)
        return items

    def _validate_snapshot_invariants(
        self,
        service_items: list[ServiceRecord],
        member_items: list[StudioMember],
        event_items: list[StudioControlPlaneEvent],
        invalid: list[str],
    ) -> None:
        service_ids: set[str] = set()
        environment_urls: set[tuple[str, str]] = set()
        for record in service_items:
            if record.service_id in service_ids:
                invalid.append(f"duplicate-service-id:{record.service_id}")
            service_ids.add(record.service_id)
            identity = (record.environment, record.base_url)
            if identity in environment_urls:
                invalid.append(f"duplicate-environment-base-url:{record.environment}:{record.base_url}")
            environment_urls.add(identity)
        dedupe_keys: set[str] = set()
        for event in event_items:
            if event.dedupe_key in dedupe_keys:
                invalid.append(f"duplicate-event-dedupe:{event.dedupe_key}")
            dedupe_keys.add(event.dedupe_key)
        if member_items and not any(
            item.role.value == "admin" and item.status.value == "active" for item in member_items
        ):
            invalid.append("studio:auth:active-admin-invariant")

    async def _validate_event_history(self, invalid: list[str]) -> None:
        patterns = (
            f"{self.prefixes.events}:service:*:history",
            f"{self.prefixes.events}:task:*:history",
        )
        for pattern in patterns:
            async for raw_key in self.redis.scan_iter(match=pattern):
                key = _decode(raw_key)
                dedupe_keys = await cast(Awaitable[list[str | bytes]], self.redis.lrange(key, 0, -1))
                for raw_dedupe in dedupe_keys:
                    dedupe_key = _decode(raw_dedupe)
                    event_key = f"{self.prefixes.events}:event:{dedupe_key}"
                    if not await self.redis.exists(event_key):
                        invalid.append(f"{key}->missing:{dedupe_key}")

    async def _scan_models(self, pattern: str, model: Any, invalid: list[str]) -> list[Any]:
        items: list[Any] = []
        async for raw_key in self.redis.scan_iter(match=pattern):
            key = _decode(raw_key)
            payload = await self.redis.get(key)
            if payload is None:
                continue
            try:
                items.append(model.model_validate_json(payload))
            except Exception:
                invalid.append(key)
        return items

    async def _json_value(self, key: str, invalid: list[str]) -> dict[str, Any] | None:
        payload = await self.redis.get(key)
        if payload is None:
            return None
        try:
            value = json.loads(_decode(payload))
        except (TypeError, ValueError):
            invalid.append(key)
            return None
        if not isinstance(value, dict):
            invalid.append(key)
            return None
        return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill retained Relayna Studio Redis state into PostgreSQL.")
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--allow-invalid", action="store_true")
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    redis = Redis.from_url(args.redis_url)
    database = StudioDatabase(args.database_url)
    try:
        result = await RedisStudioBackfill(redis=redis, database=database, redis_url=args.redis_url).run(
            validate_only=args.validate_only, allow_invalid=args.allow_invalid
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        await redis.aclose()
        await database.dispose()


def main() -> None:
    args = _parser().parse_args()
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
