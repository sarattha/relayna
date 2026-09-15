from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from relayna_studio.load_testing import _Chamber, _check_schema, _create_load_testing_router, _load_profiles
from relayna_studio.registry import ServiceNotFoundError

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def configured(monkeypatch, tmp_path):
    document = json.loads((ROOT / "docs/examples/studio-chamber-profiles.json").read_text())
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(document))
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_URL", "http://chamber.internal")
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_TOKEN", "operator-secret")
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_PROFILES_PATH", str(path))
    return document, path


@pytest.fixture
def harness(configured):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    registry = SimpleNamespace(
        get_service=AsyncMock(return_value=SimpleNamespace(environment="staging", status="healthy"))
    )
    calls = []
    state = {"status": "running", "fail_start_once": False, "bad_response": False}

    def upstream(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer operator-secret"
        if state["bad_response"]:
            return httpx.Response(200, json=[])
        path = request.url.path
        if path.endswith("/plans"):
            return httpx.Response(200, json={"run_id": "plan-upstream", "run_dir": "/private/workspace"})
        if path == "/api/v1/runs":
            if state["fail_start_once"]:
                state["fail_start_once"] = False
                raise httpx.ReadTimeout("lost reply")
            return httpx.Response(202, json={"job_id": "job-1", "run_id": "run-1", "state": "queued"})
        if path.startswith("/api/v1/jobs/"):
            return httpx.Response(
                200,
                json={
                    "job_id": "job-1",
                    "run_id": "run-1",
                    "state": state["status"],
                    "output": "task accepted\n" + "x" * 70000,
                    "config_path": "/private/config.yaml",
                    "cancel_requested": path.endswith("/cancel"),
                    "cleanup_required": False,
                },
            )
        if path == "/api/v1/runs/run-1":
            return httpx.Response(
                200,
                json={
                    "config": {"secret": "never-forward"},
                    "run": {"updated_at": "2026-09-14T01:02:00Z"},
                    "result": {"status": "inconclusive", "readiness_score": None, "evidence_coverage_percent": 0},
                    "relayna": {"tasks": [{"task_id": "task-1", "terminal_status": "completed", "success": True}]},
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = FastAPI()
    app.include_router(_create_load_testing_router(registry, redis, client))
    return TestClient(app), calls, state, registry, redis


BASE = "/studio/services/translation-staging/load-tests"
PAYLOAD = {
    "profile_id": "translate-text",
    "inputs": {"text": "Hello", "language_target": "Thai"},
    "vus": 2,
    "iterations": 4,
    "duration_seconds": 30,
}


def test_plan_start_retry_status_cancel_and_history(harness):
    client, calls, state, _, _ = harness
    options = client.get(f"{BASE}/profiles").json()
    assert options["available"]
    assert options["profiles"][0]["input_schema"]["required"] == ["text", "language_target"]
    assert "config" not in options["profiles"][0]
    planned = client.post(f"{BASE}/plans", json=PAYLOAD)
    assert planned.status_code == 201
    plan = planned.json()
    assert "chamber_plan_id" not in plan and "context" not in plan
    config = json.loads(calls[-1].content)["config"]
    journey = config["traffic"]["journeys"][0]
    assert journey["body"] == PAYLOAD["inputs"]
    assert journey["vus"] == 2 and journey["iterations"] == 4
    assert config["runtime"]["faults"] == []
    identity = plan["id"]
    state["fail_start_once"] = True
    assert client.post(f"{BASE}/{identity}/start").status_code == 502
    assert client.post(f"{BASE}/{identity}/start").status_code == 202
    starts = [request for request in calls if request.url.path == "/api/v1/runs"]
    assert len(starts) == 2
    assert starts[0].headers["Idempotency-Key"] == starts[1].headers["Idempotency-Key"]
    assert json.loads(starts[1].content) == {
        "plan_id": "plan-upstream",
        "mode": "kubernetes",
        "context": "aks-staging",
        "prometheus_url": None,
    }
    assert client.post(f"{BASE}/{identity}/start").status_code == 202
    assert len([request for request in calls if request.url.path == "/api/v1/runs"]) == 2
    status = client.get(f"{BASE}/{identity}").json()
    assert status["tasks"][0]["task_id"] == "task-1"
    assert len(status["output"]) == 65536
    assert "private" not in json.dumps(status) and "never-forward" not in json.dumps(status)
    assert status["result"]["readiness_score"] is None
    assert client.post(f"{BASE}/{identity}/cancel").json()["cancel_requested"] is True
    state["status"] = "cancelled"
    assert client.get(f"{BASE}/{identity}").json()["finished_at"] == "2026-09-14T01:02:00+00:00"
    assert client.get(BASE).json()["items"][0]["state"] == "cancelled"


@pytest.mark.parametrize(
    "change",
    [
        {"inputs": {"text": "", "language_target": "Thai"}},
        {"inputs": {"text": "Hello", "language_target": "invalid"}},
        {"inputs": {"text": "Hello", "language_target": "Thai", "target": "http://other"}},
        {"inputs": {"text": "Hello", "language_target": "Thai", "priority": "5"}},
        {"inputs": {"text": "x" * 70000}},
        {"vus": 9},
        {"vus": True},
        {"iterations": 101},
        {"duration_seconds": 301},
        {"context": "prod"},
    ],
)
def test_invalid_inputs_never_reach_chamber(harness, change):
    client, calls, *_ = harness
    response = client.post(f"{BASE}/plans", json={**PAYLOAD, **change})
    assert response.status_code == 422
    assert not calls


def test_service_environment_binding_and_disabled_targets(harness):
    client, calls, _, registry, _ = harness
    identity = client.post(f"{BASE}/plans", json=PAYLOAD).json()["id"]
    assert client.get(f"/studio/services/other/load-tests/{identity}").status_code == 404
    assert client.get(f"{BASE}/history").status_code == 404
    assert client.post(f"{BASE}/{identity}/cancel").status_code == 409
    assert client.get(f"{BASE}/{identity}").json()["state"] == "planned"
    registry.get_service.return_value.environment = "production"
    assert not client.get(f"{BASE}/profiles").json()["available"]
    assert client.post(f"{BASE}/{identity}/start").status_code == 409
    assert client.get(BASE).json()["items"] == []
    registry.get_service.return_value.environment = "staging"
    registry.get_service.return_value.status = "disabled"
    assert client.post(f"{BASE}/plans", json=PAYLOAD).status_code == 409
    assert client.post(f"{BASE}/{identity}/start").status_code == 409
    assert len(calls) == 1
    registry.get_service.side_effect = ServiceNotFoundError("missing")
    assert client.get(f"{BASE}/profiles").status_code == 404


def test_missing_profile_and_malformed_upstream(harness):
    client, calls, state, *_ = harness
    assert client.post(f"{BASE}/plans", json={**PAYLOAD, "profile_id": "other"}).status_code == 409
    assert not calls
    state["bad_response"] = True
    assert client.post(f"{BASE}/plans", json=PAYLOAD).status_code == 502


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["config"]["runtime"].update(mode="deploy"),
        lambda p: p["config"]["runtime"].update(cleanup=True),
        lambda p: p["config"]["runtime"].update(faults=[{"type": "kill-pod"}]),
        lambda p: p.update(max_vus=101),
        lambda p: p.update(max_iterations=1001),
        lambda p: p["input_schema"].update(additionalProperties=True),
        lambda p: p["input_schema"]["properties"]["text"].update(default=""),
        lambda p: p["input_schema"]["properties"]["text"].update(oneOf=[{"type": "string"}]),
    ],
)
def test_invalid_operator_profiles_fail_at_startup(configured, change):
    document, path = configured
    change(document["translation-staging"]["profiles"][0])
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        _load_profiles()


def test_supported_nested_schema_and_rejected_unbounded_array():
    child = {"type": "array", "items": {"type": "integer", "minimum": 1}, "maxItems": 3}
    _check_schema({"type": "object", "additionalProperties": False, "properties": {"counts": child}})
    with pytest.raises(ValueError):
        _check_schema({**child, "maxItems": 101})


def test_disabled_integration_does_not_require_configuration(monkeypatch):
    for key in ("URL", "TOKEN", "PROFILES_PATH"):
        monkeypatch.delenv(f"RELAYNA_STUDIO_CHAMBER_{key}", raising=False)
    bridge = _Chamber(SimpleNamespace(), fakeredis.aioredis.FakeRedis(), httpx.AsyncClient())
    assert bridge.profiles == {}


@pytest.mark.parametrize("url", ["file:///tmp/chamber", "https://user:secret@chamber", "http://chamber?token=x"])
def test_invalid_upstream_url(configured, monkeypatch, url):
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_URL", url)
    with pytest.raises(ValueError):
        _Chamber(SimpleNamespace(), fakeredis.aioredis.FakeRedis(), httpx.AsyncClient())


def test_http_profile_pins_reviewed_load(configured):
    document, path = configured
    profile = document["translation-staging"]["profiles"][0]
    journey = profile["config"]["traffic"]["journeys"][0]
    journey.update(adapter="http", iterations=999, vus=100, durationSeconds=1000)
    path.write_text(json.dumps(document))
    captured = []

    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"run_id": "http-plan"})

    registry = SimpleNamespace(
        get_service=AsyncMock(return_value=SimpleNamespace(environment="staging", status="healthy"))
    )
    app = FastAPI()
    app.include_router(
        _create_load_testing_router(
            registry, fakeredis.aioredis.FakeRedis(), httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        )
    )
    assert TestClient(app).post(f"{BASE}/plans", json=PAYLOAD).status_code == 201
    actual = captured[0]["config"]["traffic"]["journeys"][0]
    assert actual["stages"] == [{"duration": "30s", "targetVus": 2}]
    assert "iterations" not in actual and "vus" not in actual


