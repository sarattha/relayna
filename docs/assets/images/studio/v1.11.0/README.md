# Studio 1.11.0 screenshot record

These images show the production frontend bundle for coordinated release
1.11.0, captured on 2026-10-25. They replace the historical operator-tour images
in [Studio Frontend](../../../../studio-frontend.md#studio-operator-tour).

The interface uses the approved Mulberry & Linen semantic tokens, Slim Leaf
artwork, and local Portico 0.101 upright/italic font files. Light is the default;
the dark capture uses the header's sun/moon control. Images are browser captures,
not generated mockups.

## Capture setup

- Build with Node 24.22.1 using `make -C apps/studio build`, then serve with
  `make -C apps/studio preview` at `localhost:5174`.
- Use synthetic API responses based on the frontend integration-test fixtures
  in `apps/studio/src/App.test.tsx`. The example administrator, hostnames,
  services, tasks, timestamps and logs are fixtures, not production records.
  The event source is simulated as connected; captures do not demonstrate an
  external Loki, Prometheus, Tempo or broker connection.
- Use Chromium at 1440 × 1000 CSS pixels for desktop and 390 × 1000 for mobile,
  reduced motion, loaded local fonts and a fixed example clock. Full-page
  captures extend beyond the viewport. Registration images isolate the actual
  form/details element; the sticky header is placed in normal document flow
  during those two captures to avoid obscuring the component.
- Exercise the real theme control and routing. Show 50 tasks and 200 log entries
  in Task explorer; scroll to the final task for the dark capture.
- Inspect each selected image for clipped controls and overlapping text.

## Images

| Image | View | Pixels |
| --- | --- | --- |
| [Overview](overview.png) | Operational overview, light | 1440 × 1000 |
| [Registry](services-registry.png) | Services and search, light | 1440 × 1054 |
| [Registration](service-registration-basic.png) | Unsaved example draft | 1338 × 816 |
| [Log configuration](service-registration-observability.png) | Expanded Loki group | 1338 × 1198 |
| [Service detail](service-detail.png) | Service overview and telemetry controls | 1440 × 1000 |
| [Task search](task-search.png) | Exact-ID search result | 1440 × 1000 |
| [Failed tasks](failed-tasks.png) | Failure filters and evidence entry | 1440 × 1000 |
| [Topology](topology.png) | Service-scoped workflow | 1440 × 1000 |
| [DLQ](dlq.png) | Indexed failure messages | 1440 × 1000 |
| [Light monitor](monitor-explorer-light.png) | Task explorer, first tasks | 1440 × 1350 |
| [Dark monitor](monitor-explorer-dark.png) | Task explorer, last task reachable | 1440 × 1350 |
| [Mobile monitor](monitor-mobile.png) | Bounded task list above logs | 390 × 2319 |

## Verification

The final production build was checked in both themes. Desktop Task explorer's
608-pixel list reaches the sidebar bottom with a zero-pixel gap; the final task
can be scrolled into view and focused. Mobile has a bounded 240-pixel list and
no page-level horizontal overflow at 390 pixels.

ANSI foregrounds were measured from actual rendered `LogMessage` spans on the
muted log surface for codes 30–37 and 90–97. Minimum contrast is 5.03:1 in light
and 6.34:1 in dark mode. The regular and italic Portico faces, theme persistence,
keyboard switching and responsive headers were also verified during this branch's
visual checks. These captures document presentation, not backend integration
coverage.
