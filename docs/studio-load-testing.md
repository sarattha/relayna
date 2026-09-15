# Load testing services in Studio

Open **Services → a service → Load testing**. Choose an approved operation,
fill in its typed request fields (automatically imported from OpenAPI when enabled), set load within the displayed limits and select
**Review load test**. Review creates a Chamber plan without sending traffic.
**Start load test** executes that plan against the named environment. A lost
start response can be retried on the same plan without starting duplicate work.
Chamber-backed requests have a 15-second server deadline, below Studio’s
20-second browser timeout. Check recent runs before retrying a timed-out plan;
planning itself does not generate load.

The run URL can be bookmarked. Recent plans and runs remain in Studio for
30 days from plan creation (latest 20 displayed); opening or polling a run
does not extend that deadline. Retained terminal runs remain readable during
Chamber outages using their last stored snapshot. Administrators can plan, start and cancel;
active read-only members can inspect. Cancellation remains pending until
Chamber reports a terminal state, and cleanup warnings remain visible.
Started runs remain visible and cancellable if the service environment changes;
new starts from old plans are blocked. Their original environment/target stays
pinned, and current service telemetry is hidden when its environment differs.

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

Relayna Studio **1.9.0** targets **Ampule Chamber 1.10.0**, the version identified in its
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
middleware protects these routes. Planning, starting and cancelling record
`load_test.plan`, `load_test.start` and `load_test.cancel` request/outcome audit
events with the actor and service/run target; request bodies and tokens are not
written to audit details. Configure Studio Entra authentication or the opt-in
[shared operator login](studio-entra-auth.md) for sandbox deployments. Read-only users have the same global service visibility
as other Studio views; this feature does not introduce tenant isolation.

Chamber needs its normal persistent workspace, Kubernetes credentials/context
for the target AKS cluster, and the tools required by its runner. Kubernetes
attach profiles can use `kubernetes://in-cluster/<namespace>/<workload>` as
`service.repo`; profiles using a local repository path require that repository
to be mounted inside the Chamber container. Attach-mode permissions must cover target
workload observation and service port-forwarding. Studio does not need Kubernetes
credentials. Retain the existing Chamber safety gates; this integration creates
observe-only tests with faults and cleanup disabled. Do not expose Chamber's
operator API publicly. Existing Studio ingress `/studio` routing also covers
`/studio/services/{service_id}/load-tests`, so no new hostname or ingress path is
necessary.

## ConfigMaps and Secrets

The sandbox uses the following resources. Create equivalents in each environment;
resource names are conventions, while the environment variable names are the
backend contract. Secrets must be supplied through your deployment secret store.

| Namespace | Resource | Keys / purpose |
| --- | --- | --- |
| `relayna` | ConfigMap `relayna-studio-config` | Authentication mode, internal Chamber URL, profile path, outbound host allowlist and worker settings. |
| `relayna` | ConfigMap `relayna-studio-chamber-profiles` | `profiles.json`: approved profiles keyed by immutable Studio service ID, with the exact registered environment. |
| `relayna` | Secret `relayna-studio-runtime-secrets` | `RELAYNA_STUDIO_DATABASE_URL`, `RELAYNA_STUDIO_REDIS_URL`, `RELAYNA_STUDIO_CHAMBER_TOKEN`; also `RELAYNA_STUDIO_OPERATOR_TOKEN` when using operator login. |
| `ampule-system` | Secret `ampule-ampule-chamber-auth` | `admin-token`: Chamber's operator credential. Copy its value into Studio's `RELAYNA_STUDIO_CHAMBER_TOKEN` using your secret-management workflow; Kubernetes Secret references cannot cross namespaces. |
| `ampule-system` | ConfigMap `ampule-ampule-chamber` | Helm-managed Chamber runtime configuration, including Kubernetes context and discovery namespaces. Preserve its chart-generated service-account and workspace wiring. |

The two tokens serve different purposes. Generate a distinct Studio login token
starting with `op_live_` (at least 24 characters total); never reuse the Chamber
admin credential as the browser login token. Restart the corresponding workloads
after rotating environment-injected credentials. Studio token rotation invalidates
existing operator sessions. Entra deployments instead configure their
[Entra variables and certificate Secret mounts](studio-entra-auth.md).

