"""Administrator imports from Chamber saved plans; execution config stays server-side."""

from __future__ import annotations

import copy
import json
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from .auth import StudioMemberStatus, StudioRole
from .database import StudioDatabase, _load_profiles, services

if TYPE_CHECKING:
    from .load_testing import _Chamber


class _ProfileStore:
    def __init__(self, database: StudioDatabase | None):
        self.database = database

    def required(self) -> StudioDatabase:
        if self.database is None:
            raise HTTPException(503, "Profile management requires PostgreSQL and the latest schema migration.")
        return self.database

    async def get_profiles(self, service_id: str, environment: str) -> list[dict[str, Any]]:
        if self.database is None:
            return []
        async with self.database.sessions() as session:
            rows = await session.execute(
                select(_load_profiles.c.profile)
                .where(
                    _load_profiles.c.service_id == service_id,
                    _load_profiles.c.environment == environment,
                )
                .order_by(_load_profiles.c.profile_id)
            )
            return list(rows.scalars())

    async def save(self, service_id: str, environment: str, profile: dict[str, Any]) -> None:
        async with self.required().transaction() as session:
            # Serialize with service deletion so an in-flight import cannot
            # recreate profile rows after the soft-delete transaction clears them.
            active = await session.scalar(
                select(services.c.service_id)
                .where(
                    services.c.service_id == service_id,
                    services.c.deleted_at.is_(None),
                    services.c.environment == environment,
                    services.c.status != "disabled",
                )
                .with_for_update()
            )
            if active is None:
                raise HTTPException(409, "The service was removed, disabled or changed environment. Preview again.")
            await session.execute(
                insert(_load_profiles)
                .values(
                    service_id=service_id,
                    environment=environment,
                    profile_id=profile["id"],
                    profile=profile,
                )
                .on_conflict_do_nothing()
            )
            current = await session.scalar(
                select(_load_profiles.c.profile).where(
                    _load_profiles.c.service_id == service_id,
                    _load_profiles.c.environment == environment,
                    _load_profiles.c.profile_id == profile["id"],
                )
            )
            if current != profile:
                raise HTTPException(
                    409, "This operation is already imported. Remove its imported profile before replacing it."
                )

    async def remove(self, service_id: str, environment: str, profile_id: str) -> None:
        async with self.required().transaction() as session:
            await session.execute(
                delete(_load_profiles).where(
                    _load_profiles.c.service_id == service_id,
                    _load_profiles.c.environment == environment,
                    _load_profiles.c.profile_id == profile_id,
                )
            )


class _Preview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    operation: int = Field(default=0, ge=0, le=99)


