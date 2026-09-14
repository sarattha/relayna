"""Service-bound Ampule Chamber adapter. Chamber credentials never enter the browser."""

from __future__ import annotations

import copy
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, HTTPException
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from .registry import ServiceNotFoundError, ServiceRegistryService

_RETENTION = 30 * 86400
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
}


class _LoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile_id: str = Field(min_length=1, max_length=100)
    inputs: dict[str, Any] = Field(default_factory=dict)
    vus: int = Field(ge=1, le=100)
    iterations: int = Field(ge=1, le=10000)
    duration_seconds: int = Field(ge=1, le=3600)


def _check_schema(schema: dict[str, Any], depth: int = 0) -> None:
    Draft202012Validator.check_schema(schema)
    if depth > 8 or set(schema) - _SCHEMA_KEYS:
        raise ValueError("Request schema uses unsupported form keywords")
    kind = schema.get("type")
    if kind not in {"object", "array", "string", "number", "integer", "boolean"}:
        raise ValueError("Each request field needs a concrete type")
    if kind == "object":
        if schema.get("additionalProperties") is not False:
            raise ValueError("Object schemas must forbid additional properties")
        for child in schema.get("properties", {}).values():
            _check_schema(child, depth + 1)
    if kind == "array":
        if not isinstance(schema.get("maxItems"), int) or not 0 <= schema["maxItems"] <= 100:
            raise ValueError("Array schemas need maxItems between 0 and 100")
        _check_schema(schema.get("items", {}), depth + 1)
    if "default" in schema and not Draft202012Validator(schema).is_valid(schema["default"]):
        raise ValueError("Request default does not match its schema")


