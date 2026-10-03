from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from relayna_studio._chamber_api import _StartConfirmation, _workspace_router
from relayna_studio._chamber_connection import _origin, _redact
from relayna_studio.auth import StudioMemberStatus, StudioRole
from relayna_studio.load_testing import _Chamber
from relayna_studio.registry import StudioOutboundUrlPolicy

ROOT = Path(__file__).resolve().parents[3]
BASE = "/studio/services/svc/load-tests"
API = BASE + "/chamber"
FEATURES = [
    "cleanup_verification",
    "run_metadata",
    "task_pagination",
    "managed_uploads",
    "scenario_document",
    "run_summary",
]


def test_full_documents_preserve_environment_references_but_mask_credentials():
    document = {
        "runtime": {"secretEnv": ["DATABASE_PASSWORD"], "requiredEnv": ["API_TOKEN"]},
        "journey": {"headersFromEnv": {"Authorization": "API_TOKEN"}, "headers": {"Authorization": "Bearer private"}},
        "secretEnv": [],
        "password": "private",
    }
    public = _redact(document)
    assert public["runtime"]["secretEnv"] == ["DATABASE_PASSWORD"]
    assert public["journey"]["headersFromEnv"] == {"Authorization": "API_TOKEN"}
    assert public["secretEnv"] == []
    assert public["password"] == public["journey"]["headers"]["Authorization"] == "[redacted]"
    assert _redact({"headersFromEnv": {"Authorization": "Bearer actual credential"}}) == {
        "headersFromEnv": {"Authorization": "[redacted]"}
    }


@pytest.mark.parametrize(
    "key",
    [
        "client_secret",
        "clientSecret",
        "client-secret",
        "CLIENT_SECRET",
        "client.secret",
        "client secret",
        "oauth_client_secret",
    ],
)
def test_client_secret_variants_are_redacted_in_nested_documents_and_validation_errors(workspace, key):
    client, _, _, member, calls, state, config, _ = workspace
    credential = "synthetic-client-credential"
    config["agents"] = {"oauth": [{key: credential, "client_id": "visible-client"}]}
    config["traffic"]["journeys"][0]["body"] = {key: credential}
    config["runtime"]["secretEnv"] = ["OAUTH_CLIENT_SECRET"]
    config["traffic"]["journeys"][0]["headersFromEnv"] = {"Authorization": "API_TOKEN"}
    planned = client.post(API + "/plans", json={"config": config, "mode": "kubernetes"})
    assert planned.status_code == 201, planned.text
    assert credential not in planned.text
    assert planned.json()["review_config"]["agents"]["oauth"][0]["client_id"] == "visible-client"
    assert planned.json()["review_config"]["runtime"]["secretEnv"] == ["OAUTH_CLIENT_SECRET"]
    assert json.loads(calls[-1].content)["config"] == config

    state["response"] = httpx.Response(200, json={"config": config, "file": {"pathToken": "signed-descriptor"}})
    member.role = StudioRole.READONLY
    readable = client.get(API + "/runs/run-1")
    assert readable.status_code == 200 and credential not in readable.text
    assert readable.json()["file"]["pathToken"] == "signed-descriptor"
    assert readable.json()["config"]["traffic"]["journeys"][0]["headersFromEnv"] == {"Authorization": "API_TOKEN"}

    member.role = StudioRole.ADMIN
    state["response"] = httpx.Response(422, json={"detail": "invalid " + credential})
    invalid = client.post(API + "/actions/validate", json={"nested": [{key: credential}]})
    assert invalid.status_code == 409 and credential not in invalid.text
    assert "invalid [redacted]" in invalid.text


def test_retained_public_snapshots_are_sanitized_without_rewriting_stored_state(workspace):
    _, bridge, *_ = workspace
    record = {
        "id": "old-run",
        "review_config": {"clientSecret": "legacy-credential"},
        "load_summary": {"journeys": [{"client_secret": "legacy-credential"}]},
        "result": {"nested": [{"client-secret": "legacy-credential"}]},
        "connection": {"id": "old-connection", "token_cipher": "encrypted-private"},
    }
    before = copy.deepcopy(record)
    result = bridge.public(record)
    assert "legacy-credential" not in json.dumps(result)
    assert "encrypted-private" not in json.dumps(result)
    assert result["chamber"]["connection_id"] == "old-connection"
    assert record == before


