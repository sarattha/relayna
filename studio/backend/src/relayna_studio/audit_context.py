from __future__ import annotations

from contextvars import ContextVar, Token

_actor_user_id: ContextVar[str | None] = ContextVar("studio_actor_user_id", default=None)


def current_actor_user_id() -> str | None:
    return _actor_user_id.get()


def set_actor_user_id(user_id: str | None) -> Token[str | None]:
    return _actor_user_id.set(user_id)


def reset_actor_user_id(token: Token[str | None]) -> None:
    _actor_user_id.reset(token)
