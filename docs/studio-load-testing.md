# Load testing services in Studio

Open **Services → a service → Load testing**. Choose an approved operation,
fill in its typed request fields (automatically imported from OpenAPI when enabled), set load within the displayed limits and select
**Review load test**. Review creates a Chamber plan without sending traffic.
**Start load test** executes that plan against the named environment. A lost
start response can be retried on the same plan without starting duplicate work.
Chamber-backed JSON requests have a five-second upstream deadline and a
15-second adapter deadline, below Studio’s 20-second browser timeout. Check recent runs before retrying a timed-out plan;
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

## Native Chamber workspace

The five tabs are **Configure**, **Monitor**, **Results**, **Profiles** and
**Connection**. Configure preserves the approved-operation form and adds a
**Full assessment** builder. Administrators can select or discover targets,
inspect repositories, propose traffic from goals, import/save complete YAML or
JSON scenarios, add multiple HTTP or Relayna journeys, upload multipart files,
configure arrival/capacity/soak suites, set performance gates, select experiments
and configure agents. Advanced editors preserve the complete Chamber document.
Credentials masked in saved documents must be replaced with environment
references; Studio preserves `secretEnv` and `headersFromEnv` variable names.
Managed upload path tokens are temporary and may need re-uploading after Chamber
restarts. Managed uploads support up to 128 MiB per file and 256 MiB per plan.
Public credential masking inspects up to 32 nested container/encoding levels and
limits expanded strings to 2 MiB. Sections beyond these inspection limits use
`[redacted]`; validation details that cannot be safely inspected are explicitly
omitted. Original configuration sent to Chamber is unaffected by these public
projection limits.
Diagnostic inspection also stops at 4,096 nodes, 512 collected values or 64 KiB
of collected credential representations; exceeding any budget produces the same
explicit omission message instead of relaying an unsafe upstream detail.
The browser gives this upload endpoint a bounded five-minute request deadline;
ordinary API requests retain their 20-second deadline. Studio's forwarding leg
has a four-minute total deadline for sending the file and reading the response,
including responses that continue making progress. The bundled frontend proxy
accepts up to 129 MiB on that route to allow multipart overhead. If an external ingress is present, align
its body-size and timeout limits with these values. A timed-out upload can have
reached Chamber without returning its signed reference; re-upload to obtain a
usable descriptor rather than assuming it was attached to a plan.
Planning validates configuration and creates a review; starting is a
separate action. Plans pin their target, environment and connection.

Monitor offers the existing Task explorer, Logs focus and Investigation layouts.
Its task source uses paginated exact Chamber task identities. Results separates
execution state from assessment verdict, score, evidence coverage and limitations;
it exposes findings, timeline, filtered evidence, complete configuration, agents,
report/evidence downloads, history, comparison, tags, archive and reviewed reruns.
Task pagination retains tasks beyond the bounded 25-item summary. Large documents
and evidence previews disclose truncation; download the retained report/evidence
for their complete contents. Studio JSON transport is limited to 2 MiB and
attachment downloads to 32 MiB. Reports are attachments, including HTML; Studio
does not execute upstream report markup. Cleanup verification requires an explicit
operator confirmation after actual restoration. Reusable targets expose admission
budgets, occupancy and readiness separately.

### Administrator connection settings

Connection lets an administrator test a draft, save an encrypted override, check
the saved API, return to deployment settings, or disable new assessments. The API
status distinguishes unset, authentication, network, incompatible, limited and
ready states, with last-check and last-success timestamps. API readiness does not
claim Kubernetes, target or telemetry readiness. All mutations require Studio
administrator authorization and normal CSRF protection and produce metadata-only
audit records. Active read-only members can inspect results and settings.

Use the private Chamber Kubernetes Service in AKS. A laptop port-forward works
only when the Studio backend can reach that laptop address, typically with both
running locally. A port-forward available only to the browser cannot serve a
remote Studio backend. The URL must satisfy Studio’s backend outbound allowlist;
literal IP addresses require an explicitly allowed network. No public Chamber
hostname or browser token is needed.

UI credentials require PostgreSQL and `RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY`.
Generate a Fernet key with `cryptography.fernet.Fernet.generate_key()` and inject it
through your deployment secret-management workflow. Never put keys or tokens in a
ConfigMap or frontend variable. Blank credentials retain the token only for the
same saved endpoint; changing the URL requires a new token. Existing plans retain
the encrypted connection snapshot and cannot silently move to another Chamber.
Keep the encryption key when rotating connection credentials. Changing that key
requires re-saving the active override, and old run snapshots require the original
key for inspection/cancellation. Deployment-token rotation uses the current token
only while the original deployment URL still matches. Returning to deployment
settings does not erase already pinned run snapshots.

### Set up the settings encryption key

The key belongs to the **Studio backend**, where it encrypts saved Chamber
credentials in PostgreSQL. It is distinct from the Chamber integration token and
Studio login token. All backend replicas must receive the same key. PostgreSQL
and matching Studio backend/frontend versions must already be configured.

Generate a key only for the initial setup. If settings or saved run connections
already exist, reuse their original key instead of generating a replacement.
From the Relayna repository root, run:

```bash
umask 077
studio_key_file="$(mktemp)"
uv run --directory studio/backend python -c \
  'import sys; from cryptography.fernet import Fernet; sys.stdout.write(Fernet.generate_key().decode())' \
  > "$studio_key_file"
```

The file contains the URL-safe base64 Fernet key, with no trailing newline.
Store a backup through your approved secret-management workflow, such as Azure
Key Vault. Never commit the file, print the key into CI logs, or put it in a
ConfigMap or frontend variable. Keep this file only until the key is safely
backed up and injected; then remove the temporary copy.

