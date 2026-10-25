"""Scoped reliability configuration and a bridge to existing alert infrastructure."""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, insert, select, update

from ._profile_import import _admin
from .database import StudioDatabase, _append_audit, operator_settings, services
from .failed_task_notifications import FailedTaskEmailClient
from .registry import ServiceNotFoundError

if TYPE_CHECKING:
    from .app import StudioRuntime

_PREFIX = "reliability:v1:"
_Kind = Literal[
    "backlog_age", "retry_activity", "stale_leases", "missing_worker", "completion_success", "end_to_end_latency"
]
_BUSINESS = {"completion_success", "end_to_end_latency"}


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("A timezone is required.")
    return result.astimezone(UTC)


class _GoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    service_id: str = Field(min_length=1, max_length=500)
    environment: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    owner: str = Field(min_length=1, max_length=120)
    kind: _Kind
    threshold: float = Field(gt=0, le=1e9)
    window_seconds: int = Field(default=300, ge=60, le=2592000)
    deadline_seconds: int = Field(default=120, ge=1, le=86400)
    freshness_seconds: int = Field(default=180, ge=30, le=3600)
    hold_seconds: int = Field(default=60, ge=0, le=3600)
    runtime_scope_confirmed: bool = False
    receivers: list[str] = Field(min_length=1, max_length=20)

    @field_validator("receivers")
    @classmethod
    def _emails(cls, values: list[str]) -> list[str]:
        import re

        if any(not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value) or len(value) > 320 for value in values):
            raise ValueError("Use email addresses without display names.")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def _ratio(self) -> _GoalInput:
        if self.kind in _BUSINESS and self.threshold > 100:
            raise ValueError("Business objective threshold is a percentage from 0 to 100.")
        return self


class _Goal(_GoalInput):
    id: str
    revision: str
    created_at: str