class _Save(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")
    max_vus: int = Field(ge=1, le=100)
    max_iterations: int = Field(ge=1, le=10000)
    max_duration_seconds: int = Field(ge=1, le=3600)


def _admin(request: Request) -> None:
    member = getattr(request.state, "studio_member", None)
    if member is None:
        raise HTTPException(401, "Studio authentication is required.")
    if member.role != StudioRole.ADMIN or member.status != StudioMemberStatus.ACTIVE:
        raise HTTPException(403, "Studio administrator access is required.")


def _config(original: dict[str, Any], index: int) -> dict[str, Any]:
    """Copy supported execution fields; discard source inputs, credentials and faults."""
    runtime = original["runtime"]
    if runtime.get("provider") != "kubernetes" or runtime.get("mode") != "attach":
        raise ValueError("Select a Kubernetes attach-mode plan.")
    if original.get("experiment") or original.get("traffic", {}).get("load"):
        raise ValueError("Experiment and multi-suite plans cannot be imported as a service operation.")
    access = runtime["trafficAccess"]
    if access.get("mode") != "port-forward" or not access.get("service") or type(access.get("servicePort")) is not int:
        raise ValueError("Import requires explicit service port-forward access.")
    if not any(
        item.get("name") == access["service"] and item.get("port") == access["servicePort"]
        for item in original["deployment"]["services"]
    ):
        raise ValueError("The forwarded service must match a declared service and port.")
    journey = original["traffic"]["journeys"][index]
    if journey.get("method") not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        raise ValueError("Unsupported HTTP method.")
    if journey.get("adapter") == "relayna":
        lifecycle = journey.get("relayna", {})
        events = lifecycle.get("eventsPath", "")
        if (
            not isinstance(events, str)
            or not events.startswith("/")
            or events.startswith("//")
            or any(c in events for c in ("?", "#", "\\"))
        ):
            raise ValueError("Task events must use a service-relative path.")
    path = journey["path"]
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or any(c in path for c in ("?", "#", "\\"))
    ):
        raise ValueError("The operation must use a service-relative path without query or fragment.")
    from ._openapi import _is_sdk_operation

    if _is_sdk_operation(path):
        raise ValueError("Relayna SDK control endpoints cannot be imported as load targets.")
    if journey.get("headers"):
        raise ValueError("Operations with custom headers need an approved deployment profile.")
    picked = {
        k: copy.deepcopy(journey[k])
        for k in (
            "name",
            "method",
            "path",
            "tool",
            "adapter",
            "requestEncoding",
            "expectedStatus",
            "contentType",
            "relayna",
        )
        if k in journey
    }
    if journey.get("multipart"):
        picked["multipart"] = {"fields": {}, "files": copy.deepcopy(journey["multipart"].get("files", []))}
    namespace = runtime["namespace"]
    context = runtime["kubernetesContext"]
    deployment = original["deployment"]
    workloads = [{k: item[k] for k in ("name", "role", "kind") if k in item} for item in deployment["workloads"]]
    if not workloads:
        raise ValueError("The plan must identify its target workload.")
    result: dict[str, Any] = {
        "apiVersion": "chamber.ampule.dev/v1alpha1",
        "kind": "ChamberConfig",
        "service": {
            "name": original["service"]["name"],
            "repo": f"kubernetes://{context}/{namespace}/{workloads[0]['name']}",
        },
        "deployment": {
            "manifests": [],
            "images": {},
            "workloads": workloads,
            "services": [{k: item[k] for k in ("name", "port") if k in item} for item in deployment["services"]],
        },
        "traffic": {"entrypoint": original["traffic"]["entrypoint"], "journeys": [picked]},
        "dependencies": {"internal": [], "external": []},
        "runtime": {
            "provider": "kubernetes",
            "mode": "attach",
            "kubernetesContext": context,
            "namespace": namespace,
            "cleanup": False,
            "faults": [],
            "requiredEnv": [],
            "secretEnv": [],
            "config": {},
            "trafficAccess": {
                "mode": "port-forward",
                "service": access["service"],
                "servicePort": access["servicePort"],
            },
        },
        "agents": {"mode": "off"},
    }

    if original.get("chamber"):
        result["chamber"] = {"id": original["chamber"]["id"]}
        result["runtime"]["prometheusUrl"] = runtime.get("prometheusUrl", "")
    return result


