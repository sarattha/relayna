# Studio Monitor verification — 3 October 2026

Computer Use exercised the React app in Chrome against a loopback Vite preview
on port 18992 and a temporary synthetic HTTP API on port 18991. The fixture
contained 120 tasks, 50-task cursor pages, four task events, 14 log entries with
a long stack trace, and CPU samples. It did not connect to a live service,
Loki, Prometheus, Redis, or Kubernetes. Fixture data is not part of production.

Confirmed service and task Monitor entry, all three layout buttons, retained
task/log details, next/previous task pages, service versus selected-task scope,
empty 15-minute service window and populated 24-hour window, Overview diagnostics,
Configure, selected-event ±30-second logs/CPU, and responsive stacking at
390 × 844. Expand, Tab, Escape, and restored focus were exercised through the UI.
Automated tests additionally cover filters, scroll retention, custom range
validation, corrupt preferences, slow reads, stale responses, unavailable
providers, refresh failures, and clipboard success/failure feedback.

Screenshots below contain only the application viewport, not unrelated browser
tabs. Source comparisons place the approved mockup on the left and the actual
React app on the right. The mockup's fake counts and broad task search are
adapted to the existing API's bounded pages and exact task-ID search. Logs focus
uses the same detached inspector as the other modes. CPU uses the API's cores
unit. These are functional adaptations, not claims of exact pixel matching.

- [Explorer desktop](explorer-desktop.jpg) · [source comparison](explorer-comparison.jpg) · [reader detail](explorer-detail-comparison.jpg)
- [Logs desktop](logs-desktop.jpg) · [source comparison](logs-comparison.jpg) · [reader detail](logs-detail-comparison.jpg)
- [Investigation desktop](investigation-desktop.jpg) · [source comparison](investigation-comparison.jpg) · [reader detail](investigation-detail-comparison.jpg)
- [Mobile task entry](mobile-task-top.jpg) · [task explorer](mobile-explorer.jpg) · [logs](mobile-logs.jpg) · [investigation](mobile-investigation.jpg)
- [Mobile chart correction, before/after](mobile-chart-comparison.jpg)
- Service-control spacing follow-up: [user screenshot above / fixed UI below](service-controls-spacing-comparison.jpg), [desktop](service-controls-desktop.jpg), [390×844 mobile](service-controls-mobile.jpg). Computer Use confirmed 18px section spacing, bottom-aligned selector/button, and mobile wrapping with a 12px row gap. The supplied screenshot is cropped, so the comparison is qualitative. Re-ran frontend tests (148 passed) and production build (passed).

Validation results:

- Pod memory follow-up: 151 frontend tests and production build passed. [Desktop](pod-resources-desktop.jpg) and [390×844 mobile](pod-resources-mobile.jpg) Computer Use evidence shows matching event markers/time windows, CPU in cores and memory in MiB. A temporary fixture supplies memory bytes; production uses the existing `memory_usage` group. Automated coverage includes independent missing-memory behavior and gaps/invalid samples.
- Final consolidated frontend suite: 233 tests passed, including the production-freeze checks.
- Production build and TypeScript checking passed.
- Mandatory verification script passed in sequence: SDK format/lint/typecheck,
  686 tests passed and 9 skipped; backend format/lint/typecheck, 495 tests passed,
  including real PostgreSQL integration tests. SDK coverage is 97.94%; backend
  coverage is 98.13%. Native Ampule backend additions are documented in the
  [Ampule ExecPlan](../../internal/ampule-studio-api-execplan.md).
- The initial frontend coverage gap is resolved by 67 additional Studio regression
  tests. Whole-source coverage under CI's Node 20 is 98.04% statements, 89.02%
  branches, 99.36% functions, and 98.67% lines. Required gates remain
  98%/89%/98%/98%; no coverage thresholds or exclusions were relaxed. SDK and
  frontend coverage are now enforced in CI. Studio PR #131 and companion Chamber
  PR #43 both have green CI; Chamber has 394 tests and 97.80% CI coverage.
- Console inspected through Chrome UI: an unload-policy message points to a
  Chrome extension. A connection-to-receiving-end message also appeared (consistent
  with extension messaging; its source was not conclusively resolved). No React
  render failure or failed fixture API response was observed during the flows.

Remaining acceptance: exercise the workspace against staging Loki/Prometheus
latency, retention, and real task volumes. The loopback fixture verifies frontend
layout and interaction, not production provider performance.