def test_readonly_cached_connection_capabilities_are_sanitized_on_read(workspace, monkeypatch):
    client, bridge, _, member, *_ = workspace
    member.role = StudioRole.READONLY
    stored = json.dumps({"status": "ready", "capabilities": {"client_secret": "legacy-capability-secret"}})
    monkeypatch.setattr(bridge.redis, "get", AsyncMock(return_value=stored))
    response = client.get(API + "/connection")
    assert response.status_code == 200 and "legacy-capability-secret" not in response.text
    assert response.json()["capabilities"]["client_secret"] == "[redacted]"
    assert response.json()["token_configured"] is True


class SettingsDatabase:
    """Exercise the store's SQL values and transaction behavior without a live database."""

    def __init__(self):
        self.value = None
        self.statements = []

    @asynccontextmanager
    async def sessions(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        original = copy.deepcopy(self.value)
        try:
            yield self
        except Exception:
            self.value = original
            raise

    async def scalar(self, statement):
        return copy.deepcopy(self.value)

    async def execute(self, statement):
        values = statement.compile().params
        self.statements.append((statement.table.name, values))
        if statement.table.name.endswith("operator_settings"):
            self.value = copy.deepcopy(values["value"])


@pytest.fixture
def workspace(monkeypatch):
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_URL", "http://chamber.internal")
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_TOKEN", "deployment-secret")
    monkeypatch.setenv("RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("RELAYNA_STUDIO_CHAMBER_PROFILES_PATH", raising=False)
    config = json.loads((ROOT / "docs/examples/studio-chamber-openapi-profiles.json").read_text())[
        "translation-staging"
    ]["profiles"][0]["config"]
    calls = []
    state = {"response": None}

    def upstream(request):
        calls.append(request)
        if state["response"] is not None:
            return state["response"]
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(
                200,
                json={
                    "schema_version": "chamber.ampule.dev/capabilities/v1",
                    "api_features": FEATURES,
                    "readiness": {"cluster": "unchecked"},
                },
            )
        if request.url.path.endswith("/plans") or request.url.path.endswith("/rerun"):
            return httpx.Response(201, json={"run_id": "chamber-plan"})
        if request.url.path == "/api/v1/runs" and request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1", "run_id": "execution-1", "state": "queued"})
        if request.url.path.endswith("/tasks"):
            return httpx.Response(
                200,
                json={
                    "items": [{"task_id": "task/exact.201", "success": False, "total_duration_ms": 123}],
                    "total_count": 305,
                    "pagination": {"page": 3, "total_pages": 4},
                },
            )
        if request.url.path.endswith("/uploads"):
            return httpx.Response(
                201,
                json={
                    "file": {
                        "field": "file",
                        "path": "/managed/file",
                        "pathToken": "signed-token",
                        "filename": "input.txt",
                        "size": 5,
                    }
                },
            )
        if request.url.path.startswith("/api/v1/runs/"):
            return httpx.Response(
                200,
                json={
                    "config": config,
                    "result": {"status": "inconclusive", "headers": {"X-API-Key": "secret-input"}},
                    "run_dir": "/private/run",
                    "token_cipher": "hidden",
                    "relayna": {"tasks": [], "total_task_count": 305, "tasks_truncated": True},
                },
            )
        return httpx.Response(200, json={"state": "completed", "cleanup_required": False})

    service = SimpleNamespace(
        name="Translation", environment="staging", status="healthy", base_url="http://svc.internal"
    )
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    bridge = _Chamber(
        SimpleNamespace(get_service=AsyncMock(return_value=service)),
        redis,
        httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
        StudioOutboundUrlPolicy(allowed_hosts=(".internal",)),
    )
    database = SettingsDatabase()
    bridge.connections.database = database
    member = SimpleNamespace(role=StudioRole.ADMIN, status=StudioMemberStatus.ACTIVE)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.studio_member = member
        return await call_next(request)

    app.include_router(_workspace_router(bridge), prefix=BASE.replace("/svc/", "/{service_id}/"))
    # Add legacy actions using the same bridge, rather than creating another adapter.
    from fastapi import APIRouter

    actions = APIRouter()

    @actions.post(BASE + "/{reference}/start", response_model=None)
    async def start(reference: str, payload: _StartConfirmation):
        return await bridge.start(
            "svc", reference, confirmed_target=payload.confirmed_target, confirmed_faults=payload.confirmed_faults
        )

    app.include_router(actions)
    return TestClient(app), bridge, database, member, calls, state, config, service


def test_connection_encrypts_token_never_echoes_and_rotates_safely(workspace):
    client, bridge, database, _, calls, _, _, _ = workspace
    response = client.put(API + "/connection", json={"mode": "ui", "url": "http://new.internal", "token": "ui-secret"})
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "ui"
    assert "ui-secret" not in json.dumps(database.value)
    assert "token_cipher" not in response.text
    assert bridge.connections.token(database.value) == "ui-secret"
    assert client.put(API + "/connection", json={"mode": "ui", "url": "http://new.internal"}).status_code == 200
    assert client.put(API + "/connection", json={"mode": "ui", "url": "http://other.internal"}).status_code == 422
    assert (
        client.put(
            API + "/connection", json={"mode": "ui", "url": "http://169.254.169.254", "token": "secret"}
        ).status_code
        == 422
    )
    assert not calls
    assert database.statements[-1][0].endswith("audit_log")
    assert "ui-secret" not in json.dumps(database.statements, default=str)


def test_deployment_fallback_disable_and_missing_encryption_key(workspace, monkeypatch):
    client, bridge, database, *_ = workspace
    assert client.get(API + "/connection").json()["source"] == "deployment"
    assert client.put(API + "/connection", json={"mode": "disabled"}).status_code == 200
    assert client.get(API + "/connection").json()["status"] == "not_configured"
    assert client.put(API + "/connection", json={"mode": "deployment"}).status_code == 200
    assert client.get(API + "/connection").json()["url"] == "http://chamber.internal"
    monkeypatch.delenv("RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY")
    assert client.get(API + "/connection").json()["ui_settings_available"] is False
    assert (
        client.put(
            API + "/connection", json={"mode": "ui", "url": "http://new.internal", "token": "secret"}
        ).status_code
        == 503
    )
    bridge.connections.database = None
    assert client.put(API + "/connection", json={"mode": "deployment"}).status_code == 503


@pytest.mark.parametrize(
    "url",
    [
        "http://u:p@chamber.internal",
        "http://chamber.internal/path",
        "http://chamber.internal?secret=x",
        "ftp://chamber.internal",
        "http://chamber.internal:invalid",
    ],
)
def test_ui_origin_rejects_credential_path_and_invalid_port(url):
    with pytest.raises((HTTPException, ValueError)):
        _origin(url)


def test_connection_health_distinguishes_api_compatibility_auth_and_readiness(workspace):
    client, _, _, _, calls, state, *_ = workspace
    ready = client.post(API + "/connection/test").json()
    assert ready["status"] == "ready" and ready["last_success_at"]
    assert ready["capabilities"]["readiness"]["cluster"] == "unchecked"
    state["response"] = httpx.Response(401, json={"detail": "deployment-secret"})
    failed = client.post(API + "/connection/test").json()
    assert failed["status"] == "unauthorized" and "deployment-secret" not in json.dumps(failed)
    assert failed["last_success_at"] == ready["last_success_at"]
    state["response"] = httpx.Response(
        200, json={"schema_version": "chamber.ampule.dev/capabilities/v1", "api_features": []}
    )
    assert client.post(API + "/connection/test").json()["status"] == "limited"
    state["response"] = httpx.Response(200, json={"schema_version": "unknown"})
    assert client.post(API + "/connection/test").json()["status"] == "incompatible"
    assert all(r.headers.get("X-Request-ID") for r in calls)


def test_advanced_review_preserves_full_config_requires_confirmations_and_pins_connection(workspace):
    client, bridge, database, _, calls, _, config, _ = workspace
    config["traffic"]["load"] = {"model": "capacity", "stages": [{"ratePerSecond": 3, "durationSeconds": 30}]}
    config["traffic"]["journeys"].append(copy.deepcopy(config["traffic"]["journeys"][0]))
    config["experiment"] = {"family": "dependency_delay", "pod": "target", "dependencyPod": "redis-0"}
    config["agents"] = {"mode": "live", "exclude": ["report-writer"]}
    result = client.post(API + "/plans", json={"config": config, "mode": "kubernetes", "name": "Advanced capacity"})
    assert result.status_code == 201, result.text
    plan = result.json()
    assert plan["requires_fault_confirmation"] and plan["requires_target_confirmation"]
    submitted = json.loads(calls[-1].content)
    assert submitted["config"] == config
    assert submitted["origin"]["studio_service_id"] == "svc"
    assert not any(r.url.path == "/api/v1/runs" for r in calls)
    assert "connection" not in plan
    assert (
        client.put(
            API + "/connection", json={"mode": "ui", "url": "http://new.internal", "token": "new-secret"}
        ).status_code
        == 200
    )
    start = BASE + "/" + plan["id"] + "/start"
    response = client.post(start, json={})
    assert response.status_code == 422, response.text
    assert client.post(start, json={"confirmed_target": True}).status_code == 422
    started = client.post(start, json={"confirmed_target": True, "confirmed_faults": True})
    assert started.status_code == 200, started.text
    assert calls[-1].url.host == "chamber.internal"
    assert calls[-1].headers["Authorization"] == "Bearer deployment-secret"
    assert json.loads(calls[-1].content)["origin"] == submitted["origin"]
    assert calls[-1].headers["Idempotency-Key"] == "studio-" + plan["id"]
    count = len(calls)
    assert client.post(start, json={}).status_code == 200
    assert len(calls) == count
    assert client.get(API + "/runs/execution-1?reference=" + plan["id"]).status_code == 200
    assert calls[-1].url.host == "chamber.internal"
    assert client.get(API + "/runs/other?reference=" + plan["id"]).status_code == 404


def test_complete_tasks_and_evidence_filters_use_canonical_api(workspace):
    client, _, _, _, calls, *_ = workspace
    response = client.get(API + "/runs/run-1/tasks?page=3&page_size=100&failed_first=true&status=failed&search=exact")
    assert response.json()["items"][0]["task_id"] == "task/exact.201"
    assert response.json()["total_count"] == 305
    assert calls[-1].url.params["page"] == "3"
    client.get(
        API
        + "/runs/run-1/evidence-explorer?start=2026-10-03T00:00:00Z&end=2026-10-03T00:01:00Z"
        + "&finding=f1&workload=worker&evil=ignored"
    )
    assert calls[-1].url.params["finding"] == "f1" and calls[-1].url.params["workload"] == "worker"
    assert "evil" not in calls[-1].url.params
    assert client.get(API + "/runs/run-1/tasks?page_size=1000").status_code == 422
    client.get(API + "/scenarios/user/release.baseline/document")
    assert calls[-1].url.path.endswith("/release.baseline/document")
    response = client.get(API + "/runs/run-1")
    assert calls[-1].url.params["include_task_details"] == "false"
    assert "secret-input" not in response.text and response.json()["token_cipher"] == "[redacted]"
    assert "run_dir" not in response.text


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("PUT", "/connection", {"mode": "disabled"}),
        ("POST", "/connection/test", {}),
        ("POST", "/plans", {}),
        ("POST", "/actions/inspect", {"repo": "/repo"}),
        ("POST", "/runs/run-1/archive", {"archived": True}),
        ("POST", "/runs/run-1/tags", {"tags": []}),
        ("POST", "/runs/run-1/rerun", {}),
        ("POST", "/jobs/job-1/cleanup-verified", {"confirmed": True}),
    ],
)
def test_mutations_are_admin_only(workspace, method, path, payload):
    client, _, _, member, calls, *_ = workspace
    member.role = StudioRole.READONLY
    assert client.request(method, API + path, json=payload).status_code == 403
    assert not calls
    assert client.get(API + "/connection").status_code == 200
    member.status = StudioMemberStatus.BLOCKED
    assert client.get(API + "/connection").status_code == 403


