"""Service-scoped native Chamber workspace; no browser connection to Chamber."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote, urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import UploadFile

from ._chamber_connection import _chamber_response, _ConnectionUpdate, _redact
from .audit_context import current_actor_user_id
from .auth import StudioMemberStatus, StudioRole


def _admin(request: Request) -> None:
    _reader(request)
    if request.state.studio_member.role != StudioRole.ADMIN:
        raise HTTPException(403, "Studio administrator access is required.")


def _reader(request: Request) -> None:
    member = getattr(request.state, "studio_member", None)
    if member is None:
        raise HTTPException(401, "Studio authentication is required.")
    if member.status != StudioMemberStatus.ACTIVE:
        raise HTTPException(403, "Active Studio membership is required.")


def _identity(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", value) or value in {".", ".."}:
        raise HTTPException(422, "Invalid Chamber identity.")
    return quote(value, safe="")


def _target(config: dict[str, Any]) -> dict[str, Any]:
    runtime = config.get("runtime", {})
    access = runtime.get("trafficAccess", {})
    deployment = config.get("deployment", {})
    return {
        "context": runtime.get("kubernetesContext", ""),
        "namespace": runtime.get("namespace", runtime.get("namespaceBase", "")),
        "service": access.get("service") or config.get("service", {}).get("name", ""),
        "port": access.get("servicePort"),
        "workloads": [item.get("name") for item in deployment.get("workloads", [])],
        "runtime_mode": runtime.get("mode", "local"),
        "provider": runtime.get("provider", "local"),
        "prometheus_configured": bool(runtime.get("prometheusUrl")),
    }


class _AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    config: dict[str, Any]
    mode: Literal["local", "kubernetes"] = "kubernetes"
    name: str = Field(default="Chamber assessment", min_length=1, max_length=120)


class _StartConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmed_target: bool = False
    confirmed_faults: bool = False


async def _advanced_plan(
    bridge: Any,
    service_id: str,
    payload: _AssessmentRequest,
    *,
    connection: dict[str, Any] | None = None,
    upstream_plan: str = "",
) -> dict[str, Any]:
    service = await bridge.service(service_id, mutate=True)
    try:
        size = len(json.dumps(payload.config, allow_nan=False).encode())
    except ValueError as exc:
        raise HTTPException(422, "Assessment configuration must contain finite JSON values.") from exc
    if size > 1024 * 1024:
        raise HTTPException(422, "Assessment configuration exceeds 1 MiB.")
    config = payload.config
    for name in ("service", "deployment", "traffic", "runtime"):
        if not isinstance(config.get(name), dict):
            raise HTTPException(422, f"Assessment requires a {name} object.")
    runtime = config["runtime"]
    journeys = config["traffic"].get("journeys")
    if (
        not isinstance(journeys, list)
        or not 1 <= len(journeys) <= 20
        or not all(isinstance(item, dict) for item in journeys)
    ):
        raise HTTPException(422, "Assessment traffic needs 1–20 journey objects.")
    for area in ("experiment", "agents"):
        if area in config and not isinstance(config[area], dict):
            raise HTTPException(422, f"Assessment {area} must be an object.")
    load = config["traffic"].get("load", {})
    if not isinstance(load, dict):
        raise HTTPException(422, "Assessment load must be an object.")

    def has_redaction(value: Any) -> bool:
        if isinstance(value, dict):
            return any(has_redaction(item) for item in value.values())
        if isinstance(value, list):
            return any(has_redaction(item) for item in value)
        return value == "[redacted]"

    if has_redaction(config):
        raise HTTPException(422, "Replace redacted credentials with server environment references before planning.")
    if payload.mode == "kubernetes" and (
        not runtime.get("kubernetesContext") or not (runtime.get("namespace") or runtime.get("namespaceBase"))
    ):
        raise HTTPException(422, "Review an explicit Kubernetes context and namespace before planning.")
    connection = connection or await bridge.connections.current()
    identity = uuid4().hex
    origin = {
        "studio_service_id": service_id,
        "studio_environment": service.environment,
        "studio_reference": identity,
        "actor": current_actor_user_id() or "studio-operator",
    }
    reply = (
        {"run_id": upstream_plan}
        if upstream_plan
        else await bridge.call("POST", "plans", {"config": config, "origin": origin}, connection=connection)
    )
    if not isinstance(reply.get("run_id"), str) or not reply["run_id"]:
        raise HTTPException(502, "Chamber did not return a plan identity.")
    journeys = config["traffic"].get("journeys", [])
    first = journeys[0] if journeys else {}
    load = config["traffic"].get("load", {})
    requires_faults = bool(
        runtime.get("faults") or config.get("experiment", {}).get("family") not in {None, "queue_drain"}
    )
    record = {
        "id": identity,
        "chamber_plan_id": reply["run_id"],
        "environment": service.environment,
        "profile_name": payload.name,
        "created_at": datetime.now(UTC).isoformat(),
        "state": "planned",
        "context": runtime.get("kubernetesContext", ""),
        "namespace": runtime.get("namespace", runtime.get("namespaceBase", "")),
        "prometheus_url": runtime.get("prometheusUrl"),
        "mode": payload.mode,
        "kind": "assessment",
        "connection": connection,
        "target": _target(config),
        "origin": origin,
        "requires_target_confirmation": True,
        "requires_fault_confirmation": requires_faults,
        "review_config": _redact(config),
        "load_summary": _redact(config["traffic"]),
        "method": first.get("method", ""),
        "path": first.get("path", ""),
        "adapter": first.get("adapter", "http"),
        "request": {
            "profile_id": "",
            "inputs": {},
            "vus": first.get(
                "vus",
                load.get("maxInFlight", max([1, *(stage.get("targetVus", 0) for stage in first.get("stages", []))])),
            ),
            "iterations": first.get("iterations", 0),
            "duration_seconds": first.get("durationSeconds", load.get("durationSeconds", 0)),
        },
        "files": [file for journey in journeys for file in bridge.files(journey)],
    }
    await bridge.save(service_id, record)
    async with bridge.redis.pipeline(transaction=True) as pipe:
        pipe.zadd(bridge.key(service_id, "history"), {identity: time.time()})
        pipe.zremrangebyrank(bridge.key(service_id, "history"), 0, -101)
        pipe.expire(bridge.key(service_id, "history"), 30 * 86400)
        await pipe.execute()
    return bridge.public(record)


def _workspace_router(bridge: Any) -> APIRouter:
    router = APIRouter(prefix="/chamber", dependencies=[Depends(_reader)])

    async def connection_for(service_id: str, reference: str = "", run_id: str = ""):
        await bridge.service(service_id)
        if reference:
            record = await bridge.bound(service_id, reference)
            if run_id and run_id not in {record.get("run_id"), record.get("chamber_plan_id")}:
                raise HTTPException(404, "This Chamber run does not belong to the selected Studio reference.")
            return record.get("connection") or await bridge.connections.current()
        return await bridge.connections.current()

    async def call(service_id: str, method: str, path: str, payload: Any = None, reference: str = "", run_id: str = ""):
        return _redact(
            await bridge.call(method, path, payload, connection=await connection_for(service_id, reference, run_id))
        )

    @router.get("/connection")
    async def connection_settings(service_id: str) -> dict[str, Any]:
        current = await connection_for(service_id)
        result = bridge.connections.public(current)
        stored = await bridge.redis.get("studio:chamber:health:" + str(current["id"]))
        if stored:
            result.update(_redact(json.loads(stored)))
        return result

    @router.put("/connection", dependencies=[Depends(_admin)])
    async def update_connection(service_id: str, payload: _ConnectionUpdate) -> dict[str, Any]:
        await bridge.service(service_id)
        return await bridge.connections.update(payload)

    @router.post("/connection/test", dependencies=[Depends(_admin)])
    async def test_connection(service_id: str, payload: _ConnectionUpdate | None = None) -> dict[str, Any]:
        await bridge.service(service_id)
        current = await bridge.connections.candidate(payload) if payload else await bridge.connections.current()
        result = bridge.connections.public(current)
        now = datetime.now(UTC).isoformat()
        result["checked_at"] = now
        if not current.get("url"):
            result.update(status="not_configured", message="Set a reachable internal Chamber URL and credential.")
        else:
            started = time.monotonic()
            try:
                capabilities = await bridge.call("GET", "capabilities", connection=current)
                if capabilities.get("schema_version") != "chamber.ampule.dev/capabilities/v1":
                    result.update(
                        status="incompatible",
                        message="The endpoint does not provide the supported Chamber capabilities contract.",
                    )
                elif not {"cleanup_verification", "run_metadata", "task_pagination", "managed_uploads"}.issubset(
                    set(capabilities.get("api_features", []))
                ):
                    result.update(
                        status="limited",
                        message=(
                            "Chamber is reachable but lacks native workspace APIs. "
                            "Upgrade Chamber to 1.11 or later for the complete workflow."
                        ),
                        capabilities=_redact(capabilities),
                        last_success_at=now,
                    )
                else:
                    result.update(
                        status="ready",
                        message=(
                            "Authenticated Chamber API verified. "
                            "Cluster, target and telemetry still need their own checks."
                        ),
                        capabilities=_redact(capabilities),
                        last_success_at=now,
                    )
                result["latency_ms"] = round((time.monotonic() - started) * 1000)
            except HTTPException as exc:
                category = (exc.headers or {}).get("X-Chamber-Error", "unavailable")
                result.update(status=category, message=exc.detail)
        if payload is None:
            old = await bridge.redis.get("studio:chamber:health:" + str(current["id"]))
            if old and "last_success_at" not in result:
                result["last_success_at"] = json.loads(old).get("last_success_at")
            await bridge.redis.set(
                "studio:chamber:health:" + str(current["id"]),
                json.dumps(
                    {
                        key: result[key]
                        for key in ("status", "message", "checked_at", "last_success_at", "latency_ms", "capabilities")
                        if key in result
                    }
                ),
                ex=86400,
            )
        return result

    @router.get("/catalog/{kind}")
    async def catalog(service_id: str, kind: Literal["capabilities", "chambers", "scenarios"]) -> dict[str, Any]:
        return await call(service_id, "GET", kind)

    @router.get("/scenarios/{source}/{scenario_id}")
    async def scenario(service_id: str, source: Literal["bundled", "user"], scenario_id: str) -> dict[str, Any]:
        service = await bridge.service(service_id)
        return await call(
            service_id,
            "GET",
            f"scenarios/{source}/{_identity(scenario_id)}?{urlencode({'service_name': service.name})}",
        )

    @router.get("/scenarios/{source}/{scenario_id}/document")
    async def scenario_document(
        service_id: str, source: Literal["bundled", "user"], scenario_id: str
    ) -> dict[str, Any]:
        return await call(service_id, "GET", f"scenarios/{source}/{_identity(scenario_id)}/document")

    @router.post("/actions/{action}", dependencies=[Depends(_admin)])
    async def action(
        service_id: str,
        action: Literal[
            "discover",
            "inspect",
            "propose",
            "validate",
            "validate-document",
            "save-scenario",
            "create-chamber",
            "compare",
        ],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        await bridge.service(service_id, mutate=True)
        if len(json.dumps(payload).encode()) > 1024 * 1024:
            raise HTTPException(422, "Request exceeds 1 MiB.")
        paths = {
            "inspect": "inspect",
            "propose": "scenarios/propose",
            "validate": "scenarios/validate",
            "validate-document": "scenarios/validate-document",
            "save-scenario": "scenarios",
            "create-chamber": "chambers",
            "compare": "compare",
        }
        if action == "discover":
            if set(payload) != {"context", "namespace"} or not all(
                isinstance(item, str) and 0 < len(item) <= 200 for item in payload.values()
            ):
                raise HTTPException(422, "Discovery needs an explicit context and namespace.")
            return await call(service_id, "GET", "kubernetes/discovery?" + urlencode(payload))
        if action == "compare":
            reference = payload.pop("reference", "")
            candidate = payload.get("candidate_run_id", "")
            return await call(service_id, "POST", paths[action], payload, reference=reference, run_id=candidate)
        return await call(service_id, "POST", paths[action], payload)

    @router.post("/plans", status_code=201, dependencies=[Depends(_admin)])
    async def assessment_plan(service_id: str, payload: _AssessmentRequest) -> dict[str, Any]:
        return await _advanced_plan(bridge, service_id, payload)

    @router.get("/runs")
    async def chamber_history(
        service_id: str,
        q: str = Query("", max_length=200),
        state: str = Query("", max_length=50),
        outcome: str = Query("", max_length=50),
        environment: str = Query("", max_length=128),
        coverage: str = Query("", max_length=50),
        fault: str = Query("", max_length=100),
        date_from: str = Query("", max_length=30),
        date_to: str = Query("", max_length=30),
        view: str = Query("", max_length=50),
        page: int = Query(1, ge=1, le=1000),
        page_size: int = Query(20, ge=1, le=100),
        archived: bool = False,
    ) -> dict[str, Any]:
        values = {
            "q": q,
            "state": state,
            "outcome": outcome,
            "environment": environment,
            "coverage": coverage,
            "fault": fault,
            "date_from": date_from,
            "date_to": date_to,
            "view": view,
            "page": page,
            "page_size": page_size,
            "archived": str(archived).lower(),
        }
        return await call(service_id, "GET", "runs?" + urlencode(values))

    @router.get("/runs/{run_id}")
    async def chamber_run(service_id: str, run_id: str, reference: str = "") -> dict[str, Any]:
        return await call(
            service_id,
            "GET",
            f"runs/{_identity(run_id)}?include_task_details=false",
            reference=reference,
            run_id=run_id,
        )

    @router.get("/runs/{run_id}/{section}")
    async def run_section(
        service_id: str,
        run_id: str,
        section: Literal["tasks", "evidence-explorer"],
        request: Request,
        reference: str = "",
    ) -> dict[str, Any]:
        allowed = (
            {"page", "page_size", "search", "status", "failed_first"}
            if section == "tasks"
            else {
                "page",
                "page_size",
                "start",
                "end",
                "finding",
                "workload",
                "signal",
                "severity",
                "task_id",
                "pod",
                "journey",
            }
        )
        values = {k: v for k, v in request.query_params.items() if k in allowed and len(v) <= 200}
        for field in ("page", "page_size"):
            if field in values:
                try:
                    numeric = int(values[field])
                    if not 1 <= numeric <= (100 if field == "page_size" else 10000):
                        raise ValueError
                except ValueError as exc:
                    raise HTTPException(422, "Invalid evidence pagination.") from exc
        return await call(
            service_id,
            "GET",
            f"runs/{_identity(run_id)}/{section}?{urlencode(values)}",
            reference=reference,
            run_id=run_id,
        )

    @router.post("/runs/{run_id}/{operation}", dependencies=[Depends(_admin)])
    async def run_action(
        service_id: str,
        run_id: str,
        operation: Literal["archive", "tags", "rerun"],
        payload: dict[str, Any],
        reference: str = "",
    ) -> dict[str, Any]:
        if operation == "rerun":
            connection = await connection_for(service_id, reference, run_id)
            reply = await bridge.call("POST", f"runs/{_identity(run_id)}/rerun", payload, connection=connection)
            planned = await bridge.call("GET", f"runs/{_identity(reply['run_id'])}", connection=connection)
            config = planned.get("config", {})
            mode = "kubernetes" if config.get("runtime", {}).get("provider") == "kubernetes" else "local"
            return await _advanced_plan(
                bridge,
                service_id,
                _AssessmentRequest(config=config, mode=mode, name="Rerun assessment"),
                connection=connection,
                upstream_plan=reply["run_id"],
            )
        reply = await call(
            service_id,
            "POST",
            f"runs/{_identity(run_id)}/{operation}?include_task_details=false",
            payload,
            reference,
            run_id,
        )
        return reply

    @router.post("/jobs/{job_id}/cleanup-verified", dependencies=[Depends(_admin)])
    async def cleanup(service_id: str, job_id: str, payload: dict[str, Any], reference: str = "") -> dict[str, Any]:
        if payload != {"confirmed": True}:
            raise HTTPException(422, "Explicitly confirm cleanup and fault restoration before releasing admission.")
        if reference:
            record = await bridge.bound(service_id, reference)
            if record.get("job_id") != job_id:
                raise HTTPException(404, "This job does not belong to the selected Studio reference.")
        return await call(service_id, "POST", f"jobs/{_identity(job_id)}/cleanup-verified", payload, reference)

    @router.post("/uploads", dependencies=[Depends(_admin)])
    async def upload(service_id: str, request: Request) -> dict[str, Any]:
        async with request.form(max_files=1, max_fields=1, max_part_size=1024) as form:
            file = form.get("file")
            field = str(form.get("field") or "file")
            if not isinstance(file, UploadFile) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", field):
                raise HTTPException(422, "Choose a file and valid multipart field name.")
            content = await file.read(128 * 1024 * 1024 + 1)
            if len(content) > 128 * 1024 * 1024:
                raise HTTPException(413, "Studio uploads are limited to 128 MiB per file.")
            current = await connection_for(service_id)
            raw, _ = await _chamber_response(
                bridge.client,
                bridge.connections,
                current,
                "POST",
                "uploads",
                upload=(file.filename or "upload.bin", content, file.content_type or "application/octet-stream"),
                field=field,
            )
        return json.loads(raw)

    @router.get("/download/{run_id}/{kind}")
    async def download(
        service_id: str,
        run_id: str,
        kind: Literal["report", "evidence"],
        evidence_id: str = "",
        format: Literal["markdown", "json", "html"] = "markdown",
        reference: str = "",
    ) -> Response:
        path = (
            f"runs/{_identity(run_id)}/report?format={format}"
            if kind == "report"
            else f"runs/{_identity(run_id)}/evidence/{_identity(evidence_id)}"
        )
        current = await connection_for(service_id, reference, run_id)
        raw, _ = await _chamber_response(bridge.client, bridge.connections, current, "GET", path, binary=True)
        suffix = {"markdown": "md", "json": "json", "html": "html"}[format] if kind == "report" else "bin"
        return Response(
            raw,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="chamber-{_identity(run_id)}-{kind}.{suffix}"',
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox",
                "Cache-Control": "no-store",
            },
        )

    return router
