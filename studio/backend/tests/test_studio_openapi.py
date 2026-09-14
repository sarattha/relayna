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
from relayna_studio._openapi import _is_sdk_operation, _request_schema
from relayna_studio.load_testing import _check_schema, _create_load_testing_router
from relayna_studio.registry import StudioOutboundUrlPolicy


def document(schema=None):
    return {
        "openapi": "3.1.0",
        "paths": {
            "/translations": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Translation"}}},
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "Translation": schema
                or {
                    "type": "object",
                    "required": ["text", "language_target"],
                    "properties": {
                        "text": {"type": "string", "minLength": 1, "maxLength": 100},
                        "language_target": {"type": "string", "enum": ["Thai", "English"]},
                        "priority": {
                            "anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "null"}],
                            "default": None,
                        },
                        "id": {"type": "string", "readOnly": True},
                    },
                }
            }
        },
    }


JOURNEY = {"path": "/translations", "method": "POST", "requestEncoding": "json"}


def test_fastapi_references_nullable_fields_and_readonly_properties():
    imported = _request_schema(document(), JOURNEY)
    _check_schema(imported)
    assert imported["properties"]["priority"]["type"] == ["integer", "null"]
    assert "id" not in imported["properties"]
    assert imported["additionalProperties"] is False
    assert imported["required"] == ["text", "language_target"]


def test_openapi30_composition_and_exclusive_limits():
    schema = {
        "allOf": [
            {"type": "object", "properties": {"ratio": {"type": "number", "minimum": 0, "exclusiveMinimum": True}}},
            {"type": "object", "properties": {"note": {"type": "string", "nullable": True}}},
        ]
    }
    doc = document(schema)
    doc["openapi"] = "3.0.3"
    result = _request_schema(doc, JOURNEY)
    _check_schema(result)
    assert result["properties"]["ratio"]["exclusiveMinimum"] == 0
    assert result["properties"]["note"]["type"] == ["string", "null"]


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://external.invalid/schema"},
        {"$ref": "#/components/schemas/Translation"},
        {"oneOf": [{"type": "string"}, {"type": "integer"}]},
        {"type": "object", "properties": {"self": {"$ref": "#/components/schemas/Translation"}}},
        {
            "allOf": [
                {"type": "object", "additionalProperties": False, "properties": {"a": {"type": "string"}}},
                {"type": "object", "properties": {"b": {"type": "string"}}},
            ]
        },
    ],
)
def test_ambiguous_external_recursive_and_closed_composed_schemas_fail(schema):
    with pytest.raises(ValueError):
        _check_schema(_request_schema(document(schema), JOURNEY))


@pytest.mark.parametrize(
    "path",
    [
        "/relayna/capabilities",
        "/relayna/runtime/backpressure",
        "/events/feed",
        "/status/{task_id}",
        "/history",
        "/workflow/topology",
        "/workflow/stages",
        "/executions/{task_id}/graph",
        "/dlq/messages",
        "/broker/dlq/queues",
        "/failed-tasks",
        "/api/v1/relayna/health/workers",
        "/v2/dlq/messages",
    ],
)
def test_sdk_operations_are_excluded(path):
    assert _is_sdk_operation(path)


@pytest.mark.parametrize("path", ["/translations", "/ocr", "/tasks", "/orders", "/api/v1/translations"])
def test_service_operations_are_not_mistaken_for_sdk_routes(path):
    assert not _is_sdk_operation(path)
    assert _is_sdk_operation(path, {"tags": ["relayna:status"]})


def test_multipart_file_fields_match_pinned_fixtures():
    doc = document()
    doc["paths"]["/translations"]["post"]["requestBody"]["content"] = {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file", "language"],
                "properties": {"file": {"type": "string", "format": "binary"}, "language": {"type": "string"}},
            }
        }
    }
    journey = {
        **JOURNEY,
        "requestEncoding": "multipart",
        "multipart": {"files": [{"field": "file", "path": "/fixture.pdf"}]},
    }
    assert set(_request_schema(doc, journey)["properties"]) == {"language"}
    journey["multipart"]["files"] = []
    with pytest.raises(ValueError, match="fixtures"):
        _request_schema(doc, journey)