@pytest.mark.parametrize(
    "role,csrf,expected", [("readonly", "valid", 403), ("admin", "", 403), ("admin", "valid", 201)]
)
def test_studio_auth_middleware_protects_load_test_mutations(configured, role, csrf, expected):
    from relayna_studio.auth import StudioAuthMiddleware, StudioMemberStatus, StudioRole

    registry = SimpleNamespace(
        get_service=AsyncMock(return_value=SimpleNamespace(environment="staging", status="healthy"))
    )
    member = SimpleNamespace(role=StudioRole(role), status=StudioMemberStatus.ACTIVE, user_id="actor-1")
    auth = SimpleNamespace(session_context=AsyncMock(return_value=(SimpleNamespace(csrf_token="valid"), member)))
    app = FastAPI()
    app.include_router(
        _create_load_testing_router(
            registry,
            fakeredis.aioredis.FakeRedis(),
            httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"run_id": "plan-1"}))),
        )
    )
    app.add_middleware(StudioAuthMiddleware, service=auth)
    client = TestClient(app)
    assert client.get(f"{BASE}/profiles").status_code == 200
    assert client.post(f"{BASE}/plans", json=PAYLOAD, headers={"X-CSRF-Token": csrf}).status_code == expected
    auth.session_context.return_value = None
    assert client.get(f"{BASE}/profiles").status_code == 401


