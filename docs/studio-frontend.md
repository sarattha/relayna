# Studio Frontend

This guide covers the Studio single-page application under `apps/studio/` and
how it is wired to the Studio backend in development and production.

If you need to operate the backend or understand `/studio/*` endpoints first,
see [Studio Backend](studio-backend.md).

## Role In The Architecture

The frontend is a React SPA. It does not talk directly to registered Relayna
services.

Its contract is:

- browser talks to the frontend origin
- frontend fetches `/studio/*`
- backend federates calls to registered Relayna services

That boundary keeps service discovery, normalization, and error handling in the
backend instead of in the browser.

## App Boundaries

The frontend lives in:

- source: `apps/studio/src`
- build config: `apps/studio/vite.config.ts`
- runtime Nginx template: `apps/studio/nginx/default.conf.template`
- package scripts: `apps/studio/package.json`

Key dependencies include:

- React
- React Router
- Vite
- Vitest
- `@xyflow/react` for execution and topology views

## Development Mode

Install dependencies:

```bash
cd apps/studio
npm ci
```

Start the dev server:

```bash
STUDIO_BACKEND_URL=http://localhost:8000 npm run dev
```

Or via the frontend Makefile:

```bash
cd apps/studio
make sync
STUDIO_BACKEND_URL=http://localhost:8000 make dev
```

### Vite proxy behavior

During development, Vite proxies `/studio/*` to:

- `process.env.STUDIO_BACKEND_URL` when set
- otherwise `http://localhost:8000`

This means frontend code should continue calling relative paths such as:

- `/studio/services`
- `/studio/tasks/search`

The browser still sees a same-origin contract from the app’s point of view.

## Production Mode

The production image is built from the repo root:

```bash
docker build -f apps/studio/Dockerfile -t relayna-studio-frontend .
```

Tag releases publish the frontend image to GHCR as:

```text
ghcr.io/sarattha/relayna-studio-frontend
```

The image:

- builds the SPA with Node
- serves static assets with Nginx
- proxies `/studio/*` to `${STUDIO_BACKEND_UPSTREAM}`

Runtime environment:

- `STUDIO_BACKEND_UPSTREAM=studio-backend:8000`
- `PORT=80`

Run example:

```bash
docker run --rm -p 8080:80 \
  -e STUDIO_BACKEND_UPSTREAM=host.docker.internal:8000 \
  relayna-studio-frontend
```

### Nginx routing behavior

Production routing is intentional:

- `/studio/*`
  - proxied to the Studio backend
- `/`
  - serves static assets or falls back to `index.html`

That fallback preserves deep links such as:

- `/services`
- `/tasks/search`

If you get `404` on those routes in production, the Nginx fallback is broken or
missing.

## Frontend-Backend Contract

The frontend API layer in `apps/studio/src/api.ts` calls only backend routes.

Primary requests include:

- `/studio/services`
- `/studio/gateway/services`
- `/studio/services/search`
- `/studio/tasks/search`
- `/studio/tasks/{service_id}/{task_id}`
- `/studio/services/{service_id}/events`
- `/studio/tasks/{service_id}/{task_id}/events`
- `/studio/services/{service_id}/logs`
- `/studio/tasks/{service_id}/{task_id}/logs`
- `/studio/services/{service_id}/workflow/topology`
- `/studio/services/{service_id}/dlq/messages`
- `/studio/services/{service_id}/broker/dlq/messages`

Important constraint:

- the browser does not call registered service `base_url` values directly

That is a design rule, not an incidental implementation detail.

## Environment And Config Reference

### Development

| Variable | Default | Purpose |
| --- | --- | --- |
| `STUDIO_BACKEND_URL` | `http://localhost:8000` | Target for Vite dev proxy for `/studio/*`. |

### Production container

