from __future__ import annotations

import importlib
import sys

import pytest
from relayna_studio.config import StudioBackendSettings
from relayna_studio.factory import create_app


def test_settings_require_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RELAYNA_STUDIO_REDIS_URL", raising=False)

    with pytest.raises(RuntimeError, match="RELAYNA_STUDIO_REDIS_URL"):
        StudioBackendSettings.from_env()


def test_settings_parse_optional_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYNA_STUDIO_REDIS_URL", "redis://studio-test/0")
    monkeypatch.setenv("RELAYNA_STUDIO_EVENT_STORE_TTL_SECONDS", "none")
    monkeypatch.setenv("RELAYNA_STUDIO_PULL_SYNC_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("RELAYNA_STUDIO_PUSH_INGEST_ENABLED", "yes")
    monkeypatch.setenv("RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS", "studio.internal, api.internal ")

    settings = StudioBackendSettings.from_env()

    assert settings.redis_url == "redis://studio-test/0"
    assert settings.event_store_ttl_seconds is None
    assert settings.pull_sync_interval_seconds == 15.0
    assert settings.push_ingest_enabled is True
    assert settings.capability_refresh_allowed_hosts == ("studio.internal", "api.internal")


def test_create_app_uses_env_backed_settings() -> None:
    settings = StudioBackendSettings(redis_url="redis://studio-test/0", pull_sync_interval_seconds=None)

    app = create_app(settings=settings)

    assert app.title == "Relayna Studio Backend"
    assert any(route.path == "/studio/services" for route in app.routes)


def test_asgi_module_exposes_module_level_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYNA_STUDIO_REDIS_URL", "redis://studio-test/0")
    sys.modules.pop("relayna_studio.asgi", None)

    module = importlib.import_module("relayna_studio.asgi")

    assert module.app.title == "Relayna Studio Backend"
