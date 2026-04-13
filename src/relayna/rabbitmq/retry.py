from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .client import RetryInfrastructure


def clear_retry_headers(headers: Mapping[str, Any]) -> dict[str, Any]:
    updated = dict(headers)
    for key in (
        "x-relayna-retry-attempt",
        "x-relayna-failure-reason",
        "x-relayna-exception-type",
    ):
        updated.pop(key, None)
    return updated


__all__ = ["RetryInfrastructure", "clear_retry_headers"]