def _import_router(bridge: _Chamber) -> APIRouter:
    from .load_testing import _request_deadline, _validate_profiles

    router = APIRouter(prefix="/profile-import", dependencies=[Depends(_admin)])

    @router.get("")
    async def imported(service_id: str) -> dict[str, Any]:
        service = await bridge.service(service_id)
        bridge.profile_store.required()
        profiles = await bridge.profile_store.get_profiles(service_id, service.environment)
        return {
            "profiles": [
                {k: p[k] for k in ("id", "name", "max_vus", "max_iterations", "max_duration_seconds")} for p in profiles
            ]
        }

    @router.get("/sources")
    async def sources(
        service_id: str, page: int = Query(1, ge=1, le=1000), search: str = Query("", max_length=200)
    ) -> dict[str, Any]:
        await bridge.service(service_id)
        bridge.profile_store.required()
        data = await _request_deadline(
            bridge.call("GET", "runs?" + urlencode({"page": page, "page_size": 20, "q": search}))
        )
        return {
            "items": [
                {key: item.get(key) for key in ("run_id", "service_name", "state", "created_at")}
                for item in data.get("runs", [])[:20]
            ],
            "pagination": data.get("pagination", {}),
        }

    @router.post("/inspect")
    async def inspect(service_id: str, payload: _Preview) -> dict[str, Any]:
        await bridge.service(service_id, mutate=True)
        bridge.profile_store.required()
        data = await _request_deadline(bridge.call("GET", f"runs/{quote(payload.run_id, safe='')}"))
        journeys = data.get("config", {}).get("traffic", {}).get("journeys", [])
        return {
            "operations": [
                {
                    "index": i,
                    "name": str(j.get("name", "Operation")),
                    "method": str(j.get("method", "")),
                    "path": str(j.get("path", "")),
                }
                for i, j in enumerate(journeys[:100])
            ]
        }

    async def preview_impl(service_id: str, payload: _Preview) -> dict[str, Any]:
        service = await bridge.service(service_id, mutate=True)
        bridge.profile_store.required()
        data = await bridge.call("GET", f"runs/{quote(payload.run_id, safe='')}")
        try:
            config = _config(data["config"], payload.operation)
            journey = config["traffic"]["journeys"][0]
            profile = {
                "id": "chamber-" + sha256(f"{payload.run_id}:{payload.operation}".encode()).hexdigest()[:24],
                "name": str(journey.get("name", "Imported operation"))[:120],
                "max_vus": 4,
                "max_iterations": 20,
                "max_duration_seconds": 300,
                "config": config,
            }
            settings = {**await bridge.settings(service_id, service.environment), "profiles": [profile]}
            _validate_profiles({service_id: settings})
            resolved, errors = await bridge.resolved_profiles(service_id, service, settings)
            if errors or not resolved:
                raise ValueError("; ".join(errors) or "No supported request schema found.")
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            raise HTTPException(422, f"This operation cannot be imported: {exc}") from exc
        preview_id = uuid4().hex
        schema = resolved[0]
        await bridge.redis.set(
            bridge.key(service_id, "import:" + preview_id),
            json.dumps(
                {
                    "environment": service.environment,
                    "base_url": service.base_url,
                    "profile": profile,
                    "schema_revision": schema["schema_revision"],
                }
            ),
            ex=1800,
        )
        return {
            "preview_id": preview_id,
            "environment": service.environment,
            "name": profile["name"],
            "method": journey["method"],
            "path": journey["path"],
            "adapter": journey.get("adapter", "http"),
            "context": config["runtime"]["kubernetesContext"],
            "namespace": config["runtime"]["namespace"],
            "target_service": config["runtime"]["trafficAccess"].get("service"),
            "target_port": config["runtime"]["trafficAccess"].get("servicePort"),
            "workloads": [w["name"] for w in config["deployment"]["workloads"]],
            "files": bridge.files(journey),
            "input_schema": schema["input_schema"],
            "max_vus": 4,
            "max_iterations": 20,
            "max_duration_seconds": 300,
        }

    @router.post("/preview")
    async def preview(service_id: str, payload: _Preview) -> dict[str, Any]:
        return await _request_deadline(preview_impl(service_id, payload))

    async def save_impl(service_id: str, payload: _Save) -> dict[str, Any]:
        service = await bridge.service(service_id, mutate=True)
        raw = await bridge.redis.get(bridge.key(service_id, "import:" + payload.preview_id))
        if not raw:
            raise HTTPException(409, "Import preview expired or belongs to another service. Preview again.")
        snapshot = json.loads(raw)
        if snapshot["environment"] != service.environment or snapshot["base_url"] != service.base_url:
            raise HTTPException(409, "The service target changed. Preview again.")
        profile = snapshot["profile"]
        for key in ("name", "max_vus", "max_iterations", "max_duration_seconds"):
            profile[key] = getattr(payload, key)
        settings = {**await bridge.settings(service_id, service.environment), "profiles": [profile]}
        try:
            _validate_profiles({service_id: settings})
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        resolved, errors = await bridge.resolved_profiles(service_id, service, settings)
        if errors or not resolved or resolved[0]["schema_revision"] != snapshot["schema_revision"]:
            raise HTTPException(409, "The request schema changed or is unavailable. Preview again.")
        if any(p["id"] == profile["id"] for p in bridge.profiles.get(service_id, {}).get("profiles", [])):
            raise HTTPException(409, "This ID is reserved by a deployment profile.")
        await bridge.profile_store.save(service_id, service.environment, profile)
        return {"id": profile["id"], "name": profile["name"]}

    @router.post("", status_code=201)
    async def save(service_id: str, payload: _Save) -> dict[str, Any]:
        return await _request_deadline(save_impl(service_id, payload))

    @router.delete("/{profile_id}")
    async def remove(service_id: str, profile_id: str) -> dict[str, Any]:
        service = await bridge.service(service_id)
        await bridge.profile_store.remove(service_id, service.environment, profile_id)
        return {"removed": True}

    return router
