from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from ..contracts import TaskEnvelope

if TYPE_CHECKING:
    from .worker import SpooltailTaskContext

_CELL_REQUEST_LIMIT = 256 * 1024
_CELL_CLEANUP_SECONDS = 35
_CELL_STARTUP_SECONDS = 5


class _CellInferenceEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=256)
    timestamp: AwareDatetime
    decision: Literal["allow", "deny", "result", "error"]
    status: int | None = Field(default=None, ge=100, le=599)
    reason: str | None = Field(default=None, max_length=256, pattern=r"^[a-z][a-z0-9_]*$")


class _CellInferenceSummary(BaseModel):
    events: list[_CellInferenceEvent] = Field(max_length=64)
    truncated: bool


@dataclass(frozen=True, slots=True)
class SpooltailLocalProfile:
    """Run a trusted asynchronous task handler inside the SDK worker."""

    handler: Callable[[TaskEnvelope, SpooltailTaskContext], Awaitable[None]]

    async def __call__(self, task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        await self.handler(task, context)


@dataclass(frozen=True, slots=True)
class SpooltailCellProfile:
    """Run a task through the deployment's Cell launcher and fixed OCI image."""

    launcher: str
    image: str
    socket_path: str = "/run/relayna-cell/api.sock"
    timeout_seconds: int = 60
    inference: bool = False

    def __post_init__(self) -> None:
        if not Path(self.launcher).is_absolute() or not Path(self.socket_path).is_absolute() or not self.image:
            raise ValueError("Cell requires absolute launcher and socket paths and a fixed image")
        if not 1 <= self.timeout_seconds <= 300:
            raise ValueError("Cell requires a timeout of 1–300 seconds")

    async def __call__(self, task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        await context.publish_status(status="processing")
        request = json.dumps(
            {"task_id": task.task_id, "attempt_id": context.attempt_id, "payload": task.payload},
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        if len(request) > _CELL_REQUEST_LIMIT:
            await context.publish_status(status="failed", message="Task exceeds the Cell input limit")
            return

        process = await asyncio.create_subprocess_exec(
            self.launcher,
            "--image",
            self.image,
            "--socket",
            self.socket_path,
            "--timeout",
            str(self.timeout_seconds),
            *(("--inference",) if self.inference else ()),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={},
        )
        exchange = asyncio.create_task(process.communicate(request))
        try:
            output, _ = await asyncio.wait_for(
                asyncio.shield(exchange), timeout=self.timeout_seconds + _CELL_STARTUP_SECONDS + _CELL_CLEANUP_SECONDS
            )
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            await exchange
            raise
        except asyncio.CancelledError:
            try:
                with suppress(ProcessLookupError):
                    process.terminate()
                output, _ = await asyncio.wait_for(asyncio.shield(exchange), timeout=_CELL_CLEANUP_SECONDS)
                _cell_response(output, task.task_id, context)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await exchange
            raise

        response = _cell_response(output, task.task_id, context)
        result = response.get("result")
        if (
            process.returncode == 0
            and response.get("status") == "completed"
            and context._execution_meta
            and "cell_id" in context._execution_meta["cell"]
            and isinstance(result, dict)
        ):
            await context.publish_status(status="completed", result=result, meta=context._execution_meta)
        else:
            await context.publish_status(
                status="failed", message="Cell task did not complete", meta=context._execution_meta
            )


def _cell_response(output: bytes, task_id: str, context: SpooltailTaskContext) -> dict[str, Any]:
    try:
        response = json.loads(output)
    except ValueError:
        return {}
    if (
        not isinstance(response, dict)
        or response.get("task_id") != task_id
        or response.get("attempt_id") != context.attempt_id
    ):
        return {}
    cell: dict[str, Any] = {
        key: value for key in ("cell_id", "execution_id") if isinstance(value := response.get(key), str) and value
    }
    if cell:
        if response.get("inference") is not None:
            try:
                summary = _CellInferenceSummary.model_validate(response["inference"])
            except ValidationError:
                pass
            else:
                cell["inference"] = summary.model_dump(mode="json", exclude_none=True)
        context._execution_meta = {"cell": cell}
    return response
