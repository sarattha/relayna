from __future__ import annotations

import json
from typing import Any

from pydantic_core import from_json, to_json

_RECURSION_LIMIT_ERROR = "recursion limit exceeded"


def encode_transport_json(value: Any) -> bytes:
    """Encode a Relayna AMQP body with the production transport JSON codec."""

    return to_json(value)


def parse_transport_json(payload: bytes) -> Any:
    """Parse a strict UTF-8 Relayna AMQP body with the production transport JSON codec."""

    try:
        return from_json(payload)
    except ValueError as exc:
        if _RECURSION_LIMIT_ERROR not in str(exc):
            raise

    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, RecursionError) as exc:
        raise ValueError(str(exc)) from exc
