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
pip install https://github.com/sarattha/relayna/releases/download/v1.13.0/relayna-1.13.0-py3-none-any.whl
```

## Install the source distribution

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.13.0/relayna-1.13.0.tar.gz
```

## Build artifacts locally

```bash
uv build
```

Expected artifacts:

- `dist/relayna-1.13.0.tar.gz`
- `dist/relayna-1.13.0-py3-none-any.whl`

## Versioning policy

The SDK, Studio backend, and Studio frontend share one stable SemVer release
line. The documented SDK API, documented Studio backend API, and
frontend/backend Studio contract follow semantic versioning. Undocumented
internals may change outside of SemVer guarantees.

### Upgrading to 1.13.0

Deploy matching SDK, Studio backend and frontend **1.13.0** packages/images.
The backend now automatically applies its bundled Alembic migrations during
application startup, like Relayna Gateway. This works with Docker, Compose,
Kubernetes, and the Python entry point. A separate migration job is optional.
PostgreSQL advisory locking serializes replicas and explicit Alembic commands;
failed migrations roll back and abort startup before requests or workers run.
Already-current databases are a no-op; unknown/newer revisions fail rather than
downgrade. No new schema revision is introduced relative to 1.12.0: both expect
`0003_recovery`. Public contract freeze manifests remain at v1.12.0.

Before deploying, back up PostgreSQL and Redis, retain settings encryption keys,
and ensure the database role owns Studio schema objects or has the required DDL
privileges. Budget startup probes for migration work and advisory-lock waits.
Send traffic only after `/readyz` passes PostgreSQL, exact-schema, and Redis
checks. When upgrading from an older schema head, stop old replicas first and
use the maintenance-window guidance below; automatic migration does not make
mixed schema-version backends compatible. Legacy Redis backfill remains
explicit; startup never imports those records.

To return to 1.12.0 while the database remains at `0003_recovery`, stop 1.13.0
backends and restore matching older packages/images without downgrading the
database. If startup upgraded an older schema, stop every new backend before
following the coordinated schema rollback below or restoring a compatible
backup. A still-running 1.13.0 backend would reapply pending upgrades on restart.

Publication follows the existing v1.12.0 template: merge the verified PR, tag
that main commit `v1.13.0`, and let the tag workflow publish the SDK wheel/sdist,
signed backend/frontend images, SBOMs/provenance, and changelog-derived GitHub
Release notes. See [Studio persistence](studio-persistence.md) for migration
commands, privileges, backups, and recovery.

### Upgrading to 1.12.0

Deploy matching SDK, Studio backend and frontend **1.12.0** packages/images.
This minor release combines Incident Inbox, Recovery Center, Queue & Worker
Cockpit, Reliability Goals & Alert Routing, Integration Doctor & Studio Health,
and the Manrope design-system update. The approved additive contracts are
recorded by the v1.12.0 freeze labels; all inherited approved entries remain
intact. Existing SDK collectors/response fields and broker/Redis wire formats
remain compatible. This combined release **requires a Studio database upgrade**.

#### Database upgrade from 1.11.0

Use a maintenance window. Pause/stop authorized recovery dispatch if upgrading
an intermediate feature build, reconcile in-flight/unknown acceptance, and stop
all old backend replicas. Back up PostgreSQL and Redis, record image digests,
and retain settings encryption keys and provider credentials. With
`RELAYNA_STUDIO_DATABASE_URL` set, run from the repository:

```bash
make -C studio/backend db-current
make -C studio/backend db-upgrade
make -C studio/backend db-current
```