@pytest.mark.parametrize("encoding", ["form", "raw", "multipart", "none"])
def test_request_encodings_keep_fields_typed_and_paths_server_side(configured, encoding):
    document, path = configured
    profile = document["translation-staging"]["profiles"][0]
    journey = profile["config"]["traffic"]["journeys"][0]
    journey.update(adapter="http", requestEncoding=encoding)
    payload = dict(PAYLOAD)
    if encoding == "raw":
        profile["input_schema"] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["body"],
            "properties": {"body": {"type": "string"}},
        }
        journey["contentType"] = "text/plain"
        payload["inputs"] = {"body": "Typed sample"}
    elif encoding == "none":
        profile["input_schema"] = {"type": "object", "additionalProperties": False, "properties": {}}
        payload["inputs"] = {}
    elif encoding == "multipart":
        journey["multipart"] = {
            "files": [
                {
                    "field": "file",
                    "path": "/workspace/uploads/sample.pdf",
                    "filename": "sample.pdf",
                    "contentType": "application/pdf",
                }
            ]
        }
    path.write_text(json.dumps(document))
    captured = []

    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"run_id": "plan-1"})

    registry = SimpleNamespace(
        get_service=AsyncMock(return_value=SimpleNamespace(environment="staging", status="healthy"))
    )
    app = FastAPI()
    app.include_router(
        _create_load_testing_router(
            registry, fakeredis.aioredis.FakeRedis(), httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        )
    )
    client = TestClient(app)
    response = client.post(f"{BASE}/plans", json=payload)
    assert response.status_code == 201
    actual = captured[0]["config"]["traffic"]["journeys"][0]
    if encoding == "form":
        assert actual["form"] == PAYLOAD["inputs"]
    elif encoding == "raw":
        assert actual["body"] == "Typed sample"
    elif encoding == "none":
        assert "body" not in actual
    else:
        assert actual["multipart"]["fields"] == PAYLOAD["inputs"]
        assert actual["multipart"]["files"][0]["path"] == "/workspace/uploads/sample.pdf"
        assert response.json()["files"] == [
            {"field": "file", "filename": "sample.pdf", "content_type": "application/pdf"}
        ]
        assert "/workspace" not in json.dumps(response.json())
        assert "path" not in client.get(f"{BASE}/profiles").json()["profiles"][0]["files"][0]