def test_upload_download_recovery_and_rerun_are_native_and_bounded(workspace):
    client, _, _, _, calls, state, *_ = workspace
    upload = client.post(
        API + "/uploads", data={"field": "file"}, files={"file": ("input.txt", b"hello", "text/plain")}
    )
    assert upload.status_code == 200 and upload.json()["file"]["pathToken"] == "signed-token"
    assert calls[-1].url.path.endswith("/uploads") and b"hello" in calls[-1].content
    assert calls[-1].extensions["timeout"]["write"] == 240
    assert (
        client.post(API + "/uploads", data={"field": "../secret"}, files={"file": ("f.txt", b"x")}).status_code == 422
    )
    assert client.post(API + "/jobs/job-1/cleanup-verified", json={"confirmed": False}).status_code == 422
    assert client.post(API + "/jobs/job-1/cleanup-verified", json={"confirmed": True}).status_code == 200
    rerun = client.post(API + "/runs/run-1/rerun", json={})
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["chamber"]["plan_id"] == "chamber-plan" and rerun.json()["state"] == "planned"
    state["response"] = httpx.Response(200, content=b"<script>unsafe()</script>", headers={"Content-Type": "text/html"})
    download = client.get(API + "/download/run-1/report?format=html")
    assert download.headers["Content-Type"] == "application/octet-stream"
    assert "attachment" in download.headers["Content-Disposition"]
    assert download.headers["Content-Security-Policy"] == "sandbox"


