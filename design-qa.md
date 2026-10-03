# Studio Monitor design QA

**Findings**

No actionable P0/P1/P2 findings remain after the final source/implementation and
mobile comparisons. Earlier P2 findings and fixes are recorded below. This is
frontend design acceptance with a synthetic API, not live telemetry acceptance.

**Visual truth and captures**

Approved sources are under
`/Users/jobz/.codex/generated_images/01a0fcee-5428-7012-a85d-703f18db5f39/`:

| Mode | Source image | Implementation | Combined comparison |
| --- | --- | --- | --- |
| Task explorer | `exec-0a935613-e1fa-4e38-b38c-3959dde8dbcf.png` | [Explorer](docs/qa/studio-monitor-workspace/explorer-desktop.jpg) | [Source left, app right](docs/qa/studio-monitor-workspace/explorer-comparison.jpg) |
| Logs focus | `exec-37658ce2-ffc3-4cbd-9c3d-ab29a72924fd.png` | [Logs](docs/qa/studio-monitor-workspace/logs-desktop.jpg) | [Source left, app right](docs/qa/studio-monitor-workspace/logs-comparison.jpg) |
| Investigation | `exec-ae94a09b-fba9-40ac-b896-c9f2775ed4a8.png` | [Investigation](docs/qa/studio-monitor-workspace/investigation-desktop.jpg) | [Source left, app right](docs/qa/studio-monitor-workspace/investigation-comparison.jpg) |

All source images are 1487 × 1058 pixels. Source density is unspecified; uniformly
scale width to 1440 and crop the last 25 pixels of scaled height to 1000. Actual
CSS viewport is 1440 × 1000. Computer Use captures the 3024 × 1722 desktop;
crop the application rectangle `(94,134)-(1822,1334)`, then downsample
1728 × 1200 to 1440 × 1000. Chrome uses DPR 2 with fit-to-window emulation;
the effective capture scale is 1.2 pixels per CSS pixel. Each combined comparison
is 2880 × 1000, without browser chrome.

State: light theme, synthetic administrator, Orders API in production,
order-7842 selected and failed, long payment-timeout details open, Wrap enabled.
Explorer uses task lifetime and paused refresh; Logs focus enables five-second
refresh as in its source. Investigation selects the failure event and shares
±30 seconds with CPU. Fixture failure is 14:32:09 rather than the source's
14:32:08; sample values, timestamps, event counts, and fetch dates differ.
These are state-equivalent comparisons, not exact record or pixel equivalence.

Readable focused inputs were also opened with source and implementation together:
[Explorer reader/inspector](docs/qa/studio-monitor-workspace/explorer-detail-comparison.jpg),
[Logs reader/inspector](docs/qa/studio-monitor-workspace/logs-detail-comparison.jpg),
and [Investigation reader/inspector](docs/qa/studio-monitor-workspace/investigation-detail-comparison.jpg).
Each retains a corresponding table/detail region at the normalized density;
independent crop origins account for the explicit shared controls above the app.

Mobile CSS viewport is 390 × 844; crop `(602,134)-(1312,1670)` from the desktop
capture and downsample 710 × 1536 to 390 × 844. Effective scale is approximately
1.82 with DPR 2 and fit-to-window emulation. The approved visual targets are
desktop only; mobile captures verify responsive behavior rather than fidelity to
a nonexistent mobile mockup.

**Comparison history**

| Iteration | Finding and impact | Fix and recaptured evidence |
| --- | --- | --- |
| 1, blocked | [P2] Existing large service heading/card and control spacing crowded the reader compared with the compact source. | Removed monitor header elevation, reduced heading/gaps and log-row padding. [Initial paired capture](docs/qa/studio-monitor-workspace/explorer-compare-before.jpg), final Explorer pair above. |
| 2, blocked | [P2] Tall event cards and oversized investigation canvas pushed CPU below the useful desktop region. | Compact two-column event cards, bounded log area, responsive chart width. [Intermediate pair](docs/qa/studio-monitor-workspace/investigation-compare.jpg), final Investigation pair above. |
| 3, blocked | [P2] Sticky global header consumed mobile reader space; separate Source column could crowd messages. | Header scrolls away on mobile; source moves into the message cell; controls wrap with 44px targets. [Mobile reader](docs/qa/studio-monitor-workspace/mobile-logs.jpg). |
| 4, blocked | [P2] Five time labels overlapped at 390px, harming interpretation of the selected CPU window. | Three ticks below 500px chart width. [Same-state before/after input](docs/qa/studio-monitor-workspace/mobile-chart-comparison.jpg), visibly distinct first/selected/last labels. |
| 5, blocked | [P2] Long error details in Explorer/Logs focus extended below the 1000px viewport. | Bound the desktop log reader when the inspector opens; keep a separately scrollable stack/fields panel. Final Explorer/Logs pairs and focused inputs show the error, copy action, and footer in view. |
| 6, passed | Reopened all three final combined inputs and focused regions; inspected mobile entry/list/log/CPU captures. | No remaining overlap, inaccessible persistent control, broken source hierarchy, or unreadable axis labels in tested states. |