@pytest.mark.parametrize(
    "extra", [{"experiment": {"family": "pod_loss"}}, {"traffic": {"load": {"ratePerSecond": 999}}}]
)
def test_other_execution_modes_cannot_override_reviewed_load(configured, extra):
    document, path = configured
    document["translation-staging"]["profiles"][0]["config"].update(extra)
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="experiments or separate load suites"):
        _load_profiles()


def test_disabling_service_still_allows_cancellation(harness):
    client, _, _, registry, _ = harness
    identity = client.post(f"{BASE}/plans", json=PAYLOAD).json()["id"]
    assert client.post(f"{BASE}/{identity}/start").status_code == 202
    registry.get_service.return_value.status = "disabled"
    assert client.post(f"{BASE}/{identity}/cancel").status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(id=""),
        lambda p: p.update(input_schema={"type": "string"}),
        lambda p: p["config"]["runtime"].pop("namespace"),
        lambda p: p["config"]["traffic"].update(journeys=[]),
        lambda p: p["config"]["traffic"]["journeys"][0].update(requestEncoding="multipart"),
        lambda p: p["config"]["traffic"]["journeys"][0].update(requestEncoding="raw"),
        lambda p: p["config"]["traffic"]["journeys"][0].update(requestEncoding="form"),
        lambda p: p["config"]["traffic"]["journeys"][0].update(requestEncoding="none", adapter="http"),
        lambda p: p["config"]["traffic"]["journeys"][0].update(adapter="other"),
        lambda p: p.update(max_duration_seconds=0),
        lambda p: p["input_schema"]["properties"].update(
            nested={"type": "object", "properties": {}, "additionalProperties": False}
        )
        or p["config"]["traffic"]["journeys"][0].update(requestEncoding="form"),
    ],
)
def test_misconfigured_profiles_cannot_enable_execution(configured, change):
    document, path = configured
    change(document["translation-staging"]["profiles"][0])
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        _load_profiles()


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"service": {}},
        {"service": {"environment": "staging", "profiles": [{"id": "same", "input_schema": {"type": "string"}}]}},
    ],
)
def test_invalid_profile_document_rejected(configured, document):
    _, path = configured
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        _load_profiles()


