# Studio admin experience and performance

This living ExecPlan follows `/Users/jobz/Works/relayna/PLANS.md`.

## Purpose / Big Picture

Implement the 21 findings from the September 12 Studio source and Chrome audit. Administrators should retain environment scope, find tasks directly, distinguish missing telemetry from failures, safely operate failed tasks, and use the interface on narrow screens. Backend work must keep large searches and upstream queries bounded. Ampule Chamber integration is explicitly deferred by the user.

## Progress

- [x] (2026-09-12) Read compatibility and verification skills, inspect audit, create `codex/studio-admin-experience-performance` from clean main.
- [x] (2026-09-12) R1/R3/R4/R5: correct failure pagination, database search, bounded upstream queries and metrics work.
- [x] (2026-09-12) R2/R6/R7/R8/R10: environment navigation, request ownership, freshness, search execution and accurate badges.
- [x] (2026-09-12) R9/U3/U4/U5: safe failure actions, availability states, labels and capability checks.
- [x] (2026-09-12) R11/R12/U1/U2/U6/U7/U8/U9: progressive telemetry, setup, saved queries, shared windows, responsive layout and copy.
- [x] (2026-09-12) Run full Python verification, frontend tests/build, and Chrome verification with local services.

## Surprises & Discoveries

Repository guidance names the historical v1.4.30 production freeze; current manifests and latest release tag are v1.7.0. Both require preserving the public perimeter. The local audit stack uses real PostgreSQL, Redis and Studio authentication with synthetic service and observability fixtures.

## Decision Log

2026-09-12: Preserve current API exports, route response models and persisted record shapes; implement fixes with private helpers and existing fields. Compare against v1.7.0, retain existing opaque cursor decoding where possible. No freeze manifest changes are planned. The user approved all 21 Studio findings and excluded Chamber integration.

## Outcomes & Retrospective

All 21 findings have implementation coverage. Chrome desktop/mobile checks and frontend verification are complete. Public exports, route response models, persisted records and freeze manifests are unchanged. Chamber was not modified.

The Python verification stack passed (686 SDK tests and 263 backend tests; environment-dependent skips remain in the standard suite). All 15 real PostgreSQL/Redis integration tests passed against an isolated database and Redis database 14. Frontend tests passed at 108 tests, including deferred telemetry, cursor merging and in-flight read coalescing. Chrome confirmed a 390px document width at a 390px viewport, successful telemetry connection probes, retained environment scope in search and task links, disabled unsupported mutations, and readable 551px task date fields.

Performance limits are enforced in code and tested; this work does not claim production latency or throughput measurements. Environment federation uses existing per-service APIs with opaque browser cursors. Saved searches are browser-local. Bulk investigation is capped at 20 and processed sequentially. Task summary still uses the existing backend bundle contract; cross-service joins and optional external telemetry are deferred without introducing a new summary endpoint.

## Context and Orientation

The React control plane lives in `/Users/jobz/Works/relayna/apps/studio/src`. Its Python API lives in `/Users/jobz/Works/relayna/studio/backend/src/relayna_studio`. The SDK under `src/relayna` supplies service contracts but is not intended to change. Failed tasks are retained service errors; the DLQ is the dead-letter queue. Studio federates reads across services and stores searchable task projections in PostgreSQL.

## Compatibility Boundary

Latest released tag: v1.7.0. Keep exported API symbols, request/response shapes, SDK contracts and persisted documents unchanged. Cursor contents are opaque but existing cursor inputs must continue to decode. New SQL query paths must match existing filtering, ordering and expiration semantics. Database indexes, if needed, require reversible migrations. Do not alter freeze manifests to satisfy tests.

## Plan of Work

First fix federation pagination and bounded upstream work in `federation.py` and `metrics.py`, then push PostgreSQL search filtering/pagination into `database.py` through an internal store optimization in `search.py`. Add regression coverage for continuation, partial failures and ordering. Next improve shared frontend request/navigation primitives and task search. Then address failed-task operations and service/task telemetry loading. Finish layout, labels, setup states and focused usability tests before full verification and Chrome checks.

