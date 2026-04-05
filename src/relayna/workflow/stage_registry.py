from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class StageMetadata:
    name: str
    description: str | None = None
    owner: str | None = None
    tags: tuple[str, ...] = ()
    expected_actions: tuple[str, ...] = ()
    concurrency_hint: int | None = None


class StageRegistry:
    def __init__(self, items: tuple[StageMetadata, ...] = ()) -> None:
        self._items = {item.name: item for item in items}

    def register(self, item: StageMetadata) -> None:
        self._items[item.name] = item

    def get(self, name: str) -> StageMetadata | None:
        return self._items.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._items)

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "description": item.description,
                "owner": item.owner,
                "tags": list(item.tags),
                "expected_actions": list(item.expected_actions),
                "concurrency_hint": item.concurrency_hint,
            }
            for name, item in self._items.items()
        }


__all__ = ["StageMetadata", "StageRegistry"]
