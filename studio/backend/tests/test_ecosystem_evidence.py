from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from relayna_studio._ecosystem_evidence import _GatewayUsage, _SpooltailEvidence
from relayna_studio.events import StudioEventStore
from relayna_studio.registry import StudioOutboundUrlPolicy


def test_gateway_usage_can_be_disabled_with_an_empty_token(monkeypatch):
    monkeypatch.setenv("RELAYNA_STUDIO_GATEWAY_URL", "https://gateway.example.test")
    monkeypatch.setenv("RELAYNA_STUDIO_GATEWAY_USAGE_TOKEN", "")
    assert _GatewayUsage.from_environment() is None


@pytest.mark.parametrize("mapping", ["not-json", "[]", "{}", '{"sdk":5}', '{"sdk":""}'])
def test_gateway_usage_requires_an_explicit_valid_service_map(monkeypatch, mapping):
    monkeypatch.setenv("RELAYNA_STUDIO_GATEWAY_URL", "https://gateway.example.test")
    monkeypatch.setenv("RELAYNA_STUDIO_GATEWAY_USAGE_TOKEN", "local-test")
    monkeypatch.setenv("RELAYNA_STUDIO_GATEWAY_SERVICE_MAP", mapping)
    with pytest.raises(ValueError):
        _GatewayUsage.from_environment()


@pytest.mark.asyncio
async def test_unavailable_or_invalid_observations_do_not_inflate_totals():
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"broker": "spooltail", "payload_reads": -1}))
    async with httpx.AsyncClient(transport=transport) as client:
        policy = StudioOutboundUrlPolicy(allowed_hosts=["sdk.example.test"])
        projection = _SpooltailEvidence(AsyncMock(spec=StudioEventStore), client, policy)
        assert await projection._broker("https://sdk.example.test") is None
        assert await projection._usage("sdk", "task-1") is None
    invalid_row = {"event_type": "gateway.usage", "payload": {"total_tokens": 100}}
    usage = projection._summarize_usage([invalid_row], source_available=False)
    assert usage["observations"] == usage["total_tokens"] == 0
