# Studio load-testing verification — 14 September 2026

Computer Use exercised Chrome against a loopback-only Vite preview on port
18992 and a temporary FastAPI fixture on port 18991. The fixture mounted the
**actual Studio load-testing adapter**, fakeredis and a synthetic Chamber HTTP
transport. The profile used the documented translation input schema. No
Kubernetes credentials or real target were used; no traffic was generated.

Confirmed typed text, enum and bounded-number controls; plan creation and
review; starting the selected plan; live runner output; exact task links;
service log entries; CPU/memory/pod charts; matching pod-color legends; and
responsive stacking at 390 pixels, cancellation, and retained cancellation after a page reload. Browser console measurement returned
`{"viewport":390,"document":390}`. Browser messages were an extension's unload
policy warning and the existing missing favicon, not application errors.

- [Desktop review](review-desktop.png)
- [Mobile pod metrics with overflow measurement](metrics-mobile.png)

The actual Chamber 1.10.0 `ChamberApplication.plan` accepted the generated
Relayna JSON configuration against its local sample repository. This validates
planning, not Kubernetes execution. That check corrected the example's agent
mode to `off`.

Automated checks:

- Full code-change-verification script: SDK formatting/lint/types/tests and
  Studio backend formatting/lint/types/tests all passed in sequence.
- SDK: 686 passed, 9 skipped.
- Studio backend: 337 passed, 15 skipped (external PostgreSQL/Redis integration
  infrastructure was not configured).
- Studio frontend: 119 passed; production build passed.
- New tests cover schema constraints, nested typed forms, all supported request
  encodings, fixed multipart fixtures, load limits, invalid configuration,
  forbidden experiment overrides, service/environment isolation, missing
  services, administrator/CSRF enforcement, retry idempotency, retained status,
  cancellation, URL restoration, task links and telemetry errors.

Remaining deployment acceptance: configure the Chamber internal endpoint and
operator token, mount accurate per-service profiles and repository/file
fixtures, supply Chamber's AKS context/permissions and confirm the existing
Studio telemetry selectors target those workloads. Then run a small approved
staging test and inspect task completion, Loki entries and Prometheus samples.

OpenAPI follow-up: Computer Use exercised the actual adapter with a synthetic
service OpenAPI document on ports 18993/18994. Confirmed imported request fields,
schema provenance, switching a nullable priority to a typed integer, and
successful plan review. The fixture included SDK paths; the UI offered only the
approved translation operation. Automated tests verify SDK path/tag exclusion,
reference resolution, unsafe/unsupported documents and stale schema rejection.

- [OpenAPI-derived form (synthetic preview)](openapi-form.png)

Release 1.8.0 verification: 398 backend tests passed with temporary local
PostgreSQL/Redis and 98.13% aggregate coverage; both new load-testing modules
reached 100% statement coverage. SDK checks (686 passed), frontend tests
(119 passed), production build and strict documentation build passed. The
PostgreSQL upgrade/downgrade/re-upgrade cycle passed.
