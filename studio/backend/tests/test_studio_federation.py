from __future__ import annotations

import asyncio

import httpx
import relayna_studio.app as studio_app
from fastapi.testclient import TestClient
from relayna_studio import ServiceRecord, ServiceStatus, create_studio_app, get_studio_runtime

from relayna.api import AliasConfigSummary, CapabilityDocument, CapabilityServiceMetadata


class FakeRedis:
    instances: list[FakeRedis] = []

    def __init__(self, url: str = "redis://test") -> None:
        self.url = url
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.close_calls = 0
        FakeRedis.instances.append(self)

    @classmethod
    def from_url(cls, url: str) -> FakeRedis:
        return cls(url)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        del ex
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

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self.lists.get(key, [])
        return items[int(start) : int(stop) + 1]

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    async def aclose(self) -> None:
        self.close_calls += 1


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[object, ...]]] = []

    def lpush(self, key: str, payload: str) -> None:
        self._ops.append(("lpush", (key, payload)))

    def ltrim(self, key: str, start: int, stop: int) -> None:
        self._ops.append(("ltrim", (key, start, stop)))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", (key, ttl)))

    def publish(self, channel: str, payload: str) -> None:
        self._ops.append(("publish", (channel, payload)))

    def set(self, key: str, value: str) -> None:
        self._ops.append(("set", (key, value)))

    async def execute(self) -> list[object]:
        results: list[object] = []
        for op, args in self._ops:
            if op == "lpush":
                key, payload = args
                self._redis.lists.setdefault(str(key), []).insert(0, str(payload))
                results.append(1)
            elif op == "ltrim":
                key, start, stop = args
                items = self._redis.lists.get(str(key), [])
                self._redis.lists[str(key)] = items[int(start) : int(stop) + 1]
                results.append(True)
            elif op == "expire":
                results.append(True)
            elif op == "publish":
                results.append(1)
            elif op == "set":
                key, value = args
                self._redis.values[str(key)] = str(value)
                results.append(True)
        return results


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

        status_payload = client.get("/studio/services/payments-api/status/task-123").json()
        assert history_response.status_code == 200
        assert dlq_response.status_code == 200
        assert workflow_response.status_code == 200
        assert graph_response.status_code == 200
        assert "attemptId=task-123" in observed_params["history"]
        assert "attemptId=task-123" in observed_params["dlq"]
        assert status_payload["service_id"] == "payments-api"
        assert status_payload["task_ref"]["service_id"] == "payments-api"
        assert status_payload["task_ref"]["task_id"] == "task-123"
        assert history_response.json()["events"][0]["task_ref"]["task_id"] == "task-123"
        assert graph_response.json()["service_id"] == "payments-api"
        assert graph_response.json()["task_ref"]["task_id"] == "task-123"