| Variable | Default | Purpose |
| --- | --- | --- |
| `STUDIO_BACKEND_UPSTREAM` | `studio-backend:8000` | Nginx upstream for proxied `/studio/*` requests. |
| `PORT` | `80` | Nginx listen port. |

## UI Data Model Guidance

### Service registry views

The UI manages service records with fields including:

- `service_id`
- `name`
- `base_url`
- `environment`
- `auth_mode`
- `tags`
- optional `log_config`
- optional `metrics_config`
- optional `trace_config`

These are not cosmetic fields. They determine how the backend resolves and
federates the service.

The registered-services screen reads this data through the shared
`StudioServicesProvider`, which polls `/studio/services` roughly every 60
seconds so backend health-refresh results appear without a manual browser
reload. The explicit `Reload List` action remains available for operator-driven
refreshes.

The same screen exposes a Gateway Import panel. Its `Open Export` link points
to `/studio/gateway/services`, a backend catalog that Relayna Gateway Admin can
use to preview and import Studio-registered services. The export maps Studio
`service_id` to `studio_service_id`, provides a lowercase Gateway-safe `name`
and `default_route_pattern`, and omits Studio log, metric, trace, and credential
configuration.

For Loki-backed log views, the service editor now exposes AKS-friendly inputs in
addition to the raw generic contract:

- `service label key`
- `service label value`
- `app label key`
- `task match mode`
- `task match template`

The UI maps those inputs back into the backend `log_config`:

- `service label key` + `service label value`
  - become one entry in `service_selector_labels`
- `app label key`
  - becomes `source_label`
- `task match mode`
  - controls whether task detail logs use a Loki label, plain-text contains
    query, or regex query
- `task match template`
  - is rendered with `{task_id}` when task matching uses `contains` or `regex`

Recommended AKS example:

- `service label key`: `service`
- `service label value`: `checker-service`
- `app label key`: `app`
- `task match mode`: `contains`
- `task match template`: `{task_id}`

That lets the service page query all logs under the shared `service` label while
the task page finds logs whose line text mentions the current task ID.

For Prometheus-backed metric views, the service editor exposes provider,
backend URL, namespace, selector labels, runtime service label value, step, and
task-window padding fields. These map to backend `metrics_config` and drive
service metrics, task-window Kubernetes metrics, aggregate Relayna runtime
charts, and the exact task resource sample panel.

For Tempo-backed trace views, the service editor exposes provider, backend URL,
optional public URL, tenant ID, and query path fields. These map to backend
`trace_config`. The task detail page uses that config to load traces through:

```text
GET /studio/tasks/{service_id}/{task_id}/traces
```

### Task search and task detail

The UI addresses tasks as:

```text
service_id + task_id
```

That is why the backend and docs treat `service_id` as part of the identity
model. In a federated control plane, `task_id` alone is not globally safe.

Task views may also render:

- correlation-based joins
- lineage joins
- event timelines
- execution graphs
- logs
- metrics
- trace correlation spans

Task detail log behavior is now intentionally lifecycle-aware:

- the page derives an automatic `from` / `to` window from queued-to-terminal
  task activity when possible
- the page still allows a manual override when the automatic window is too
  narrow or no usable timestamps are available
- when the service has `source_label` configured, source/app suggestions are
  discovered from the returned logs and exposed as input suggestions rather than
  a hard-coded list

Task detail trace behavior is optional:

- if no `trace_config` is registered, the Trace Correlation section shows a
  non-error empty state
- if trace IDs are present in task detail, observations, or log fields, Studio
  queries Tempo through the backend
- span rows open a Studio-native detail modal instead of sending the operator to
  Tempo's raw API response
- the trace action can apply the trace ID to the task log text filter so logs
  and spans can be compared in one task view

### Workflow, logs, events, and DLQ views

These views depend on backend support:

- workflow pages depend on federated workflow routes
- log pages depend on Studio-side `log_config`
- metric pages depend on Studio-side `metrics_config`
- trace panels depend on Studio-side `trace_config`
- event views depend on Studio event ingestion or service-scoped event reads
- DLQ pages depend on service DLQ routes