@pytest.fixture
def discovery(monkeypatch, tmp_path):
    config = json.loads(
        (Path(__file__).resolve().parents[3] / "docs/examples/studio-chamber-profiles.json").read_text()
    )
    config["translation-staging"]["profiles"][0].pop("input_schema")
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_URL", "http://chamber.internal")
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_TOKEN", "secret")
    monkeypatch.setenv("RELAYNA_STUDIO_CHAMBER_PROFILES_PATH", str(path))
    current = {"document": document(), "response": None}
    calls = []

    def upstream(request):
        calls.append(request)
        if request.url.host == "service.internal":
            assert "authorization" not in request.headers
            assert request.url.path == "/openapi.json"
            return current["response"] or httpx.Response(200, json=current["document"])
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"run_id": "approved-plan"})

    registry = SimpleNamespace(
        get_service=AsyncMock(
            return_value=SimpleNamespace(environment="staging", status="healthy", base_url="http://service.internal")
        )
    )
    app = FastAPI()
    app.include_router(
        _create_load_testing_router(
            registry,
            fakeredis.aioredis.FakeRedis(),
            httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
            StudioOutboundUrlPolicy(allowed_hosts=["service.internal"]),
        )
    )
    return TestClient(app), current, calls, registry


BASE = "/studio/services/translation-staging/load-tests"


def test_discovery_to_plan_uses_fresh_schema_and_rejects_drift(discovery):
    client, current, calls, _ = discovery
    options = client.get(f"{BASE}/profiles").json()
    assert options["available"] and not options["errors"]
    profile = options["profiles"][0]
    assert profile["schema_source"] == "openapi"
    payload = {
        "profile_id": profile["id"],
        "schema_revision": profile["schema_revision"],
        "inputs": {"text": "Hello", "language_target": "Thai", "priority": None},
        "vus": 1,
        "iterations": 1,
        "duration_seconds": 30,
    }
    assert client.post(f"{BASE}/plans", json=payload).status_code == 201
    assert json.loads(calls[-1].content)["config"]["traffic"]["journeys"][0]["body"]["priority"] is None
    current["document"]["components"]["schemas"]["Translation"]["properties"]["text"]["maxLength"] = 3
    assert client.post(f"{BASE}/plans", json=payload).status_code == 409
    payload["schema_revision"] = client.get(f"{BASE}/profiles").json()["profiles"][0]["schema_revision"]
    assert client.post(f"{BASE}/plans", json=payload).status_code == 422
    assert len([call for call in calls if call.url.host == "chamber.internal"]) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "http://external.invalid"}),
        httpx.Response(404),
        httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)),
        httpx.Response(200, json=[]),
    ],
)
def test_unavailable_invalid_oversized_and_redirected_documents_fail_closed(discovery, response):
    client, current, calls, _ = discovery
    current["response"] = response
    result = client.get(f"{BASE}/profiles").json()
    assert not result["available"] and result["errors"]
    assert len(calls) == 1


def test_outbound_policy_blocks_untrusted_service(discovery):
    client, _, calls, registry = discovery
    registry.get_service.return_value.base_url = "http://169.254.169.254"
    result = client.get(f"{BASE}/profiles").json()
    assert not result["available"] and result["errors"]
    assert not calls


def test_required_parameters_and_sdk_tags_are_not_ignored(discovery):
    client, current, _, _ = discovery
    operation = current["document"]["paths"]["/translations"]["post"]
    operation["parameters"] = [{"in": "query", "name": "account", "required": True, "schema": {"type": "string"}}]
    assert not client.get(f"{BASE}/profiles").json()["available"]
    operation.pop("parameters")
    operation["tags"] = ["relayna"]
    assert not client.get(f"{BASE}/profiles").json()["available"]