@pytest.mark.parametrize("schema", [{"type": ["string", "integer"]}, {}, {"type": "null"}])
def test_forms_require_one_concrete_type(schema):
    with pytest.raises(ValueError):
        _check_schema(schema)


@pytest.mark.asyncio
async def test_missing_token_and_disabled_upstream(configured, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.delenv("RELAYNA_STUDIO_CHAMBER_TOKEN")
    with pytest.raises(ValueError, match="TOKEN"):
        _Chamber(SimpleNamespace(), fakeredis.aioredis.FakeRedis(), httpx.AsyncClient())
    monkeypatch.delenv("RELAYNA_STUDIO_CHAMBER_URL")
    bridge = _Chamber(SimpleNamespace(), fakeredis.aioredis.FakeRedis(), httpx.AsyncClient())
    with pytest.raises(HTTPException) as caught:
        await bridge.call("GET", "jobs/x")
    assert caught.value.status_code == 503


@pytest.mark.parametrize(
    "response, expected",
    [
        (httpx.Response(422), 409),
        (httpx.Response(503), 502),
        (httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)), 502),
        (httpx.Response(200, json={}), 502),
    ],
)
def test_upstream_rejections_and_missing_plan_do_not_create_history(configured, response, expected):
    registry = SimpleNamespace(
        get_service=AsyncMock(return_value=SimpleNamespace(environment="staging", status="healthy"))
    )
    app = FastAPI()
    app.include_router(
        _create_load_testing_router(
            registry,
            fakeredis.aioredis.FakeRedis(),
            httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)),
        )
    )
    client = TestClient(app)
    assert client.post(f"{BASE}/plans", json=PAYLOAD).status_code == expected
    assert client.get(BASE).json()["items"] == []


def test_noncanonical_plan_id_is_not_accepted(harness):
    from uuid import UUID

    client, *_ = harness
    identity = client.post(f"{BASE}/plans", json=PAYLOAD).json()["id"]
    assert client.get(f"{BASE}/{UUID(identity)}").status_code == 404


@pytest.mark.asyncio
async def test_nonfinite_payload_and_unconfigured_environment(configured):
    from fastapi import HTTPException
    from relayna_studio.load_testing import _LoadRequest

    registry = SimpleNamespace(
        get_service=AsyncMock(return_value=SimpleNamespace(environment="staging", status="healthy"))
    )
    bridge = _Chamber(registry, fakeredis.aioredis.FakeRedis(), httpx.AsyncClient())
    payload = _LoadRequest(**{**PAYLOAD, "inputs": {"value": float("nan")}})
    with pytest.raises(HTTPException) as caught:
        await bridge.plan("translation-staging", payload)
    assert caught.value.status_code == 422 and "finite" in caught.value.detail
    registry.get_service.return_value.environment = "production"
    with pytest.raises(HTTPException) as caught:
        await bridge.plan("translation-staging", payload)
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_missing_job_and_unavailable_evidence_keep_plan_recoverable(configured):
    from fastapi import HTTPException
    from relayna_studio.load_testing import _LoadRequest

    registry = SimpleNamespace(
        get_service=AsyncMock(return_value=SimpleNamespace(environment="staging", status="healthy"))
    )
    bridge = _Chamber(registry, fakeredis.aioredis.FakeRedis(), httpx.AsyncClient())
    bridge.call = AsyncMock(return_value={"run_id": "plan"})
    identity = (await bridge.plan("translation-staging", _LoadRequest(**PAYLOAD)))["id"]
    bridge.call.return_value = {}
    with pytest.raises(HTTPException, match="job ID"):
        await bridge.start("translation-staging", identity)
    bridge.call.return_value = {"job_id": "job"}
    await bridge.start("translation-staging", identity)
    bridge.call.side_effect = [
        {"state": "failed", "run_id": "run", "output": "failure detail"},
        HTTPException(502, "not ready"),
    ]
    status = await bridge.status("translation-staging", identity)
    assert status["output"] == "failure detail" and status["evidence_error"]
    bridge.call.side_effect = [{"state": "failed", "run_id": "run"}, {"run": {"updated_at": "invalid"}}]
    status = await bridge.status("translation-staging", identity)
    assert status["finished_at"] and not status["evidence_error"]