def _load_profiles() -> dict[str, Any]:
    path = os.getenv("RELAYNA_STUDIO_CHAMBER_PROFILES_PATH", "").strip()
    if not path:
        return {}
    profiles = json.loads(Path(path).read_text())
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
            schema = profile["input_schema"]
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
                child["type"] in {"object", "array"} for child in schema.get("properties", {}).values()
            ):
                raise ValueError("Form fields must have scalar types")
            if encoding == "multipart" and not journeys[0].get("multipart", {}).get("files"):
                raise ValueError("Multipart profiles need approved file fixtures in Chamber storage")
            if encoding == "raw" and (
                set(schema.get("properties", {})) != {"body"}
                or schema["properties"]["body"]["type"] != "string"
                or schema.get("required") != ["body"]
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
    def __init__(self, registry: ServiceRegistryService, redis: Redis, client: httpx.AsyncClient):
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
        self.profiles = _load_profiles()

    async def call(self, method: str, path: str, payload: Any = None, key: str | None = None) -> dict[str, Any]:
        if not self.url:
            raise HTTPException(503, "Load testing is not configured. Ask an administrator to connect Ampule Chamber.")
        headers = {"Authorization": f"Bearer {self.token}"}
        if key:
            headers["Idempotency-Key"] = key
        try:
            async with self.client.stream(
                method,
                f"{self.url}/api/v1/{path}",
                json=payload,
                headers=headers,
                timeout=30,
                follow_redirects=False,
            ) as response:
                if response.status_code >= 300:
                    status = 409 if response.status_code in {400, 409, 422} else 502
                    raise HTTPException(
                        status, "Chamber rejected the request. Check its configuration and runner logs."
                    )
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 2 * 1024 * 1024:
                        raise HTTPException(502, "Chamber response exceeded the supported size.")
                data = json.loads(chunks)
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                return data
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(
                502, "Chamber is unavailable or returned an invalid response. You can retry safely."
            ) from exc

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
        if record["environment"] != service.environment:
            raise HTTPException(409, "The service environment changed after this plan was created.")
        return record

    async def save(self, service_id: str, record: dict[str, Any]) -> None:
        await self.redis.set(self.key(service_id, record["id"]), json.dumps(record), ex=_RETENTION)

    async def options(self, service_id: str) -> dict[str, Any]:
        service = await self.service(service_id)
        configured = self.profiles.get(service_id, {})
        enabled = bool(
            self.url and configured.get("environment") == service.environment and service.status != "disabled"
        )
        profiles = []
        for profile in configured.get("profiles", []) if enabled else []:
            journey = profile["config"]["traffic"]["journeys"][0]
            profiles.append(
                {
                    **{
                        key: profile[key]
                        for key in ("id", "name", "input_schema", "max_vus", "max_iterations", "max_duration_seconds")
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
            "message": ""
            if profiles
            else "This service needs an approved load-test profile for its environment. "
            "Ask an administrator to configure Chamber and the service request schema.",
        }

    async def plan(self, service_id: str, payload: _LoadRequest) -> dict[str, Any]:
        service = await self.service(service_id, mutate=True)
        options = await self.options(service_id)
        if not any(item["id"] == payload.profile_id for item in options["profiles"]):
            raise HTTPException(409, "This load-test profile is not available for this service and environment.")
        profile = next(item for item in self.profiles[service_id]["profiles"] if item["id"] == payload.profile_id)
        try:
            input_size = len(json.dumps(payload.inputs, allow_nan=False).encode())
        except ValueError as exc:
            raise HTTPException(422, "Inputs must contain finite JSON values.") from exc
        if input_size > 65536:
            raise HTTPException(422, "Request inputs exceed 64 KiB.")
        errors = list(Draft202012Validator(profile["input_schema"]).iter_errors(payload.inputs))
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
        response = await self.call("POST", "plans", {"config": config})
        if not isinstance(response.get("run_id"), str) or not response["run_id"]:
            raise HTTPException(502, "Chamber did not return a plan ID.")
        record = {
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
        return {
            key: value
            for key, value in record.items()
            if key not in {"context", "prometheus_url", "chamber_plan_id", "job_id"}
        }

    async def start(self, service_id: str, identity: str) -> dict[str, Any]:
        record = await self.bound(service_id, identity, mutate=True)
        if record.get("job_id"):
            return self.public(record)
        response = await self.call(
            "POST",
            "runs",
            {
                "plan_id": record["chamber_plan_id"],
                "mode": "kubernetes",
                "context": record["context"],
                "prometheus_url": record["prometheus_url"],
            },
            key=f"studio-{identity}",
        )
        if not response.get("job_id"):
            raise HTTPException(502, "Chamber did not return a job ID. Retry this plan to recover the same execution.")
        record.update(
            job_id=response["job_id"],
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
        job = await self.call("POST" if cancel else "GET", f"{job_path}/cancel" if cancel else job_path)
        record.update({key: job.get(key) for key in ("state", "run_id", "cancel_requested", "cleanup_required")})
        record["output"] = str(job.get("output") or "")[-65536:]
        record["error"] = str(job.get("error") or "")[:2000]
        if record["state"] in _TERMINAL and not record.get("finished_at"):
            record["finished_at"] = datetime.now(UTC).isoformat()
        if record.get("run_id"):
            try:
                run = await self.call("GET", f"runs/{quote(str(record['run_id']), safe='')}")
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
                record["result"] = {
                    key: result.get(key)
                    for key in ("status", "readiness_score", "evidence_coverage_percent", "limitations")
                }
                record["tasks"] = [
                    {key: task.get(key) for key in ("task_id", "terminal_status", "success", "total_duration_ms")}
                    for task in (run.get("relayna") or {}).get("tasks", [])[:200]
                ]
                record["evidence_error"] = ""
            except HTTPException:
                record["evidence_error"] = "Run evidence is not available yet. Execution output remains available."
        await self.save(service_id, record)
        return self.public(record)


def _create_load_testing_router(registry: ServiceRegistryService, redis: Redis, client: httpx.AsyncClient) -> APIRouter:
    bridge = _Chamber(registry, redis, client)
    router = APIRouter(prefix="/studio/services/{service_id}/load-tests", tags=["load-testing"])

    @router.get("/profiles")
    async def profiles(service_id: str) -> dict[str, Any]:
        return await bridge.options(service_id)

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
        return {"items": [bridge.public(item) for item in parsed if item["environment"] == service.environment]}

    @router.post("/plans", status_code=201)
    async def plan(service_id: str, payload: _LoadRequest) -> dict[str, Any]:
        return await bridge.plan(service_id, payload)

    @router.post("/{identity}/start", status_code=202)
    async def start(service_id: str, identity: str) -> dict[str, Any]:
        return await bridge.start(service_id, identity)

    @router.get("/{identity}")
    async def status(service_id: str, identity: str) -> dict[str, Any]:
        return await bridge.status(service_id, identity)

    @router.post("/{identity}/cancel")
    async def cancel(service_id: str, identity: str) -> dict[str, Any]:
        return await bridge.status(service_id, identity, cancel=True)

    return router
