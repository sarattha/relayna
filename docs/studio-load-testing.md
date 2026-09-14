# Load testing services in Studio

Open **Services → a service → Load testing**. Choose an approved operation,
fill in its typed request fields, set load within the displayed limits and select
**Review load test**. Review creates a Chamber plan without sending traffic.
**Start load test** executes that plan against the named environment. A lost
start response can be retried on the same plan without starting duplicate work.

The run URL can be bookmarked. Recent plans and runs remain in Studio for
30 days (latest 20 displayed). Administrators can plan, start and cancel;
active read-only members can inspect. Cancellation remains pending until
Chamber reports a terminal state, and cleanup warnings remain visible.

The workspace includes retained runner output, exact task links from Chamber's
Relayna evidence, service/task logs, and per-pod CPU, memory, restart, OOM and
readiness charts. Task links open the existing Studio task investigation page.
While Chamber is collecting task evidence, task IDs from Loki entries also link
to task details. Telemetry uses the service's existing Loki and Prometheus
connections and the run window. Service-window telemetry can include unrelated
traffic; it is not presented as exact per-test attribution. Empty samples and
unavailable providers are shown explicitly. Refresh telemetry after completion
if the provider has ingestion delay.

## Deployment

This integration targets **Ampule Chamber 1.10.0**, the version identified in its
Studio preparation artifact. Chamber remains an internal service; only the
Studio hostname is exposed. Studio renders native React forms, run views and
its existing log/metric components, consuming Chamber's execution and evidence
APIs. Chamber does not provide a reusable React component package.

Set these variables on the **Studio backend** deployment:

| Variable | Purpose |
| --- | --- |
| `RELAYNA_STUDIO_CHAMBER_URL` | Internal HTTP(S) base URL for Chamber, e.g. `http://ampule-chamber.reliability.svc:8765`. |
| `RELAYNA_STUDIO_CHAMBER_TOKEN` | Chamber operator bearer token, injected from a Kubernetes Secret. Required when URL is set. |
| `RELAYNA_STUDIO_CHAMBER_PROFILES_PATH` | Absolute path to the mounted service-profile JSON file. |

Mount the profile file read-only, configure the matching operator token in
Chamber, and restart Studio after profile changes. Never put the bearer token
in frontend variables. Existing Studio authentication, CSRF and mutation audit
middleware protects these routes. Configure normal Studio Entra authentication
for shared deployments. Read-only users have the same global service visibility
as other Studio views; this feature does not introduce tenant isolation.

Chamber needs its normal persistent workspace, the specified repository mounted
inside its container, Kubernetes credentials/context for the target AKS cluster,
and the tools required by its runner. Attach-mode permissions must cover target
workload observation and service port-forwarding. Studio does not need Kubernetes
credentials. Retain the existing Chamber safety gates; this integration creates
observe-only tests with faults and cleanup disabled. Do not expose Chamber's
operator API publicly. Existing Studio ingress `/studio` routing also covers
`/studio/services/{service_id}/load-tests`, so no new hostname or ingress path is
necessary.

## Service profiles

Copy [the example profile](examples/studio-chamber-profiles.json) and replace its
service ID, exact Studio environment, namespace, Kubernetes context, deployment
names, port, repository mount path, operation and task lifecycle contract with
those of the target service. The top-level key is **Studio's immutable service
ID**, not the display name. Add one entry per service, and one profile per
operation. Profile namespaces must match the target of that service's metrics
and log selectors. The backend loads and validates profiles at startup.

Profiles are operator-owned deployment configuration. A service name or topology
alone does not define an HTTP request body or Kubernetes deployment. Studio's
current capability document does not publish request schemas, so onboarding
must supply an accurate schema from the service's actual request contract.
There is no fallback to a free-form JSON/YAML editor. Unconfigured services show
setup guidance and cannot create tests.

Each profile contains `id`, `name`, `input_schema`, `max_vus`, `max_iterations`,
`max_duration_seconds`, and a valid Chamber `config`. Optional `prometheus_url`
is passed server-to-server to Chamber for its own assessment collection; the
Studio charts independently use the service registry's metrics configuration.

Supported form schemas use concrete object, array, string, integer, number and
boolean types. Nested fields, required fields, enums, defaults, string lengths,
numeric bounds and array bounds are supported. Objects require
`additionalProperties: false`; arrays require `maxItems` no greater than 100.
Unsupported schema keywords, references and unions fail during startup rather
than producing an inaccurate form. Flatten references and choose a specific
operation variant during onboarding. Inputs are validated on the backend with
JSON Schema, with a 64-KiB request-input limit. They are retained in the reviewed
plan; use representative test data.

A profile selects one HTTP or Relayna journey. For a bodyless journey,
use `requestEncoding: none` and an empty object schema. JSON Relayna journeys
submit and follow tasks using the pinned `taskIdPath`, `eventsPath`, terminal
statuses and timeout. Maximum supported Relayna load is 32 users and 1,000 tasks;
profiles should normally choose lower limits. HTTP profiles allow up to 100 users.
Multipart profiles use approved file fixtures already stored in Chamber, with typed
form fields and file names displayed at review. Pin files under the journey
`multipart.files` configuration; Studio never accepts client-provided server paths.
Use separate named profiles for representative file datasets. Uploading new
files through Studio is not supported. URL-encoded profiles use `requestEncoding:
form` and scalar schema fields. Raw profiles use `requestEncoding: raw`, a pinned
`contentType`, and a schema with exactly one required string property named
`body`. Schema unions are not supported.

HTTP load ramps to the selected concurrency over the reviewed duration; request
count depends on response time and k6's graceful completion can extend execution.
Relayna load uses fixed task iterations and concurrency. Its `durationSeconds`
is scheduling metadata, **not a wall-clock kill timeout**. Each task is bounded
by the operator-configured `relayna.timeoutSeconds`; total runtime depends on
iterations, concurrency and service completion. Cancellation is available while
execution is active.

The backend pins Kubernetes attach mode, target configuration, no faults and no
cleanup. Browsers cannot replace repository paths, upstream URLs, Kubernetes
contexts or run IDs. Plans bind the service environment and snapshot execution
context; changing the registry environment blocks old plans. Disabled services
cannot start work. Existing run status and cancellation remain service-bound.

## Persistence and compatibility

This is an additive Studio feature authorized by the integration request.
Existing SDK contracts, service records, route responses and broker protocols
are unchanged. The production freeze route/page manifests intentionally add the
new Studio endpoints and page. No existing data migration is required.

Studio stores service-bound plans/job references under `studio:load-testing:v1:`
in its existing Redis connection, with 30-day retention. Persist Redis if run
bookmarks must survive restarts. Chamber separately persists jobs and evidence
in its workspace. Both stores are needed to recover a started job. The adapter
uses a stable `Idempotency-Key` per Studio plan and never accepts a browser-supplied
Chamber job or plan ID. Profile changes apply to new plans; previously reviewed
plans retain their snapshot. Revoke a service by disabling its registry entry.
