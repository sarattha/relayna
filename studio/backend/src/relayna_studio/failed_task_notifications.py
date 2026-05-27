from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .federation import StudioFederationService
from .registry import OutboundUrlPolicyError, StudioOutboundUrlPolicy

LOGGER = logging.getLogger(__name__)
MAX_BATCH_WAIT_SECONDS = 604800


class FailedTaskEmailNotificationError(RuntimeError):
    """Raised when the failed-task email service rejects a notification."""


@dataclass(slots=True, frozen=True)
class FailedTaskEmailNotificationConfig:
    service_url: str
    api_key: str
    receivers: tuple[str, ...]
    interval_seconds: float = 30.0
    timeout_seconds: float = 5.0
    dedupe_ttl_seconds: int = 604800
    title_prefix: str = "[Relayna] Failed task"
    redis_prefix: str = "studio:failed_task_email"
    scan_limit: int = 200
    default_enabled: bool = False
    default_batch_wait_seconds: int = 0

    def __post_init__(self) -> None:
        if not self.service_url.strip():
            raise ValueError("service_url must be set.")
        if not self.api_key.strip():
            raise ValueError("api_key must be set.")
        if not self.receivers:
            raise ValueError("receivers must contain at least one email address.")
        if self.interval_seconds < 0:
            raise ValueError("interval_seconds must be greater than or equal to zero.")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero.")
        if self.dedupe_ttl_seconds <= 0:
            raise ValueError("dedupe_ttl_seconds must be greater than zero.")
        if self.scan_limit <= 0:
            raise ValueError("scan_limit must be greater than zero.")
        if self.default_batch_wait_seconds < 0 or self.default_batch_wait_seconds > MAX_BATCH_WAIT_SECONDS:
            raise ValueError("default_batch_wait_seconds must be between 0 and 604800.")


class FailedTaskEmailSettingsResponse(BaseModel):
    configured: bool
    enabled: bool
    batch_wait_seconds: int = Field(ge=0, le=MAX_BATCH_WAIT_SECONDS)
    max_batch_wait_seconds: int = MAX_BATCH_WAIT_SECONDS
    receivers: list[str] = Field(default_factory=list)


class FailedTaskEmailSettingsUpdate(BaseModel):
    enabled: bool | None = None
    batch_wait_seconds: int | None = Field(default=None, ge=0, le=MAX_BATCH_WAIT_SECONDS)


@dataclass(slots=True, frozen=True)
class FailedTaskEmailRuntimeSettings:
    enabled: bool
    batch_wait_seconds: int


class RedisFailedTaskEmailSettingsStore:
    def __init__(
        self,
        redis: Any,
        *,
        redis_prefix: str = "studio:failed_task_email",
        default_enabled: bool = False,
        default_batch_wait_seconds: int = 0,
    ) -> None:
        self._redis = redis
        self._settings_key = f"{redis_prefix}:settings"
        self._default_enabled = default_enabled
        self._default_batch_wait_seconds = _normalize_batch_wait_seconds(default_batch_wait_seconds)

    async def get(self) -> FailedTaskEmailRuntimeSettings:
        payload = await self._redis.get(self._settings_key)
        if not payload:
            return self._default()
        try:
            data = json.loads(payload.decode() if isinstance(payload, bytes) else str(payload))
        except (TypeError, ValueError):
            return self._default()
        return FailedTaskEmailRuntimeSettings(
            enabled=bool(data.get("enabled", self._default_enabled)),
            batch_wait_seconds=_normalize_batch_wait_seconds(
                data.get("batch_wait_seconds", self._default_batch_wait_seconds)
            ),
        )

    async def update(
        self,
        *,
        enabled: bool | None = None,
        batch_wait_seconds: int | None = None,
    ) -> FailedTaskEmailRuntimeSettings:
        current = await self.get()
        next_settings = FailedTaskEmailRuntimeSettings(
            enabled=current.enabled if enabled is None else enabled,
            batch_wait_seconds=current.batch_wait_seconds
            if batch_wait_seconds is None
            else _normalize_batch_wait_seconds(batch_wait_seconds),
        )
        await self._redis.set(
            self._settings_key,
            json.dumps(
                {
                    "enabled": next_settings.enabled,
                    "batch_wait_seconds": next_settings.batch_wait_seconds,
                    "updated_at": _utc_now(),
                }
            ),
        )
        return next_settings

    def _default(self) -> FailedTaskEmailRuntimeSettings:
        return FailedTaskEmailRuntimeSettings(
            enabled=self._default_enabled,
            batch_wait_seconds=self._default_batch_wait_seconds,
        )


