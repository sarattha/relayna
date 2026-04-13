from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .resources import list_runtime_resources


class RelaynaMCPServer:
    """Minimal MCP-style dispatcher for Relayna runtime resources and tools."""

    def __init__(
        self,
        *,
        read_tools: dict[str, Callable[..., Any]] | None = None,
        ops_tools: dict[str, Callable[..., Any]] | None = None,
    ) -> None:
        self._read_tools = dict(read_tools or {})
        self._ops_tools = dict(ops_tools or {})

    def list_tools(self) -> dict[str, list[str]]:
        return {
            "read": sorted(self._read_tools),
            "ops": sorted(self._ops_tools),
        }

    async def call_tool(self, name: str, *args, **kwargs) -> Any:
        tool = self._read_tools.get(name) or self._ops_tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown MCP tool '{name}'")
        result = tool(*args, **kwargs)
        if hasattr(result, "__await__"):
            return await result
        return result

    def list_resources(self, **kwargs) -> list[dict[str, object]]:
        return list_runtime_resources(**kwargs)


__all__ = ["RelaynaMCPServer"]
