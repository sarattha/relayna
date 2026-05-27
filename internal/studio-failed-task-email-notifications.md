# Studio Failed-Task Email Notifications

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This plan follows `PLANS.md`.

## Purpose / Big Picture

Studio operators can enable automatic email notifications for newly discovered
failed tasks. When enabled, the Studio backend periodically scans registered
Relayna services for unreviewed failed-task snapshots and sends alerts through
a configured email service. The feature is disabled by default, requires
receiver emails and an API key through environment variables, and exposes a
Failed Tasks page control for runtime enablement and batch wait period.

## Progress

- [x] (2026-05-26 23:58Z) Created implementation plan for issue #84.
- [x] (2026-05-27 00:05Z) Added Studio backend configuration and runtime wiring.
- [x] (2026-05-27 00:08Z) Added notification worker, email client, Redis dedupe, and tests.
- [x] (2026-05-27 00:11Z) Ran required SDK and Studio backend verification.
- [x] (2026-05-27 02:45Z) Added API-key delivery, Redis runtime settings, frontend controls, and batch-window behavior.

## Surprises & Discoveries

- Observation: The current production freeze manifest is pinned to `v1.4.11`,
  while the latest repository tag is `v1.4.14`.
  Evidence: `studio/backend/tests/freeze/*.json` and `git tag`.

## Decision Log

- Decision: Implement as a Studio backend background worker, not a frontend
  action.
  Rationale: Notifications must be automatic and must not depend on the Failed
  Tasks page being opened.
  Date/Author: 2026-05-26 / Codex.

- Decision: Keep the first toggle environment-based.
  Rationale: Receiver emails are preconfigured by environment, and no new UI is
  required for the first version.
  Date/Author: 2026-05-26 / Codex.

- Decision: Add a runtime UI toggle and batch wait setting backed by Redis.
  Rationale: Operators need to enable or disable delivery and tune batching
  without restarting Studio; secrets and receivers remain environment-owned.
  Date/Author: 2026-05-27 / Codex.

- Decision: Treat config/runtime changes as an approved freeze-perimeter
  update.
  Rationale: Issue #84 explicitly allows API, config, and Studio backend
  behavior changes needed for this feature even if they cross freeze perimeters.
  Date/Author: 2026-05-26 / Codex.

## Outcomes & Retrospective

Implemented automatic failed-task email notifications as a disabled-by-default
Studio backend worker. The feature uses env-owned email service configuration,
Redis-backed runtime settings, Redis lock/notified keys to prevent duplicate
sends, and retries later after email-service failures.

## Context and Orientation

The Studio backend lives under `studio/backend/src/relayna_studio/`. Existing
background workers are wired through `app.py` and started during the FastAPI
lifespan. Failed tasks are currently read by `StudioFederationService` in
`federation.py`, which aggregates service-level `/failed-tasks` routes.

## Compatibility Boundary

Compatibility boundary: production freeze `v1.4.11`; latest tag observed
locally is `v1.4.14`. This change intentionally adds Studio backend environment
configuration and runtime behavior. Freeze manifests may be updated only as an
intentional review item.

## Plan of Work

Add notification settings to `StudioBackendSettings` and pass them through
`create_studio_app`. Add a new internal failed-task notification module with an
email client, Redis-backed runtime settings, dedupe store, and background
worker. Wire the worker into `StudioRuntime` and the lifespan lifecycle. Add
Failed Tasks page controls for enablement and wait period. Add focused tests for
config, send behavior, batching, dedupe, retry after failure, UI controls, and
worker resilience.

## Concrete Steps

    cd /Users/jobz/Works/relayna
    make -C studio/backend test
    bash .codex/skills/code-change-verification/scripts/run.sh

## Validation and Acceptance

Acceptance criteria:

- Notifications are disabled by default.
- Email service URL, receivers, and API key remain server-side env settings.
- The Failed Tasks page can toggle delivery and set the wait period from 0 to
  604800 seconds.
- A 0 second wait sends one email per failed task.
- A positive wait sends one email containing all newly discovered failures in
  the batch window.
- Failed email sends are retried later and do not create a notified marker.
- Duplicate scans and multiple Studio replicas are protected by Redis lock and
  notified keys.
- Existing failed-task listing remains unaffected.

## Idempotence and Recovery

The worker is safe to restart. Redis notified markers prevent duplicate emails
after successful sends, and short lock keys prevent concurrent sends while
allowing retry after lock expiry.

## Artifacts and Notes

Email service request body:

    {
      "receivers": ["ops@example.com"],
      "title": "[Relayna] Failed task: payments-api / failure-1",
      "body": "Service: payments-api\nFailure: failure-1\n..."
    }

## Interfaces and Dependencies

New environment variables:

- `RELAYNA_STUDIO_FAILED_TASK_EMAIL_ENABLED`
- `RELAYNA_STUDIO_FAILED_TASK_EMAIL_SERVICE_URL`
- `RELAYNA_STUDIO_FAILED_TASK_EMAIL_API_KEY`
- `RELAYNA_STUDIO_FAILED_TASK_EMAIL_RECEIVERS`
- `RELAYNA_STUDIO_FAILED_TASK_EMAIL_INTERVAL_SECONDS`
- `RELAYNA_STUDIO_FAILED_TASK_EMAIL_TIMEOUT_SECONDS`
- `RELAYNA_STUDIO_FAILED_TASK_EMAIL_DEDUPE_TTL_SECONDS`
- `RELAYNA_STUDIO_FAILED_TASK_EMAIL_TITLE_PREFIX`
- `RELAYNA_STUDIO_FAILED_TASK_EMAIL_BATCH_WAIT_SECONDS`
