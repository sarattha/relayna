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
