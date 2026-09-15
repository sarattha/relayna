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
        "/metrics",
        "/api/v1/metrics",
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


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#/missing"},
        {"type": ["string", "integer"]},
        {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        {"type": "object", "additionalProperties": {"type": "string"}},
        {"allOf": [{"type": "string"}, {"type": "integer"}]},
        {
            "allOf": [
                {"properties": {"a": {"type": "string"}}},
                {"properties": {"a": {"type": "integer"}}},
            ]
        },
        {"type": "object", "properties": {"bad": False}},
    ],
)
def test_unsupported_schema_constraints_report_errors(schema):
    with pytest.raises(ValueError):
        _request_schema(document(schema), JOURNEY)


def test_required_composition_arrays_and_nullable_type_lists():
    doc = document(
        {
            "allOf": [
                {"properties": {"a": {"type": ["string", "null"]}}, "required": ["a"]},
                {
                    "properties": {
                        "b": {"type": "array", "items": {"type": "number", "maximum": 9, "exclusiveMaximum": False}}
                    },
                    "required": ["b"],
                },
            ]
        }
    )
    schema = _request_schema(doc, JOURNEY)
    _check_schema(schema)
    assert schema["required"] == ["a", "b"]
    assert schema["properties"]["b"]["maxItems"] == 100
    assert schema["properties"]["b"]["items"] == {"type": "number", "maximum": 9}
    assert schema["properties"]["a"]["type"] == ["string", "null"]


def test_field_budget_is_bounded():
    with pytest.raises(ValueError, match="1000 fields"):
        _request_schema(document({"properties": {str(n): {"type": "string"} for n in range(1001)}}), JOURNEY)


@pytest.mark.parametrize(
    "journey",
    [
        {**JOURNEY, "path": "/translations/{id}"},
        {**JOURNEY, "path": "/translations?x=1"},
        {**JOURNEY, "method": "DELETE"},
        {**JOURNEY, "requestEncoding": "raw"},
        {**JOURNEY, "requestEncoding": "form"},
        {**JOURNEY, "requestEncoding": "none"},
    ],
)
def test_operation_contract_mismatch_is_rejected(journey):
    with pytest.raises(ValueError):
        _request_schema(document(), journey)


def test_bodyless_raw_and_custom_encoding_contracts():
    doc = document()
    operation = doc["paths"]["/translations"]["post"]
    operation.pop("requestBody")
    assert _request_schema(doc, {**JOURNEY, "requestEncoding": "none"})["properties"] == {}
    operation["requestBody"] = {"content": {"text/plain": {"schema": {"type": "string", "minLength": 1}}}}
    schema = _request_schema(doc, {**JOURNEY, "requestEncoding": "raw", "contentType": "text/plain"})
    assert schema["required"] == ["body"]
    assert schema["properties"]["body"]["minLength"] == 1
    operation["requestBody"]["content"]["text/plain"]["encoding"] = {"body": {"style": "form"}}
    with pytest.raises(ValueError, match="Custom form"):
        _request_schema(doc, {**JOURNEY, "requestEncoding": "raw", "contentType": "text/plain"})
    doc["openapi"] = "2.0"
    with pytest.raises(ValueError, match="3.0 or 3.1"):
        _request_schema(doc, JOURNEY)


@pytest.mark.parametrize("path", ["//evil.invalid/schema", "/a/../schema", "/schema?q=1", "/schema%2fsecret"])
@pytest.mark.asyncio
async def test_openapi_source_cannot_escape_registered_service(monkeypatch, path):
    from relayna_studio.load_testing import _Chamber

    monkeypatch.delenv("RELAYNA_STUDIO_CHAMBER_URL", raising=False)
    bridge = _Chamber(SimpleNamespace(), fakeredis.aioredis.FakeRedis(), httpx.AsyncClient())
    with pytest.raises(ValueError, match="service-relative"):
        await bridge.openapi(SimpleNamespace(base_url="http://service.internal"), {"openapi_path": path})


