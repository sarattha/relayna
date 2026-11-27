# Correlate and recover Spooltail tasks in Studio

Maintain this ExecPlan using PLANS.md. The integration baseline is PR #146 in the monorepo. A dead letter here means a failed SDK task whose terminal status was committed with broker settlement.

## Progress

- [x] (2026-11-27 17:58Z) Inspect the existing Studio path, Gateway usage projection, and Spooltail task settlement.
- [x] (2026-11-27 17:58Z) Preserve Gateway trace identifiers and connect scoped evidence to execution attempts.
- [x] (2026-11-27 17:58Z) Implement operator retry and dead-letter replay using the retained failure and work records.
- [x] (2026-11-27 17:58Z) Verify the UI, both Python workspaces, and recovery against a disposable Spooltail broker.

## Surprises & Discoveries

The Gateway usage projection omitted the trace ID even when Gateway returned it. The repository's existing execution-path fallback builds from retained task history without requiring a workflow graph.

## Decision Log

The compatibility boundary is released v1.14.0 and the v1.4.30 freeze perimeter. The user authorized perimeter changes. This work extends the unreleased Spooltail router and reuses the existing Studio failed-task contract. Optional recovery uses the existing atomic terminal status as its durable failure record. It submits a new deterministic task ID so cancellation, SSE boundaries, and history for the original task remain meaningful.

## Outcomes & Retrospective

The SDK and Studio Python checks and frontend test and build passed. Twenty-eight SDK tests passed against a disposable native broker. A separate Studio API smoke used that broker to list a failure, retry it with corrected input, and open the completed replacement result. The portable native client supports topic scans. The original v0.0.1 wheel supports only task-specific recovery reads. The changes are local.

## Validation

Run the repository verification script from this worktree, Studio frontend test and build using the supported Node version, and a live SDK test against a fresh local Spooltail journal. Exercise a failed task, a replay with replacement input, concurrent duplicate retry, and the terminal result of the replacement task. Retry the test with a new journal and port if needed.
