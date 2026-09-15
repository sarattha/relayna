from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from relayna_studio._profile_import import _config, _import_router
from relayna_studio.auth import StudioMemberStatus, StudioRole
from relayna_studio.load_testing import _Chamber
from relayna_studio.registry import StudioOutboundUrlPolicy

BASE = "/studio/services/svc/load-tests/profile-import"
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def imported(monkeypatch):
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_URL", "http://chamber.internal")
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_TOKEN", "private-token")
    monkeypatch.delenv("RELAYNA_STUDIO_CHAMBER_PROFILES_PATH", raising=False)
    config = json.loads((ROOT / "docs/examples/studio-chamber-openapi-profiles.json").read_text())[
        "translation-staging"
    ]["profiles"][0]["config"]
    document = {
        "openapi": "3.0.3",
        "paths": {
            "/translations": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                    "required": ["text"],
                                }
                            }
                        },
                    }
                }
            }
        },
    }
    calls = []

    def upstream(request):
        calls.append(request)
        if request.url.host == "chamber.internal":
            assert request.headers["Authorization"] == "Bearer private-token"
            if request.url.path == "/api/v1/runs":
                return httpx.Response(
                    200,
                    json={
                        "runs": [{"run_id": "run-1", "service_name": "Translation", "config": {"secret": "hidden"}}],
                        "pagination": {"page": 1, "total_pages": 1},
                    },
                )
            return httpx.Response(200, json={"config": config})
        assert "authorization" not in request.headers
        return httpx.Response(200, json=document)

    service = SimpleNamespace(environment="staging", status="healthy", base_url="http://translation.internal")
    bridge = _Chamber(
        SimpleNamespace(get_service=AsyncMock(return_value=service)),
        fakeredis.aioredis.FakeRedis(decode_responses=True),
        httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
        StudioOutboundUrlPolicy(allowed_hosts=(".internal",)),
    )
    records = {}

    async def save(service_id, env, profile):
        key = (service_id, env, profile["id"])
        if key in records and records[key] != profile:
            raise HTTPException(409, "Already imported")
        records[key] = copy.deepcopy(profile)

    async def listing(service_id, env):
        return [copy.deepcopy(p) for (s, e, _), p in records.items() if s == service_id and e == env]

    async def remove(service_id, env, identity):
        records.pop((service_id, env, identity), None)

    bridge.profile_store = SimpleNamespace(required=lambda: True, save=save, get_profiles=listing, remove=remove)
    member = SimpleNamespace(role=StudioRole.ADMIN, status=StudioMemberStatus.ACTIVE)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.studio_member = member
        return await call_next(request)

    app.include_router(_import_router(bridge), prefix="/studio/services/{service_id}/load-tests")
    return TestClient(app), bridge, service, config, document, calls, member


def save_payload(preview):
    return {
        "preview_id": preview["preview_id"],
        "name": "Translate test",
        "max_vus": 16,
        "max_iterations": 200,
        "max_duration_seconds": 600,
    }


def test_import_preview_save_retry_delete_without_execution(imported):
    client, bridge, service, config, document, calls, _ = imported
    sources = client.get(BASE + "/sources?search=translation").json()
    assert "config" not in sources["items"][0]
    assert client.post(BASE + "/inspect", json={"run_id": "run-1"}).json()["operations"][0]["path"] == "/translations"
    response = client.post(BASE + "/preview", json={"run_id": "run-1"})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert "config" not in preview and "repo" not in preview
    assert preview["input_schema"]["properties"]["text"]["type"] == "string"
    assert preview["environment"] == "staging"
    payload = save_payload(preview)
    saved = client.post(BASE, json=payload)
    assert saved.status_code == 201, saved.text
    assert client.post(BASE, json=payload).status_code == 201
    assert client.get(BASE).json()["profiles"][0]["max_vus"] == 16
    assert client.post(BASE, json={**payload, "max_vus": 17}).status_code == 409
    assert client.delete(BASE + "/" + saved.json()["id"]).status_code == 200
    assert client.get(BASE).json()["profiles"] == []
    assert all(r.method == "GET" for r in calls)


@pytest.mark.parametrize("change", ["environment", "base_url", "schema", "expired", "other_service"])
def test_preview_is_bound_and_revalidated(imported, change):
    client, bridge, service, config, document, calls, _ = imported
    preview = client.post(BASE + "/preview", json={"run_id": "run-1"}).json()
    if change == "environment":
        service.environment = "production"
    if change == "base_url":
        service.base_url = "http://other.internal"
    if change == "schema":
        document["paths"]["/translations"]["post"]["requestBody"]["content"]["application/json"]["schema"][
            "properties"
        ]["text"]["minLength"] = 3
    if change == "expired":
        preview["preview_id"] = "0" * 32
    target = BASE.replace("/svc/", "/other/") if change == "other_service" else BASE
    assert client.post(target, json=save_payload(preview)).status_code == 409


def test_limits_and_browser_config_rejected(imported):
    client, *_ = imported
    preview = client.post(BASE + "/preview", json={"run_id": "run-1"}).json()
    payload = save_payload(preview)
    assert client.post(BASE, json={**payload, "max_vus": 33}).status_code == 422
    assert client.post(BASE, json={**payload, "max_iterations": 1001}).status_code == 422
    assert client.post(BASE, json={**payload, "config": {}}).status_code == 422
    assert client.post(BASE + "/preview", json={"run_id": "../secret"}).status_code == 422