def test_validation_errors_retain_field_reason_without_input_credentials(workspace):
    client, _, _, _, _, state, *_ = workspace
    state["response"] = httpx.Response(
        422,
        json={
            "detail": [
                {
                    "loc": ["body", "traffic", "load", "ratePerSecond"],
                    "msg": "must be positive",
                    "input": "deployment-secret",
                }
            ]
        },
    )
    response = client.post(API + "/actions/validate", json={"content": "{}"})
    assert response.status_code == 409 and "ratePerSecond" in response.text and "must be positive" in response.text
    assert "deployment-secret" not in response.text


@pytest.mark.parametrize(
    "role,csrf,expected", [("admin", "valid", 200), ("admin", "", 403), ("readonly", "valid", 403)]
)
def test_real_auth_middleware_covers_connection_and_native_actions(workspace, role, csrf, expected):
    from relayna_studio.auth import StudioAuthMiddleware

    _, bridge, _, _, calls, *_ = workspace
    member = SimpleNamespace(role=StudioRole(role), status=StudioMemberStatus.ACTIVE, user_id="actor")
    auth = SimpleNamespace(session_context=AsyncMock(return_value=(SimpleNamespace(csrf_token="valid"), member)))
    app = FastAPI()
    app.include_router(_workspace_router(bridge), prefix=BASE.replace("/svc/", "/{service_id}/"))
    app.add_middleware(StudioAuthMiddleware, service=auth)
    client = TestClient(app)
    assert client.get(API + "/connection").status_code == 200
    assert (
        client.put(API + "/connection", json={"mode": "disabled"}, headers={"X-CSRF-Token": csrf}).status_code
        == expected
    )
    if expected != 200:
        assert not calls
    auth.session_context.return_value = None
    assert client.get(API + "/connection").status_code == 401


