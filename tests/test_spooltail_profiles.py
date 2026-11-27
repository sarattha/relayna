from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from relayna.contracts import TaskEnvelope
from relayna.spooltail import SpooltailCellProfile
from relayna.spooltail import profiles as profiles_module


@pytest.mark.parametrize(
    "options", [{"launcher": "relative"}, {"image": ""}, {"socket_path": "relative"}, {"timeout_seconds": 301}]
)
def test_cell_profile_validates_deployment_settings(options):
    with pytest.raises(ValueError, match="Cell requires"):
        SpooltailCellProfile(**({"launcher": "/cell-task", "image": "fixed"} | options))


@pytest.mark.parametrize(
    ("response", "returncode", "completed"),
    [
        ({"status": "completed", "cell_id": "cell", "result": {"number": 2}}, 0, True),
        ({"status": "failed", "cell_id": "cell"}, 1, False),
        ({"status": "completed", "result": {}}, 0, False),
        (
            {"task_id": "wrong", "attempt_id": "attempt", "status": "completed", "cell_id": "cell", "result": {}},
            0,
            False,
        ),
        (None, 1, False),
    ],
)
@pytest.mark.asyncio
async def test_cell_profile_uses_fixed_argv_and_preserves_validated_evidence(
    monkeypatch, response, returncode, completed
):
    summary = {
        "events": [{"event_id": "evt", "timestamp": "2026-11-27T00:00:00Z", "decision": "result", "status": 200}],
        "truncated": False,
    }
    wire = (
        b"invalid"
        if response is None
        else json.dumps({"task_id": "t", "attempt_id": "attempt", "inference": summary} | response).encode()
    )
    process = Mock(returncode=returncode, communicate=AsyncMock(return_value=(wire, b"")))
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    profile = SpooltailCellProfile("/cell-task", "fixed", inference=True)
    context = Mock(attempt_id="attempt", _execution_meta={}, publish_status=AsyncMock())
    await profile(TaskEnvelope(task_id="t", payload={"number": 2}), context)
    assert launch.await_args.args == (
        "/cell-task",
        "--image",
        "fixed",
        "--socket",
        "/run/relayna-cell/api.sock",
        "--timeout",
        "60",
        "--inference",
    )
    assert launch.await_args.kwargs["env"] == {}
    assert json.loads(process.communicate.await_args.args[0]) == {
        "task_id": "t",
        "attempt_id": "attempt",
        "payload": {"number": 2},
    }
    terminal = context.publish_status.await_args.kwargs
    assert terminal["status"] == ("completed" if completed else "failed")
    if completed:
        assert terminal["meta"]["cell"] == {"cell_id": "cell", "inference": summary}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count,code,valid", [(64, "model_not_allowed", True), (65, "", False), (1, "request content", False)]
)
async def test_cell_profile_keeps_only_bounded_machine_readable_audit(monkeypatch, count, code, valid):
    events = [
        {"event_id": str(i), "timestamp": "2026-11-27T00:00:00Z", "decision": "deny", "reason": code or None}
        for i in range(count)
    ]
    audit = {"events": events, "truncated": True}
    response = {
        "task_id": "t",
        "attempt_id": "a",
        "cell_id": "c",
        "status": "completed",
        "result": {},
        "inference": audit,
    }
    process = Mock(returncode=0, communicate=AsyncMock(return_value=(json.dumps(response).encode(), b"")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    context = Mock(attempt_id="a", _execution_meta={}, publish_status=AsyncMock())
    await SpooltailCellProfile("/cell-task", "fixed", inference=True)(TaskEnvelope(task_id="t"), context)
    assert context.publish_status.await_args.kwargs["status"] == "completed"
    assert context._execution_meta["cell"] == ({"cell_id": "c", "inference": audit} if valid else {"cell_id": "c"})


@pytest.mark.asyncio
async def test_cell_profile_cancellation_terminates_then_collects_cleanup_evidence(monkeypatch):
    running, stopped = asyncio.Event(), asyncio.Event()

    async def exchange(_: bytes):
        running.set()
        await stopped.wait()
        return b'{"task_id":"t","attempt_id":"a","cell_id":"finished-cleanup"}', b""

    process = Mock(communicate=exchange, terminate=stopped.set, kill=Mock())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    context = Mock(attempt_id="a", _execution_meta={}, publish_status=AsyncMock())
    work = asyncio.create_task(SpooltailCellProfile("/cell-task", "fixed")(TaskEnvelope(task_id="t"), context))
    await running.wait()
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert context._execution_meta == {"cell": {"cell_id": "finished-cleanup"}}
    process.kill.assert_not_called()


@pytest.mark.asyncio
async def test_cell_profile_kills_unresponsive_launcher_after_cancellation_grace(monkeypatch):
    running, stopped = asyncio.Event(), asyncio.Event()

    async def exchange(_: bytes):
        running.set()
        await stopped.wait()
        return b"", b""

    process = Mock(communicate=exchange, terminate=Mock(), kill=stopped.set)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(profiles_module, "_CELL_CLEANUP_SECONDS", 0.001)
    context = Mock(attempt_id="a", _execution_meta={}, publish_status=AsyncMock())
    work = asyncio.create_task(SpooltailCellProfile("/cell-task", "fixed")(TaskEnvelope(task_id="t"), context))
    await running.wait()
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    process.terminate.assert_called_once()
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_cell_profile_bounds_payload_and_subprocess_deadline(monkeypatch):
    stopped = asyncio.Event()

    async def exchange(_: bytes):
        await stopped.wait()
        return b"", b""

    process = Mock(communicate=exchange, terminate=Mock(), kill=stopped.set)
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(profiles_module, "_CELL_REQUEST_LIMIT", 256)
    monkeypatch.setattr(profiles_module, "_CELL_STARTUP_SECONDS", -1)
    monkeypatch.setattr(profiles_module, "_CELL_CLEANUP_SECONDS", 0.001)
    profile = SpooltailCellProfile("/cell-task", "fixed", timeout_seconds=1)
    context = Mock(attempt_id="a", _execution_meta={}, publish_status=AsyncMock())
    await profile(TaskEnvelope(task_id="t", payload={"value": "x" * 500}), context)
    assert context.publish_status.await_args.kwargs["status"] == "failed"
    launch.assert_not_awaited()
    with pytest.raises(TimeoutError):
        await profile(TaskEnvelope(task_id="t"), context)
    assert stopped.is_set()


@pytest.mark.parametrize(
    ("status", "manifest", "accepted"),
    [
        ("completed", "attempt-" + "a" * 32 + "/manifest.json", True),
        ("failed", "attempt-" + "a" * 32 + "/manifest.json", False),
        ("completed", "../../private/manifest.json", False),
    ],
)
def test_cell_artifact_evidence_only_records_valid_completed_manifests(status, manifest, accepted):
    context = SimpleNamespace(attempt_id="a", _execution_meta={})
    response = {
        "task_id": "t",
        "attempt_id": "a",
        "cell_id": "c",
        "profile": "reports",
        "status": status,
        "artifact_manifest": manifest,
    }
    profiles_module._cell_response(json.dumps(response).encode(), "t", context)
    evidence = context._execution_meta["cell"]
    assert evidence["profile"] == "reports"
    assert evidence.get("artifact_manifest") == (manifest if accepted else None)