class _Alert(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    startsAt: str
    endsAt: str = ""

    @field_validator("startsAt")
    @classmethod
    def _start(cls, value: str) -> str:
        return _iso(_timestamp(value))


class _Webhook(BaseModel):
    model_config = ConfigDict(extra="ignore")
    alerts: list[_Alert] = Field(min_length=1, max_length=100)
    truncatedAlerts: int = Field(default=0, ge=0)


def _key(goal_id: str) -> str:
    return _PREFIX + goal_id


class _Store:
    def __init__(self, database: StudioDatabase | None):
        self.database = database

    def required(self) -> StudioDatabase:
        if self.database is None:
            raise HTTPException(503, "Reliability goals require PostgreSQL.")
        return self.database

    async def list_documents(self) -> list[dict[str, Any]]:
        if self.database is None:
            return []
        async with self.database.sessions() as session:
            result = await session.scalars(
                select(operator_settings.c.value)
                .where(operator_settings.c.setting_key.startswith(_PREFIX))
                .order_by(operator_settings.c.setting_key)
            )
            return list(result)

    async def get(self, goal_id: str) -> dict[str, Any]:
        async with self.required().sessions() as session:
            value = await session.scalar(
                select(operator_settings.c.value).where(operator_settings.c.setting_key == _key(goal_id))
            )
        if value is None:
            raise HTTPException(404, "Reliability goal not found.")
        return value

    async def save(self, goal: _Goal) -> None:
        async with self.required().transaction() as session:
            await session.execute(
                insert(operator_settings).values(
                    setting_key=_key(goal.id), value={"goal": goal.model_dump(), "delivery": {}}
                )
            )
            await _append_audit(
                session,
                action="reliability.goal.create",
                target_type="reliability_goal",
                target_id=goal.id,
                details=goal.model_dump(),
            )

    async def remove(self, goal_id: str) -> None:
        async with self.required().transaction() as session:
            result = await session.execute(
                delete(operator_settings)
                .where(operator_settings.c.setting_key == _key(goal_id))
                .returning(operator_settings.c.setting_key)
            )
            if result.scalar_one_or_none() is None:
                raise HTTPException(404, "Reliability goal not found.")
            await _append_audit(
                session, action="reliability.goal.delete", target_type="reliability_goal", target_id=goal_id, details={}
            )


def _selector(labels: dict[str, str]) -> str:
    return ",".join(f"{key}={json.dumps(value)}" for key, value in sorted(labels.items()))


def _queries(goal: _Goal, runtime_service: str) -> dict[str, str]:
    runtime = _selector({"service": runtime_service})
    custom = _selector(
        {
            "service": runtime_service,
            "environment": goal.environment,
            "objective": goal.kind,
            "window_seconds": str(goal.window_seconds),
            "deadline_seconds": str(goal.deadline_seconds),
        }
    )
    if goal.kind == "retry_activity":
        metric = f"relayna_tasks_retried_total{{{runtime}}}"
        return {"value": f"sum(increase({metric}[{goal.window_seconds}s]))", "freshness": f"min(timestamp({metric}))"}
    if goal.kind == "missing_worker":
        metric = f"relayna_worker_heartbeat_timestamp{{{runtime}}}"
        return {"value": f"time() - max({metric})", "freshness": f"min(timestamp({metric}))"}
    if goal.kind in _BUSINESS:
        return {
            "good": f"relayna_reliability_good_work{{{custom}}}",
            "eligible": f"relayna_reliability_eligible_work{{{custom}}}",
            "window_start": f"relayna_reliability_window_start_timestamp_seconds{{{custom}}}",
            "window_end": f"relayna_reliability_window_end_timestamp_seconds{{{custom}}}",
            "freshness": f"relayna_reliability_evidence_timestamp_seconds{{{custom}}}",
        }
    metric = "backlog_oldest_age_seconds" if goal.kind == "backlog_age" else "stale_leases"
    return {
        "value": f"relayna_reliability_{metric}{{{custom}}}",
        "freshness": f"relayna_reliability_evidence_timestamp_seconds{{{custom}}}",
    }


def _expression(goal: _Goal, queries: dict[str, str]) -> tuple[str, str]:
    fresh = f"(time() - ({queries['freshness']}) <= {goal.freshness_seconds}) and ({queries['freshness']} <= time())"
    if goal.kind in _BUSINESS:
        # Exporters provide mature cohorts ending at evaluation time minus deadline.
        valid = (
            f"({queries['eligible']} > 0) and ({queries['good']} >= 0) "
            f"and ({queries['good']} <= {queries['eligible']}) "
            f"and (abs(({queries['window_end']}) - (time() - {goal.deadline_seconds})) <= 30) "
            f"and (abs(({queries['window_end']}) - ({queries['window_start']}) - {goal.window_seconds}) <= 1)"
        )
        condition = f"(100 * ({queries['good']}) / ({queries['eligible']}) < {goal.threshold})"
    else:
        valid = f"({queries['value']} >= 0)"
        condition = f"({queries['value']} >= {goal.threshold})"
    for query in queries.values():
        valid += f" and (abs({query}) < Inf) and on() (count({query}) == 1)"
    return f"{condition} and {valid} and {fresh}", f"{valid} and {fresh}"


def _investigation(goal: _Goal, now: datetime | None = None) -> str:
    end = now or _now()
    params = urlencode(
        {
            "service_id": goal.service_id,
            "environment": goal.environment,
            "from": _iso(end - timedelta(seconds=goal.window_seconds + goal.deadline_seconds)),
            "to": _iso(end),
        }
    )
    return f"/tasks/search?{params}"


async def _service(runtime: StudioRuntime, goal: _GoalInput):
    try:
        service = await runtime.registry_service.get_service(goal.service_id)
    except ServiceNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if service.environment != goal.environment or service.status == "disabled":
        raise HTTPException(409, "Goal environment must match an enabled registered service.")
    return service


async def _sample(runtime: StudioRuntime, base_url: str, expression: str, now: datetime) -> float | None:
    runtime.outbound_policy.validate_url(base_url, label="Reliability Prometheus URL")
    response = await runtime.http_client.get(
        base_url.rstrip("/") + "/api/v1/query",
        params={"query": expression, "time": str(now.timestamp())},
        headers={"Accept": "application/json"},
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") != "success" or payload.get("warnings"):
        raise ValueError("Prometheus returned unsuccessful or partial evidence.")
    data = payload["data"]
    if not isinstance(data, dict) or data.get("resultType") != "vector":
        raise ValueError("Expected an instant vector.")
    rows = data["result"]
    if not isinstance(rows, list):
        raise ValueError("Expected a vector result list.")
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("Evidence must have exactly one aggregate series; check exporter scope.")
    if not isinstance(rows[0], dict):
        raise ValueError("Expected a metric sample.")
    sample = rows[0]["value"]
    if not isinstance(sample, list) or len(sample) != 2:
        raise ValueError("Expected a timestamp/value pair.")
    if abs(float(sample[0]) - now.timestamp()) > 30:
        raise ValueError("Prometheus returned an unexpected evaluation timestamp.")
    value = float(sample[1])
    if not math.isfinite(value):
        return None
    return value


async def _preview(runtime: StudioRuntime, goal: _Goal) -> dict[str, Any]:
    now = _now()
    result: dict[str, Any] = {
        "state": "unavailable",
        "evaluated_at": _iso(now),
        "from": _iso(now - timedelta(seconds=goal.window_seconds + goal.deadline_seconds)),
        "to": _iso(now),
        "value": None,
        "eligible": None,
        "good": None,
        "investigation_url": _investigation(goal, now),
    }
    service = await _service(runtime, goal)
    config = service.metrics_config
    if goal.kind in {"retry_activity", "missing_worker"} and not goal.runtime_scope_confirmed:
        return {
            **result,
            "state": "unsupported",
            "detail": "Confirm the Prometheus runtime service selector is isolated to this environment.",
        }
    if config is None:
        return {**result, "state": "unsupported", "detail": "Configure Prometheus for this service."}
    queries = _queries(
        goal, config.runtime_service_label_value or config.service_selector_labels.get("service") or service.service_id
    )
    result["queries"] = queries
    try:
        values = {key: await _sample(runtime, config.base_url, expression, now) for key, expression in queries.items()}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
        return {
            **result,
            "detail": "Prometheus evidence is unavailable, partial, or ambiguous; inspect provider and series scope.",
        }
    if any(value is None for value in values.values()):
        return {
            **result,
            "state": "no_data",
            "detail": "Required metrics are missing or non-finite. Missing evidence is not healthy.",
        }
    samples = {key: float(value) for key, value in values.items() if value is not None}
    evidence = samples["freshness"]
    if not 0 <= now.timestamp() - evidence <= goal.freshness_seconds:
        return {**result, "state": "stale", "detail": "Evidence timestamp is stale or in the future."}
    if goal.kind in _BUSINESS:
        good, eligible = samples["good"], samples["eligible"]
        result.update(good=good, eligible=eligible)
        if eligible <= 0:
            return {**result, "state": "no_data", "detail": "No eligible accepted work in this mature cohort."}
        if (
            good < 0
            or good > eligible
            or abs(samples["window_end"] - (now.timestamp() - goal.deadline_seconds)) > 30
            or abs(samples["window_end"] - samples["window_start"] - goal.window_seconds) > 1
        ):
            return {
                **result,
                "state": "insufficient",
                "detail": "Invalid counts or incomplete/misaligned cohort window.",
            }
        value = 100 * good / eligible
        breached = value < goal.threshold
    else:
        value = samples["value"]
        if value < 0:
            return {**result, "state": "insufficient", "detail": "Evidence value cannot be negative."}
        breached = value >= goal.threshold
    return {
        **result,
        "state": "breaching" if breached else "observed",
        "value": value,
        "detail": (
            "Point-in-time preview; deploy exported rules for continuous evaluation. "
            "Retry counts are observed activity, not per-task retry identity or complete-window proof."
        )
        if goal.kind == "retry_activity"
        else "Point-in-time preview; deploy exported rules for continuous evaluation.",
    }


def _export(goal: _Goal, runtime_service: str, public_url: str = "") -> dict[str, Any]:
    queries = _queries(goal, runtime_service)
    condition, valid = _expression(goal, queries)
    labels = {
        "relayna_goal_id": goal.id,
        "relayna_goal_revision": goal.revision,
        "service_id": goal.service_id,
        "environment": goal.environment,
        "owner": goal.owner,
    }
    # Strip all source labels so rule identity and no-evidence routing are stable.
    rule = {
        "alert": "RelaynaReliabilityGoal",
        "expr": f"sum({condition})",
        "for": f"{goal.hold_seconds}s",
        "labels": labels,
        "annotations": {
            "summary": goal.name,
            "studio_url": public_url
            + "/reliability?"
            + urlencode({"service_id": goal.service_id, "environment": goal.environment}),
            "studio_path": "/reliability?"
            + urlencode({"service_id": goal.service_id, "environment": goal.environment}),
        },
    }
    missing = {
        **rule,
        "alert": "RelaynaReliabilityEvidenceMissing",
        "expr": f"absent(sum({valid}))",
        "for": f"{goal.freshness_seconds}s",
    }
    return {
        "prometheus": {"groups": [{"name": "relayna-" + goal.id, "rules": [rule, missing]}]},
        "alertmanager_route": {
            "matchers": [
                f"relayna_goal_id={json.dumps(goal.id)}",
                f"environment={json.dumps(goal.environment)}",
                f"owner={json.dumps(goal.owner)}",
            ],
            "receiver": "relayna-studio",
            "group_by": ["relayna_goal_id", "alertname", "environment"],
            "group_wait": "30s",
            "group_interval": "5m",
            "repeat_interval": "4h",
        },
        "receiver": {
            "name": "relayna-studio",
            "webhook_configs": [
                {
                    "url": "https://YOUR-STUDIO/studio/ingest/reliability-alerts",
                    "send_resolved": True,
                    "http_config": {
                        "authorization": {
                            "type": "Bearer",
                            "credentials_file": "/run/secrets/relayna-reliability-webhook",
                        }
                    },
                }
            ],
        },
        "queries": queries,
        "notes": [
            "JSON is valid YAML. Merge snippets into existing configuration and reload externally; "
            "saving a goal does not install rules.",
            "Alertmanager owns grouping, silences, inhibition and routing. "
            "Bridge deduplicates each alert episode/status.",
            "SDK runtime series must be environment-isolated in this Prometheus datasource; "
            "business/queue/lease series require the documented external aggregate contract.",
        ],
    }


async def _deliver(runtime: StudioRuntime, store: _Store, alert: _Alert, public_url: str = "") -> dict[str, Any]:
    goal_id = alert.labels.get("relayna_goal_id", "")
    failure: str | None = None
    settings_store = runtime.failed_task_email_settings_store
    settings = await settings_store.get() if settings_store is not None else None
    async with store.required().transaction() as session:
        document = await session.scalar(
            select(operator_settings.c.value).where(operator_settings.c.setting_key == _key(goal_id)).with_for_update()
        )
        if not document:
            raise HTTPException(404, "Unknown reliability goal.")
        goal = _Goal.model_validate(document["goal"])
        # Keep registry scope and receipt ownership in this transaction. Opening
        # registry/settings sessions while holding every pool connection can deadlock.
        service = (
            (
                await session.execute(
                    select(services.c.environment, services.c.status, services.c.deleted_at)
                    .where(services.c.service_id == goal.service_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if service is None or service["deleted_at"] is not None:
            raise HTTPException(404, "Reliability goal service no longer exists.")
        if service["environment"] != goal.environment or service["status"] == "disabled":
            raise HTTPException(409, "Reliability goal service scope changed or is disabled.")
        expected = {
            "relayna_goal_revision": goal.revision,
            "service_id": goal.service_id,
            "environment": goal.environment,
            "owner": goal.owner,
        }
        if any(alert.labels.get(key) != value for key, value in expected.items()) or alert.labels.get(
            "alertname"
        ) not in {"RelaynaReliabilityGoal", "RelaynaReliabilityEvidenceMissing"}:
            raise HTTPException(409, "Alert labels do not match the current goal and routing scope.")
        if _timestamp(alert.startsAt) > _now() + timedelta(seconds=30):
            raise HTTPException(422, "Alert start time is in the future.")
        delivery = dict(document.get("delivery", {}))
        episodes = dict(delivery.get("episodes", {}))
        episode_key = hashlib.sha256((alert.labels["alertname"] + alert.startsAt).encode()).hexdigest()
        episode = dict(episodes.get(episode_key, {}))
        delivery["received"] = delivery.get("received", 0) + 1
        delivery["last_received_at"] = _iso(_now())
        if episode.get(alert.status) or episode.get("resolved"):
            delivery["duplicates"] = delivery.get("duplicates", 0) + 1
            state = "duplicate"
        else:
            worker = runtime.failed_task_email_worker
            if settings is None or not settings.enabled or worker is None:
                state = "disabled"
                failure = "Shared email delivery is disabled or not configured."
            else:
                # Reuse transport, credentials, timeout and egress policy from failed-task notifications.
                email: FailedTaskEmailClient = worker.notification_service.email_client
                try:
                    await email.send(
                        receivers=tuple(goal.receivers),
                        title=f"[Relayna] {alert.status}: {goal.name}",
                        body=(
                            f"Goal: {goal.id}\nOwner: {goal.owner}\nService: {goal.service_id}\n"
                            f"Environment: {goal.environment}\nAlert: {alert.labels['alertname']}\n"
                            f"Started: {alert.startsAt}\nInvestigation path: "
                            f"{public_url}{_investigation(goal, _timestamp(alert.startsAt))}"
                        ),
                    )
                    state = "sent"
                    episode[alert.status] = _iso(_now())
                    episodes[episode_key] = episode
                    # Bound receipt storage. Alertmanager owns long-lived alert history.
                    delivery["episodes"] = dict(list(episodes.items())[-100:])
                    delivery["sent"] = delivery.get("sent", 0) + 1
                except Exception:
                    state = "failed"
                    failure = "Email transport failed; Alertmanager should retry."
            delivery.update(
                last_status=alert.status,
                alert_started_at=alert.startsAt,
                investigation_url=_investigation(goal, _timestamp(alert.startsAt)),
            )
        delivery.update(state=state, detail=failure)
        await session.execute(
            update(operator_settings)
            .where(operator_settings.c.setting_key == _key(goal_id))
            .values(value={"goal": goal.model_dump(), "delivery": delivery}, updated_at=_now())
        )
        await _append_audit(
            session,
            action="reliability.alert.delivery",
            target_type="reliability_goal",
            target_id=goal.id,
            details={"state": state, "status": alert.status},
        )
    if failure:
        raise HTTPException(503 if state == "disabled" else 502, failure)
    return {"goal_id": goal_id, "state": state}


def _create_reliability_router(
    runtime: StudioRuntime, webhook_token: str | None, public_url: str | None = None
) -> APIRouter:
    public_origin = (public_url or "").rstrip("/")
    if public_origin:
        parts = urlsplit(public_origin)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.path
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Reliability public URL must be an HTTP(S) origin without credentials or a path.")
    router = APIRouter()
    store = _Store(runtime.database)

    @router.get("/studio/reliability/goals")
    async def list_goals(service_id: str | None = None, environment: str | None = None):
        items = [
            item
            for item in await store.list_documents()
            if (not service_id or item["goal"]["service_id"] == service_id)
            and (not environment or item["goal"]["environment"] == environment)
        ]
        return {
            "items": items,
            "configured": runtime.database is not None,
            "webhook_configured": bool(webhook_token),
            "email_configured": runtime.failed_task_email_configured,
        }

    @router.post("/studio/reliability/goals", dependencies=[Depends(_admin)], status_code=201)
    async def create_goal(payload: _GoalInput):
        await _service(runtime, payload)
        goal = _Goal(**payload.model_dump(), id=uuid4().hex, revision=secrets.token_hex(8), created_at=_iso(_now()))
        await store.save(goal)
        return goal

    @router.delete("/studio/reliability/goals/{goal_id}", dependencies=[Depends(_admin)])
    async def delete_goal(goal_id: str):
        await store.remove(goal_id)
        return {"deleted": True}

    @router.get("/studio/reliability/goals/{goal_id}/preview")
    async def preview_goal(goal_id: str):
        return await _preview(runtime, _Goal.model_validate((await store.get(goal_id))["goal"]))

    @router.get("/studio/reliability/goals/{goal_id}/export")
    async def export_goal(goal_id: str):
        goal = _Goal.model_validate((await store.get(goal_id))["goal"])
        service = await _service(runtime, goal)
        config = service.metrics_config
        if goal.kind in {"retry_activity", "missing_worker"} and not goal.runtime_scope_confirmed:
            raise HTTPException(409, "Confirm environment isolation for SDK runtime metrics before export.")
        if config is None:
            raise HTTPException(409, "Configure service Prometheus before exporting rules.")
        return _export(
            goal,
            config.runtime_service_label_value or config.service_selector_labels.get("service") or service.service_id,
            public_origin,
        )

    @router.post("/studio/ingest/reliability-alerts")
    async def receive_alerts(request: Request):
        if not webhook_token:
            raise HTTPException(503, "Reliability webhook is not configured.")
        if not secrets.compare_digest(
            request.headers.get("authorization", "").encode(), ("Bearer " + webhook_token).encode()
        ):
            raise HTTPException(401, "Invalid reliability webhook credential.")
        # Authenticate before reading callback content. Do not accept browser session credentials.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 262144:
                raise HTTPException(413, "Reliability callback is too large.")
        from pydantic import ValidationError

        try:
            payload = _Webhook.model_validate_json(raw)
        except ValidationError as exc:
            raise HTTPException(422, "Invalid Alertmanager callback.") from exc
        if payload.truncatedAlerts:
            raise HTTPException(
                422, "Configure Alertmanager max_alerts=0; truncated deliveries cannot be acknowledged."
            )
        return {"items": [await _deliver(runtime, store, alert, public_origin) for alert in payload.alerts]}

    return router