def test_unusable_credentials_and_invalid_config_do_not_create_plans(workspace, monkeypatch):
    client, bridge, database, _, calls, _, config, _ = workspace
    assert client.post(API + "/plans", json={"config": {**config, "traffic": {"journeys": []}}}).status_code == 422
    assert client.post(API + "/plans", json={"config": {**config, "agents": []}}).status_code == 422
    assert (
        client.post(API + "/plans", json={"config": {**config, "agents": {"token": "[redacted]"}}}).status_code == 422
    )
    assert not calls
    client.put(API + "/connection", json={"mode": "ui", "url": "http://chamber.internal", "token": "private"})
    database.value["token_cipher"] = "tampered"
    assert client.post(API + "/connection/test").json()["status"] == "unavailable"
    assert not calls


def test_catalog_history_discovery_and_metadata_forward_only_supported_contracts(workspace):
    client, _, _, _, calls, *_ = workspace
    assert client.get(API + "/catalog/capabilities").status_code == 200
    assert calls[-1].url.path.endswith("/capabilities")
    assert client.get(API + "/scenarios/user/release.baseline").status_code == 200
    assert calls[-1].url.params["service_name"] == "Translation"
    history = client.get(API + "/runs?q=release&state=completed&outcome=inconclusive&page=2&page_size=10&archived=true")
    assert history.status_code == 200
    assert dict(calls[-1].url.params)["archived"] == "true"
    assert calls[-1].url.params["q"] == "release" and calls[-1].url.params["page"] == "2"
    assert client.post(API + "/actions/discover", json={"context": "cluster", "namespace": "dev"}).status_code == 200
    assert calls[-1].method == "GET" and calls[-1].url.params["context"] == "cluster"
    assert client.post(API + "/actions/discover", json={"namespace": "dev"}).status_code == 422
    for action, payload in [("archive", {"archived": True}), ("tags", {"tags": ["release"]})]:
        assert client.post(API + f"/runs/run-1/{action}", json=payload).status_code == 200
        assert calls[-1].url.params["include_task_details"] == "false"
        assert json.loads(calls[-1].content) == payload


