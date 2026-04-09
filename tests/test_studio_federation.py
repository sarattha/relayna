from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

import relayna.studio.app as studio_app
from relayna.api import AliasConfigSummary, CapabilityDocument, CapabilityServiceMetadata
from relayna.studio import ServiceRecord, ServiceStatus, create_studio_app, get_studio_runtime


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

    async def set(self, key: str, value: str, *, nx: bool = False) -> bool:
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

    async def sadd(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    async def srem(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        for member in members:
            bucket.discard(member)
        return before - len(bucket)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def aclose(self) -> None:
        self.close_calls += 1


class TrackingAsyncClient(httpx.AsyncClient):
    instances: list[TrackingAsyncClient] = []

    def __init__(self, *, transport: httpx.BaseTransport | None = None, timeout: float = 5.0) -> None:
        super().__init__(transport=transport, timeout=timeout)
        self.close_calls = 0
        TrackingAsyncClient.instances.append(self)

    async def aclose(self) -> None:
        self.close_calls += 1
        await super().aclose()


def make_capability_document(
    *,
    supported_routes: list[str],
    http_aliases: dict[str, str] | None = None,
    compatibility: str = "capabilities_v1",
) -> dict[str, object]:
    return CapabilityDocument(
        relayna_version="1.3.4",
        topology_kind="shared_tasks_shared_status",
        alias_config_summary=AliasConfigSummary(
            aliasing_enabled=bool(http_aliases),
            payload_aliases={},
            http_aliases=http_aliases or {},
        ),
        supported_routes=supported_routes,
        feature_flags=[],
        service_metadata=CapabilityServiceMetadata(
            service_title="Payments API",
            capability_path="/relayna/capabilities",
            discovery_source="live",
            compatibility=compatibility,
        ),
    ).model_dump(mode="json")


def make_record(
    *,
    service_id: str,
    base_url: str,
    status: ServiceStatus = ServiceStatus.HEALTHY,
    capabilities: dict[str, object] | None = None,
    auth_mode: str = "internal_network",
) -> ServiceRecord:
    return ServiceRecord(
        service_id=service_id,
        name=service_id.replace("-", " ").title(),
        base_url=base_url,
        environment="prod",
        tags=["core"],
        auth_mode=auth_mode,
        status=status,
        capabilities=capabilities,
        last_seen_at=None,
    )


def install_service(app, record: ServiceRecord) -> None:
    runtime = get_studio_runtime(app)
    asyncio.run(runtime.registry_store.create(record))


def test_service_scoped_routes_proxy_payloads_and_apply_http_aliases(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    observed_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status/task-123":
            return httpx.Response(200, json={"task_id": "task-123", "event": {"status": "completed"}})
        if request.url.path == "/history":
            observed_params["history"] = str(request.url.query)
            return httpx.Response(200, json={"count": 1, "events": [{"task_id": "task-123", "status": "completed"}]})
        if request.url.path == "/workflow/topology":
            return httpx.Response(200, json={"stages": [], "status_queue": "status.queue"})
        if request.url.path == "/dlq/messages":
            observed_params["dlq"] = str(request.url.query)
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if request.url.path == "/executions/task-123/graph":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "topology_kind": "shared_tasks_shared_status",
                    "summary": {"status": "completed", "graph_completeness": "full"},
                    "nodes": [],
                    "edges": [],
                    "annotations": {},
                    "related_task_ids": [],
                },
            )
        raise AssertionError(f"Unhandled upstream request {request.method} {request.url}")

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(
            app,
            make_record(
                service_id="payments-api",
                base_url="https://payments.example.test",
                capabilities=make_capability_document(
                    supported_routes=[
                        "status.latest",
                        "status.history",
                        "workflow.topology",
                        "dlq.messages",
                        "execution.graph",
                    ],
                    http_aliases={"task_id": "attemptId"},
                ),
            ),
        )

        assert client.get("/studio/services/payments-api/status/task-123").json()["service_id"] == "payments-api"
        history_response = client.get(
            "/studio/services/payments-api/history",
            params={"task_id": "task-123", "max_scan": 2},
        )
        dlq_response = client.get(
            "/studio/services/payments-api/dlq/messages",
            params={"task_id": "task-123", "limit": 10},
        )
        workflow_response = client.get("/studio/services/payments-api/workflow/topology")
        graph_response = client.get("/studio/services/payments-api/executions/task-123/graph")

        assert history_response.status_code == 200
        assert dlq_response.status_code == 200
        assert workflow_response.status_code == 200
        assert graph_response.status_code == 200
        assert "attemptId=task-123" in observed_params["history"]
        assert "attemptId=task-123" in observed_params["dlq"]
        assert graph_response.json()["service_id"] == "payments-api"


def test_task_search_fans_out_and_falls_back_to_history(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "payments.example.test" and request.url.path == "/status/task-123":
            return httpx.Response(200, json={"task_id": "task-123", "event": {"status": "completed"}})
        if request.url.host == "legacy.example.test" and request.url.path == "/status/task-123":
            return httpx.Response(405, json={"detail": "Method Not Allowed"})
        if request.url.host == "legacy.example.test" and request.url.path == "/history":
            return httpx.Response(
                200,
                json={"count": 1, "events": [{"task_id": "task-123", "status": "processing"}]},
            )
        if request.url.host == "broken.example.test" and request.url.path == "/status/task-123":
            return httpx.Response(401, json={"detail": "Unauthorized"})
        raise AssertionError(f"Unhandled upstream request {request.method} {request.url}")

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(
            app,
            make_record(
                service_id="payments-api",
                base_url="https://payments.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="legacy-api",
                base_url="https://legacy.example.test",
                capabilities=make_capability_document(
                    supported_routes=[],
                    compatibility="legacy_no_capabilities_endpoint",
                ),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="broken-api",
                base_url="https://broken.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="disabled-api",
                base_url="https://disabled.example.test",
                status=ServiceStatus.DISABLED,
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )

        response = client.get("/studio/tasks/search", params={"task_id": "task-123"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        assert {item["service_id"] for item in payload["items"]} == {"payments-api", "legacy-api"}
        assert set(payload["scanned_services"]) == {"payments-api", "legacy-api", "broken-api"}
        assert payload["items"][0]["detail_path"].startswith("/studio/tasks/")
        assert payload["errors"] == [
            {
                "detail": "Relayna service 'broken-api' rejected Studio credentials for 'status.latest'.",
                "code": "upstream_auth_failure",
                "service_id": "broken-api",
                "upstream_status": 401,
                "retryable": False,
            }
        ]


def test_task_detail_tolerates_partial_failures_and_derives_latest_from_history(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status/task-123":
            return httpx.Response(405, json={"detail": "Method Not Allowed"})
        if request.url.path == "/history":
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "events": [
                        {"task_id": "task-123", "status": "completed"},
                        {"task_id": "task-123", "status": "processing"},
                    ],
                },
            )
        if request.url.path == "/dlq/messages":
            return httpx.Response(200, json={"items": [{"dlq_id": "dlq-1"}], "next_cursor": None})
        if request.url.path == "/executions/task-123/graph":
            return httpx.Response(404, json={"detail": "No execution graph found for task_id 'task-123'."})
        raise AssertionError(f"Unhandled upstream request {request.method} {request.url}")

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(
            app,
            make_record(
                service_id="legacy-api",
                base_url="https://legacy.example.test",
                capabilities=make_capability_document(
                    supported_routes=[],
                    compatibility="legacy_no_capabilities_endpoint",
                ),
            ),
        )

        response = client.get("/studio/tasks/legacy-api/task-123")

        assert response.status_code == 200
        payload = response.json()
        assert payload["latest_status"]["event"]["status"] == "completed"
        assert payload["history"]["count"] == 2
        assert payload["dlq_messages"]["items"][0]["dlq_id"] == "dlq-1"
        assert payload["execution_graph"] is None
        assert payload["errors"] == [
            {
                "detail": "No execution graph found for task_id 'task-123'.",
                "code": "upstream_not_found",
                "service_id": "legacy-api",
                "upstream_status": 404,
                "retryable": False,
            }
        ]


def test_federation_routes_normalize_disabled_timeout_invalid_json_and_not_found(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "timeout.example.test":
            raise httpx.ReadTimeout("timed out")
        if request.url.host == "invalid-json.example.test":
            return httpx.Response(200, text="{nope")
        if request.url.host == "missing.example.test":
            return httpx.Response(404, json={"detail": "No status found for task_id 'task-123'."})
        raise AssertionError(f"Unhandled upstream request {request.method} {request.url}")

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(
            app,
            make_record(
                service_id="disabled-api",
                base_url="https://disabled.example.test",
                status=ServiceStatus.DISABLED,
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="timeout-api",
                base_url="https://timeout.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="invalid-json-api",
                base_url="https://invalid-json.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="missing-api",
                base_url="https://missing.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )

        disabled_response = client.get("/studio/services/disabled-api/status/task-123")
        timeout_response = client.get("/studio/services/timeout-api/status/task-123")
        invalid_json_response = client.get("/studio/services/invalid-json-api/status/task-123")
        missing_response = client.get("/studio/services/missing-api/status/task-123")

        assert disabled_response.status_code == 503
        assert disabled_response.json()["code"] == "service_disabled"
        assert timeout_response.status_code == 504
        assert timeout_response.json()["code"] == "upstream_timeout"
        assert invalid_json_response.status_code == 502
        assert invalid_json_response.json()["code"] == "invalid_json"
        assert missing_response.status_code == 404
        assert missing_response.json()["code"] == "upstream_not_found"


def test_create_studio_app_closes_shared_federation_client(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    TrackingAsyncClient.instances.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/studio/services")
        assert response.status_code == 200

    assert TrackingAsyncClient.instances[0].close_calls == 1