@pytest.mark.asyncio
async def test_polling_does_not_extend_retention_or_resurrect_expired_runs(configured):
    from datetime import UTC, datetime, timedelta

    from fastapi import HTTPException

    redis = fakeredis.aioredis.FakeRedis()
    bridge = _Chamber(SimpleNamespace(), redis, httpx.AsyncClient())
    record = {"id": "retention-test", "created_at": (datetime.now(UTC) - timedelta(days=29)).isoformat()}
    await bridge.save("service", record)
    deadline = await redis.expiretime(bridge.key("service", record["id"]))
    assert 86398 <= await redis.ttl(bridge.key("service", record["id"])) <= 86400
    record["output"] = "new output"
    await bridge.save("service", record)
    assert await redis.expiretime(bridge.key("service", record["id"])) == deadline
    record["created_at"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    await redis.delete(bridge.key("service", record["id"]))
    with pytest.raises(HTTPException) as caught:
        await bridge.save("service", record)
    assert caught.value.status_code == 404
    assert not await redis.exists(bridge.key("service", record["id"]))


@pytest.mark.asyncio
async def test_terminal_snapshots_survive_outage_but_running_and_cancel_errors_surface(configured):
    from fastapi import HTTPException
    from relayna_studio.load_testing import _LoadRequest

    registry = SimpleNamespace(
        get_service=AsyncMock(return_value=SimpleNamespace(environment="staging", status="healthy"))
    )
    bridge = _Chamber(registry, fakeredis.aioredis.FakeRedis(), httpx.AsyncClient())
    bridge.call = AsyncMock(return_value={"run_id": "plan"})
    identity = (await bridge.plan("translation-staging", _LoadRequest(**PAYLOAD)))["id"]
    bridge.call.return_value = {"job_id": "job", "state": "running"}
    await bridge.start("translation-staging", identity)
    bridge.call.side_effect = HTTPException(502, "offline")
    with pytest.raises(HTTPException):
        await bridge.status("translation-staging", identity)
    bridge.call.side_effect = [
        {"state": "completed", "run_id": "run", "output": "finished"},
        {"result": {"status": "passed"}, "relayna": {"tasks": [{"task_id": "task-1", "success": True}]}},
    ]
    completed = await bridge.status("translation-staging", identity)
    bridge.call.side_effect = HTTPException(502, "offline")
    retained = await bridge.status("translation-staging", identity)
    assert retained["output"] == completed["output"] == "finished"
    assert retained["tasks"] == completed["tasks"]
    assert retained["result"] == completed["result"]
    assert retained["state"] == "completed" and "snapshot" in retained["evidence_error"]
    with pytest.raises(HTTPException):
        await bridge.status("translation-staging", identity, cancel=True)


def test_nested_array_expansion_and_large_defaults_are_bounded():
    leaf = {"type": "array", "minItems": 100, "maxItems": 100, "items": {"type": "string"}}
    with pytest.raises(ValueError, match="1000 values"):
        _check_schema({"type": "array", "minItems": 100, "maxItems": 100, "items": leaf})
    with pytest.raises(ValueError, match="1000 values"):
        _check_schema({"type": "array", "maxItems": 100, "items": leaf, "default": [[""] * 100 for _ in range(100)]})
    _check_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"mode": {"type": "string"}},
            "enum": [{"mode": "a"}, {"mode": "b"}],
        }
    )
    _check_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"mode": {"type": "string"}},
            "default": {"mode": "a"},
        }
    )


