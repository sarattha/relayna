from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx
import pytest
import relayna_studio.app as studio_app
from fastapi.testclient import TestClient
from relayna_studio import create_studio_app, get_studio_runtime
from relayna_studio.failed_task_notifications import (
    FailedTaskEmailClient,
    FailedTaskEmailNotificationConfig,
    FailedTaskEmailNotificationError,
    FailedTaskEmailNotificationService,
    FailedTaskEmailNotificationWorker,
    build_failed_task_email_body,
    build_failed_task_email_title,
)


class FakeRedis:
    instances: list[FakeRedis] = []

    def __init__(self, url: str = "redis://test") -> None:
        self.url = url
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.close_calls = 0
        FakeRedis.instances.append(self)

    @classmethod
    def from_url(cls, url: str) -> FakeRedis:
        return cls(url)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
        return deleted

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def aclose(self) -> None:
        self.close_calls += 1


class FakeFederationService:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items
        self.calls: list[dict[str, object]] = []

    async def list_failed_tasks(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"items": self.items, "errors": [], "scanned_services": ["payments-api"]}


class RecordingEmailClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict[str, object]] = []

    async def send(self, *, receivers: tuple[str, ...], title: str, body: str) -> None:
        self.sent.append({"receivers": receivers, "title": title, "body": body})
        if self.fail:
            raise RuntimeError("email service unavailable")


def failed_task_item() -> dict[str, object]:
    return {
        "service_id": "payments-api",
        "service_name": "Payments API",
        "failure_id": "failure-1",
        "task_id": "task-123",
        "correlation_id": "corr-1",
        "queue_name": "payments.created",
        "dlq_name": "payments.created.dlq",
        "status": "DLQ",
        "failed_at": "2026-05-26T23:00:00Z",
        "error_type": "RuntimeError",
        "error_message": "card processor timeout",
        "worker_id": "worker-1",
        "investigation_status": "unreviewed",
        "retry_status": "not_retried",
    }


def make_notification_service(
    *,
    redis: FakeRedis | None = None,
    email_client: RecordingEmailClient | None = None,
) -> FailedTaskEmailNotificationService:
    return FailedTaskEmailNotificationService(
        federation_service=cast(Any, FakeFederationService([failed_task_item()])),
        redis=redis or FakeRedis(),
        email_client=cast(Any, email_client or RecordingEmailClient()),
        config=FailedTaskEmailNotificationConfig(
            service_url="https://email.example.test/send",
            receivers=("ops@example.com", "oncall@example.com"),
            interval_seconds=0.0,
            timeout_seconds=1.0,
            dedupe_ttl_seconds=3600,
            title_prefix="[Relayna] Failed task",
        ),
    )


@pytest.mark.asyncio
async def test_failed_task_notification_sends_email_and_records_dedupe_marker() -> None:
    redis = FakeRedis()
    email_client = RecordingEmailClient()
    service = make_notification_service(redis=redis, email_client=email_client)

    sent = await service.notify_new_failed_tasks()

    assert sent == 1
    assert len(email_client.sent) == 1
    message = email_client.sent[0]
    assert message["receivers"] == ("ops@example.com", "oncall@example.com")
    assert message["title"] == "[Relayna] Failed task: Payments API / failure-1"
    assert "Task ID: task-123" in str(message["body"])
    assert redis.values["studio:failed_task_email:notified:payments-api:failure-1"]


@pytest.mark.asyncio
async def test_failed_task_notification_deduplicates_repeated_scans() -> None:
    redis = FakeRedis()
    email_client = RecordingEmailClient()
    service = make_notification_service(redis=redis, email_client=email_client)

    assert await service.notify_new_failed_tasks() == 1
    assert await service.notify_new_failed_tasks() == 0

    assert len(email_client.sent) == 1


@pytest.mark.asyncio
async def test_failed_task_notification_retries_when_email_send_fails() -> None:
    redis = FakeRedis()
    failing_client = RecordingEmailClient(fail=True)
    service = make_notification_service(redis=redis, email_client=failing_client)

    assert await service.notify_new_failed_tasks() == 0
    assert "studio:failed_task_email:notified:payments-api:failure-1" not in redis.values

    service.email_client = cast(Any, RecordingEmailClient())

    assert await service.notify_new_failed_tasks() == 1


@pytest.mark.asyncio
async def test_failed_task_notification_skips_when_lock_is_held() -> None:
    redis = FakeRedis()
    await redis.set("studio:failed_task_email:lock:payments-api:failure-1", "locked")
    email_client = RecordingEmailClient()
    service = make_notification_service(redis=redis, email_client=email_client)

    assert await service.notify_new_failed_tasks() == 0
    assert email_client.sent == []


@pytest.mark.asyncio
async def test_failed_task_email_client_posts_expected_payload_and_rejects_non_success() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        email_client = FailedTaskEmailClient(
            http_client=client,
            service_url="https://email.example.test/send",
            timeout_seconds=1.0,
        )
        await email_client.send(receivers=("ops@example.com",), title="Failure", body="Body")

    assert requests[0].url == "https://email.example.test/send"
    assert requests[0].read() == b'{"receivers":["ops@example.com"],"title":"Failure","body":"Body"}'

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        email_client = FailedTaskEmailClient(
            http_client=client,
            service_url="https://email.example.test/send",
            timeout_seconds=1.0,
        )
        with pytest.raises(FailedTaskEmailNotificationError, match="HTTP 500"):
            await email_client.send(receivers=("ops@example.com",), title="Failure", body="Body")


def test_failed_task_email_content_uses_failed_task_fields() -> None:
    item = failed_task_item()

    assert build_failed_task_email_title(item, title_prefix="[Relayna] Failed task") == (
        "[Relayna] Failed task: Payments API / failure-1"
    )
    body = build_failed_task_email_body(item)
    assert "Service: Payments API" in body
    assert "Error Message: card processor timeout" in body


@pytest.mark.asyncio
async def test_failed_task_email_worker_logs_and_continues_after_failure(caplog: pytest.LogCaptureFixture) -> None:
    class FlakyNotificationService:
        def __init__(self) -> None:
            self.calls = 0
            self.worker: FailedTaskEmailNotificationWorker | None = None

        async def notify_new_failed_tasks(self) -> int:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary notification failure")
            assert self.worker is not None
            self.worker.stop()
            return 0

    notification_service = FlakyNotificationService()
    worker = FailedTaskEmailNotificationWorker(
        notification_service=cast(Any, notification_service),
        interval_seconds=0.0,
    )
    notification_service.worker = worker

    with caplog.at_level("ERROR"):
        await asyncio.wait_for(worker.run_forever(), timeout=1.0)

    assert notification_service.calls == 2
    assert "Failed-task email notification iteration failed." in caplog.text


def test_studio_runtime_builds_failed_task_email_worker_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", type("RedisFactory", (), {"from_url": staticmethod(FakeRedis.from_url)}))

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        pull_sync_interval_seconds=None,
        health_refresh_interval_seconds=None,
        retention_prune_interval_seconds=None,
        failed_task_email_enabled=True,
        failed_task_email_service_url="https://email.example.test/send",
        failed_task_email_receivers=("ops@example.com",),
        failed_task_email_interval_seconds=60.0,
    )

    with TestClient(app):
        runtime = get_studio_runtime(app)
        assert runtime.failed_task_email_worker is not None
