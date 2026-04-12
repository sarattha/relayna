FROM python:3.13-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.7.22 /uv /uvx /bin/

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY studio/backend /app/studio/backend

RUN uv build --wheel --out-dir /dist /app \
    && uv build --wheel --out-dir /dist /app/studio/backend

FROM python:3.13-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.7.22 /uv /uvx /bin/

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/relayna
ENV RELAYNA_STUDIO_HOST=0.0.0.0
ENV RELAYNA_STUDIO_PORT=8000
ENV UV_LINK_MODE=copy
ENV UV_SYSTEM_PYTHON=1

COPY --from=build /dist /dist

RUN uv pip install --system /dist/relayna-*.whl /dist/relayna_studio-*.whl \
    && rm -rf /usr/local/bin/pip* \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
    && rm -rf /usr/local/lib/python3.13/ensurepip \
    && rm -rf /dist \
    && groupadd --system relayna \
    && useradd --system --gid relayna --create-home --home-dir /home/relayna relayna \
    && chown -R relayna:relayna /app /home/relayna

USER relayna

EXPOSE 8000

CMD ["sh", "-c", "uv run --no-sync uvicorn relayna_studio.asgi:app --host ${RELAYNA_STUDIO_HOST:-0.0.0.0} --port ${RELAYNA_STUDIO_PORT:-8000}"]