## Concrete Steps

Work from `/Users/jobz/Works/relayna`. Run focused tests while editing, then `bash .codex/skills/code-change-verification/scripts/run.sh`, `make -C apps/studio test`, and `make -C apps/studio build`. All must exit successfully. Restart the local backend after Python edits and use the existing Chrome session at `http://127.0.0.1:5173` to verify desktop and 390px layouts, registration focus, scoped navigation, automatic search, provider empty states and failure actions.

## Validation and Acceptance

Tests must demonstrate complete cross-service pagination, bounded concurrency, correct database keyset filtering and stale-response protection. Chrome must show no horizontal overflow at 390px, visible registration title and focused editor, persistent filter labels, actionable unavailable states, disabled unsupported operations and readable time inputs. Review every R/U finding against the final implementation and record residual limits here.

## Idempotence and Recovery

Local fixtures are disposable and can be restarted. Keep changes on the dedicated branch and preserve unrelated user work. Do not commit, push, or deploy without a further request. Changes can be reviewed and reverted per file; no production data migration is planned.

## Finding Coverage

| Finding | Implemented behavior |
| --- | --- |
| R1 | Failure pagination tracks each service cursor and consumed position, including short upstream pages. Legacy offset cursors remain readable. |
| R2 | Environment selection is controlled by the URL, retained through links, and applied through scoped service reads. |
| R3 | PostgreSQL filters expiration, fields and time ranges and returns only a keyset page plus one row. |
| R4 | Failure reads use bounded concurrency and per-read deadlines, preserving partial errors. |
| R5 | Metric groups run concurrently with a limit of four and at most 1,200 time points; frontend reads are bounded and deduplicated. |
| R6 | Requests time out and honor cancellation; request versions protect page state, and hidden tabs pause pod/registry polling. |
| R7 | Registry freshness is exposed and refresh failures remain visible; streams show connection/reconnection state. |
| R8 | URL and global searches execute automatically, with URL-driven back/forward state and stale-response guards. |
| R9 | Mutation locks, error reporting, signed-in attribution, detailed confirmations and bounded bulk review. |
| R10 | Service health count appears on Overview, not as a failed-task count. |
| R11 | Cross-service joins require an explicit action; external logs, metrics and trace reads wait for opened sections. |
| R12 | Provider guidance, saved searches, saved-provider query checks, shared windows, and one post-health registry reload. |
| U1 | Header reflows at narrow widths, retaining scope and avoiding overflow. |
| U2 | Registration scroll margin and focus move the editor below the header. |
| U3 | Failed reads produce unavailable/partial states with service details and retry. |
| U4 | Persistent filter labels and named evidence boxes in failures and DLQ. |
| U5 | Unsupported mutations and broker inspection are disabled with explanations. |
| U6 | Gateway/email setup and service metadata/configuration are collapsible; metrics follow the service header. |
| U7 | Missing providers show concise setup states and disabled reload controls. |
| U8 | Task time inputs use full column width and shared windows identify the browser timezone. |
| U9 | Primary page descriptions explain administrator tasks; implementation details are disclosed separately. |

## Chrome Evidence

Screenshots are stored outside the repository at `/Users/jobz/.codex/visualizations/2026/09/12/01a094e4-13ca-72b3-aae6-76271778a90e/studio-audit/`. `21-services-mobile-after.png` records the 390px layout, `25-no-provider-after.png` records setup states, and `26-register-after.png` records registration focus and header clearance (editor top 110px; header bottom 82px). `23-failure-detail-after.png` and `24-task-windows-after.png` capture failure and task investigation states. Synthetic upstream data was used; no production actions were taken.

New, initially untracked repository files are the scoped-link helper, scoped-results helper and tests, and this ExecPlan. They are part of the deliverable and must be included when committing this branch.
