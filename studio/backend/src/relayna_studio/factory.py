from __future__ import annotations

from fastapi import FastAPI

from .app import create_studio_app
from .config import StudioBackendSettings


def create_app(*, settings: StudioBackendSettings | None = None) -> FastAPI:
    resolved = settings or StudioBackendSettings.from_env()
    return create_studio_app(**resolved.to_app_kwargs())


__all__ = ["create_app"]