def test_bound_comparison_and_cleanup_cannot_target_another_job(workspace):
    client, _, _, _, calls, _, config, _ = workspace
    plan = client.post(API + "/plans", json={"config": config}).json()
    reference = plan["id"]
    client.post(BASE + f"/{reference}/start", json={"confirmed_target": True})
    compare = {"baseline_run_id": "baseline", "candidate_run_id": "execution-1", "reference": reference}
    assert client.post(API + "/actions/compare", json=compare).status_code == 200
    assert "reference" not in json.loads(calls[-1].content)
    assert client.post(API + "/actions/compare", json={**compare, "candidate_run_id": "foreign"}).status_code == 404
    assert (
        client.post(API + f"/jobs/foreign/cleanup-verified?reference={reference}", json={"confirmed": True}).status_code
        == 404
    )
    assert (
        client.post(API + f"/jobs/job-1/cleanup-verified?reference={reference}", json={"confirmed": True}).status_code
        == 200
    )


def test_rejected_configuration_is_never_sent_and_missing_plan_id_is_actionable(workspace):
    client, _, _, _, calls, state, config, _ = workspace
    for invalid in [
        {**config, "service": []},
        {**config, "traffic": {**config["traffic"], "load": []}},
        {**config, "runtime": {**config["runtime"], "kubernetesContext": ""}},
        {**config, "oversized": "x" * (1024 * 1024)},
    ]:
        assert client.post(API + "/plans", json={"config": invalid}).status_code == 422
    assert client.post(API + "/actions/inspect", json={"repo": "x" * (1024 * 1024)}).status_code == 422
    assert not calls
    state["response"] = httpx.Response(201, json={"unexpected": True})
    assert client.post(API + "/plans", json={"config": config}).status_code == 502


def test_disabled_status_and_cached_health_do_not_claim_target_readiness(workspace):
    client, _, _, _, calls, *_ = workspace
    ready = client.post(API + "/connection/test").json()
    saved = client.get(API + "/connection").json()
    assert saved["checked_at"] == ready["checked_at"]
    assert saved["status"] == "ready" and saved["capabilities"]["readiness"]["cluster"] == "unchecked"
    client.put(API + "/connection", json={"mode": "disabled"})
    count = len(calls)
    assert client.post(API + "/connection/test").json()["status"] == "not_configured"
    assert len(calls) == count
    assert (
        client.put(
            API + "/connection", json={"mode": "ui", "url": "http://chamber.internal", "token": "bad\nheader"}
        ).status_code
        == 422
    )


