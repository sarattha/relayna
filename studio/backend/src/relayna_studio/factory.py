from __future__ import annotations

from fastapi import FastAPI

from ._operator_auth import _install_operator_auth
from .app import create_studio_app
from .config import StudioBackendSettings


def create_app(*, settings: StudioBackendSettings | None = None) -> FastAPI:
    resolved = settings or StudioBackendSettings.from_env()
    app = create_studio_app(**resolved.to_app_kwargs())
    if resolved.auth_mode == "operator":
        _install_operator_auth(app, resolved)
    return app


__all__ = ["create_app"]
