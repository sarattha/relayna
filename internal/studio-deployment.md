# Studio Deployment

This file documents the internal deployment model for Relayna Studio after
feature 11. It is internal-only and must not be published under `docs/`.

## Boundary

- `relayna` remains the SDK/runtime package consumed by downstream services.
- Studio backend is packaged separately in `studio/backend/` as
  `relayna-studio` with import root `relayna_studio`.
- Studio frontend remains the SPA in `apps/studio/`.
- Image publication, registry promotion, Kubernetes manifests, and Helm charts
  are out of scope for this feature.

## Source Build Targets

Build both images from the repo root:

```bash
docker build -f studio/backend/Dockerfile -t relayna-studio-backend .
docker build -f apps/studio/Dockerfile -t relayna-studio-frontend .
```

## Backend Runtime Config

Required:

- `RELAYNA_STUDIO_REDIS_URL`

Optional:

- `RELAYNA_STUDIO_HOST`
- `RELAYNA_STUDIO_PORT`
- `RELAYNA_STUDIO_FEDERATION_TIMEOUT_SECONDS`
- `RELAYNA_STUDIO_EVENT_STORE_PREFIX`
- `RELAYNA_STUDIO_EVENT_STORE_TTL_SECONDS`
- `RELAYNA_STUDIO_EVENT_HISTORY_MAXLEN`
- `RELAYNA_STUDIO_PUSH_INGEST_ENABLED`
- `RELAYNA_STUDIO_PULL_SYNC_INTERVAL_SECONDS`
- `RELAYNA_STUDIO_HEALTH_STORE_PREFIX`
- `RELAYNA_STUDIO_HEALTH_REFRESH_INTERVAL_SECONDS`
- `RELAYNA_STUDIO_CAPABILITY_STALE_AFTER_SECONDS`
- `RELAYNA_STUDIO_OBSERVATION_STALE_AFTER_SECONDS`
- `RELAYNA_STUDIO_WORKER_HEARTBEAT_STALE_AFTER_SECONDS`
- `RELAYNA_STUDIO_TASK_SEARCH_INDEX_PREFIX`
- `RELAYNA_STUDIO_TASK_INDEX_TTL_SECONDS`
- `RELAYNA_STUDIO_RETENTION_PRUNE_INTERVAL_SECONDS`
- `RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS`
- `RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_NETWORKS`

The container entrypoint serves `uvicorn relayna_studio.asgi:app` and keeps all
Studio backend routes under `/studio/*`.

The capability refresh allowlist variables are the shared Studio backend egress
policy for registered service URLs, Loki URLs, and Prometheus URLs. For AKS DNS
names, allow suffixes such as `.svc.cluster.local`; for literal private IP URLs,
allow the required CIDR explicitly. Push ingestion is disabled by default, so the
default internal deployment should rely on pull sync unless
`RELAYNA_STUDIO_PUSH_INGEST_ENABLED=true` is set intentionally.

## Frontend Single-Origin Routing

- The frontend container serves the built SPA with Nginx.
- `/studio/*` is proxied to `${STUDIO_BACKEND_UPSTREAM}`.
- All non-asset SPA routes fall back to `index.html`, preserving BrowserRouter
  deep links such as `/services` and `/tasks/search`.
- The React app keeps the same-origin API contract and continues to fetch
  `/studio/*` directly from the browser origin.

Recommended frontend runtime env:

- `STUDIO_BACKEND_UPSTREAM=studio-backend:8000`
- `PORT=80`

## Local Source Run

Backend from source:

```bash
PYTHONPATH=src:studio/backend/src \
RELAYNA_STUDIO_REDIS_URL=redis://localhost:6379/0 \
uv run python -m relayna_studio
```

Frontend dev server from source:

```bash
cd apps/studio
STUDIO_BACKEND_URL=http://localhost:8000 npm run dev
```

## Example Container Topology

```text
browser
  -> studio-frontend (Nginx, public origin)
       /                -> static SPA assets
       /services        -> SPA fallback -> index.html
       /tasks/search    -> SPA fallback -> index.html
       /studio/*        -> proxy -> studio-backend
  -> studio-backend (FastAPI + Redis-backed Studio runtime)
       /studio/*        -> Studio API routes
  -> Redis
```

## Verification Steps

Build and run:

```bash
docker build -f studio/backend/Dockerfile -t relayna-studio-backend .
docker build -f apps/studio/Dockerfile -t relayna-studio-frontend .

docker run --rm -p 8000:8000 \
  -e RELAYNA_STUDIO_REDIS_URL=redis://host.docker.internal:6379/0 \
  relayna-studio-backend

docker run --rm -p 8080:80 \
  -e STUDIO_BACKEND_UPSTREAM=host.docker.internal:8000 \
  relayna-studio-frontend
```

Verify:

```bash
curl -s http://localhost:8000/studio/services
curl -s http://localhost:8080/studio/services
curl -I http://localhost:8080/services
curl -I http://localhost:8080/tasks/search
```

Expected results:

- backend returns a valid Studio API response on `/studio/services`
- frontend proxies `/studio/services` to the backend without changing origin
- `/services` and `/tasks/search` return `index.html` instead of `404`
