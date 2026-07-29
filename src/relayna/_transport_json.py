from __future__ import annotations

from typing import Any

from pydantic_core import from_json, to_json


def encode_transport_json(value: Any) -> bytes:
    """Encode a Relayna AMQP body with the production transport JSON codec."""

    return to_json(value)


def parse_transport_json(payload: bytes) -> Any:
    """Parse a strict UTF-8 Relayna AMQP body with the production transport JSON codec."""

    return from_json(payload)