For AKS, create a dedicated Secret in the Studio namespace. Replace `relayna`
with your deployment namespace if different. This command creates a new Secret
without replacing existing runtime credentials:

```bash
kubectl -n relayna create secret generic relayna-studio-settings-encryption \
  --from-file=RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY="$studio_key_file"
```

Add the following entry to the existing backend container's `env` list in the
Deployment manifest, retaining its other environment entries. Apply the manifest
through your normal deployment pipeline. Secret and backend must share a
namespace:

```yaml
env:
  - name: RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY
    valueFrom:
      secretKeyRef:
        name: relayna-studio-settings-encryption
        key: RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY
```

The pod-template change rolls out new replicas. If you instead add the key to a
Secret already referenced by `envFrom`, restart the existing backend pods so
they receive it. Replace `YOUR_BACKEND_DEPLOYMENT` with the actual Deployment:

```bash
kubectl -n relayna rollout restart deployment/YOUR_BACKEND_DEPLOYMENT
kubectl -n relayna rollout status deployment/YOUR_BACKEND_DEPLOYMENT
```

For a local backend, use the same generated file in the same shell instead of
the Kubernetes Secret:

```bash
export RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY="$(cat "$studio_key_file")"
uv run --directory studio/backend relayna-studio
```

Supply the backend's other database, Redis and authentication variables as usual.
If using Docker or Compose, pass this variable explicitly to the backend
container; exporting it on the host alone does not inject it into a container.

After rollout, sign in as an administrator and open a service's **Load testing →
Connection** tab. Select **Administrator settings**, enter a backend-reachable
Chamber URL and its integration token, save, and choose **Check saved connection**.
A `ready` status confirms authenticated Chamber API access; target and Kubernetes
readiness are checked separately. A storage warning indicates missing PostgreSQL
or an invalid/missing encryption key. A host-allowlist error requires updating
the backend outbound policy. Check the deployment configuration without printing
the Secret value or asking the frontend to expose it.

Keep the key stable across restarts, replicas and Chamber-token rotation. Studio
currently accepts one settings key; it does not automatically re-encrypt old
snapshots when that key changes. Losing the original key makes those saved
credentials unavailable. Restore it to inspect/cancel existing runs; deliberately
re-saving an active connection with a new key does not repair older snapshots.

Native Chamber history has its own workspace retention. Studio references last
30 days; archiving a native run changes its history visibility and does not extend
retention. Filters/pagination expose at most the Chamber history index limit of
1,000 entries. Telemetry shows last successful refresh and retained samples during
provider outages; service/pod measurements may include unrelated traffic.

## Deployment

The complete native workspace targets **Ampule Chamber 1.11.0**. Older
1.10 connections retain the approved-operation flow; capability checks identify
missing workspace features. Chamber remains an internal service; only the
Studio hostname is exposed. Studio renders native React forms, run views and
its existing log/metric components, consuming Chamber's execution and evidence
APIs. Chamber does not provide a reusable React component package.

Set these variables on the **Studio backend** deployment:

| Variable | Purpose |
| --- | --- |
| `RELAYNA_STUDIO_CHAMBER_URL` | Internal HTTP(S) base URL for Chamber, e.g. `http://ampule-chamber.reliability.svc:8765`. |
| `RELAYNA_STUDIO_CHAMBER_TOKEN` | Chamber integration bearer token, injected from a Kubernetes Secret. Required when URL is set. Prefer Chamber’s dedicated `AMPULE_CHAMBER_STUDIO_TOKEN`; an existing operator token remains supported. |
| `RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY` | Fernet key injected from a separate backend Secret, required for encrypted administrator overrides. Keep stable across restarts and all backend replicas. |
| `RELAYNA_STUDIO_CHAMBER_PROFILES_PATH` | Absolute path to the mounted service-profile JSON file. |

Mount deployment profiles read-only, configure the matching integration token
in Chamber, and restart Studio after deployment configuration changes.
Administrator overrides are stored in the existing PostgreSQL operator-settings
table and take effect immediately. Never put the bearer token
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
credentials. Retain the existing Chamber safety gates. Approved-operation tests remain
observe-only; full assessments may enable experiments and cleanup explicitly,
with separate reviewed-target and fault confirmations before execution. Do not expose Chamber's
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
| `relayna` | Secret `relayna-studio-runtime-secrets` | `RELAYNA_STUDIO_DATABASE_URL`, `RELAYNA_STUDIO_REDIS_URL`, `RELAYNA_STUDIO_CHAMBER_TOKEN`; also `RELAYNA_STUDIO_OPERATOR_TOKEN` when using operator login. Existing deployments may keep their settings key here through `envFrom`. |
| `relayna` | Secret `relayna-studio-settings-encryption` | `RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY`, injected into the backend as shown in [key setup](#set-up-the-settings-encryption-key). |
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
and import again. Removing it does not remove already-reviewed plans or runs. Deleting a service
also clears all of its imported profiles, so re-registering the same ID does not
restore old target approvals.

Before deploying this feature, stop old backend replicas during a maintenance
window, back up PostgreSQL and run `alembic upgrade head`
from `studio/backend` with `RELAYNA_STUDIO_DATABASE_URL` set. Revision
`0002_load_profiles` adds `studio_load_profiles`; it does not migrate or remove
ConfigMap profiles. Deploy matching 1.10.1 backend/frontend images together; do not mix backends
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
uses a stable `Idempotency-Key` per Studio plan. Bound execution and job IDs are
validated against the saved reference; native Chamber history can also be
inspected through the active administrator connection. Profile changes apply to new plans; previously reviewed
plans retain their snapshot. Revoke a service by disabling its registry entry.