def test_nullable_default_does_not_bypass_expansion_bound():
    with pytest.raises(ValueError, match="1000 values"):
        _check_schema(
            {
                "type": ["array", "null"],
                "default": None,
                "minItems": 100,
                "maxItems": 100,
                "items": {"type": "array", "minItems": 100, "maxItems": 100, "items": {"type": "string"}},
            }
        )


@pytest.mark.parametrize(
    "constraint", [{"minimum": 2**53 + 1}, {"maximum": 2**63 - 1}, {"default": 2**53 + 1}, {"enum": [2**53 + 1]}]
)
def test_unsafe_integer_schemas_are_rejected(constraint):
    with pytest.raises(ValueError):
        _check_schema({"type": "integer", **constraint})


def test_unbounded_integer_fields_gain_exact_browser_limits():
    from jsonschema import Draft202012Validator

    schema = {"type": "integer"}
    _check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid(2**53 - 1)
    assert not validator.is_valid(2**53)
    assert not validator.is_valid(-(2**53))


def test_plan_deadline_cancels_slow_work_before_browser_timeout(configured, monkeypatch):
    import asyncio

    import relayna_studio.load_testing as module

    assert module._REQUEST_TIMEOUT_SECONDS < 20
    monkeypatch.setattr(module, "_REQUEST_TIMEOUT_SECONDS", 0.01)
    cancelled = []

    async def slow_upstream(request):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return httpx.Response(200, json={"run_id": "late-plan"})

    registry = SimpleNamespace(
        get_service=AsyncMock(return_value=SimpleNamespace(environment="staging", status="healthy"))
    )
    app = FastAPI()
    app.include_router(
        _create_load_testing_router(
            registry, fakeredis.aioredis.FakeRedis(), httpx.AsyncClient(transport=httpx.MockTransport(slow_upstream))
        )
    )
    client = TestClient(app)
    response = client.post(f"{BASE}/plans", json=PAYLOAD)
    assert response.status_code == 504 and "recent runs" in response.json()["detail"]
    assert cancelled == [True]
    assert client.get(BASE).json()["items"] == []


def test_environment_edit_preserves_started_run_history_status_and_cancellation(harness):
    client, calls, _, registry, _ = harness
    identity = client.post(f"{BASE}/plans", json=PAYLOAD).json()["id"]
    assert client.post(f"{BASE}/{identity}/start").status_code == 202
    registry.get_service.return_value.environment = "production"
    status = client.get(f"{BASE}/{identity}")
    assert status.status_code == 200 and status.json()["environment"] == "staging"
    assert client.get(BASE).json()["items"][0]["id"] == identity
    assert client.post(f"{BASE}/{identity}/cancel").status_code == 200
    assert calls[-2].url.path == "/api/v1/jobs/job-1/cancel"
    assert client.post(f"{BASE}/{identity}/start").status_code == 409
    assert client.post(f"{BASE}/plans", json=PAYLOAD).status_code == 409
    assert len([request for request in calls if request.url.path == "/api/v1/runs"]) == 1


@pytest.mark.parametrize(
    "value,multiple,valid",
    [
        (1000000000000000.5, 1, False),
        (1000000000000000, 1, True),
        (0.3, 0.1, True),
        (0.3, 0.2, False),
        (3e-20, 1e-20, True),
    ],
)
def test_decimal_multiples_do_not_depend_on_float_division(value, multiple, valid):
    from relayna_studio.load_testing import _RequestValidator

    assert _RequestValidator({"type": "number", "multipleOf": multiple}).is_valid(value) is valid


def test_decimal_multiple_defaults_are_validated_consistently():
    _check_schema({"type": "number", "multipleOf": 0.1, "default": 0.3})
    with pytest.raises(ValueError):
        _check_schema({"type": "number", "multipleOf": 1, "default": 1000000000000000.5})
