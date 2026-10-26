from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from relayna_studio._incident_groups import _dimensions, _group_id, _summarize
from relayna_studio._incidents import _create_incident_router, _IncidentService
from relayna_studio.federation import StudioFederationError


def _failure(fid: str, **values):
    return {
        "failure_id": fid,
        "task_id": f"task-{fid}",
        "queue_name": "payment-capture",
        "stage": "capture",
        "error_type": "TimeoutError",
        "error_message": f"Capture {fid} timed out after 120 seconds",
        "failed_at": "2026-10-25T10:42:00Z",
        **values,
    }


def _service(sid="payments", env="prod", status="healthy"):
    return SimpleNamespace(service_id=sid, environment=env, status=status)


def _handler(services, reader, budget=20):
    federation = SimpleNamespace(
        registry_service=SimpleNamespace(list_services=AsyncMock(return_value=services)),
        list_failed_tasks=AsyncMock(side_effect=reader),
        search_max_concurrency=2,
    )
    return _IncidentService(federation, AsyncMock(), page_budget=budget)


def test_conservative_versioned_signature_and_boundaries():
    first = _dimensions(
        "prod",
        "payments",
        _failure("100", error_message="Request 123 (a014bb9a-05e6-42ed-a3ca-e338423c8ae3) timed out"),
    )
    second = _dimensions(
        "prod",
        "payments",
        _failure("200", error_message="Request 456 (671d473e-6861-4a6e-985e-ef95c3d0da08)  timed out"),
    )
    assert first == second
    assert _group_id(first) == _group_id(second)
    for key in ("environment", "service_id", "queue_name", "stage", "error_type", "signature_version"):
        assert _group_id({**first, key: "other"}) != _group_id(first)
    assert _dimensions("prod", "payments", _failure("1", error_message=None)) != _dimensions(
        "prod", "payments", _failure("2", error_message=None)
    )
    assert (
        _dimensions("prod", "payments", _failure("3", error_message="Address 0xABC123"))["signature"] == "Address <id>"
    )


@pytest.mark.asyncio
async def test_scan_uses_every_page_dedupes_and_scopes_before_reads():
    async def read(**query):
        assert query["service_id"] == "payments"
        if query["cursor"] is None:
            return {"items": [_failure("1")], "next_cursor": "empty"}
        if query["cursor"] == "empty":
            return {"items": [], "next_cursor": "last"}
        return {"items": [_failure("1"), _failure("2")], "next_cursor": None}

    handler = _handler([_service(), _service("staging", "stage"), _service("off", status="disabled")], read)
    result = await handler.scan("prod")
    assert result["complete"] is True
    assert result["observed_failures"] == 2
    assert result["coverage"][0]["pages"] == 3
    assert handler.federation.list_failed_tasks.await_count == 3
    assert "body" not in result["evidence"][0]


@pytest.mark.asyncio
async def test_scan_preserves_partial_errors_looping_and_truncation():
    async def read(**query):
        sid = query["service_id"]
        if sid == "down":
            raise StudioFederationError(status_code=503, detail="unreachable", code="unavailable")
        return {"items": [_failure("1")], "next_cursor": "again"}

    result = await _handler([_service("down"), _service("loop")], read).scan()
    assert result["complete"] is False
    assert result["observed_failures"] == 1
    assert [item["status"] for item in result["coverage"]] == ["unavailable", "unavailable"]
    assert "did not advance" in result["coverage"][1]["detail"]
    limited = await _handler([_service("limited")], read, budget=1).scan()
    assert limited["coverage"][0]["status"] == "truncated"
    assert limited["complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [{"items": [{}]}, {"items": "bad"}, {"items": [], "errors": [{"detail": "partial"}]}])
async def test_malformed_feed_never_claims_completeness(page):
    result = await _handler([_service()], lambda **_: page).scan()
    assert result["complete"] is False
    assert result["coverage"][0]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_build_applies_manual_overrides_and_merge_redirects():
    handler = _handler([_service()], lambda **_: {"items": [_failure("1"), _failure("2"), _failure("3")]})
    natural_id = _group_id(_dimensions("prod", "payments", _failure("1")))
    states = {
        natural_id: {"incident_id": natural_id, "merged_into": "merged"},
        "merged": {"incident_id": "merged", "merged_into": None},
        "split": {"incident_id": "split", "merged_into": None},
    }
    for state in states.values():
        state["dimensions"] = _dimensions("prod", "payments", _failure("1"))
    handler.store.snapshot.return_value = (states, {("prod", "payments", "2"): "split"})
    result = await handler.build()
    assert len(result["groups"]["merged"]) == 2
    assert result["groups"]["split"][0]["failure_id"] == "2"


def test_summary_has_explicit_time_buckets_distinct_tasks_and_unknowns():
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    items = [
        _failure("1", task_id="same", failed_at=(now - timedelta(minutes=10)).isoformat()),
        _failure("2", task_id="same", failed_at=(now - timedelta(minutes=70)).isoformat()),
        _failure("3", task_id=None, failed_at="bad"),
        _failure("4", failed_at="2026-10-25T09:00:00"),
    ]
    summary = _summarize({}, items, now)
    assert summary["failure_count"] == 4
    assert summary["affected_tasks"] == 2
    assert summary["missing_task_ids"] == 1
    assert summary["trend"] == {"recent_hour": 1, "preceding_hour": 1, "unknown_timestamps": 2, "outside_two_hours": 0}
    assert len(summary["representative"]) == 3


def test_incident_routes_require_postgres_and_validate_input():
    app = FastAPI()
    app.include_router(_create_incident_router(SimpleNamespace(), None))
    with TestClient(app) as client:
        assert client.get("/studio/incidents").status_code == 503
        assert client.get("/studio/incidents?status=bogus").status_code == 422
        assert client.post("/studio/incidents/id", json={"revision": -1}).status_code == 422
        assert client.post("/studio/incidents/id", json={"revision": 0, "status": None}).status_code == 422
        assert client.post("/studio/incidents/id", json={"revision": 0, "actor": "spoofed"}).status_code == 422