The published 1.11.0 schema is `0002_load_profiles`. The upgrade chain is
`0002_load_profiles -> 0003_incident_inbox -> 0003_recovery`; the final current
revision must be `0003_recovery`. The released 1.12.0 image requires an explicit
migration; newer backends with startup migration support automatically apply
their bundled chain. All versions check the exact schema head at startup and
readiness. Do not mix old and
new backend replicas across these schema expectations. Incident identity,
ownership/corrections/history and Recovery plans/claims are durable PostgreSQL
data. Existing load profiles and earlier tables remain intact. See
[persistence](studio-persistence.md#release-1120-schema-upgrade-and-rollback).

Start only matching 1.12.0 backend/frontend images after migration, then verify
`/readyz`, login and scoped feature access before restoring traffic. `/livez` and
`/healthz` report process liveness; `/readyz` establishes PostgreSQL/Redis/schema
readiness, not fresh upstream data or healthy providers. Health and Doctor show
those independent evidence states. SDK service runtimes remain Redis-only.

#### Feature setup and operational limits

- [Incident Inbox](studio-backend.md#incident-inbox) retains owner/lifecycle and
  manual grouping corrections separately from live failure evidence. Include
  incident history in backups and retention planning.
- [Recovery Center](studio-recovery-center.md) requires retained eligible
  failures and existing retry capability/authorization. Batches have per-plan
  pacing, not a global service rate limit. Accepted or ambiguous dispatch is not
  proof of recovery; unknown acceptance is never automatically resent. Reports
  require fresh terminal status, with the documented same-ID attribution limits.
- [Queue & Worker Cockpit](studio-backend.md#queue-worker-cockpit-reads) is
  read-only. Built-in AMQP observations provide ready depth and consumer count;
  unacknowledged depth, oldest age and matching rates require an appropriate
  source. Missing data disables drain estimates. Task-boundary activity cannot
  establish periodic liveness or prove a worker is missing.
- [Reliability goals](studio-reliability.md) require a registered, correctly
  scoped Prometheus provider. Deploy exported Prometheus rules and Alertmanager
  routing yourself. Configure the dedicated webhook credential and shared email
  transport for receipts/delivery. Missing-worker goals need an external
  service-wide periodic pulse aggregate while idle or busy plus a fresh source
  watermark; they do not detect one absent member of a worker pool. Completion/latency
  objectives need durable mature-cohort aggregates. SDK task activity and handler
  duration are not substitutes. Absent evidence remains missing, never healthy.
- [Health and Doctor](studio-backend.md#integration-doctor-and-studio-health)
  distinguish configuration from successful probes and empty from unavailable
  queries. Administrator live checks do not actively probe metrics or traces in
  this version. A scheduled/running loop is not evidence of a successful pass.

Studio and docs now self-host official Manrope 4.505 and refreshed 0.4.0 brand
artwork, with SIL OFL 1.1 distributed alongside the font. Code roles also use
Manrope under the current design system, preserving whitespace and structural
table alignment. Studio auth gates always use light while the workspace keeps
its saved choice. Docs start light independent of system appearance and retain
an explicit saved toggle. Historical 1.11.0 screenshots remain labeled as such.

#### Recovery-only and full rollback

Stop every new backend/recovery worker before any downgrade. Export Recovery
reports and Incident history, back up both stores, and reconcile accepted or
unknown dispatches with the service: stopping Studio never undoes published
work or authorizes blind retries. Recovery history/reservations are the record
that prevents duplicate plan selection.

For a **Recovery-only** rollback, run in `studio/backend`:

```bash
uv run alembic downgrade 0003_incident_inbox
```

This drops only `studio_recovery_plans` and `studio_recovery_claims`, retaining
Incident Inbox and earlier tables. Deploy only a deliberately built intermediate
backend expecting `0003_incident_inbox`. That intermediate build is not the
published 1.11.0 release, and a 1.12.0 backend will reject that schema.

For rollback to **published 1.11.0**, after the same shutdown/export safeguards:

```bash
uv run alembic downgrade 0002_load_profiles
```

This additionally drops Incident Inbox state, corrections/history and its history
trigger/function, while preserving load profiles and earlier Studio data. Restore
matching 1.11.0 SDK/backend/frontend packages/images only after verifying that
revision. Restore a compatible backup if lost feature records are needed;
re-upgrade alone cannot recover dropped data. Do not use `downgrade base` as a
rollback shortcut. Reliability goals/receipts use older operator settings, but
1.11.0 does not expose them: disable exported rules/Alertmanager callbacks before
rollback and preserve their configuration for a later upgrade. Keep the settings
encryption key, and do not treat a saved browser theme as a server migration.

Publication follows the same v1.11.0 workflow: the coordinator audits the merged
stack, tags the verified main commit `v1.12.0`, and the tag workflow builds the
SDK wheel/sdist, signed Studio images, SBOMs/provenance and changelog-derived
GitHub Release. Preparation does not mean those artifacts are already published.

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
