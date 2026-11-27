from __future__ import annotations

import pytest
from relayna_studio._ecosystem_evidence import _GatewayUsage


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
