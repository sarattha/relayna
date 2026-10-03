"""Service-bound Ampule Chamber adapter. Chamber credentials never enter the browser."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from collections.abc import Awaitable, Iterator
from datetime import UTC, datetime
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, HTTPException
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from jsonschema.exceptions import SchemaError
from jsonschema.validators import extend
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from ._chamber_api import _StartConfirmation
from ._chamber_connection import _chamber_json, _ConnectionStore
from ._openapi import _is_sdk_operation, _request_schema
from .database import StudioDatabase
from .registry import OutboundUrlPolicyError, ServiceNotFoundError, ServiceRegistryService, StudioOutboundUrlPolicy

_RETENTION = 30 * 86400
_REQUEST_TIMEOUT_SECONDS = 15
_MAX_SAFE_INTEGER = 2**53 - 1
_TERMINAL = {"completed", "failed", "cancelled"}
_SCHEMA_KEYS = {
    "type",
    "title",
    "description",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "default",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "format",
    "pattern",
    "multipleOf",
    "exclusiveMinimum",
    "exclusiveMaximum",
}


def _decimal_multiple_of(validator: Any, divisor: Any, instance: Any, schema: Any) -> Iterator[ValidationError]:
    if not validator.is_type(instance, "number"):
        return
    try:
        valid = Fraction(str(instance)) % Fraction(str(divisor)) == 0
    except (ValueError, ZeroDivisionError):
        valid = False
    if not valid:
        yield ValidationError(f"Value is not a multiple of {divisor}")


_RequestValidator = extend(Draft202012Validator, {"multipleOf": _decimal_multiple_of})


class _LoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_revision: str = Field(default="", max_length=64)
    profile_id: str = Field(min_length=1, max_length=100)
    inputs: dict[str, Any] = Field(default_factory=dict)
    vus: int = Field(ge=1, le=100)
    iterations: int = Field(ge=1, le=10000)
    duration_seconds: int = Field(ge=1, le=3600)


def _form_size(schema: dict[str, Any]) -> int:
    if "enum" in schema:
        return 1
    default_size = 1
    if "default" in schema:

        def size(value: Any) -> int:
            children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
            return 1 + sum(size(child) for child in children)

        default_size = size(schema["default"])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next(item for item in kind if item != "null")
    if kind == "object":
        return max(default_size, 1 + sum(_form_size(child) for child in schema.get("properties", {}).values()))
    if kind == "array":
        return max(default_size, 1 + max(1, schema.get("minItems", 0)) * _form_size(schema["items"]))
    return 1


def _check_schema(schema: dict[str, Any], depth: int = 0) -> None:
    Draft202012Validator.check_schema(schema)
    if depth > 8 or set(schema) - _SCHEMA_KEYS:
        raise ValueError("Request schema uses unsupported form keywords")
    kind = schema.get("type")
    if isinstance(kind, list):
        concrete = [item for item in kind if item != "null"]
        if len(concrete) != 1 or len(kind) != 2:
            raise ValueError("Each nullable field needs one concrete type")
        kind = concrete[0]
    if kind not in {"object", "array", "string", "number", "integer", "boolean"}:
        raise ValueError("Each request field needs a concrete type")
    if kind == "integer":
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
            if key in schema and abs(schema[key]) > _MAX_SAFE_INTEGER:
                raise ValueError("Integer constraints exceed the exact supported range; use a string contract")
        schema.setdefault("minimum", -_MAX_SAFE_INTEGER)
        schema.setdefault("maximum", _MAX_SAFE_INTEGER)
    if kind == "object":
        if schema.get("additionalProperties") is not False:
            raise ValueError("Object schemas must forbid additional properties")
        for child in schema.get("properties", {}).values():
            _check_schema(child, depth + 1)
    if kind == "array":
        if not isinstance(schema.get("maxItems"), int) or not 0 <= schema["maxItems"] <= 100:
            raise ValueError("Array schemas need maxItems between 0 and 100")
        minimum = schema.get("minItems", 0)
        if type(minimum) is not int or not 0 <= minimum <= schema["maxItems"]:
            raise ValueError("Array schemas need 0 <= minItems <= maxItems <= 100")
        _check_schema(schema.get("items", {}), depth + 1)
    if "default" in schema and not _RequestValidator(schema).is_valid(schema["default"]):
        raise ValueError("Request default does not match its schema")
    if any(not _RequestValidator(schema).is_valid(item) for item in schema.get("enum", [])):
        raise ValueError("Enum choices must match the supported request schema")
    if _form_size(schema) > 1000:
        raise ValueError("Request form initialization exceeds 1000 values")


def _load_profiles() -> dict[str, Any]:
    path = os.getenv("RELAYNA_STUDIO_CHAMBER_PROFILES_PATH", "").strip()
    if not path:
        return {}
    return _validate_profiles(json.loads(Path(path).read_text()))


def _validate_profiles(profiles: Any) -> dict[str, Any]:
    if not isinstance(profiles, dict):
        raise ValueError("Chamber profiles must be keyed by service ID")
    for settings in profiles.values():
        if not settings.get("environment") or not isinstance(settings.get("profiles"), list):
            raise ValueError("Each Chamber service needs an environment and profiles")
        ids: set[str] = set()
        for profile in settings["profiles"]:
            if not profile.get("id") or profile["id"] in ids:
                raise ValueError("Chamber profile IDs must be unique within a service")
            ids.add(profile["id"])
            schema = profile.get("input_schema", {"type": "object", "properties": {}, "additionalProperties": False})
            _check_schema(schema)
            if schema["type"] != "object":
                raise ValueError("Request schema must describe an object")
            config = profile["config"]
            if config.get("experiment") or config.get("traffic", {}).get("load"):
                raise ValueError("Studio profiles must not include experiments or separate load suites")
            runtime = config["runtime"]
            if runtime.get("provider") != "kubernetes" or runtime.get("mode") != "attach":
                raise ValueError("Studio load testing requires Kubernetes attach mode")
            if runtime.get("faults") or runtime.get("cleanup") is not False:
                raise ValueError("Studio load profiles must disable faults and cleanup")
            if not runtime.get("kubernetesContext") or not runtime.get("namespace"):
                raise ValueError("Studio load profiles need an explicit context and namespace")
            journeys = config["traffic"]["journeys"]
            if len(journeys) != 1 or journeys[0].get("requestEncoding", "json") not in {
                "json",
                "none",
                "multipart",
                "form",
                "raw",
            }:
                raise ValueError("A profile requires one supported request journey")
            encoding = journeys[0].get("requestEncoding", "json")
            if encoding in {"multipart", "form"} and any(
                isinstance(child["type"], list) or child["type"] in {"object", "array"}
                for child in schema.get("properties", {}).values()
            ):
                raise ValueError("Form fields must have scalar types")
            if encoding == "multipart" and not journeys[0].get("multipart", {}).get("files"):
                raise ValueError("Multipart profiles need approved file fixtures in Chamber storage")
            if (
                "input_schema" in profile
                and encoding == "raw"
                and (
                    set(schema.get("properties", {})) != {"body"}
                    or schema["properties"]["body"]["type"] != "string"
                    or schema.get("required") != ["body"]
                )
            ):
                raise ValueError("Raw request schemas need one required string field named body")
            if journeys[0].get("adapter") == "relayna" and encoding not in {"json", "multipart"}:
                raise ValueError("Relayna journeys require JSON or multipart encoding")
            if journeys[0].get("adapter") == "relayna" and (
                profile["max_vus"] > 32 or profile["max_iterations"] > 1000
            ):
                raise ValueError("Relayna profiles support at most 32 users and 1000 iterations")
            if journeys[0].get("requestEncoding") == "none" and schema.get("properties"):
                raise ValueError("Bodyless journeys must have an empty request schema")
            if journeys[0].get("adapter", "http") not in {"http", "relayna"}:
                raise ValueError("Unsupported Chamber adapter")
            for field, maximum in (("max_vus", 100), ("max_iterations", 10000), ("max_duration_seconds", 3600)):
                value = profile.get(field)
                if type(value) is not int or not 1 <= value <= maximum:
                    raise ValueError(f"Profile {field} must be between 1 and {maximum}")
    return profiles


class _Chamber:
    def __init__(
        self,
        registry: ServiceRegistryService,
        redis: Redis,
        client: httpx.AsyncClient,
        url_policy: StudioOutboundUrlPolicy | None = None,
        database: StudioDatabase | None = None,
    ):
        self.url_policy = url_policy or StudioOutboundUrlPolicy()
        self.registry = registry
        self.redis = redis
        self.client = client
        self.url = os.getenv("RELAYNA_STUDIO_CHAMBER_URL", "").rstrip("/")
        self.token = os.getenv("RELAYNA_STUDIO_CHAMBER_TOKEN", "").strip()
        if self.url:
            parts = urlsplit(self.url)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
            ):
                raise ValueError("Chamber URL must be an HTTP origin without credentials, query or fragment")
            if not self.token:
                raise ValueError("RELAYNA_STUDIO_CHAMBER_TOKEN is required when Chamber is enabled")
        self.connections = _ConnectionStore(database, self.url, self.token, self.url_policy)
        self.profiles = _load_profiles()
        from ._profile_import import _ProfileStore

        self.profile_store = _ProfileStore(database)

    async def settings(self, service_id: str, environment: str) -> dict[str, Any]:
        configured = self.profiles.get(service_id, {})
        settings: dict[str, Any] = (
            copy.deepcopy(configured)
            if configured.get("environment") == environment
            else {"environment": environment, "profiles": []}
        )
        settings["profiles"].extend(await self.profile_store.get_profiles(service_id, environment))
        return settings

    async def call(
        self,
        method: str,
        path: str,
        payload: Any = None,
        key: str | None = None,
        *,
        connection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.connections.deployment_url = self.url
        self.connections.deployment_token = self.token
        current = connection if connection is not None else await self.connections.current()
        return await _chamber_json(self.client, self.connections, current, method, path, payload, key)

    def key(self, service_id: str, identity: str) -> str:
        return f"studio:load-testing:v1:{quote(service_id, safe='')}:{identity}"

    async def service(self, service_id: str, *, mutate: bool = False):
        try:
            service = await self.registry.get_service(service_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(404, "Service not found.") from exc
        if mutate and service.status == "disabled":
            raise HTTPException(409, "Enable this service before running a load test.")
        return service

    async def bound(self, service_id: str, plan_id: str, *, mutate: bool = False) -> dict[str, Any]:
        service = await self.service(service_id, mutate=mutate)
        try:
            if UUID(plan_id).hex != plan_id:
                raise ValueError("Invalid ID")
        except ValueError as exc:
            raise HTTPException(404, "Load test not found.") from exc
        raw = await self.redis.get(self.key(service_id, plan_id))
        if not raw:
            raise HTTPException(404, "Load test not found for this service, or its 30-day retention expired.")
        record = json.loads(raw)
        if record["environment"] != service.environment and (mutate or not record.get("job_id")):
            raise HTTPException(409, "The service environment changed after this plan was created.")
        return record

    async def save(self, service_id: str, record: dict[str, Any]) -> None:
        deadline = int(datetime.fromisoformat(record["created_at"]).timestamp()) + _RETENTION
        if deadline <= time.time():
            raise HTTPException(404, "This load test's 30-day retention expired.")
        await self.redis.set(self.key(service_id, record["id"]), json.dumps(record), exat=deadline)

    async def openapi(self, service: Any, settings: dict[str, Any]) -> dict[str, Any]:
        path = settings.get("openapi_path", "/openapi.json")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or any(char in path for char in ("?", "#", "%", "\\"))
            or ".." in path
        ):
            raise ValueError("openapi_path must be a service-relative path without query or traversal")
        url = f"{service.base_url.rstrip('/')}{path}"
        self.url_policy.validate_url(url, label="OpenAPI source")
        # No Chamber token, Studio cookies or OpenAPI server URLs are forwarded.
        async with self.client.stream(
            "GET", url, timeout=5, follow_redirects=False, headers={"Accept": "application/json"}
        ) as response:
            if response.status_code != 200:
                raise ValueError("Service OpenAPI document is unavailable (expected HTTP 200)")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 2 * 1024 * 1024:
                    raise ValueError("OpenAPI document exceeds 2 MiB")
        document = json.loads(content)
        if not isinstance(document, dict):
            raise ValueError("OpenAPI document must be an object")
        return document

    async def resolved_profiles(
        self, service_id: str, service: Any, settings: dict[str, Any] | None = None
    ) -> tuple[list[dict[str, Any]], list[str]]:
        settings = settings if settings is not None else await self.settings(service_id, service.environment)
        configured = settings.get("profiles", [])
        resolved = []
        errors = []
        document = None
        discovery_error = ""
        if any("input_schema" not in profile for profile in configured):
            try:
                document = await self.openapi(service, settings)
            except (ValueError, httpx.HTTPError, OutboundUrlPolicyError) as exc:
                discovery_error = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "OpenAPI discovery failed; check the service connection and outbound allowlist."
                )
        active_origin = (
            (await self.connections.current()).get("url") if any(p.get("chamber_origin") for p in configured) else None
        )
        for original in configured:
            if original.get("chamber_origin") and original["chamber_origin"] != active_origin:
                errors.append(f"{original['name']}: imported from another Chamber endpoint. Import it again here.")
                continue
            if _is_sdk_operation(original["config"]["traffic"]["journeys"][0]["path"]):
                errors.append(f"{original['name']}: Relayna SDK control endpoints are excluded.")
                continue
            profile = copy.deepcopy(original)
            if "input_schema" not in profile:
                try:
                    if document is None:
                        raise ValueError(discovery_error)
                    journey = profile["config"]["traffic"]["journeys"][0]
                    schema = _request_schema(document, journey)
                    _check_schema(schema)
                    if journey.get("requestEncoding") in {"form", "multipart"} and any(
                        isinstance(child["type"], list) or child["type"] in {"object", "array"}
                        for child in schema.get("properties", {}).values()
                    ):
                        raise ValueError("Form fields must have non-null scalar types")
                    profile["input_schema"] = schema
                    profile["schema_source"] = "openapi"
                    profile["schema_revision"] = sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
                except (ValueError, KeyError, TypeError, RecursionError, SchemaError) as exc:
                    errors.append(f"{profile['name']}: {exc}")
                    continue
            else:
                profile["schema_source"] = "configured"
                profile["schema_revision"] = ""
            resolved.append(profile)
        return resolved, errors

    async def options(self, service_id: str) -> dict[str, Any]:
        service = await self.service(service_id)
        configured = await self.settings(service_id, service.environment)
        connection = await self.connections.current()
        enabled = bool(
            connection.get("url")
            and configured.get("environment") == service.environment
            and service.status != "disabled"
        )
        profiles = []
        resolved, errors = await self.resolved_profiles(service_id, service, configured) if enabled else ([], [])
        for profile in resolved:
            journey = profile["config"]["traffic"]["journeys"][0]
            profiles.append(
                {
                    **{
                        key: profile[key]
                        for key in (
                            "id",
                            "name",
                            "input_schema",
                            "max_vus",
                            "max_iterations",
                            "max_duration_seconds",
                            "schema_source",
                            "schema_revision",
                        )
                    },
                    "method": journey["method"],
                    "path": journey["path"],
                    "adapter": journey.get("adapter", "http"),
                    "namespace": profile["config"]["runtime"]["namespace"],
                    "files": self.files(journey),
                }
            )
        return {
            "available": bool(profiles),
            "profiles": profiles,
            "errors": errors,
            "message": ""
            if profiles
            else "This service needs an approved load-test profile for its environment. "
            "Ask an administrator to configure Chamber and the service request schema.",
        }

    async def plan(self, service_id: str, payload: _LoadRequest) -> dict[str, Any]:
        service = await self.service(service_id, mutate=True)
        settings = await self.settings(service_id, service.environment)
        connection = await self.connections.current()
        if not connection.get("url") or settings.get("environment") != service.environment:
            raise HTTPException(409, "Load testing is not configured for this environment.")
        profiles, errors = await self.resolved_profiles(service_id, service, settings)
        if not any(item["id"] == payload.profile_id for item in profiles):
            raise HTTPException(409, "This load-test profile is not available for this service and environment.")
        profile = next(item for item in profiles if item["id"] == payload.profile_id)
        if profile["schema_source"] == "openapi" and payload.schema_revision != profile["schema_revision"]:
            raise HTTPException(409, "The service input schema changed. Refresh operations and review the new fields.")
        try:
            input_size = len(json.dumps(payload.inputs, allow_nan=False).encode())
        except ValueError as exc:
            raise HTTPException(422, "Inputs must contain finite JSON values.") from exc
        if input_size > 65536:
            raise HTTPException(422, "Request inputs exceed 64 KiB.")
        errors = list(
            _RequestValidator(profile["input_schema"], format_checker=FormatChecker()).iter_errors(payload.inputs)
        )
        if errors:
            error = errors[0]
            field = ".".join(map(str, error.absolute_path)) or "request"
            raise HTTPException(422, f"Invalid {field}: {error.validator} constraint failed.")
        if (
            payload.vus > profile["max_vus"]
            or payload.iterations > profile["max_iterations"]
            or payload.duration_seconds > profile["max_duration_seconds"]
        ):
            raise HTTPException(422, "Requested load exceeds the approved profile limits.")
        config = copy.deepcopy(profile["config"])
        journey = config["traffic"]["journeys"][0]
        encoding = journey.get("requestEncoding", "json")
        if encoding == "multipart":
            journey["multipart"]["fields"] = payload.inputs
        elif encoding == "form":
            journey["form"] = payload.inputs
        elif encoding == "raw":
            journey["body"] = payload.inputs["body"]
        elif encoding != "none":
            journey["body"] = payload.inputs
        if journey.get("adapter") == "relayna":
            journey.update(vus=payload.vus, iterations=payload.iterations, durationSeconds=payload.duration_seconds)
        else:
            # Pin the executor too: an operator template cannot override the reviewed load.
            for field in ("vus", "iterations", "durationSeconds"):
                journey.pop(field, None)
            journey["stages"] = [{"duration": f"{payload.duration_seconds}s", "targetVus": payload.vus}]
        response = await self.call("POST", "plans", {"config": config}, connection=connection)
        if not isinstance(response.get("run_id"), str) or not response["run_id"]:
            raise HTTPException(502, "Chamber did not return a plan ID.")
        from ._chamber_api import _target

        record = {
            "connection": connection,
            "target": _target(config),
            "id": uuid4().hex,
            "chamber_plan_id": response["run_id"],
            "environment": service.environment,
            "profile_name": profile["name"],
            "created_at": datetime.now(UTC).isoformat(),
            "state": "planned",
            "context": config["runtime"]["kubernetesContext"],
            "namespace": config["runtime"]["namespace"],
            "prometheus_url": profile.get("prometheus_url"),
            "request": payload.model_dump(),
            "method": journey["method"],
            "path": journey["path"],
            "adapter": journey.get("adapter", "http"),
            "files": self.files(journey),
        }
        await self.save(service_id, record)
        index = self.key(service_id, "history")
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.zadd(index, {record["id"]: time.time()})
            pipe.zremrangebyrank(index, 0, -101)
            pipe.expire(index, _RETENTION)
            await pipe.execute()
        return self.public(record)

    def files(self, journey: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {
                "field": str(item["field"]),
                "filename": str(item.get("filename") or Path(item["path"]).name),
                "content_type": str(item.get("contentType", "")),
            }
            for item in journey.get("multipart", {}).get("files", [])
        ]

    def public(self, record: dict[str, Any]) -> dict[str, Any]:
        from ._chamber_connection import _redact

        result = _redact(
            {
                key: value
                for key, value in record.items()
                if key not in {"context", "prometheus_url", "chamber_plan_id", "job_id", "connection"}
            }
        )
        result["chamber"] = {
            "plan_id": record.get("chamber_plan_id"),
            "job_id": record.get("job_id"),
            "run_id": record.get("run_id"),
            "connection_id": (record.get("connection") or {}).get("id"),
        }
        result["expires_at"] = (
            datetime.fromtimestamp(
                datetime.fromisoformat(record["created_at"]).timestamp() + _RETENTION, UTC
            ).isoformat()
            if record.get("created_at")
            else None
        )
        return result

    async def start(
        self, service_id: str, identity: str, *, confirmed_target: bool = False, confirmed_faults: bool = False
    ) -> dict[str, Any]:
        record = await self.bound(service_id, identity, mutate=True)
        if record.get("job_id"):
            return self.public(record)
        if record.get("requires_target_confirmation") and not confirmed_target:
            raise HTTPException(422, "Confirm the reviewed target and load before starting this assessment.")
        if record.get("requires_fault_confirmation") and not confirmed_faults:
            raise HTTPException(422, "Confirm the explicitly selected faults and recovery before starting.")
        response = await self.call(
            "POST",
            "runs",
            {
                "plan_id": record["chamber_plan_id"],
                "mode": record.get("mode", "kubernetes"),
                **({"origin": record["origin"]} if record.get("origin") else {}),
                "context": record["context"],
                "prometheus_url": record["prometheus_url"],
            },
            key=f"studio-{identity}",
            connection=record.get("connection"),
        )
        if not response.get("job_id"):
            raise HTTPException(502, "Chamber did not return a job ID. Retry this plan to recover the same execution.")
        record.update(
            job_id=response["job_id"],
            run_id=response.get("run_id"),
            started_at=response.get("created_at") or datetime.now(UTC).isoformat(),
            state=response.get("state", "queued"),
        )
        await self.save(service_id, record)
        return self.public(record)

    async def status(self, service_id: str, identity: str, *, cancel: bool = False) -> dict[str, Any]:
        # Disabling a service must not prevent stopping its existing traffic.
        record = await self.bound(service_id, identity)
        if not record.get("job_id"):
            if cancel:
                raise HTTPException(409, "This plan has not started.")
            return self.public(record)
        job_path = f"jobs/{quote(str(record['job_id']), safe='')}"
        try:
            job = await self.call(
                "POST" if cancel else "GET",
                f"{job_path}/cancel" if cancel else job_path,
                connection=record.get("connection"),
            )
        except HTTPException as exc:
            if cancel:
                raise
            return self.public(
                {
                    **record,
                    "evidence_error": f"Showing the last retained run snapshot. {exc.detail}",
                }
            )
        record.update({key: job.get(key) for key in ("state", "run_id", "cancel_requested", "cleanup_required")})
        record["output"] = str(job.get("output") or "")[-65536:]
        record["error"] = str(job.get("error") or "")[:2000]
        if record["state"] in _TERMINAL and not record.get("finished_at"):
            record["finished_at"] = datetime.now(UTC).isoformat()
        if record.get("run_id"):
            try:
                run = await self.call(
                    "GET",
                    f"runs/{quote(str(record['run_id']), safe='')}?include_task_details=false",
                    connection=record.get("connection"),
                )
                if record["state"] in _TERMINAL:
                    updated_at = (run.get("run") or {}).get("updated_at")
                    if isinstance(updated_at, str):
                        try:
                            finished = datetime.fromisoformat(updated_at)
                            if finished.tzinfo is not None:
                                record["finished_at"] = finished.isoformat()
                        except ValueError:
                            pass
                result = run.get("result") or {}
                from ._chamber_connection import _redact

                record["result"] = _redact(
                    {
                        key: result.get(key)
                        for key in (
                            "status",
                            "readiness_score",
                            "evidence_coverage_percent",
                            "limitations",
                            "conclusive",
                            "confidence",
                            "verdict",
                            "evidence_requirements",
                            "tested_scope",
                            "next_actions",
                            "cleanup_verified",
                            "rollback_verified",
                        )
                    }
                )
                record["task_count"] = (run.get("relayna") or {}).get(
                    "total_task_count", len((run.get("relayna") or {}).get("tasks", []))
                )
                record["tasks_truncated"] = (
                    bool((run.get("relayna") or {}).get("tasks_truncated")) or record["task_count"] > 200
                )
                record["tasks"] = [
                    {key: task.get(key) for key in ("task_id", "terminal_status", "success", "total_duration_ms")}
                    for task in (run.get("relayna") or {}).get("tasks", [])[:200]
                ]
                record["evidence_error"] = ""
            except HTTPException:
                record["evidence_error"] = "Run evidence is not available yet. Execution output remains available."
        await self.save(service_id, record)
        return self.public(record)


async def _request_deadline(operation: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    try:
        async with asyncio.timeout(_REQUEST_TIMEOUT_SECONDS):
            return await operation
    except TimeoutError as exc:
        raise HTTPException(504, "Load-testing request timed out. Check recent runs before retrying.") from exc


def _create_load_testing_router(
    registry: ServiceRegistryService,
    redis: Redis,
    client: httpx.AsyncClient,
    url_policy: StudioOutboundUrlPolicy | None = None,
    database: StudioDatabase | None = None,
) -> APIRouter:
    bridge = _Chamber(registry, redis, client, url_policy, database)
    router = APIRouter(prefix="/studio/services/{service_id}/load-tests", tags=["load-testing"])

    from ._chamber_api import _workspace_router
    from ._profile_import import _import_router

    router.include_router(_workspace_router(bridge))
    router.include_router(_import_router(bridge))

    @router.get("/profiles")
    async def profiles(service_id: str) -> dict[str, Any]:
        return await _request_deadline(bridge.options(service_id))

    @router.get("")
    async def history(service_id: str) -> dict[str, Any]:
        service = await bridge.service(service_id)
        ids = await redis.zrevrange(bridge.key(service_id, "history"), 0, 19)
        records = (
            await redis.mget(
                [bridge.key(service_id, item.decode() if isinstance(item, bytes) else str(item)) for item in ids]
            )
            if ids
            else []
        )
        parsed = [json.loads(item) for item in records if item]
        return {
            "items": [
                bridge.public(item)
                for item in parsed
                if item["environment"] == service.environment or item.get("job_id")
            ]
        }

    @router.post("/plans", status_code=201)
    async def plan(service_id: str, payload: _LoadRequest) -> dict[str, Any]:
        return await _request_deadline(bridge.plan(service_id, payload))

    @router.post("/{identity}/start", status_code=202)
    async def start(service_id: str, identity: str, payload: _StartConfirmation | None = None) -> dict[str, Any]:
        confirmation = payload or _StartConfirmation()
        return await _request_deadline(
            bridge.start(
                service_id,
                identity,
                confirmed_target=confirmation.confirmed_target,
                confirmed_faults=confirmation.confirmed_faults,
            )
        )

    @router.get("/{identity}")
    async def status(service_id: str, identity: str) -> dict[str, Any]:
        return await _request_deadline(bridge.status(service_id, identity))

    @router.post("/{identity}/cancel")
    async def cancel(service_id: str, identity: str) -> dict[str, Any]:
        return await _request_deadline(bridge.status(service_id, identity, cancel=True))

    return router
