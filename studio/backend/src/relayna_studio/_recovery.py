"""Studio-owned recovery. A committed dispatch intent is never automatically resent."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, insert, select, true, update
from sqlalchemy.exc import IntegrityError

from relayna.api import FAILED_TASK_RETRY_ROUTE_ID

from .audit_context import current_actor_user_id, reset_actor_user_id, set_actor_user_id
from .auth import StudioMember, StudioRole
from .database import StudioDatabase, _append_audit, _recovery_claims, _recovery_plans, members
from .federation import SERVICE_FAILED_TASK_RETRY_PATH, StudioFederationError, StudioFederationService
from .registry import ServiceNotFoundError, ServiceRecord

_LOG = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _date(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class _Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    failure_id: str = Field(min_length=1, max_length=512)
    target_queue: str | None = Field(default=None, min_length=1, max_length=512)
    override_payload: Any = None


class _PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_id: str = Field(min_length=1, max_length=512)
    environment: str = Field(min_length=1, max_length=128)
    items: list[_Selection] = Field(min_length=1, max_length=200)
    interval_seconds: int = Field(default=2, ge=1, le=60)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_cohort(self):
        if len({item.failure_id for item in self.items}) != len(self.items):
            raise ValueError("Select each failure ID only once.")
        if len(self.model_dump_json().encode()) > 512_000:
            raise ValueError("Recovery selection and overrides must fit within 512 KB.")
        return self


class _CreateRequest(_PreviewRequest):
    request_id: UUID
    preview_digest: str = Field(min_length=64, max_length=64)


class _Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["batch", "pause", "resume", "stop"]
    count: int = Field(default=5, ge=1, le=200)
    # Optimistic version prevents response-loss retries or concurrent operators
    # from accidentally authorizing a second batch.
    version: int = Field(ge=0)


def _fingerprint(detail: dict[str, Any]) -> str:
    return _hash(
        {
            key: detail.get(key)
            for key in (
                "failure_id",
                "task_id",
                "failed_at",
                "status",
                "source_queue_name",
                "payload_available",
                "raw_body_b64",
                "body",
                "headers",
                "content_type",
                "retry_status",
                "retried_at",
                "retry_note",
                "retried_task_id",
                "retry_target_queue",
            )
        }
    )


def _report(plan: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(plan)
    items = result["items"]
    requested = [item for item in items if item.get("requested_at")]
    accepted = [item for item in items if item.get("accepted_at")]
    succeeded = [item for item in items if item.get("outcome") == "succeeded"]
    failed = [item for item in items if item.get("outcome") == "failed"]
    result["report"] = {
        "selected": len(items),
        "requested": len(requested),
        "accepted": len(accepted),
        "observed_success": len(succeeded),
        "observed_failure": len(failed),
        "unknown_acceptance": sum(item["dispatch_state"] in {"unknown", "dispatching"} for item in items),
        "unknown_outcome": sum(item.get("outcome") == "unknown" for item in items),
        "awaiting_outcome": sum(item.get("outcome") == "pending" for item in items),
        "not_dispatched": sum(not item.get("requested_at") for item in items),
        "repeated_failures": len(failed),
        "cohort_recovery_seconds": None,
    }
    if len(succeeded) == len(items) and plan.get("started_at"):
        result["report"]["cohort_recovery_seconds"] = max(
            0,
            (
                max(_date(item["observed_at"]) or _now() for item in items) - (_date(plan["started_at"]) or _now())
            ).total_seconds(),
        )
    result.pop("request_hash", None)
    for item in items:
        for key in ("fingerprint", "baseline_event", "receipt_marker"):
            item.pop(key, None)
    return result


class _RecoveryStore:
    def __init__(self, database: StudioDatabase) -> None:
        self.database = database

    async def get(self, plan_id: str) -> dict[str, Any]:
        async with self.database.transaction() as session:
            doc = await session.scalar(select(_recovery_plans.c.document).where(_recovery_plans.c.plan_id == plan_id))
        if doc is None:
            raise HTTPException(404, "Recovery plan not found.")
        return copy.deepcopy(doc)

    async def list(self, environment: str | None, offset: int) -> dict[str, Any]:
        condition = _recovery_plans.c.document["environment"].as_string() == environment if environment else true()
        async with self.database.transaction() as session:
            count = await session.scalar(select(func.count()).select_from(_recovery_plans).where(condition))
            docs = (
                await session.scalars(
                    select(_recovery_plans.c.document)
                    .where(condition)
                    .order_by(_recovery_plans.c.document["created_at"].as_string().desc(), _recovery_plans.c.plan_id)
                    .offset(offset)
                    .limit(50)
                )
            ).all()
        return {"items": [_report(doc) for doc in docs], "count": count, "offset": offset}

    async def create(self, document: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self.database.transaction() as session:
                await session.execute(insert(_recovery_plans).values(plan_id=document["plan_id"], document=document))
                await session.execute(
                    insert(_recovery_claims),
                    [
                        {
                            "service_id": document["service_id"],
                            "environment": document["environment"],
                            "failure_id": item["failure_id"],
                            "plan_id": document["plan_id"],
                        }
                        for item in document["items"]
                    ],
                )
                await _append_audit(
                    session,
                    action="recovery.created",
                    target_type="recovery_plan",
                    target_id=document["plan_id"],
                    details={
                        "service_id": document["service_id"],
                        "environment": document["environment"],
                        "failure_ids": [i["failure_id"] for i in document["items"]],
                    },
                )
        except IntegrityError as exc:
            try:
                existing = await self.get(document["plan_id"])
            except HTTPException:
                raise HTTPException(409, "A selected failure is already reserved by another recovery plan.") from exc
            if existing["request_hash"] != document["request_hash"]:
                raise HTTPException(409, "Request ID already belongs to a different recovery plan.") from exc
            return existing
        return document

    async def change(
        self, plan_id: str, edit: Callable[[dict[str, Any], datetime], bool], action: str
    ) -> dict[str, Any]:
        async with self.database.transaction() as session:
            doc = await session.scalar(
                select(_recovery_plans.c.document).where(_recovery_plans.c.plan_id == plan_id).with_for_update()
            )
            if doc is None:
                raise HTTPException(404, "Recovery plan not found.")
            doc = copy.deepcopy(doc)
            before = {i["failure_id"]: (i["dispatch_state"], i["outcome"]) for i in doc["items"]}
            now = await session.scalar(select(func.clock_timestamp()))
            assert isinstance(now, datetime)
            if edit(doc, now):
                doc["updated_at"] = _iso(now)
                values: dict[str, Any] = {"document": doc}
                if action in {"recovery.batch", "recovery.resume"}:
                    values["next_poll_at"] = func.now()
                await session.execute(
                    update(_recovery_plans).where(_recovery_plans.c.plan_id == plan_id).values(**values)
                )
                changed = [i for i in doc["items"] if before[i["failure_id"]] != (i["dispatch_state"], i["outcome"])]
                if action == "recovery.observed" and not changed:
                    return doc
                token = set_actor_user_id(current_actor_user_id() or doc.get("execution_actor"))
                try:
                    await _append_audit(
                        session,
                        action=action,
                        target_type="recovery_plan",
                        target_id=plan_id,
                        details={
                            "version": doc["version"],
                            "actor_user_id": doc.get("execution_actor"),
                            "states": [
                                {
                                    "failure_id": i["failure_id"],
                                    "dispatch": i["dispatch_state"],
                                    "outcome": i["outcome"],
                                }
                                for i in changed
                            ],
                        },
                    )
                finally:
                    reset_actor_user_id(token)
        return doc

    async def due(self) -> dict[str, Any] | None:
        async with self.database.transaction() as session:
            row = (
                (
                    await session.execute(
                        select(_recovery_plans)
                        .where(_recovery_plans.c.next_poll_at <= func.now())
                        .order_by(_recovery_plans.c.next_poll_at)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            doc = row["document"]
            active = doc["state"] == "running" or any(
                i["dispatch_state"] in {"dispatching", "unknown", "accepted"}
                and i["outcome"] not in {"succeeded", "failed"}
                for i in doc["items"]
            )
            await session.execute(
                update(_recovery_plans)
                .where(_recovery_plans.c.plan_id == row["plan_id"])
                .values(next_poll_at=func.now() + timedelta(seconds=2 if active else 86400))
            )
            return copy.deepcopy(row["document"])


class _RecoveryService:
    def __init__(self, database: StudioDatabase, federation: StudioFederationService) -> None:
        self.store = _RecoveryStore(database)
        self.federation = federation

    async def _service(self, service_id: str, environment: str, base_url: str | None = None) -> ServiceRecord:
        service = await self.federation._get_proxyable_service(service_id)
        if service.environment != environment or base_url is not None and service.base_url != base_url:
            raise HTTPException(409, "Pinned service environment or base URL changed; new dispatch is blocked.")
        routes = (service.capabilities or {}).get("supported_routes", [])
        if not isinstance(routes, list) or not all(
            route in routes for route in ("failed_tasks.detail", "failed_tasks.retry")
        ):
            raise HTTPException(409, "Service must advertise failed-task detail and retry support.")
        return service

    async def _inspect(self, service: ServiceRecord, selection: _Selection) -> dict[str, Any]:
        detail = await self.federation._fetch_failed_task_detail(service, selection.failure_id)
        aliases = (service.capabilities or {}).get("alias_config_summary", {}).get("payload_aliases", {})
        baseline = detail.get("latest_status") or {}
        baseline = baseline.get("event", baseline) if isinstance(baseline, dict) else {}
        timestamp_key = aliases.get("timestamp", "timestamp")
        target = selection.target_queue or detail.get("source_queue_name")
        errors = []
        if detail.get("failure_id") != selection.failure_id:
            errors.append("Service returned a different failure ID.")
        if detail.get("status") not in {"DLQ", "retry_exhausted", "terminal_failed"}:
            errors.append("Failure is no longer eligible for manual retry.")
        if not detail.get("payload_available") or not detail.get("raw_body_b64"):
            errors.append("Retained payload is missing; an override cannot restore eligibility.")
        if detail.get("retry_status") != "not_retried":
            errors.append("This failure snapshot has already been retried.")
        if not target:
            errors.append("No target queue is available.")
        elif target != detail.get("source_queue_name"):
            topology = await self.federation.get_service_workflow_topology(service.service_id)
            graph = topology.get("graph", topology)
            allowed = {stage.get("queue") for stage in graph.get("stages", []) if isinstance(stage, dict)}
            if target not in allowed:
                errors.append("Target queue is not a current workflow stage queue of this service.")
        return {
            "failure_id": selection.failure_id,
            "original_task_id": detail.get("task_id"),
            "target_queue": target,
            "payload_available": bool(detail.get("payload_available")),
            "override_payload": selection.override_payload,
            "eligible": not errors,
            "errors": errors,
            "fingerprint": _fingerprint(detail),
            "baseline_event": {timestamp_key: baseline.get(timestamp_key)} if isinstance(baseline, dict) else {},
            "failed_at": detail.get("failed_at"),
        }

    async def preview(self, payload: _PreviewRequest) -> dict[str, Any]:
        service = await self._service(payload.service_id, payload.environment)
        semaphore = asyncio.Semaphore(8)

        async def inspect(item: _Selection) -> dict[str, Any]:
            async with semaphore:
                try:
                    return await self._inspect(service, item)
                except (StudioFederationError, HTTPException) as exc:
                    return {
                        "failure_id": item.failure_id,
                        "eligible": False,
                        "errors": [str(exc.detail)],
                        "override_payload": item.override_payload,
                        "target_queue": item.target_queue,
                        "payload_available": False,
                    }

        items = await asyncio.gather(*(inspect(item) for item in payload.items))
        preview = {
            "service_id": service.service_id,
            "service_name": service.name,
            "environment": service.environment,
            "base_url": service.base_url,
            "items": items,
            "interval_seconds": payload.interval_seconds,
            "note": payload.note,
        }
        return {
            **preview,
            "preview_digest": _hash(preview),
            "eligible": all(item["eligible"] for item in items),
            "warning": "Preview checks eligibility. It cannot prove business side effects are safe.",
        }

    async def create(self, payload: _CreateRequest, actor: StudioMember | None) -> dict[str, Any]:
        request_hash = _hash(payload.model_dump(mode="json"))
        try:
            existing = await self.store.get(str(payload.request_id))
        except HTTPException:
            existing = None
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(409, "Request ID already belongs to a different recovery plan.")
            return existing
        preview = await self.preview(
            _PreviewRequest.model_validate(payload.model_dump(exclude={"request_id", "preview_digest"}))
        )
        if not preview["eligible"] or preview["preview_digest"] != payload.preview_digest:
            raise HTTPException(409, "Eligibility or preview changed. Preview the cohort again before saving.")
        now = _iso(_now())
        document = {
            **preview,
            "plan_id": str(payload.request_id),
            "request_hash": request_hash,
            "state": "ready",
            "version": 0,
            "created_at": now,
            "updated_at": now,
            "created_by": actor.user_id if actor else None,
            "execution_actor": None,
            "execution_email": None,
            "started_at": None,
            "dispatch_finished_at": None,
            "allowance": 0,
            "next_dispatch_at": now,
        }
        document["items"] = [
            {
                **item,
                "dispatch_state": "pending",
                "outcome": "not_requested",
                "requested_at": None,
                "accepted_at": None,
                "replacement_task_id": None,
                "observed_at": None,
                "last_checked_at": None,
                "message": None,
                "receipt_marker": f"[studio-recovery:{payload.request_id}:{index}]",
            }
            for index, item in enumerate(preview["items"])
        ]
        return await self.store.create(document)

    async def control(self, plan_id: str, control: _Control, actor: StudioMember | None) -> dict[str, Any]:
        def edit(plan: dict[str, Any], now: datetime) -> bool:
            if plan["version"] != control.version:
                raise HTTPException(409, "Plan changed. Refresh before issuing another control.")
            state = plan["state"]
            if control.action == "stop":
                if state in {"stopped", "completed"}:
                    raise HTTPException(409, "Dispatch has already finished.")
                plan["state"] = "stopped"
                plan["dispatch_finished_at"] = _iso(now)
            elif control.action == "pause" and state == "running":
                plan["state"] = "paused"
            elif control.action == "resume" and state == "paused":
                if sum(i["dispatch_state"] != "pending" for i in plan["items"]) >= plan["allowance"]:
                    raise HTTPException(409, "Batch allowance is exhausted. Authorize another batch.")
                plan["state"] = "running"
            elif control.action == "batch" and state in {"ready", "paused"}:
                used = sum(i["dispatch_state"] != "pending" for i in plan["items"])
                if used == len(plan["items"]):
                    raise HTTPException(409, "No pending items remain.")
                plan["allowance"] = min(len(plan["items"]), used + control.count)
                plan["state"] = "running"
            else:
                raise HTTPException(409, "Control is not available in the current plan state.")
            if plan["state"] == "running":
                plan["execution_actor"] = actor.user_id if actor else None
                plan["execution_email"] = actor.email if actor else "operator"
                plan["started_at"] = plan["started_at"] or _iso(now)
            plan["version"] += 1
            return True

        return await self.store.change(plan_id, edit, f"recovery.{control.action}")

    async def _actor_allowed(self, plan: dict[str, Any]) -> bool:
        actor = plan["execution_actor"]
        if actor is None or actor == "shared-operator":
            return True
        async with self.store.database.transaction() as session:
            member = (await session.execute(select(members).where(members.c.user_id == actor))).mappings().first()
        return bool(member and member["role"] == "admin" and member["status"] == "active")

    async def _dispatch(self, plan: dict[str, Any]) -> None:
        item = next((i for i in plan["items"] if i["dispatch_state"] == "pending"), None)
        if plan["state"] != "running" or item is None:
            return
        if sum(i["dispatch_state"] != "pending" for i in plan["items"]) >= plan["allowance"]:
            return
        failure_id = item["failure_id"]
        try:
            service = await self._service(plan["service_id"], plan["environment"], plan["base_url"])
            if not await self._actor_allowed(plan):
                raise HTTPException(403, "The executing operator is no longer an active administrator.")
            current = await self._inspect(
                service,
                _Selection(
                    failure_id=failure_id, target_queue=item["target_queue"], override_payload=item["override_payload"]
                ),
            )
            if not current["eligible"] or current["fingerprint"] != item["fingerprint"]:
                raise HTTPException(409, "; ".join(current["errors"]) or "Failure snapshot changed after preview.")
        except (StudioFederationError, HTTPException, ServiceNotFoundError) as exc:
            message = str(getattr(exc, "detail", exc))
            retryable_preflight = (isinstance(exc, StudioFederationError) and exc.retryable) or (
                isinstance(exc, HTTPException) and exc.status_code == 403
            )

            def block(doc: dict[str, Any], now: datetime) -> bool:
                target = next(i for i in doc["items"] if i["failure_id"] == failure_id)
                if doc["state"] != "running" or target["dispatch_state"] != "pending":
                    return False
                target.update(dispatch_state="pending" if retryable_preflight else "blocked", message=message)
                doc["state"] = "paused"
                doc["version"] += 1
                return True

            await self.store.change(plan["plan_id"], block, "recovery.dispatch.blocked")
            return
        claimed = False

        def claim(doc: dict[str, Any], now: datetime) -> bool:
            nonlocal claimed
            target = next(i for i in doc["items"] if i["failure_id"] == failure_id)
            used = sum(i["dispatch_state"] != "pending" for i in doc["items"])
            if (
                doc["state"] != "running"
                or doc["version"] != plan["version"]
                or target["dispatch_state"] != "pending"
                or used >= doc["allowance"]
                or now < (_date(doc["next_dispatch_at"]) or now)
            ):
                return False
            target.update(dispatch_state="dispatching", requested_at=_iso(now), outcome="unknown")
            # Fresh baseline before sending protects reused task IDs from old terminal evidence.
            target["baseline_event"] = current.get("baseline_event")
            doc["next_dispatch_at"] = _iso(now + timedelta(seconds=doc["interval_seconds"]))
            claimed = True
            return True

        latest = await self.store.change(plan["plan_id"], claim, "recovery.dispatch.requested")
        if not claimed:
            return
        item = next(i for i in latest["items"] if i["failure_id"] == failure_id)
        try:
            response = await self.federation._mutate_failed_task(
                service,
                failure_id,
                capability_id=FAILED_TASK_RETRY_ROUTE_ID,
                path_template=SERVICE_FAILED_TASK_RETRY_PATH,
                payload={
                    "target_queue": item["target_queue"],
                    "override_payload": item["override_payload"],
                    "retried_by": latest["execution_email"],
                    "note": item["receipt_marker"] + " " + plan["note"],
                },
            )
            accepted_at = response.get("retried_at")
            if (
                response.get("failure_id") != failure_id
                or response.get("retry_status") != "retried"
                or not _date(accepted_at)
            ):
                raise ValueError("Retry response did not contain a valid acceptance receipt.")
            changes = {
                "dispatch_state": "accepted",
                "accepted_at": accepted_at,
                "replacement_task_id": response.get("retried_task_id"),
                "outcome": "pending",
                "message": None,
            }
        except Exception as exc:
            # Even a 5xx can follow publication. No mutation error ever authorizes a resend.
            changes = {"dispatch_state": "unknown", "outcome": "unknown", "message": str(exc)}
        await self._item_change(plan["plan_id"], failure_id, changes, "recovery.dispatch.recorded")

    async def _item_change(self, plan_id: str, failure_id: str, changes: dict[str, Any], action: str) -> None:
        def edit(plan: dict[str, Any], now: datetime) -> bool:
            item = next(i for i in plan["items"] if i["failure_id"] == failure_id)
            # A late request response can resolve an unknown intent; stale observers
            # cannot overwrite an already observed terminal result.
            if item["outcome"] in {"succeeded", "failed"}:
                return False
            if changes.get("dispatch_state") == "unknown" and item["dispatch_state"] == "accepted":
                return False
            item.update(changes)
            return True

        await self.store.change(plan_id, edit, action)

    async def _observe(self, plan: dict[str, Any], item: dict[str, Any]) -> None:
        if item["dispatch_state"] not in {"dispatching", "unknown", "accepted"} or item["outcome"] in {
            "succeeded",
            "failed",
        }:
            return
        now = _now()
        if item["dispatch_state"] == "dispatching" and now - (_date(item["requested_at"]) or now) < timedelta(
            seconds=60
        ):
            return
        changes: dict[str, Any] = {"last_checked_at": _iso(now)}
        if item["dispatch_state"] != "accepted":
            changes.update(dispatch_state="unknown", outcome="unknown")
        try:
            service = await self._service(plan["service_id"], plan["environment"], plan["base_url"])
            if item["dispatch_state"] != "accepted":
                receipt = await self.federation._fetch_failed_task_detail(service, item["failure_id"])
                if (
                    receipt.get("failure_id") != item["failure_id"]
                    or receipt.get("retry_status") != "retried"
                    or (receipt.get("retry_note") or "").split(" ", 1)[0] != item["receipt_marker"]
                    or not _date(receipt.get("retried_at"))
                ):
                    changes["message"] = (
                        "Acceptance unknown; no matching retained retry receipt. Never resent automatically."
                    )
                    return
                changes.update(
                    dispatch_state="accepted",
                    accepted_at=receipt["retried_at"],
                    replacement_task_id=receipt.get("retried_task_id"),
                    outcome="pending",
                    message=None,
                )
            accepted = {**item, **changes}
            replacement = accepted.get("replacement_task_id")
            if not isinstance(replacement, str) or not replacement.strip():
                changes.update(outcome="unknown", message="Accepted without a replacement task ID; outcome is unknown.")
                return
            status = await self.federation._fetch_status(service, replacement)
            event = status.get("event") or {}
            if not isinstance(event, dict) or status.get("task_id") != replacement:
                raise HTTPException(502, "Status response did not identify the replacement task.")
            aliases = (service.capabilities or {}).get("alias_config_summary", {}).get("payload_aliases", {})
            event_task_id = event.get(aliases.get("task_id", "task_id"))
            if event_task_id is not None and event_task_id != replacement:
                raise HTTPException(502, "Status event belongs to a different task.")
            timestamp = _date(event.get(aliases.get("timestamp", "timestamp")))
            baseline = item.get("baseline_event") or {}
            baseline = baseline.get("event", baseline)
            baseline_time = _date(baseline.get(aliases.get("timestamp", "timestamp")))
            boundary = _date(accepted["accepted_at"])
            label = str(event.get(aliases.get("status", "status"), "")).lower()
            if timestamp and boundary and timestamp >= boundary and (not baseline_time or timestamp > baseline_time):
                if label in {"completed", "failed", "dlq", "retry_exhausted", "terminal_failed"}:
                    changes.update(
                        outcome="succeeded" if label == "completed" else "failed",
                        observed_at=_iso(timestamp),
                        observed_event={
                            "task_id": replacement,
                            "status": label,
                            "timestamp": _iso(timestamp),
                            "event_id": event.get(aliases.get("event_id", "event_id")),
                        },
                        message=None,
                    )
        except (StudioFederationError, HTTPException, ServiceNotFoundError) as exc:
            changes["message"] = str(getattr(exc, "detail", exc))
        finally:
            accepted_at = _date(changes.get("accepted_at", item.get("accepted_at")))
            if (
                accepted_at
                and changes.get("outcome", item["outcome"]) not in {"succeeded", "failed"}
                and now - accepted_at > timedelta(hours=24)
            ):
                changes.update(
                    outcome="unknown", message="No fresh terminal evidence within 24 hours; observation continues."
                )
            await self._item_change(plan["plan_id"], item["failure_id"], changes, "recovery.observed")

    async def tick(self) -> None:
        plan = await self.store.due()
        if plan is None:
            return
        await self._dispatch(plan)
        plan = await self.store.get(plan["plan_id"])
        candidates = [
            i
            for i in plan["items"]
            if i["dispatch_state"] in {"dispatching", "unknown", "accepted"}
            and i["outcome"] not in {"succeeded", "failed"}
        ]
        for item in sorted(candidates, key=lambda i: i.get("last_checked_at") or "")[:5]:
            await self._observe(plan, item)

        def settle(doc: dict[str, Any], now: datetime) -> bool:
            used = sum(i["dispatch_state"] != "pending" for i in doc["items"])
            if doc["state"] == "running" and used >= doc["allowance"]:
                doc["state"] = "completed" if used == len(doc["items"]) else "paused"
                if doc["state"] == "completed":
                    doc["dispatch_finished_at"] = _iso(now)
                doc["version"] += 1
                return True
            return False

        await self.store.change(plan["plan_id"], settle, "recovery.batch.finished")

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                _LOG.exception("Recovery tick failed; committed intents will be reconciled, never resent")
            await asyncio.sleep(0.2)


def _create_recovery_router(service: _RecoveryService | None) -> APIRouter:
    router = APIRouter()

    def require() -> _RecoveryService:
        if service is None:
            raise HTTPException(
                503, "Recovery Center requires PostgreSQL. Configure the Studio database and upgrade its schema."
            )
        return service

    def actor(request: Request) -> StudioMember | None:
        member = getattr(request.state, "studio_member", None)
        if isinstance(member, StudioMember) and member.role != StudioRole.ADMIN:
            raise HTTPException(403, "Recovery dispatch requires an administrator.")
        return member if isinstance(member, StudioMember) else None

    @router.post("/studio/recovery/preview")
    async def preview(request: Request, payload: _PreviewRequest):
        actor(request)
        try:
            return await require().preview(payload)
        except StudioFederationError as exc:
            return exc.to_response()

    @router.post("/studio/recovery/plans")
    async def create(request: Request, payload: _CreateRequest):
        try:
            return _report(await require().create(payload, actor(request)))
        except StudioFederationError as exc:
            return exc.to_response()

    @router.get("/studio/recovery/plans")
    async def plans(environment: str | None = None, offset: int = Query(default=0, ge=0, le=100000)):
        return await require().store.list(environment, offset)

    @router.get("/studio/recovery/plans/{plan_id}")
    async def detail(plan_id: UUID):
        return _report(await require().store.get(str(plan_id)))

    @router.post("/studio/recovery/plans/{plan_id}/control")
    async def control(request: Request, plan_id: UUID, payload: _Control):
        return _report(await require().control(str(plan_id), payload, actor(request)))

    return router