**Required fidelity surfaces**

- Fonts/typography: source appears to use a neutral sans; app retains its existing
  Inter/ui-sans stack, 30px desktop service heading (25px mobile), 17px section
  headings, and 12px monospace log rows with 1.65 line height. Text hierarchy,
  weights, alignment and wrapping were checked in focused inputs. Source font
  identity is not specified, so exact font equivalence is not claimed.
- Spacing/layout: preserve top navigation, workspace tabs, dedicated mode/scope
  controls, split Explorer, full-width Logs, timeline/log split and CPU strip.
  Desktop pane is adjustable from 20–40%; mobile stacks. Shared task identity and
  explicit window controls add a row compared with Explorer's source. Bounded
  readers keep controls and long-error evidence usable with many tasks.
- Colors/tokens: Color Designer composed ivory `#FFF2DD`/`#FFFCF7`, mint
  `#E7F3F0`, border `#C7D8D3`, text `#1F2F30`/`#486062`, teal `#0A6261`,
  action orange `#B8531C`, red `#A83C28`, amber `#8A5700`, green `#176B4F`.
  Soft feedback uses composited semantic fills. Approximate contrast on the
  surface: main text 13.61:1, muted 6.57:1, teal 6.30:1, orange 4.71:1,
  red 5.66:1. Labels accompany severity colors. Borders/shadows and warm-to-mint
  canvas retain the source direction.
- Assets/image quality: reuse the actual bundled Relayna PNG and existing
  StudioIcon library; the mark remains sharp and correctly proportioned. No
  screenshot is used as app UI, no fake avatar is added, and no decorative raster
  asset was replaced with drawn shapes. CPU SVG is a data chart.
- Copy/content: Overview, Monitor, Configure and all three mode names match.
  Labels describe exact-ID search, bounded entries, scope, local timezone and
  freshness accurately. Dynamic values come from the existing API. No invented
  status counts or events are displayed.

**Intentional adaptations and open questions**

The existing header retains environment selection and admin Access. Task lists
use the existing exact-ID/status API and cursor pages instead of the source's
broader search and five-item counts. Timeline cards use real status events
rather than synthesizing a warning event from a log. All modes share the detached
inspector shown by Explorer's source, so details and reader position survive
layout switching. CPU is in cores rather than the source's unspecified percent.
These choices preserve the approved layout intent and released data contracts.

**Implementation checklist**

- [x] Apply semantic palette; preserve logo and existing icons.
- [x] Preserve Overview and Configure; wire service/task Monitor navigation.
- [x] Exercise mode switches, scopes, task paging and selected-event CPU/log window.
- [x] Check mobile controls, long-message wrapping, sources, chart labels,
  Expand/Tab/Escape and focus restoration through Computer Use.
- [x] Run frontend tests/build and the mandatory SDK/backend verification stack.
- [x] Record existing coverage-gate failure and synthetic-provider limitations in
  [verification notes](docs/qa/studio-monitor-workspace/README.md).

**Follow-up polish**

P3: additional fractional timestamp precision could match the mock's dense log
presentation. Provider latency/retention and real incident volume still require
staging acceptance; synthetic evidence does not establish production performance.

**Service-control spacing follow-up — 3 October 2026**

The user's screenshot showed the observation label touching the action row and
the telemetry button stretching across the label and selector. Restored the
existing 18px section stack and bottom-aligned the normal-height button with the
selector; narrow screens place the button on its own row with a 12px gap.
Computer Use confirmed desktop alignment and clean wrapping at 390×844, including
the configuration disclosure below the controls. The supplied cropped screenshot
and implementation are compared qualitatively (the source viewport is unknown):
[before/after](docs/qa/studio-monitor-workspace/service-controls-spacing-comparison.jpg).
Frontend validation remains 148 tests passed and production build passed.

final result: passed
