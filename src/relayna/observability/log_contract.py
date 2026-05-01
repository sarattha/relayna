from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

RELAYNA_STUDIO_REQUIRED_LOG_FIELDS = frozenset({"service", "app", "event", "level", "timestamp"})
RELAYNA_STUDIO_TASK_LOG_FIELDS = frozenset({"task_id", "correlation_id", "stage", "attempt"})
RELAYNA_STUDIO_OPTIONAL_LOG_FIELDS = frozenset(
    {
        "env",
        "cluster",
        "namespace",
        "workflow_id",
        "worker_id",
        "runtime",
        "request_id",
        "message",
        "pod",
        "container",
    }
)
RELAYNA_STUDIO_LOKI_LABEL_ALLOWLIST = frozenset(
    {"cluster", "namespace", "service", "app", "container", "level", "stage"}
)
RELAYNA_STUDIO_HIGH_CARDINALITY_BODY_FIELDS = frozenset(
    {"task_id", "correlation_id", "request_id", "pod", "worker_id"}
)

_WARNING_EVENTS = (
    "rejected",
    "retry",
    "dead_letter",
    "dlq",
    "malformed",
)
_ERROR_EVENTS = (
    "failed",
    "error",
)


class StructlogLikeLogger(Protocol):
    def bind(self, **kwargs: Any) -> StructlogLikeLogger: ...

    def info(self, event: str, **kwargs: Any) -> Any: ...

    def warning(self, event: str, **kwargs: Any) -> Any: ...

    def error(self, event: str, **kwargs: Any) -> Any: ...


def bind_studio_log_context(
    logger: StructlogLikeLogger,
    *,
    service: str,
    app: str,
    env: str | None = None,
    runtime: str | None = None,
    **fields: Any,
) -> StructlogLikeLogger:
    """Bind common Relayna Studio log fields to a structlog-style logger."""

    context = _drop_none({"service": service, "app": app, "env": env, "runtime": runtime, **fields})
    return logger.bind(**context)


def observation_to_studio_log_fields(
    event: object,
    *,
    service: str,
    app: str,
    env: str | None = None,
    runtime: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Convert a Relayna observation object to the Studio structured log shape."""

    event_fields = _event_to_dict(event)
    event_name = _event_name(event)
    timestamp = event_fields.pop("timestamp", None)
    if isinstance(timestamp, datetime):
        timestamp_value = timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    elif timestamp is None:
        timestamp_value = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    else:
        timestamp_value = str(timestamp)

    retry_attempt = event_fields.pop("retry_attempt", None)
    if retry_attempt is not None and "attempt" not in event_fields:
        event_fields["attempt"] = retry_attempt

    payload = {
        "service": service,
        "app": app,
        "env": env,
        "runtime": runtime,
        "event": event_name,
        "level": _level_for_event(event_name),
        "timestamp": timestamp_value,
        **event_fields,
        **fields,
    }
    return _drop_none(payload)


def validate_studio_log_fields(fields: Mapping[str, Any], *, require_task_context: bool = False) -> set[str]:
    """Return missing required fields for the Studio log contract."""

    required = set(RELAYNA_STUDIO_REQUIRED_LOG_FIELDS)
    if require_task_context:
        required.update(RELAYNA_STUDIO_TASK_LOG_FIELDS)
    return {key for key in required if fields.get(key) is None or fields.get(key) == ""}


def make_structlog_observation_sink(
    logger: StructlogLikeLogger,
    *,
    service: str,
    app: str,
    env: str | None = None,
    runtime: str | None = None,
    **fields: Any,
) -> Any:
    """Create an async observation sink for a structlog-style bound logger."""

    async def _sink(event: object) -> None:
        payload = observation_to_studio_log_fields(
            event,
            service=service,
            app=app,
            env=env,
            runtime=runtime,
            **fields,
        )
        event_name = str(payload.pop("event"))
        level = str(payload.get("level") or "info")
        log_method = getattr(logger, level, logger.info)
        result = log_method(event_name, **payload)
        if inspect.isawaitable(result):
            await result

    return _sink


def _event_to_dict(event: object) -> dict[str, Any]:
    if not isinstance(event, type) and is_dataclass(event):
        return asdict(event)
    if isinstance(event, Mapping):
        return dict(event)
    return dict(vars(event))


def _event_name(event: object) -> str:
    if isinstance(event, Mapping):
        raw = event.get("event") or event.get("event_type") or "relayna_event"
        return _snake_case(str(raw))
    return _snake_case(type(event).__name__)


def _level_for_event(event_name: str) -> str:
    if any(marker in event_name for marker in _ERROR_EVENTS):
        return "error"
    if any(marker in event_name for marker in _WARNING_EVENTS):
        return "warning"
    return "info"


def _snake_case(value: str) -> str:
    with_separators = re.sub(r"[^0-9A-Za-z]+", "_", value).strip("_")
    first_pass = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", with_separators)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first_pass).lower()


def _drop_none(fields: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


__all__ = [
    "RELAYNA_STUDIO_HIGH_CARDINALITY_BODY_FIELDS",
    "RELAYNA_STUDIO_LOKI_LABEL_ALLOWLIST",
    "RELAYNA_STUDIO_OPTIONAL_LOG_FIELDS",
    "RELAYNA_STUDIO_REQUIRED_LOG_FIELDS",
    "RELAYNA_STUDIO_TASK_LOG_FIELDS",
    "bind_studio_log_context",
    "make_structlog_observation_sink",
    "observation_to_studio_log_fields",
    "validate_studio_log_fields",
]
