from __future__ import annotations

import pytest

from relayna.observability import SSEKeepaliveSent, emit_observation


@pytest.mark.asyncio
async def test_emit_observation_is_noop_when_sink_is_none() -> None:
    await emit_observation(None, SSEKeepaliveSent(task_id="task-123"))


@pytest.mark.asyncio
async def test_emit_observation_suppresses_sink_failures() -> None:
    async def sink(event: object) -> None:
        raise RuntimeError("sink failed")

    await emit_observation(sink, SSEKeepaliveSent(task_id="task-123"))