def test_unsanitized_upstream_detail_never_discloses_submitted_credentials(workspace):
    client, _, _, _, _, state, *_ = workspace
    state["response"] = httpx.Response(422, json={"detail": "bad nested-password deployment-secret"})
    response = client.post(API + "/actions/validate", json={"items": [{"password": "nested-password"}]})
    assert response.status_code == 409
    assert "nested-password" not in response.text and "deployment-secret" not in response.text
    state["response"] = httpx.Response(422, json={"detail": {"input": "deployment-secret"}})
    assert "deployment-secret" not in client.post(API + "/actions/validate", json={}).text
    state["response"] = httpx.Response(422, json={"detail": "deployment-secret" * 10000})
    assert "deployment-secret" not in client.post(API + "/actions/validate", json={}).text


def test_workspace_requires_member_and_rejects_invalid_chamber_identity(workspace):
    _, bridge, *_ = workspace
    app = FastAPI()
    app.include_router(_workspace_router(bridge), prefix=BASE.replace("/svc/", "/{service_id}/"))
    assert TestClient(app).get(API + "/connection").status_code == 401
    from relayna_studio._chamber_api import _identity

    with pytest.raises(HTTPException):
        _identity("../foreign")
    assert _redact({"secretEnv": "actual credential"}) == {"secretEnv": "[redacted]"}


@pytest.mark.parametrize(
    "encoded",
    [
        "client_secret=synthetic-encoded-credential&client_id=public",
        "client%5Fsecret=synthetic%2Dencoded%2Dcredential&client_id=public",
        '{"clientSecret":"synthetic-encoded-credential","client_id":"public"}',
        "https://prometheus.internal?client_secret=synthetic-encoded-credential&target=worker",
        "https://prometheus.internal#client_secret=synthetic-encoded-credential",
    ],
)
def test_encoded_client_secrets_are_masked_in_review_readonly_and_validation_paths(workspace, encoded):
    client, _, _, member, calls, state, config, _ = workspace
    config["traffic"]["journeys"][0].update(
        requestEncoding="raw", contentType="application/x-www-form-urlencoded", body=encoded
    )
    config["runtime"]["prometheusUrl"] = encoded
    planned = client.post(API + "/plans", json={"config": config, "mode": "kubernetes"})
    assert planned.status_code == 201, planned.text
    assert "synthetic-encoded-credential" not in planned.text
    assert "synthetic%2Dencoded%2Dcredential" not in planned.text
    assert json.loads(calls[-1].content)["config"] == config
    member.role = StudioRole.READONLY
    state["response"] = httpx.Response(200, json={"config": config})
    read = client.get(API + "/runs/run-1")
    assert read.status_code == 200 and "synthetic-encoded-credential" not in read.text
    assert "synthetic%2Dencoded%2Dcredential" not in read.text
    member.role = StudioRole.ADMIN
    state["response"] = httpx.Response(422, json={"detail": "invalid synthetic-encoded-credential"})
    error = client.post(API + "/actions/validate", json={"body": encoded})
    assert error.status_code == 409 and "synthetic-encoded-credential" not in error.text


def test_redaction_preserves_plaintext_and_noncredential_encoded_controls():
    values = [
        "client_secret",
        "Ordinary request body",
        '{ "client_id": "public" }',
        "client_id=public&text=hello+world",
        "https://metrics.internal/path?target=worker#overview",
        " \t HTTPS://metrics.internal/path?target=worker#overview \n",
        "{not-json}",
    ]
    for value in values:
        assert _redact({"body": value}) == {"body": value}
    assert _redact({"body": "https://[invalid"}) == {"body": "[redacted]"}


def test_diagnostics_mask_secret_arrays_and_duplicate_encoded_fields(workspace):
    client, _, _, _, _, state, *_ = workspace
    state["response"] = httpx.Response(422, json={"detail": "invalid first-secret second-secret array-secret"})
    result = client.post(
        API + "/actions/validate",
        json={
            "url": "https://metrics.internal?client_secret=first-secret&client_secret=second-secret",
            "client_secret": ["array-secret"],
        },
    )
    assert result.status_code == 409
    assert not any(secret in result.text for secret in ("first-secret", "second-secret", "array-secret"))


