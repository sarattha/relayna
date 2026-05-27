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
    assert settings.failed_task_email_enabled is False
    assert settings.failed_task_email_receivers == ()


def test_settings_parse_failed_task_email_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYNA_STUDIO_REDIS_URL", "redis://studio-test/0")
    monkeypatch.setenv("RELAYNA_STUDIO_FAILED_TASK_EMAIL_ENABLED", "true")
    monkeypatch.setenv("RELAYNA_STUDIO_FAILED_TASK_EMAIL_SERVICE_URL", "https://email.example.test/send")
    monkeypatch.setenv("RELAYNA_STUDIO_FAILED_TASK_EMAIL_RECEIVERS", "ops@example.com, oncall@example.com")
    monkeypatch.setenv("RELAYNA_STUDIO_FAILED_TASK_EMAIL_INTERVAL_SECONDS", "12.5")
    monkeypatch.setenv("RELAYNA_STUDIO_FAILED_TASK_EMAIL_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("RELAYNA_STUDIO_FAILED_TASK_EMAIL_DEDUPE_TTL_SECONDS", "3600")
    monkeypatch.setenv("RELAYNA_STUDIO_FAILED_TASK_EMAIL_TITLE_PREFIX", "[Test] Failed")

    settings = StudioBackendSettings.from_env()

    assert settings.failed_task_email_enabled is True
    assert settings.failed_task_email_service_url == "https://email.example.test/send"
    assert settings.failed_task_email_receivers == ("ops@example.com", "oncall@example.com")
    assert settings.failed_task_email_interval_seconds == 12.5
    assert settings.failed_task_email_timeout_seconds == 2.5
    assert settings.failed_task_email_dedupe_ttl_seconds == 3600
    assert settings.failed_task_email_title_prefix == "[Test] Failed"


def test_settings_require_failed_task_email_configuration_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYNA_STUDIO_REDIS_URL", "redis://studio-test/0")
    monkeypatch.setenv("RELAYNA_STUDIO_FAILED_TASK_EMAIL_ENABLED", "true")

    with pytest.raises(RuntimeError, match="FAILED_TASK_EMAIL_SERVICE_URL"):
        StudioBackendSettings.from_env()

    monkeypatch.setenv("RELAYNA_STUDIO_FAILED_TASK_EMAIL_SERVICE_URL", "https://email.example.test/send")

    with pytest.raises(RuntimeError, match="FAILED_TASK_EMAIL_RECEIVERS"):
        StudioBackendSettings.from_env()


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
