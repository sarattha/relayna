# Releases and installation

## Release model

`relayna` v1 is published through GitHub Releases.

- Releases page: [github.com/sarattha/relayna/releases](https://github.com/sarattha/relayna/releases)
- Source repository: [github.com/sarattha/relayna](https://github.com/sarattha/relayna)

Each release publishes:

- a wheel for direct installation
- a source distribution for source-based installs

## Install the wheel

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.11.0/relayna-1.11.0-py3-none-any.whl
```

## Install the source distribution

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.11.0/relayna-1.11.0.tar.gz
```

## Build artifacts locally

```bash
uv build
```

Expected artifacts:

- `dist/relayna-1.11.0.tar.gz`
- `dist/relayna-1.11.0-py3-none-any.whl`

## Versioning policy

The SDK, Studio backend, and Studio frontend share one stable SemVer release
line. The documented SDK API, documented Studio backend API, and
frontend/backend Studio contract follow semantic versioning. Undocumented
internals may change outside of SemVer guarantees.

### Upgrading to 1.11.0

Studio 1.11.0 adopts the Natural Connection / Mulberry & Linen identity and
self-hosted Relayna Portico 0.101, matching Gateway's visual emphasis. The
moon/sun button switches themes on sign-in and operational pages. Browsers
without a saved `relayna.studio.theme` preference start in light mode; a saved
choice survives reloads and synchronizes across tabs. Blocked storage still
allows switching for the current page.

Task explorer uses the sidebar's full remaining height and scrolls to the last
task in normal and expanded desktop views. Mobile keeps the task list bounded
above the logs. Task fetching and 50-item pagination are unchanged. See the
[current operator tour](studio-frontend.md#studio-operator-tour) for release
screenshots and [visual identity](studio-frontend.md#visual-identity) for the
shared design patterns.

No PostgreSQL migration, API/wire change, or freeze-perimeter update is needed;
the v1.10.1 freeze baseline remains intact. Upgrade all three packages together.
Rollback uses matching previous images/packages without a database downgrade;
the browser-only appearance key can remain or be cleared to restore the light
default. The pending security maintenance prepared as 1.10.2 was not separately
published and is included in this minor release.

#### Security baseline

SDK and Studio backend require `opentelemetry-api>=1.45.1`; the development and
benchmark SDK pins are aligned at 1.45.1. This resolves
[CVE-2026-114603](https://github.com/open-telemetry/opentelemetry-python/security/advisories/GHSA-xcq7-p3pf-p88h)
in upstream `tracestate` header parsing. Both Python locks include the fix, and
wheel upgrades enforce the corrected runtime minimum. Applications with pinned
OpenTelemetry SDK/exporter packages should upgrade that upstream package family
together to versions compatible with API 1.45.1.

Deploy matching SDK, Studio backend and frontend 1.11.0 packages/images. This
minor release includes security maintenance and pins frontend builds and CI to
Node.js 24.22.1,
which fixes CVE-2026-111497 in the Brotli decoder. The production frontend still
serves static assets through Nginx and the backend runs Python.

For local frontend work, run `cd apps/studio && nvm install && nvm use` before
`npm ci`, or select Node 24.22.1 or a newer patched 24.x release with your version
manager. Installation and frontend commands reject unsupported runtimes, even
if dependencies were installed before switching Node. From the repository root,
rebuild custom frontend images with the patched builder:

```bash
docker build --pull -f apps/studio/Dockerfile -t relayna-studio-frontend:1.11.0 .
```

See [frontend setup](studio-frontend.md#development-mode).

The Python locks refresh certifi to 2026.10.19 and multidict to 6.9.1; the frontend
lock refreshes Vite to 8.3.5 and source-map-js to 1.2.2 with compatible tooling
dependencies to resolve the security-audit blockers. No PostgreSQL migration,
SDK/API change, wire-format change or data rewrite is required. Existing freeze
snapshots retain their v1.10.1 baseline because the public perimeter is unchanged.

The SDK wheel also declares `certifi>=2026.10.19` and `multidict>=6.9.1` as
runtime requirements, so a normal pip upgrade replaces older installed versions
without requiring the repository lockfile. The Studio backend inherits these
floors through `relayna>=1.11.0`. Retain normal dependency resolution when
upgrading; `--no-deps` skips these requirements.

### Upgrading to 1.10.1

Deploy matching Studio backend/frontend 1.10.1 images and use the aligned SDK
package. This patch fixes the three findings from the 1.10.0 Studio review:
client-secret fields are masked in current and retained JSON views/validation
errors; managed uploads have a longer bounded deadline and a scoped frontend
proxy body allowance; all supported terminal task aliases freeze the monitor
window at the task's final timestamp.

No PostgreSQL migration or SDK/wire-format change is required. Retain your
existing settings encryption key across replicas and restarts. For initial
setup, use the [complete local and AKS key guide](studio-load-testing.md#set-up-the-settings-encryption-key),
including generation, Secret/Deployment wiring, verification, backup and rotation
limits. Existing stored configurations are sanitized on read; they need no
rewrite. Replace masked credentials with environment references before replanning.
If you use another ingress in front of Studio, align its upload body/timeout
limits with the [managed upload limits](studio-load-testing.md#native-chamber-workspace).

### Upgrading to 1.10.0

Deploy matching SDK, Studio backend and frontend 1.10.0 packages/images. Studio
adds the responsive Monitor workspace with Task explorer, Logs focus and
Investigation layouts, shared event windows, and pod CPU/memory charts. Existing
Overview, configuration and approved-operation flows remain available.

The complete native load-test workspace requires Ampule Chamber 1.11.0. Studio
uses its backend to reach Chamber's private Service; Chamber needs no browser
hostname. When both processes run locally, the backend can use a local
port-forward. An AKS backend cannot reach a port-forward on an operator's laptop.
Older Chamber versions retain their supported approved-operation workflow;
capability negotiation disables unavailable native features.

No new PostgreSQL migration is needed from 1.9.0: UI connection overrides reuse
operator settings at schema revision `0002_load_profiles`. Back up PostgreSQL
and deploy matching Studio images together. For administrator-managed credentials,
provide a stable Fernet `RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY` in the backend
Secret and allow the private Chamber endpoint in the outbound policy. Tokens are
encrypted and hidden from browser responses; retain the encryption key while
saved connections and reviewed runs are needed. Existing deployment URL/token
and ConfigMap profiles remain supported. See [native Chamber setup and
operations](studio-load-testing.md#native-chamber-workspace).

This user-approved minor release intentionally advances the freeze manifests to
1.10.0 and records additive Studio routes. SDK exports, existing API responses,
profile records and broker formats remain compatible. To roll back to 1.9.0,
stop new replicas and restore matching 1.9.0 images without downgrading the
PostgreSQL schema. Existing advanced assessment references require 1.10.0 Studio
and their original Chamber endpoint/key for inspection or cancellation; complete
or cancel active assessments before rollback and preserve connection settings.

Validate credentials, target admission, Kubernetes access, telemetry and cleanup
with a reviewed staging assessment before wider use. Local UI/API validation used
synthetic data and a stubbed execution supervisor.

### Upgrading to 1.9.0

Studio administrators can import complete Kubernetes attach-mode configurations
from saved Chamber plans/runs, review typed OpenAPI inputs and targets, and save
approved service profiles without a ConfigMap edit or restart. Existing
ConfigMap profiles and reviewed load-test plans remain supported. See
[profile import](studio-load-testing.md#import-profiles-as-an-administrator).

Use a maintenance window: stop old Studio backend replicas, back up PostgreSQL,
run `alembic upgrade head` from `studio/backend` with
`RELAYNA_STUDIO_DATABASE_URL` configured, and deploy matching backend/frontend
1.9.0 images. Revision `0002_load_profiles` adds the profile table. Both versions
check their exact schema revision, so do not mix 1.8.x and 1.9.0 backends during
the upgrade. No SDK or broker wire-format change is introduced.

To roll back, stop the new backends, back up any imported profiles, downgrade
Alembic to `0001_studio_postgres`, and restore matching 1.8.x images. Downgrading
removes imported profiles; deployment-configured profiles are unchanged.

### Upgrading to 1.8.2

Deploy matching Studio backend/frontend 1.8.2 images. This patch updates release
metadata and deployment documentation; it does not change runtime behavior or
require a migration from 1.8.1. Version 1.8.1 introduced opt-in shared
operator-token login for sandbox environments; Entra remains the default.
See [authentication](studio-entra-auth.md) and the
[ConfigMap and Secret inventory](studio-load-testing.md#configmaps-and-secrets).

When upgrading Studio from before 1.6.0, complete the PostgreSQL migration below
before starting new replicas. The completed sandbox rollout runs Studio 1.8.1
with Chamber 1.10.0; this documentation release does not redeploy that environment.

### Upgrading to 1.8.0

Studio adds service-specific load testing through Ampule Chamber 1.10.0.
Deploy matching Studio backend/frontend 1.8.0 images. Existing service records,
SDK contracts and broker formats remain compatible; no data migration is needed
for this feature. The release intentionally advances the approved freeze
manifests for the new Studio routes and page.

Configure the internal Chamber endpoint/token and approved service profiles.
Profiles may import typed request fields from service OpenAPI 3.0/3.1 documents;
standard Relayna SDK endpoints are excluded. Operators still pin execution
targets, lifecycle mappings and load limits. See
[Studio load testing](studio-load-testing.md) for configuration and rollout.

For the sandbox rollout, build and deploy Studio with the Azure pipelines first,
then upgrade Chamber to 1.10.0 and connect the backend using its internal service.
Keep the integration disabled until the profiles, credentials and AKS permissions
are ready. Validate a reviewed staging test before wider use. No deployment is
performed by this release preparation.

### Upgrading to 1.7.0

Relayna 1.7.0 upgrades the SDK to redis-py 8 with RESP3 and adds explicit
`standalone` and `cluster` Redis modes. `standalone` remains the default and is
correct for either one writable Redis server or one primary with replicas when
the configured endpoint always resolves to the current writable primary. Use
`cluster` only when Redis Cluster is enabled, all 16,384 hash slots are
assigned, and every node address advertised by the cluster is reachable from
each Relayna process. See [Redis Topologies](redis-topologies.md) for the full
selection checklist, AKS service requirements, verification commands, and
failure symptoms.

The SDK's Redis key layout intentionally changed to cluster-tagged keys. Relayna
does not read or migrate pre-1.7.0 SDK keys. Upgrade all Relayna processes that
share a Redis namespace together and use an empty namespace, or retain the old
namespace only for a defined rollback window and discard it afterward. Do not
run pre-1.7.0 and 1.7.0 SDK processes against the same logical namespace.

The SDK, Studio backend, and Studio frontend versions and production-freeze
manifests advance together to 1.7.0. Studio's PostgreSQL and Redis persistence
roles introduced in 1.6.0 are otherwise unchanged.

### Upgrading to 1.6.0

Studio now requires PostgreSQL in addition to Redis. Use a maintenance window:
stop all old Studio writers, back up both stores, upgrade the Alembic schema,
validate and import the retained `studio:*` Redis state, then start only 1.6.0
replicas. Mixed old and new Studio versions are unsupported. Existing HTTP and
frontend response shapes remain stable, while `/livez`, `/healthz`, `/readyz`,
and the administrator audit route are additive.

The Relayna SDK and deployed service runtimes remain Redis-only and do not need
PostgreSQL. Upgrade the SDK, Studio backend, and Studio frontend together to
keep the shared release line aligned. See
[Studio PostgreSQL and Redis persistence](studio-persistence.md) for exact
migration, backup, validation, and rollback commands.

### Upgrading to 1.5.0

Studio now requires Microsoft Entra login. Before upgrading the control plane,
register its callback URI on the existing Entra application, create and mount a
Studio-specific certificate/private key, and configure a bootstrap
administrator using matching email and object-ID allowlists. Active readonly
members can inspect Studio data; only administrators can mutate data or manage
access. Existing service data is not migrated. See
[Studio Entra authentication](studio-entra-auth.md) for the complete rollout,
bootstrap, route-policy, and rollback considerations.

Upgrade the SDK, Studio backend, and Studio frontend together to keep the
shared release line aligned.

### Upgrading to 1.4.32

No data, wire-format, configuration, or public-API migration is required.
Relayna `1.4.32` reuses the OpenTelemetry API's standard module-level proxy
tracer and stateless carrier adapters instead of repeating provider/tracer
lookup and wrapper allocation for every producer and consumer span. Tracing,
sampling, W3C/baggage propagation, span names and relationships, attributes,
exception/status handling, async context isolation, and exporter delivery
remain enabled and unchanged.

Upgrade the SDK, Studio backend, and Studio frontend together to keep the
shared release line aligned.

### Upgrading to 1.4.31

No data, wire-format, configuration, or public-API migration is required.
Relayna `1.4.31` snapshots delivered AMQP metadata once per task, workflow, or
aggregation message and reuses that internal immutable value for tracing,
contexts, retries, dead-letter publication, metrics, and observations. Header
precedence and defaults, handler and middleware-visible context values,
acknowledgement/rejection/requeue behavior, and task/status/workflow semantics
remain unchanged.

Upgrade the SDK, Studio backend, and Studio frontend together to keep the
shared release line aligned.

### Upgrading to 1.4.30

Relayna `1.4.30` moves AMQP JSON transport encoding and parsing to Pydantic
Core. Review the
[JSON transport migration after v1.4.29](json-transport-migration.md) before
upgrading because raw outbound bytes, invalid UTF-8 handling, and some input
coercions intentionally differ from `1.4.29`.

The consumer performance change needs no migration. `TaskConsumer` skips
resource sampling and successful-path observation construction only when no
observation sink or metrics recorder could receive the result. Observation-only,
metrics-only, combined instrumentation, OpenTelemetry tracing, acknowledgements,
retries, lifecycle statuses, and message contracts keep their existing
behavior. Upgrade the SDK, Studio backend, and Studio frontend together to keep
the shared release line aligned.

### Upgrading to 1.4.29

No data migration or API change is required. Studio `1.4.29` makes
Prometheus-backed pod ownership joins resilient to duplicate
`kube_pod_labels` series and adds bounded upstream error diagnostics. Upgrade
the SDK, Studio backend, and Studio frontend together to keep the shared release
line aligned.

### Upgrading to 1.4.28

The service-event Redis feed storage changes in `1.4.28`. The SDK keeps the
same `GET /events/feed` contract but does not read or migrate the old
`{prefix}:feed` list. Upgrade all SDK instances sharing a service-event prefix
together. The indexed feed begins with new post-upgrade events; after the old
instances are drained, the legacy list can be deleted. See
[Redis Keys](redis-keys.md#service-event-feed) for the v2 keys.