def test_diagnostics_handle_malformed_encoded_inputs_and_nested_secret_values(workspace):
    client, _, _, _, _, state, *_ = workspace
    state["response"] = httpx.Response(422, json={"detail": "configuration invalid: nested-private-value"})
    error = client.post(
        API + "/actions/validate",
        json={"client_secret": {"value": "nested-private-value"}, "body": "{not-json}", "url": "https://[invalid"},
    )
    assert error.status_code == 409
    assert "configuration invalid: [redacted]" in error.text
    assert "nested-private-value" not in error.text


def test_diagnostics_mask_raw_percent_encoded_and_json_escaped_credential_echoes(workspace):
    client, _, _, _, _, state, *_ = workspace
    state["response"] = httpx.Response(
        422, json={"detail": 'invalid synthetic%2Dencoded%2Dcredential or quoted\\"credential'}
    )
    error = client.post(
        API + "/actions/validate",
        json={"body": "client%5Fsecret=synthetic%2Dencoded%2Dcredential", "client_secret": 'quoted"credential'},
    )
    assert error.status_code == 409
    assert "synthetic%2Dencoded%2Dcredential" not in error.text
    assert "quoted" not in error.text


@pytest.mark.parametrize(
    "url",
    [
        "HTTPS://user:synthetic-url-credential@metrics.internal",
        " \t HTTPS://user:synthetic-url-credential@metrics.internal \n",
        "HtTp://user:synthetic-url-credential@metrics.internal/path?target=worker",
        "HTTPS://user:synthetic%2Durl%2Dcredential@metrics.internal",
        "HTTPS://metrics.internal?client_secret=synthetic-url-credential",
        " HTTPS://metrics.internal#client_secret=synthetic-url-credential ",
    ],
)
def test_url_credentials_are_masked_across_scheme_case_and_whitespace(workspace, url):
    client, bridge, _, member, calls, state, config, _ = workspace
    config["runtime"]["prometheusUrl"] = url
    planned = client.post(API + "/plans", json={"config": config})
    assert planned.status_code == 201, planned.text
    assert "synthetic-url-credential" not in planned.text
    assert "synthetic%2Durl%2Dcredential" not in planned.text
    assert json.loads(calls[-1].content)["config"] == config
    assert "synthetic" not in json.dumps(bridge.public({"review_config": config}))
    member.role = StudioRole.READONLY
    state["response"] = httpx.Response(200, json={"config": config})
    read = client.get(API + "/runs/run-1")
    assert read.status_code == 200 and "synthetic" not in read.text
    member.role = StudioRole.ADMIN
    state["response"] = httpx.Response(422, json={"detail": "invalid URL " + url})
    error = client.post(API + "/actions/validate", json={"url": url})
    assert error.status_code == 409 and "synthetic" not in error.text
    assert "invalid URL" in error.text


def test_upload_total_deadline_cancels_progress_and_releases_upstream(workspace, monkeypatch):
    client, _, _, _, calls, state, *_ = workspace
    timeout = asyncio.timeout
    budgets = []

    def accelerated_deadline(seconds):
        budgets.append(seconds)
        return timeout(0.03 if seconds == 240 else None)

    monkeypatch.setattr("relayna_studio._chamber_connection.asyncio.timeout", accelerated_deadline)

    class ProgressStream(httpx.AsyncByteStream):
        closed = False
        parts = 0

        async def __aiter__(self):
            for _ in range(100):
                self.parts += 1
                yield b" "
                await asyncio.sleep(0.001)

        async def aclose(self):
            self.closed = True

    stream = ProgressStream()
    state["response"] = httpx.Response(200, stream=stream)
    response = client.post(API + "/uploads", files={"file": ("input.txt", b"hello", "text/plain")})
    assert response.status_code == 502
    assert response.headers["X-Chamber-Error"] == "unreachable"
    assert response.headers["X-Request-ID"]
    assert stream.parts > 1 and stream.closed
    assert budgets == [240]
    assert calls[-1].extensions["timeout"]["read"] == 240
    state["response"] = httpx.Response(200, json={"state": "completed"})
    assert client.get(API + "/runs/run-1").status_code == 200
    assert budgets[-1] is None