def test_task_search_returns_retained_indexed_matches(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
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
        ingest_response = client.post(
            "/studio/ingest/events",
            json={
                "events": [
                    {
                        "service_id": "payments-api",
                        "ingest_method": "pull",
                        "event": {
                            "cursor": "payments-evt-1",
                            "task_id": "task-123",
                            "event_type": "status.completed",
                            "source_kind": "status",
                            "component": "status",
                            "timestamp": "2026-04-10T02:00:00Z",
                            "event_id": "payments-evt-1",
                            "payload": {"status": "completed"},
                        },
                    },
                    {
                        "service_id": "legacy-api",
                        "ingest_method": "pull",
                        "event": {
                            "cursor": "legacy-evt-1",
                            "task_id": "task-123",
                            "event_type": "status.completed",
                            "source_kind": "status",
                            "component": "status",
                            "timestamp": "2026-04-10T01:30:00Z",
                            "event_id": "legacy-evt-1",
                            "payload": {"status": "completed"},
                        },
                    },
                ]
            },
        )
        assert ingest_response.status_code == 200
        assert ingest_response.json() == {"inserted": 2, "duplicate": 0, "invalid": 0}

        response = client.get("/studio/tasks/search", params={"task_id": "task-123"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        assert [item["service_id"] for item in payload["items"]] == ["payments-api", "legacy-api"]
        assert [item["status"] for item in payload["items"]] == ["completed", "completed"]
        assert payload["items"][0]["detail_path"].startswith("/studio/tasks/")
        assert payload["next_cursor"] is None


def test_task_search_uses_retained_index_even_when_task_detail_falls_back_to_history(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "legacy.example.test" and request.url.path == "/status/task-123":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.host == "legacy.example.test" and request.url.path == "/history":
            return httpx.Response(
                200,
                json={"count": 1, "events": [{"task_id": "task-123", "status": "completed"}]},
            )
        if request.url.host == "legacy.example.test" and request.url.path == "/dlq/messages":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.host == "legacy.example.test" and request.url.path == "/executions/task-123/graph":
            return httpx.Response(404, json={"detail": "Not Found"})
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
        ingest_response = client.post(
            "/studio/ingest/events",
            json={
                "events": [
                    {
                        "service_id": "legacy-api",
                        "ingest_method": "pull",
                        "event": {
                            "cursor": "legacy-evt-1",
                            "task_id": "task-123",
                            "event_type": "status.completed",
                            "source_kind": "status",
                            "component": "status",
                            "timestamp": "2026-04-10T03:00:00Z",
                            "event_id": "legacy-evt-1",
                            "payload": {"status": "completed"},
                        },
                    }
                ]
            },
        )
        assert ingest_response.status_code == 200

        search_response = client.get("/studio/tasks/search", params={"task_id": "task-123"})
        detail_response = client.get("/studio/tasks/legacy-api/task-123")

        assert search_response.status_code == 200
        assert search_response.json()["count"] == 1
        assert search_response.json()["items"][0]["service_id"] == "legacy-api"
        assert search_response.json()["items"][0]["status"] == "completed"
        assert detail_response.status_code == 200
        assert detail_response.json()["latest_status"]["event"]["status"] == "completed"


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
                        {"task_id": "task-123", "status": "processing"},
                        {"task_id": "task-123", "status": "completed"},
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
        assert payload["task_ref"]["service_id"] == "legacy-api"
        assert payload["task_ref"]["task_id"] == "task-123"
        assert payload["latest_status"]["event"]["status"] == "completed"
        assert payload["history"]["count"] == 2
        assert payload["dlq_messages"]["items"][0]["dlq_id"] == "dlq-1"
        assert payload["execution_graph"] is None
        assert payload["joined_refs"] == []
        assert payload["join_warnings"] == []
        assert payload["errors"] == [
            {
                "detail": "No execution graph found for task_id 'task-123'.",
                "code": "upstream_not_found",
                "service_id": "legacy-api",
                "upstream_status": 404,
                "retryable": False,
            }
        ]


def test_task_detail_join_all_adds_cross_service_identity_refs(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "payments.example.test" and path == "/status/task-123":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "event": {
                        "status": "processing",
                        "correlation_id": "corr-123",
                        "meta": {"parent_task_id": "parent-1"},
                    },
                },
            )
        if host == "payments.example.test" and path == "/history":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "events": [
                        {
                            "task_id": "task-123",
                            "status": "processing",
                            "correlation_id": "corr-123",
                            "meta": {"parent_task_id": "parent-1"},
                        }
                    ],
                },
            )
        if host == "payments.example.test" and path == "/dlq/messages":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if host == "payments.example.test" and path == "/executions/task-123/graph":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "topology_kind": "shared_tasks_shared_status",
                    "summary": {"status": "processing", "graph_completeness": "full"},
                    "nodes": [
                        {
                            "id": "task:task-123",
                            "kind": "task",
                            "task_id": "task-123",
                            "annotations": {"correlation_id": "corr-123", "parent_task_id": "parent-1"},
                        },
                        {
                            "id": "child:child-1",
                            "kind": "aggregation_child",
                            "task_id": "child-1",
                            "annotations": {},
                        },
                        {
                            "id": "workflow:1",
                            "kind": "workflow_message",
                            "task_id": "task-123",
                            "annotations": {"correlation_id": "flow-1"},
                        },
                    ],
                    "edges": [],
                    "annotations": {},
                    "related_task_ids": ["child-1"],
                },
            )
        if host == "billing.example.test" and path == "/status/corr-123":
            return httpx.Response(200, json={"task_id": "corr-123", "event": {"status": "completed"}})
        if host == "shipping.example.test" and path == "/status/child-1":
            return httpx.Response(200, json={"task_id": "child-1", "event": {"status": "queued"}})
        if host == "workflow.example.test" and path == "/status/flow-1":
            return httpx.Response(200, json={"task_id": "flow-1", "event": {"status": "running"}})
        if host == "warehouse.example.test" and path == "/status/parent-1":
            return httpx.Response(200, json={"task_id": "parent-1", "event": {"status": "completed"}})
        if host == "ledger.example.test" and path == "/status/parent-1":
            return httpx.Response(200, json={"task_id": "parent-1", "event": {"status": "completed"}})
        return httpx.Response(404, json={"detail": "No status found for task_id."})

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
                    supported_routes=["status.latest", "status.history", "dlq.messages", "execution.graph"]
                ),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="billing-api",
                base_url="https://billing.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="shipping-api",
                base_url="https://shipping.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="workflow-api",
                base_url="https://workflow.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="warehouse-api",
                base_url="https://warehouse.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="ledger-api",
                base_url="https://ledger.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )

        response = client.get("/studio/tasks/payments-api/task-123", params={"join": "all"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["task_ref"]["correlation_id"] == "corr-123"
        assert payload["task_ref"]["parent_refs"] == [{"service_id": "payments-api", "task_id": "parent-1"}]
        assert payload["task_ref"]["child_refs"] == [{"service_id": "payments-api", "task_id": "child-1"}]
        assert {
            (item["task_ref"]["service_id"], item["task_ref"]["task_id"], item["join_kind"], item["matched_value"])
            for item in payload["joined_refs"]
        } == {
            ("billing-api", "corr-123", "correlation_id", "corr-123"),
            ("shipping-api", "child-1", "parent_task_id", "child-1"),
            ("workflow-api", "flow-1", "workflow_lineage", "flow-1"),
        }
        assert payload["join_warnings"] == [
            {
                "code": "ambiguous_join_candidate",
                "detail": "Skipped parent_task_id join for 'parent-1' because it matched multiple services.",
                "join_kind": "parent_task_id",
                "matched_value": "parent-1",
            }
        ]


def test_task_detail_join_correlation_still_works_after_task_search_replacement(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "payments.example.test" and path == "/status/task-123":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "event": {"status": "processing", "correlation_id": "corr-123"},
                },
            )
        if host == "payments.example.test" and path == "/history":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "events": [{"task_id": "task-123", "status": "processing", "correlation_id": "corr-123"}],
                },
            )
        if host == "payments.example.test" and path == "/dlq/messages":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if host == "payments.example.test" and path == "/executions/task-123/graph":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "topology_kind": "shared_tasks_shared_status",
                    "summary": {"status": "processing", "graph_completeness": "partial"},
                    "nodes": [],
                    "edges": [],
                    "annotations": {},
                    "related_task_ids": [],
                },
            )
        if host == "billing.example.test" and path == "/status/corr-123":
            return httpx.Response(200, json={"task_id": "corr-123", "event": {"status": "completed"}})
        return httpx.Response(404, json={"detail": "No status found for task_id."})

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
                    supported_routes=["status.latest", "status.history", "dlq.messages", "execution.graph"]
                ),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="billing-api",
                base_url="https://billing.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )

        response = client.get("/studio/tasks/payments-api/task-123", params={"join": "correlation"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["task_ref"]["task_id"] == "task-123"
        assert payload["joined_refs"] == [
            {
                "task_ref": {
                    "service_id": "billing-api",
                    "task_id": "corr-123",
                    "correlation_id": None,
                    "parent_refs": [],
                    "child_refs": [],
                },
                "join_kind": "correlation_id",
                "matched_value": "corr-123",
            }
        ]
        assert payload["join_warnings"] == []


def test_task_detail_join_skips_when_candidate_scan_is_incomplete(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "payments.example.test" and path == "/status/task-123":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "event": {"status": "processing", "correlation_id": "corr-123"},
                },
            )
        if host == "payments.example.test" and path == "/history":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "events": [{"task_id": "task-123", "status": "processing", "correlation_id": "corr-123"}],
                },
            )
        if host == "payments.example.test" and path == "/dlq/messages":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if host == "payments.example.test" and path == "/executions/task-123/graph":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "topology_kind": "shared_tasks_shared_status",
                    "summary": {"status": "processing", "graph_completeness": "partial"},
                    "nodes": [],
                    "edges": [],
                    "annotations": {},
                    "related_task_ids": [],
                },
            )
        if host == "billing.example.test" and path == "/status/corr-123":
            return httpx.Response(200, json={"task_id": "corr-123", "event": {"status": "completed"}})
        if host == "shipping.example.test" and path == "/status/corr-123":
            return httpx.Response(401, json={"detail": "Unauthorized"})
        return httpx.Response(404, json={"detail": "No status found for task_id."})

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
                    supported_routes=["status.latest", "status.history", "dlq.messages", "execution.graph"]
                ),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="billing-api",
                base_url="https://billing.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )
        install_service(
            app,
            make_record(
                service_id="shipping-api",
                base_url="https://shipping.example.test",
                capabilities=make_capability_document(supported_routes=["status.latest"]),
            ),
        )

        response = client.get("/studio/tasks/payments-api/task-123", params={"join": "correlation"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["joined_refs"] == [
            {
                "task_ref": {
                    "service_id": "billing-api",
                    "task_id": "corr-123",
                    "correlation_id": None,
                    "parent_refs": [],
                    "child_refs": [],
                },
                "join_kind": "correlation_id",
                "matched_value": "corr-123",
            }
        ]
        assert payload["join_warnings"] == []


def test_service_scoped_status_and_graph_encode_reserved_task_id_characters(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    observed_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_urls.append(str(request.url))
        if str(request.url) == "https://payments.example.test/status/task%3F123%23frag":
            return httpx.Response(200, json={"task_id": "task?123#frag", "event": {"status": "completed"}})
        if str(request.url) == "https://payments.example.test/executions/task%3F123%23frag/graph":
            return httpx.Response(
                200,
                json={
                    "task_id": "task?123#frag",
                    "topology_kind": "shared_tasks_shared_status",
                    "summary": {"status": "completed", "graph_completeness": "partial"},
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
                capabilities=make_capability_document(supported_routes=["status.latest", "execution.graph"]),
            ),
        )

        status_response = client.get("/studio/services/payments-api/status/task%3F123%23frag")
        graph_response = client.get("/studio/services/payments-api/executions/task%3F123%23frag/graph")

        assert status_response.status_code == 200
        assert graph_response.status_code == 200
        assert observed_urls == [
            "https://payments.example.test/status/task%3F123%23frag",
            "https://payments.example.test/executions/task%3F123%23frag/graph",
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
