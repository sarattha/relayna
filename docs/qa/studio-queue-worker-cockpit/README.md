# Queue & Worker Cockpit visual verification

Validated the actual Studio route with representative local read fixtures at
1440 × 1000 desktop and 390 × 844 mobile widths, using the shared Studio light
palette. Full-page captures are `desktop.png` and `mobile.png`. Fixtures are
illustrative, not production measurements. The richer queue fixture represents
an explicitly configured source; built-in AMQP readings expose fewer fields.

Checked queue cards with excess arrivals, observed zero consumers, missing
unacknowledged/rates/age, a live worker with an expired lease, a stale individual
worker, service retry/DLQ samples and unsupported/unavailable/unconfigured sources.
Task/service/topology/failed-task/DLQ links preserve environment and service scope.
Both widths have document scrollWidth equal to viewport width. Header navigation
uses its existing horizontal scroll on mobile; cards, identities and controls fit
without text overlap. Mobile worker/lease and trend sections were also inspected
at their scroll positions. No runtime operations appear.

T3 preview navigation worked but snapshots timed out twice; Chrome browser
fallback completed the screenshots, responsive checks, source-state inspection
and interaction checks. Automated fixtures/behavior tests live in
`apps/studio/src/test/cockpit-fixtures.ts`,
`apps/studio/src/pages/QueueWorkerPage.test.tsx` and
`studio/backend/tests/test_studio_cockpit.py`.
