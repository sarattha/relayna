from __future__ import annotations

from .service import DLQService


async def replay_dlq_message(service: DLQService, dlq_id: str, *, force: bool = False):
    return await service.replay_message(dlq_id, force=force)


__all__ = ["replay_dlq_message"]
