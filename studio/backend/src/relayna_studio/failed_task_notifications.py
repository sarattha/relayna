from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from .federation import StudioFederationService
from .registry import OutboundUrlPolicyError, StudioOutboundUrlPolicy

LOGGER = logging.getLogger(__name__)


class FailedTaskEmailNotificationError(RuntimeError):
    """Raised when the failed-task email service rejects a notification."""


@dataclass(slots=True, frozen=True)
class FailedTaskEmailNotificationConfig:
    service_url: str
    receivers: tuple[str, ...]
    interval_seconds: float = 30.0
    timeout_seconds: float = 5.0
    dedupe_ttl_seconds: int = 604800
    title_prefix: str = "[Relayna] Failed task"
    redis_prefix: str = "studio:failed_task_email"
    scan_limit: int = 200

    def __post_init__(self) -> None:
        if not self.service_url.strip():
            raise ValueError("service_url must be set.")
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


class FailedTaskEmailClient:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        service_url: str,
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
        self._timeout_seconds = timeout_seconds

    async def send(self, *, receivers: tuple[str, ...], title: str, body: str) -> None:
        response = await self._http_client.post(
            self._service_url,
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

    async def notify_new_failed_tasks(self) -> int:
        payload = await self.federation_service.list_failed_tasks(
            investigation_status="unreviewed",
            limit=self.config.scan_limit,
        )
        sent = 0
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            if await self._notify_item(item):
                sent += 1
        return sent

    async def _notify_item(self, item: dict[str, Any]) -> bool:
        service_id = _string_field(item, "service_id")
        failure_id = _string_field(item, "failure_id")
        if not service_id or not failure_id:
            return False
        if await self._is_notified(service_id, failure_id):
            return False
        lock_key = self._lock_key(service_id, failure_id)
        lock_ttl = max(1, int(self.config.timeout_seconds + 30))
        acquired = await self.redis.set(lock_key, _utc_now(), nx=True, ex=lock_ttl)
        if not acquired:
            return False
        try:
            if await self._is_notified(service_id, failure_id):
                return False
            title = build_failed_task_email_title(item, title_prefix=self.config.title_prefix)
            body = build_failed_task_email_body(item)
            await self.email_client.send(receivers=self.config.receivers, title=title, body=body)
            await self.redis.set(
                self._notified_key(service_id, failure_id),
                _utc_now(),
                ex=self.config.dedupe_ttl_seconds,
            )
            return True
        except Exception:
            LOGGER.exception("Failed-task email notification send failed.")
            return False
        finally:
            await self.redis.delete(lock_key)

    async def _is_notified(self, service_id: str, failure_id: str) -> bool:
        return bool(await self.redis.get(self._notified_key(service_id, failure_id)))

    def _notified_key(self, service_id: str, failure_id: str) -> str:
        return f"{self.config.redis_prefix}:notified:{service_id}:{failure_id}"

    def _lock_key(self, service_id: str, failure_id: str) -> str:
        return f"{self.config.redis_prefix}:lock:{service_id}:{failure_id}"


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


def _string_field(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