Example sandbox ConfigMap (replace namespaces, hosts and mode for your environment):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: relayna-studio-config
  namespace: relayna
data:
  RELAYNA_STUDIO_AUTH_MODE: operator
  RELAYNA_STUDIO_SESSION_COOKIE_SECURE: "true"
  RELAYNA_STUDIO_CHAMBER_URL: http://ampule-ampule-chamber.ampule-system.svc.cluster.local:8765
  RELAYNA_STUDIO_CHAMBER_PROFILES_PATH: /etc/relayna/chamber/profiles.json
  RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS: .svc.cluster.local
  RELAYNA_STUDIO_PULL_SYNC_INTERVAL_SECONDS: "5"
  RELAYNA_STUDIO_HEALTH_REFRESH_INTERVAL_SECONDS: "60"
  RELAYNA_STUDIO_RETENTION_PRUNE_INTERVAL_SECONDS: "60"
```

Include the registered service, Loki and Prometheus host suffixes actually used
in that environment in the outbound allowlist. Populate the profile ConfigMap
from your approved JSON file:

```bash
kubectl -n relayna create configmap relayna-studio-chamber-profiles \
  --from-file=profiles.json=/path/to/approved-profiles.json \
  --dry-run=client -o yaml | kubectl apply -f -
```

Then add these fields to the backend pod template:

```yaml
spec:
  containers:
    - name: backend # Match the existing container name.
      envFrom:
        - configMapRef:
            name: relayna-studio-config
        - secretRef:
            name: relayna-studio-runtime-secrets
      volumeMounts:
        - name: chamber-profiles
          mountPath: /etc/relayna/chamber
          readOnly: true
  volumes:
    - name: chamber-profiles
      configMap:
        name: relayna-studio-chamber-profiles
```

The database URL uses `postgresql+asyncpg://USER:PASSWORD@HOST:5432/DATABASE`;
Redis uses the environment's authenticated Redis URL. URL-encode credentials as
needed. Provision PostgreSQL, apply Alembic migrations, and follow the
[legacy Redis import procedure](studio-persistence.md) for pre-1.6.0 Studio.
The frontend needs only `STUDIO_BACKEND_UPSTREAM` pointing at the backend service
(for example `relayna-studio-backend-service.relayna.svc.cluster.local:8000`).
Keep secure session cookies enabled behind the Studio HTTPS hostname.

## Sandbox rollout through vm-machine01

The completed rollout on 15 September 2026 runs Studio **1.8.1** backend/frontend
and **Ampule Chamber 1.10.0** in AKS context `aks-in-aic-sdbx-tara2-app-01`, accessed
through `vm-machine01`. Studio images are pinned to GHCR digests published by
repository CI after the operator-login release. Chamber pulls
`ghcr.io/sarattha/ampule-chamber:1.10.0` directly, without an image-pull Secret;
Helm release `ampule` is revision 5 in `ampule-system`. Its existing 5-GiB
workspace PVC, operator credential and observe-only permissions were preserved.

The old Studio 1.4.28 Redis state was backed up and imported into PostgreSQL:
8 services, 1,717 events and 540 task projections, with no invalid records.
A repeat import returned `already_imported` with the same checksum. Source
Redis keys and protected rollback artifacts were retained.

All eight service profiles are available. Summary imports live OpenAPI; the
other seven use constrained snapshots of their actual service schemas and
approved existing file fixtures where required. Profile environment values
match the registry (`dev`), even though the cluster is named sdbx. Operator
browser login, plan creation, Chamber connectivity, logs, metrics and pod
observations were verified. No load traffic was started. Browser validation
used a loopback tunnel; the external Studio hostname has not been verified.
This 1.8.2 documentation release does not change those deployed versions.

For subsequent environments:

1. Provision PostgreSQL and Redis; migrate old Studio state during a maintenance
   window when required. Deploy matching Studio backend/frontend images from
   Azure pipelines or the repository's published GHCR images.
2. Upgrade Chamber to 1.10.0 while preserving its workspace, credentials,
   service account, runtime tools and workload permissions. Verify readiness.