@pytest.mark.parametrize(
    "role,status", [(StudioRole.READONLY, StudioMemberStatus.ACTIVE), (StudioRole.ADMIN, StudioMemberStatus.BLOCKED)]
)
def test_management_is_admin_only(imported, role, status):
    client, *_, member = imported
    member.role, member.status = role, status
    for path in (BASE, BASE + "/sources"):
        assert client.get(path).status_code == 403
    assert client.post(BASE + "/preview", json={"run_id": "run-1"}).status_code == 403


@pytest.mark.parametrize("kind", ["sdk", "deploy", "headers", "experiment", "access", "schema"])
def test_unsupported_source_has_actionable_error(imported, kind):
    client, bridge, service, config, document, *_ = imported
    if kind == "sdk":
        config["traffic"]["journeys"][0]["path"] = "/relayna/events"
    if kind == "deploy":
        config["runtime"]["mode"] = "deploy"
    if kind == "headers":
        config["traffic"]["journeys"][0]["headers"] = {"Authorization": "secret"}
    if kind == "experiment":
        config["experiment"] = {"enabled": True}
    if kind == "access":
        config["runtime"]["trafficAccess"]["mode"] = "url"
    if kind == "schema":
        document["paths"] = {}
    result = client.post(BASE + "/preview", json={"run_id": "run-1"})
    assert result.status_code == 422, result.text
    assert "cannot be imported" in result.json()["detail"]
    assert "secret" not in result.text


def test_import_discards_sensitive_inputs_and_execution_extras(imported):
    _, _, _, config, *_ = imported
    config["runtime"]["secretEnv"] = ["SECRET"]
    config["runtime"]["config"] = {"password": "private"}
    config["runtime"]["faults"] = ["kill"]
    config["traffic"]["journeys"][0]["body"] = {"token": "private"}
    cleaned = _config(config, 0)
    assert "private" not in json.dumps(cleaned)
    assert cleaned["runtime"]["faults"] == []
    assert cleaned["runtime"]["cleanup"] is False


@pytest.mark.asyncio
async def test_merged_profiles_follow_environment(imported):
    _, bridge, service, *_ = imported
    bridge.profiles = {"svc": {"environment": "staging", "profiles": [{"id": "deployment"}]}}
    await bridge.profile_store.save("svc", "staging", {"id": "saved"})
    assert [p["id"] for p in (await bridge.settings("svc", "staging"))["profiles"]] == ["deployment", "saved"]
    assert (await bridge.settings("svc", "production"))["profiles"] == []


@pytest.mark.parametrize("kind", ["port", "method", "events", "path", "workload"])
def test_import_rejects_inconsistent_execution_target(imported, kind):
    client, _, _, config, *_ = imported
    journey = config["traffic"]["journeys"][0]
    if kind == "port":
        config["runtime"]["trafficAccess"]["servicePort"] = 9999
    if kind == "method":
        journey["method"] = "CONNECT"
    if kind == "events":
        journey["relayna"]["eventsPath"] = "https://elsewhere/events/{task_id}"
    if kind == "path":
        journey["path"] = "//elsewhere"
    if kind == "workload":
        config["deployment"]["workloads"] = []
    assert client.post(BASE + "/preview", json={"run_id": "run-1"}).status_code == 422


def test_import_keeps_file_fixtures_and_named_chamber_admission(imported):
    _, _, _, config, *_ = imported
    config["chamber"] = {"id": "existing-budget", "max_vus": 5}
    config["runtime"]["prometheusUrl"] = "http://prometheus.internal"
    journey = config["traffic"]["journeys"][0]
    journey["requestEncoding"] = "multipart"
    journey["multipart"] = {
        "fields": {"token": "private"},
        "files": [{"field": "file", "path": "/data/.chamber/uploads/fixture.pdf"}],
    }
    cleaned = _config(config, 0)
    assert cleaned["chamber"] == {"id": "existing-budget"}
    assert cleaned["runtime"]["prometheusUrl"] == "http://prometheus.internal"
    assert cleaned["traffic"]["journeys"][0]["multipart"] == {"fields": {}, "files": journey["multipart"]["files"]}


def test_import_requires_authenticated_admin_and_database():
    from relayna_studio._profile_import import _admin, _ProfileStore
    from starlette.requests import Request

    with pytest.raises(HTTPException) as auth:
        _admin(Request({"type": "http", "state": {}}))
    assert auth.value.status_code == 401
    with pytest.raises(HTTPException) as database:
        _ProfileStore(None).required()
    assert database.value.status_code == 503


def test_import_uses_configured_openapi_path(imported):
    client, bridge, _, _, _, calls, _ = imported
    bridge.profiles = {"svc": {"environment": "staging", "openapi_path": "/api/openapi.json", "profiles": []}}
    preview = client.post(BASE + "/preview", json={"run_id": "run-1"})
    assert preview.status_code == 200
    assert client.post(BASE, json=save_payload(preview.json())).status_code == 201
    assert {request.url.path for request in calls if request.url.host == "translation.internal"} == {
        "/api/openapi.json"
    }
