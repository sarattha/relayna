from __future__ import annotations

import uvicorn

from .config import StudioBackendSettings


def main() -> None:
    settings = StudioBackendSettings.from_env()
    uvicorn.run("relayna_studio.asgi:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