3. Configure the resources above, review each service's operation and target,
   and restart Studio after profile changes. Verify login and available profiles.
4. Review a small test before starting traffic. After an authorized run, confirm
   runner output, task completion, Loki logs and Prometheus pod samples.
5. If acceptance fails, disable new testing and retain workspace/run evidence
   while diagnosing. Cancel active runs before removing the Studio connection.

## Import profiles as an administrator

Open **Services → a service → Load testing → Manage profiles**. Search Chamber's
saved plans and runs, select a source and operation, then choose **Preview import**.
Studio reads the configuration through its internal Chamber connection and imports
typed request fields from the selected Studio service's OpenAPI. Verify the Studio
environment, Kubernetes context, namespace, workload, service port and file fixtures.
Set the approved load limits, confirm the binding, and select **Save imported profile**.
The operation becomes available immediately, without a restart or ConfigMap edit.
Importing does not plan or start a load test.

This imports complete configurations from saved Kubernetes attach-mode plans/runs.
Chamber's named environment profiles alone do not include an HTTP request contract.
Multi-suite/experiment configurations, SDK control endpoints and custom-header
operations are not imported. Unsupported OpenAPI schemas display a setup error;
use an approved deployment profile with a constrained schema for those operations.
Import does not expose a free-form configuration editor or accept new file paths.

Source request values and runtime credentials are discarded. Faults, cleanup and
agents are disabled. Existing multipart fixtures are retained. Named Chamber
bindings remain attached so Chamber still enforces their admission budgets when
planning and starting traffic; Studio's load limits do not override those budgets.

Import previews expire after 30 minutes and are bound to the service, its environment
and base URL. Saving checks the request schema again. Profiles are stored durably in
PostgreSQL, scoped to the immutable service ID and exact environment. Existing
ConfigMap profiles remain available alongside imported profiles. Repeating an
identical save is safe; to replace an imported operation, remove it in the manager
and import again. Removing it does not remove already-reviewed plans or runs.

Before deploying this feature, stop old backend replicas during a maintenance
window, back up PostgreSQL and run `alembic upgrade head`
from `studio/backend` with `RELAYNA_STUDIO_DATABASE_URL` set. Revision
`0002_load_profiles` adds `studio_load_profiles`; it does not migrate or remove
ConfigMap profiles. Deploy matching 1.9.0 backend/frontend images together; do not mix backends
expecting different schema revisions. Downgrading
the schema removes imported profiles, so export/back up the database first.
Read-only members can use the normal profile/run views but cannot browse import
sources, preview, save or remove profiles. Mutations retain Studio's CSRF and audit
protection. The source catalog is paginated; a bounded upstream response that is
too large or unavailable produces an error rather than a partial import.

## Service profiles

Copy [the example profile](examples/studio-chamber-profiles.json) and replace its
service ID, exact Studio environment, namespace, Kubernetes context, deployment
names, port, repository mount path, operation and task lifecycle contract with
those of the target service. The top-level key is **Studio's immutable service
ID**, not the display name. Add one entry per service, and one profile per
operation. Profile namespaces must match the target of that service's metrics
and log selectors. The backend loads and validates profiles at startup.

Profiles may be administrator-imported PostgreSQL records or operator-owned deployment configuration. A service name or topology
alone does not define an HTTP request body or Kubernetes deployment. Studio's
current capability document does not publish request schemas, so onboarding
can obtain the schema from the service’s OpenAPI document.
There is no fallback to a free-form JSON/YAML editor. Unconfigured services show
setup guidance and cannot create tests.

Each profile contains `id`, `name`, `max_vus`, `max_iterations`,
`max_duration_seconds`, and a valid Chamber `config`. An optional `input_schema` overrides OpenAPI discovery. Optional `prometheus_url`
is passed server-to-server to Chamber for its own assessment collection; the
Studio charts independently use the service registry's metrics configuration.

Supported form schemas use concrete object, array, string, integer, number and
boolean types. Nested fields, required fields, enums, defaults, string lengths,
numeric bounds and array bounds are supported. Objects require
`additionalProperties: false`; arrays require `maxItems` no greater than 100.
Manual schemas must use the supported concrete form types. OpenAPI imports resolve
local references, basic object composition and nullable variants automatically;
ambiguous multi-variant or recursive schemas show a per-operation setup error. Inputs are validated on the backend with
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
`body`. Nullable fields are supported; unions of multiple non-null types require an explicit schema override.

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

