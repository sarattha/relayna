from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class ConsumerMiddleware(Protocol):
    async def before_handle(self, payload: object, context: object) -> None: ...

    async def after_handle(self, payload: object, context: object) -> None: ...

    async def on_error(self, payload: object, context: object, exc: Exception) -> None: ...


class MiddlewareChain:
    def __init__(self, middlewares: Sequence[ConsumerMiddleware] = ()) -> None:
        self.middlewares = tuple(middlewares)

    async def before_handle(self, payload: object, context: object) -> None:
        for middleware in self.middlewares:
            await middleware.before_handle(payload, context)

    async def after_handle(self, payload: object, context: object) -> None:
        for middleware in reversed(self.middlewares):
            await middleware.after_handle(payload, context)

    async def on_error(self, payload: object, context: object, exc: Exception) -> None:
        for middleware in reversed(self.middlewares):
            await middleware.on_error(payload, context, exc)


__all__ = ["ConsumerMiddleware", "MiddlewareChain"]