@pytest.mark.asyncio
async def test_discovery_keeps_manual_profile_when_network_fails(monkeypatch):
    import copy

    from relayna_studio.load_testing import _Chamber

    monkeypatch.delenv("RELAYNA_STUDIO_CHAMBER_URL", raising=False)
    bridge = _Chamber(SimpleNamespace(), fakeredis.aioredis.FakeRedis(), httpx.AsyncClient())
    profile = json.loads(
        (Path(__file__).resolve().parents[3] / "docs/examples/studio-chamber-profiles.json").read_text()
    )["translation-staging"]["profiles"][0]
    imported = copy.deepcopy(profile)
    imported.pop("input_schema")
    sdk = copy.deepcopy(profile)
    sdk["config"]["traffic"]["journeys"][0]["path"] = "/relayna/capabilities"
    bridge.profiles = {"service": {"profiles": [profile, imported, sdk]}}
    bridge.openapi = AsyncMock(side_effect=httpx.ConnectError("connection failed"))
    profiles, errors = await bridge.resolved_profiles("service", SimpleNamespace())
    assert len(profiles) == 1 and profiles[0]["schema_source"] == "configured"
    assert len(errors) == 2
    assert "connection" in errors[0] and "SDK" in errors[1]
    bridge.openapi = AsyncMock(return_value=document())
    imported["config"]["traffic"]["journeys"][0]["requestEncoding"] = "form"
    doc = document()
    content = doc["paths"]["/translations"]["post"]["requestBody"]["content"]
    content["application/x-www-form-urlencoded"] = content.pop("application/json")
    bridge.openapi.return_value = doc
    profiles, errors = await bridge.resolved_profiles("service", SimpleNamespace())
    assert len(profiles) == 1 and "non-null scalar" in errors[0]


@pytest.mark.parametrize("minimum,maximum", [(1000000000, 1000000001), (2, 1)])
def test_array_minimum_cannot_exceed_bounded_capacity(discovery, minimum, maximum):
    client, current, calls, _ = discovery
    current["document"] = document(
        {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"}, "minItems": minimum, "maxItems": maximum}
            },
        }
    )
    result = client.get(f"{BASE}/profiles").json()
    assert not result["available"] and "minItems" in result["errors"][0]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "path", ["/metrics?format=prometheus", "/api/v1/metrics?x=1", "/%6detrics", "/other/../metrics"]
)
def test_sdk_target_classification_uses_canonical_path(path):
    assert _is_sdk_operation(path)
    assert not _is_sdk_operation("/orders?next=/metrics")


def test_imported_enum_union_preserves_null_without_widening_outer_constraints():
    from jsonschema import Draft202012Validator

    union = {"anyOf": [{"type": "string", "enum": ["fast", "safe"]}, {"type": "null"}]}
    schema = _request_schema(document({"type": "object", "properties": {"mode": union}}), JOURNEY)
    _check_schema(schema)
    assert schema["properties"]["mode"]["enum"] == ["fast", "safe", None]
    assert Draft202012Validator(schema).is_valid({"mode": None})
    assert Draft202012Validator(schema).is_valid({"mode": "fast"})
    assert not Draft202012Validator(schema).is_valid({"mode": "other"})
    union["enum"] = ["fast", "safe"]
    schema = _request_schema(document({"type": "object", "properties": {"mode": union}}), JOURNEY)
    assert not Draft202012Validator(schema).is_valid({"mode": None})
    union.pop("enum")
    union["anyOf"][1]["enum"] = ["impossible"]
    schema = _request_schema(document({"type": "object", "properties": {"mode": union}}), JOURNEY)
    assert not Draft202012Validator(schema).is_valid({"mode": None})


def test_nested_allof_references_are_flattened_without_closing_each_branch():
    doc = document({"allOf": [{"$ref": "#/components/schemas/Derived"}]})
    doc["components"]["schemas"]["Base"] = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }
    doc["components"]["schemas"]["Derived"] = {
        "allOf": [
            {"$ref": "#/components/schemas/Base"},
            {"properties": {"mode": {"type": "string"}}, "required": ["mode"]},
        ]
    }
    schema = _request_schema(doc, JOURNEY)
    _check_schema(schema)
    assert set(schema["properties"]) == {"id", "mode"}
    assert schema["required"] == ["id", "mode"]
    assert "allOf" not in schema
    doc["components"]["schemas"]["Base"] = {"allOf": [{"$ref": "#/components/schemas/Derived"}]}
    with pytest.raises(ValueError, match="composition"):
        _request_schema(doc, JOURNEY)


def test_composition_work_is_bounded():
    with pytest.raises(ValueError, match="1000 fields"):
        _request_schema(document({"allOf": [{"type": "object"} for _ in range(1001)]}), JOURNEY)