## Automatic inputs from OpenAPI

Use [the OpenAPI profile example](examples/studio-chamber-openapi-profiles.json).
Omit `input_schema` from an approved operation profile. Studio fetches
`<registered service base URL>/openapi.json`, matches the profile’s method,
path and request encoding, and generates its form automatically. Set service-level
`openapi_path` for a different service-relative document path. This removes the
need to maintain a second handwritten copy of each request body.

Administrators still configure the approved service operations, AKS targets,
load limits, task lifecycle mapping and file fixtures. OpenAPI `servers` and
security definitions do not change execution targets or supply credentials.
The document must be readable by the Studio backend and the registered service
host must satisfy Studio's existing capability-refresh outbound allowlist.
Requests have a five-second upstream timeout and a 2-MiB response limit; redirects and
external references are not followed. No Chamber bearer token is sent to services.

Studio focuses on service endpoints such as `/translations`, `/ocr` and `/tasks`.
Typical Relayna SDK endpoints under `/relayna`, `/status`, `/events`, `/history`,
`/dlq`, `/broker/dlq`, `/failed-tasks`, workflow topology/stages and execution
graphs are excluded, including common `/api/v1` mount prefixes. Operations
tagged `relayna`, `relayna:*` or `relayna.*` are excluded during import. SDK event
and status routes may still be used to follow a submitted task's lifecycle;
they are not offered as load targets. Custom SDK aliases should be tagged or
left out of approved operation profiles.

The importer supports OpenAPI 3.0/3.1 request bodies, local component references,
non-conflicting object composition, nested objects/arrays, enums, nullable
values, required fields, defaults, string/numeric limits, patterns and formats.
Array minimums must fit their maximum (at most 100 items), and initial form
expansion is limited to 1000 values, including nested arrays and defaults.
Structured enums render as fixed choices; string emptiness follows minLength,
independently of whether the property is required.
Integer inputs are restricted to −9007199254740991 through 9007199254740991
so their values remain exact in the browser. Schemas with larger integer
bounds/defaults/enums are rejected; larger identifiers need a string API contract.
Numeric bounds/multiples and ECMAScript-compatible string patterns are checked
in the form. Formats and any server-specific regular expressions are validated
when reviewing; the backend remains authoritative for every constraint.
Response-only `readOnly` properties are omitted. Free-form extra properties are
not editable; imported objects forbid them and imported arrays are limited to
100 items. These are narrower test-input limits, not changes to the service API.
Required path/query/header parameters, custom form encodings, dictionaries,
recursive definitions and ambiguous variants require explicit mapping or a
manual schema. Multipart binary properties use matching approved file fixtures;
no server file paths are generated from an OpenAPI document.

Select **Refresh operations** to reload schemas. One failed import does not hide
other usable profiles. When creating a plan, Studio fetches the definition again
and compares the form's schema revision; if it changed, the user must refresh and
review the fields. Already-reviewed plans retain their input snapshot.

The conversion follows the request-body and schema model described in the
[OpenAPI 3.0 specification](https://spec.openapis.org/oas/v3.0.3.html#request-body-object).

## Persistence and compatibility

This is an additive Studio feature authorized by the integration request.
Existing SDK contracts, service records, route responses and broker protocols
are unchanged. The production freeze route/page manifests intentionally add the
new Studio endpoints and page. The load-testing feature itself needs no data migration. Upgrading Studio
from before 1.6.0 still requires the [PostgreSQL migration](studio-persistence.md).

Studio stores service-bound plans/job references under `studio:load-testing:v1:`
in its existing Redis connection, with 30-day retention. Persist Redis if run
bookmarks must survive restarts. Chamber separately persists jobs and evidence
in its workspace. Both stores are needed to recover a started job. The adapter
uses a stable `Idempotency-Key` per Studio plan and never accepts a browser-supplied
Chamber job or plan ID. Profile changes apply to new plans; previously reviewed
plans retain their snapshot. Revoke a service by disabling its registry entry.
