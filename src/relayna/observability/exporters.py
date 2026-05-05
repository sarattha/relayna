from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any

from .log_contract import make_structlog_observation_sink
from .tracing import active_trace_fields


def event_to_dict(event: object) -> dict[str, Any]:
    if not isinstance(event, type) and is_dataclass(event):
        payload = asdict(event)
    else:
        payload = dict(vars(event))
    return {**payload, **active_trace_fields()}


def make_logging_sink(logger: logging.Logger) -> Any:
    async def _sink(event: object) -> None:
        logger.info("relayna_observation", extra={"event": event_to_dict(event)})

    return _sink


__all__ = ["event_to_dict", "make_logging_sink", "make_structlog_observation_sink"]
