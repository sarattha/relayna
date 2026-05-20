# Relayna Runtime Next Phase Plans

This directory contains future implementation plans for runtime features that
make Relayna more powerful without treating the ideas as already approved
public API changes.

These files are internal planning artifacts. They should not be linked from
`mkdocs.yml` or presented as shipped documentation until the related behavior
is implemented and reviewed.

## Scope

The next runtime planning phase covers:

- [Task leases and heartbeat expiry](task-leases-and-heartbeat-expiry.md)
- [Adaptive retry policies](adaptive-retry-policies.md)
- [Runtime backpressure signals](runtime-backpressure-signals.md)
- [DLQ diagnosis bundles](dlq-diagnosis-bundles.md)
- [Runtime policy engine](runtime-policy-engine.md)
- [Execution graph live state](execution-graph-live-state.md)

## Planning Rules

Each plan follows the living ExecPlan structure from `PLANS.md`. When one of
these plans moves from planning to implementation, keep its living sections up
to date:

- Progress
- Surprises & Discoveries
- Decision Log
- Outcomes & Retrospective

Before implementation, use `$production-freeze-guard` for any feature or public
surface change and `$implementation-strategy` for compatibility-sensitive SDK,
Studio backend, persisted data, route response, task/status/workflow contract,
or wire behavior changes.

Run `$code-change-verification` before marking implementation complete when the
change affects SDK runtime code, tests, Studio backend runtime code, packaging,
or build/test behavior.

## Suggested Implementation Order

1. Task leases and heartbeat expiry, because it gives the runtime a reliable
   ownership model for in-flight work.
2. Runtime backpressure signals, because leases and existing metrics can feed
   operator-visible pressure decisions.
3. Adaptive retry policies, because backpressure and lease failure signals make
   retry decisions more accurate.
4. DLQ diagnosis bundles, because retry and lease history provide better
   dead-letter evidence.
5. Execution graph live state, because the earlier features add richer node and
   edge transitions to display.
6. Runtime policy engine, because it should consolidate proven controls rather
   than invent abstractions before the runtime signals are settled.