class FailedTaskEmailClient:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        service_url: str,
        api_key: str,
        timeout_seconds: float,
        outbound_policy: StudioOutboundUrlPolicy | None = None,
    ) -> None:
        policy = outbound_policy or StudioOutboundUrlPolicy()
        try:
            policy.validate_url(service_url, label="Failed-task email service URL")
        except OutboundUrlPolicyError as exc:
            raise ValueError(str(exc)) from exc
        self._http_client = http_client
        self._service_url = service_url
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    async def send(self, *, receivers: tuple[str, ...], title: str, body: str) -> None:
        response = await self._http_client.post(
            self._service_url,
            headers={"X-API-Key": self._api_key},
            json={"receivers": list(receivers), "title": title, "body": body},
            timeout=self._timeout_seconds,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise FailedTaskEmailNotificationError(f"Failed-task email service returned HTTP {response.status_code}.")


@dataclass(slots=True)
class FailedTaskEmailNotificationService:
    federation_service: StudioFederationService
    redis: Any
    email_client: FailedTaskEmailClient
    config: FailedTaskEmailNotificationConfig
    settings_store: RedisFailedTaskEmailSettingsStore

    async def notify_new_failed_tasks(self) -> int:
        settings = await self.settings_store.get()
        if not settings.enabled:
            return 0
        lock_key = f"{self.config.redis_prefix}:batch:lock"
        lock_ttl = max(1, int(self.config.timeout_seconds + 30))
        acquired = await self.redis.set(lock_key, _utc_now(), nx=True, ex=lock_ttl)
        if not acquired:
            return 0
        try:
            return await self._notify_new_failed_tasks(settings)
        finally:
            await self.redis.delete(lock_key)

    async def _notify_new_failed_tasks(self, settings: FailedTaskEmailRuntimeSettings) -> int:
        payload = await self.federation_service.list_failed_tasks(
            investigation_status="unreviewed",
            limit=self.config.scan_limit,
        )
        pending = await self._load_pending_batch()
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            if await self._should_queue_item(item, pending):
                pending["items"].append(item)
        if not pending["items"]:
            await self._clear_pending_batch()
            return 0
        if settings.batch_wait_seconds == 0:
            return await self._send_immediate_notifications(pending["items"])
        started_at = _parse_datetime(str(pending["started_at"]))
        if datetime.now(UTC) < started_at + timedelta(seconds=settings.batch_wait_seconds):
            await self._save_pending_batch(pending)
            return 0
        return await self._send_batch_notification(pending["items"])

    async def _should_queue_item(self, item: dict[str, Any], pending: dict[str, Any]) -> bool:
        service_id = _string_field(item, "service_id")
        failure_id = _string_field(item, "failure_id")
        if not service_id or not failure_id:
            return False
        if await self._is_notified(service_id, failure_id):
            return False
        if _item_key(item) in {_item_key(existing) for existing in pending["items"]}:
            return False
        if not pending.get("started_at"):
            pending["started_at"] = _utc_now()
        return True

    async def _send_immediate_notifications(self, items: list[dict[str, Any]]) -> int:
        sent = 0
        remaining: list[dict[str, Any]] = []
        for item in items:
            service_id = _string_field(item, "service_id")
            failure_id = _string_field(item, "failure_id")
            if not service_id or not failure_id:
                continue
            title = build_failed_task_email_title(item, title_prefix=self.config.title_prefix)
            body = build_failed_task_email_body(item)
            try:
                await self.email_client.send(receivers=self.config.receivers, title=title, body=body)
            except Exception:
                LOGGER.exception("Failed-task email notification send failed.")
                remaining.append(item)
                continue
            await self._mark_notified(service_id, failure_id)
            sent += 1
        if remaining:
            await self._save_pending_batch({"started_at": _utc_now(), "items": remaining})
        else:
            await self._clear_pending_batch()
        return sent

    async def _send_batch_notification(self, items: list[dict[str, Any]]) -> int:
        valid_items = [
            item
            for item in items
            if _string_field(item, "service_id") is not None and _string_field(item, "failure_id") is not None
        ]
        if not valid_items:
            await self._clear_pending_batch()
            return 0
        title = build_failed_task_batch_email_title(valid_items, title_prefix=self.config.title_prefix)
        body = build_failed_task_batch_email_body(valid_items)
        try:
            await self.email_client.send(receivers=self.config.receivers, title=title, body=body)
        except Exception:
            LOGGER.exception("Failed-task email notification send failed.")
            await self._save_pending_batch({"started_at": _utc_now(), "items": valid_items})
            return 0
        for item in valid_items:
            service_id = _string_field(item, "service_id")
            failure_id = _string_field(item, "failure_id")
            if service_id and failure_id:
                await self._mark_notified(service_id, failure_id)
        await self._clear_pending_batch()
        return len(valid_items)

    async def _mark_notified(self, service_id: str, failure_id: str) -> None:
        await self.redis.set(
            self._notified_key(service_id, failure_id),
            _utc_now(),
            ex=self.config.dedupe_ttl_seconds,
        )

    async def _is_notified(self, service_id: str, failure_id: str) -> bool:
        return bool(await self.redis.get(self._notified_key(service_id, failure_id)))

    def _notified_key(self, service_id: str, failure_id: str) -> str:
        return f"{self.config.redis_prefix}:notified:{service_id}:{failure_id}"

    async def _load_pending_batch(self) -> dict[str, Any]:
        payload = await self.redis.get(self._pending_key())
        if not payload:
            return {"started_at": _utc_now(), "items": []}
        try:
            data = json.loads(payload.decode() if isinstance(payload, bytes) else str(payload))
        except (TypeError, ValueError):
            return {"started_at": _utc_now(), "items": []}
        items = [item for item in data.get("items", []) if isinstance(item, dict)]
        return {"started_at": str(data.get("started_at") or _utc_now()), "items": items}

    async def _save_pending_batch(self, pending: dict[str, Any]) -> None:
        await self.redis.set(
            self._pending_key(),
            json.dumps({"started_at": pending["started_at"], "items": pending["items"]}),
            ex=self.config.dedupe_ttl_seconds,
        )

    async def _clear_pending_batch(self) -> None:
        await self.redis.delete(self._pending_key())

    def _pending_key(self) -> str:
        return f"{self.config.redis_prefix}:pending"


@dataclass(slots=True)
class FailedTaskEmailNotificationWorker:
    notification_service: FailedTaskEmailNotificationService
    interval_seconds: float = 30.0
    _stopped: asyncio.Event = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.notification_service.notify_new_failed_tasks()
            except Exception:
                LOGGER.exception("Failed-task email notification iteration failed.")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue


def build_failed_task_email_title(item: dict[str, Any], *, title_prefix: str) -> str:
    service = _string_field(item, "service_name") or _string_field(item, "service_id") or "unknown-service"
    failure_id = _string_field(item, "failure_id") or "unknown-failure"
    return f"{title_prefix}: {service} / {failure_id}"


def build_failed_task_batch_email_title(items: list[dict[str, Any]], *, title_prefix: str) -> str:
    if len(items) == 1:
        return build_failed_task_email_title(items[0], title_prefix=title_prefix)
    return f"{title_prefix}: {len(items)} failed tasks"


def build_failed_task_email_body(item: dict[str, Any]) -> str:
    rows = [
        ("Service", _string_field(item, "service_name") or _string_field(item, "service_id")),
        ("Service ID", _string_field(item, "service_id")),
        ("Failure ID", _string_field(item, "failure_id")),
        ("Task ID", _string_field(item, "task_id")),
        ("Correlation ID", _string_field(item, "correlation_id")),
        ("Queue", _string_field(item, "queue_name")),
        ("DLQ", _string_field(item, "dlq_name")),
        ("Status", _string_field(item, "status")),
        ("Failed At", _string_field(item, "failed_at")),
        ("Error Type", _string_field(item, "error_type")),
        ("Error Message", _string_field(item, "error_message")),
        ("Worker", _string_field(item, "worker_id")),
        ("Investigation", _string_field(item, "investigation_status")),
        ("Retry", _string_field(item, "retry_status")),
    ]
    return "\n".join(f"{label}: {value or '-'}" for label, value in rows)


def build_failed_task_batch_email_body(items: list[dict[str, Any]]) -> str:
    return "\n\n---\n\n".join(build_failed_task_email_body(item) for item in items)


def create_failed_task_email_settings_router(
    *,
    configured: bool,
    receivers: tuple[str, ...],
    settings_store: RedisFailedTaskEmailSettingsStore,
    prefix: str = "/studio",
) -> APIRouter:
    router = APIRouter()

    @router.get(f"{prefix}/failed-task-email-settings", response_model=FailedTaskEmailSettingsResponse)
    async def get_failed_task_email_settings() -> FailedTaskEmailSettingsResponse:
        settings = await settings_store.get()
        return _settings_response(configured=configured, receivers=receivers, settings=settings)

    @router.patch(f"{prefix}/failed-task-email-settings", response_model=FailedTaskEmailSettingsResponse)
    async def update_failed_task_email_settings(
        payload: FailedTaskEmailSettingsUpdate,
    ) -> FailedTaskEmailSettingsResponse:
        if payload.enabled and not configured:
            raise HTTPException(status_code=400, detail="Failed-task email service is not configured.")
        settings = await settings_store.update(
            enabled=payload.enabled,
            batch_wait_seconds=payload.batch_wait_seconds,
        )
        return _settings_response(configured=configured, receivers=receivers, settings=settings)

    return router


def _settings_response(
    *,
    configured: bool,
    receivers: tuple[str, ...],
    settings: FailedTaskEmailRuntimeSettings,
) -> FailedTaskEmailSettingsResponse:
    return FailedTaskEmailSettingsResponse(
        configured=configured,
        enabled=settings.enabled if configured else False,
        batch_wait_seconds=settings.batch_wait_seconds,
        max_batch_wait_seconds=MAX_BATCH_WAIT_SECONDS,
        receivers=list(receivers) if configured else [],
    )


def _string_field(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _item_key(item: dict[str, Any]) -> str:
    return f"{_string_field(item, 'service_id') or ''}:{_string_field(item, 'failure_id') or ''}"


def _normalize_batch_wait_seconds(value: Any) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_BATCH_WAIT_SECONDS, normalized))


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
