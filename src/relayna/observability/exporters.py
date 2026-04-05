from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any, cast


def event_to_dict(event: object) -> dict[str, Any]:
    if not isinstance(event, type) and is_dataclass(event):
        return cast(dict[str, Any], asdict(event))
    return dict(vars(event))


def make_logging_sink(logger: logging.Logger) -> Any:
    async def _sink(event: object) -> None:
        logger.info("relayna_observation", extra={"event": event_to_dict(event)})

    return _sink


__all__ = ["event_to_dict", "make_logging_sink"]