DLQ views now have two explicit modes:

- indexed mode
  - uses `/studio/services/{service_id}/dlq/messages`
  - shows indexed Relayna DLQ records with pagination and replay/index metadata
- broker mode
  - uses `/studio/services/{service_id}/broker/dlq/messages`
  - is enabled only when the service capability document advertises `broker.dlq.messages`
  - is a live emergency inspection path and does not show `dlq_id`, replay state, or pagination

Task detail remains indexed-first. When indexed DLQ data is empty and broker
inspection is supported, the UI links operators into broker mode instead of
automatically replacing the indexed view.

### How operators use the broker DLQ mode

From the UI point of view, broker mode is a separate inspection path:

1. open `/services/:serviceId/dlq`
2. switch to broker mode, or follow the task-detail CTA when indexed DLQ data
   is empty
3. optionally filter by `task_id`
4. inspect live message headers and bodies coming from
   `/studio/services/{service_id}/broker/dlq/messages`

The frontend does not infer broker support on its own. It waits for the service
capability document to advertise `broker.dlq.messages`, then enables the broker
mode affordances.

Expectations to communicate to operators:

- indexed mode is the normal operational view
- broker mode is the emergency or drift-recovery view
- broker mode does not provide `dlq_id`, replay state, or pagination because
  the underlying service route is reading live broker messages rather than the
  indexed Redis model

The frontend is intentionally thin here: if the backend cannot provide a route,
the UI should degrade rather than invent client-side service calls.

## Local Verification

Run the backend first on `localhost:8000`, then start the frontend dev server:

```bash
cd apps/studio
STUDIO_BACKEND_URL=http://localhost:8000 npm run dev
```

Verify:

```bash
curl -s http://localhost:8000/studio/services
curl -I http://localhost:5173/
```

Then check in the browser:

- `/services`
  - service list renders and can create or edit service records
  - registry and health badges refresh automatically after backend health updates
- `/tasks/search`
  - task search page loads without direct service-origin calls
- task detail pages
  - logs, events, and execution views render when the backend exposes data

For container verification:

```bash
curl -s http://localhost:8080/studio/services
curl -I http://localhost:8080/services
curl -I http://localhost:8080/tasks/search
```

Expected behavior:

- `/studio/services` proxies successfully to the backend
- `/services` returns `index.html`
- `/tasks/search` returns `index.html`

## Troubleshooting

### Proxy mismatch

Symptoms:

- frontend loads, but all API calls fail
- browser dev tools show failed `/studio/*` requests

Checks:

- in dev, confirm `STUDIO_BACKEND_URL`
- in production, confirm `STUDIO_BACKEND_UPSTREAM`
- confirm the backend is actually listening on the expected host and port

### Deep-link 404s

Symptoms:

- refreshing `/services` or `/tasks/search` returns `404`

Checks:

- confirm the Nginx `try_files $uri $uri/ /index.html;` fallback exists
- confirm your reverse proxy in front of Nginx preserves SPA routing behavior

### CORS or origin assumptions

The normal deployment model avoids browser CORS complexity because the frontend
and backend share one public origin and Nginx proxies `/studio/*`.

If you split origins in development or staging, you own the extra browser and
proxy configuration. The current app is designed around same-origin fetches.

### Empty UI

Symptoms:

- the shell loads, but service and task views are empty

Checks:

- confirm the backend has a valid Redis connection
- confirm at least one service is registered
- confirm capability refresh succeeded for that service
- confirm the backend can reach the registered service `base_url`
- confirm events, logs, workflow, or DLQ panels are not empty simply because the
  service does not expose those routes

## Related Docs

- [Getting Started](getting-started.md) for making a downstream service
  Studio-compatible
- [Studio Backend](studio-backend.md) for backend runtime, Redis, and route
  behavior
