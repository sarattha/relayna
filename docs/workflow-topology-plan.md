# First-class stage-inbox workflow topology

This document explains the implementation shape for Relayna's multi-stage
workflow topology and why the library models it as a stage-inbox system rather
than as explicit producer-owned outbox queues.

## Why this topology exists

Relayna already supported:

- one shared task queue plus one shared status queue/stream
- routed task queues by `task_type`
- shard-owned aggregation queues on the shared status exchange

Those shapes work well for single-hop and routed worker systems, but they do
not express planner, re-planner, search, writer, and file-creation chains in a
single topology object.

`SharedStatusWorkflowTopology` fills that gap with:

- one workflow topic exchange for stage-to-stage work
- one durable inbox queue per consuming stage
- one shared status exchange and shared status queue/stream
- named entry routes for ingress traffic such as planner and re-planner entry
  points

## Why stage-inbox is the first-class choice

Stage-inbox matches RabbitMQ ownership semantics.

When the "next pod consumes it", the durable queue is naturally that next
stage's inbox. That queue is where:

- backlog accumulates
- retry and DLQ semantics apply
- autoscaling decisions are made
- consumer lag is measured
- operator ownership usually lives

Modeling that queue as an upstream stage's outbox hides the real owner of the
work. The producer publishes to an exchange. The consumer owns the durable
mailbox.

This is why the first-class topology chooses:

- producer publishes with a routing key
- downstream stage owns the queue
- workflow queue names represent inboxes, not transitions

Explicit edge/outbox queues are deferred. They are useful in some systems, but
they add extra queue objects, extra routing metadata, and extra operational
surfaces before the library has proven the simpler stage-inbox abstraction.

## Workflow and status stay on separate lanes

Relayna keeps workflow transport and user-visible progress separate on purpose.

Workflow lane:

- exchange: `workflow_exchange`
- payload: `WorkflowEnvelope`
- purpose: move work between internal stages

Status lane:

- exchange: `status_exchange`
- payload: `StatusEventEnvelope`
- purpose: publish progress, history, and final state for `StatusHub`,
  `StreamHistoryReader`, Redis history, and SSE

This separation avoids coupling internal workflow hops to user-facing history.
It keeps the existing status stack unchanged and preserves current operational
and API behavior.

## Public model

`relayna.topology` exposes:

- `WorkflowStage`
- `WorkflowEntryRoute`
- `SharedStatusWorkflowTopology`

`WorkflowStage` defines:

- `name`
- `queue`
- `binding_keys`
- `publish_routing_key`
- `description`
- `role`
- `owner`
- `tags`
- `sla_ms`
- `accepted_actions`
- `produced_actions`
- `allowed_next_stages`
- `terminal`
- `timeout_seconds`
- `max_retries`
- `retry_delay_ms`
- `max_inflight`
- `dedup_key_fields`
- `queue_arguments_overrides`
- `queue_kwargs`

`WorkflowEntryRoute` defines:

- `name`
- `routing_key`
- `target_stage`

`SharedStatusWorkflowTopology` defines:

- one workflow topic exchange
- one shared status topic exchange
- one shared status queue/stream
- any number of workflow stages
- optional named entry routes
- curated workflow queue arguments plus broker-specific escape hatches

Validation rules:

- stage names must be unique
- queue names must be unique
- entry route names must be unique
- each stage must have at least one binding key
- each stage must define one default `publish_routing_key`
- action definitions within `accepted_actions` and `produced_actions` must be unique
- `allowed_next_stages` must reference existing stages
- `terminal` stages cannot define downstream transitions or produced actions
- entry routes must target an existing stage
- duplicate queue-argument keys across built-ins, overrides, and kwargs fail
  fast

## Workflow message contract

`relayna.contracts.WorkflowEnvelope` is the canonical stage-to-stage transport
shape:

- `task_id`
- `message_id`
- `correlation_id`
- `stage`
- `origin_stage`
- `action`
- `payload`
- `meta`

Contract semantics:

- `task_id` is the workflow identity visible to status/history/SSE
- `message_id` identifies a specific hop
- `stage` is the intended destination stage
- `origin_stage` is populated by downstream handoff helpers
- `correlation_id` defaults to `task_id`

Ingress task delivery and workflow-hop delivery remain distinct concepts.
Relayna therefore keeps `TaskEnvelope` and `WorkflowEnvelope` separate rather
than overloading one transport shape for both.

## Publishing and consuming

`relayna.rabbitmq.RelaynaRabbitClient` now supports:

- `publish_workflow(...)`
- `publish_to_stage(...)`
- `publish_to_entry(...)`
- `publish_workflow_message(...)`
- `ensure_workflow_queue(...)`

`publish_task(...)` remains the legacy task-topology entry point and raises on
`SharedStatusWorkflowTopology` so callers do not silently publish to the wrong
lane.

`relayna.consumer` now supports:

- `WorkflowConsumer`
- `WorkflowContext`

`WorkflowConsumer` consumes a named stage inbox queue and reuses the same retry,
DLQ, and status-publishing model already used by the task and aggregation
consumers.

`WorkflowContext` supports:

- `publish_status(...)`
- `publish_to_stage(...)`
- `publish_workflow_message(...)`

Downstream publish behavior:

- preserve `task_id`
- preserve `correlation_id`
- generate a new `message_id`
- set `origin_stage` from the current stage
- set `stage` to the destination stage

## Planner, re-planner, and writer all fit the same abstraction

Planner flow:

- API publishes to `planner.topic_planner.in`
- `topic_planner` consumes from its inbox queue
- it publishes to `planner.docsearch_planner.in`
- `docsearch_planner` consumes from its inbox queue
- the chain continues stage by stage

Re-planner flow:

- API publishes to a named re-planner entry route such as
  `replanner.docsearch_planner.in`
- the same `docsearch_planner` inbox queue can bind both planner and re-planner
  keys
- the consuming stage therefore stays the same while ingress changes

Writer flow:

- API can publish into `docsearcher` and `websearcher`
- both publish to the same `researcher_aggregator` inbox queue
- fan-in stays simple because the queue belongs to the downstream stage

This is the key design win: one abstraction covers straight chains, alternate
entry paths, and fan-in without introducing special queue families.

## Compatibility with existing topologies

The workflow topology is additive.

Existing topologies remain first-class and unchanged:

- `SharedTasksSharedStatusTopology`
- `SharedTasksSharedStatusShardedAggregationTopology`
- `RoutedTasksSharedStatusTopology`
- `RoutedTasksSharedStatusShardedAggregationTopology`

The topology protocol was generalized so workflow-aware code can talk about
"workflow queues" while older topologies map their single task queue into the
same compatibility surface.

## Retry, DLQ, and failure model

Workflow stage queues use the same retry infrastructure already used by
Relayna's existing consumers:

- retry queue suffix: `.retry`
- dead-letter queue suffix: `.dlq`
- retry metadata carried in `x-relayna-*` headers
- DLQ indexing continues to store replay metadata in Redis

Failure semantics:

- malformed JSON can be rejected or dead-lettered depending on retry policy
- invalid `WorkflowEnvelope` payloads follow the same rule
- handler failures either reject, retry, or dead-letter according to the
  configured policy
- retry status publishing remains on the shared status exchange

## Observability

Workflow-specific observation events now exist for stage activity:

- `WorkflowStageStarted`
- `WorkflowMessageReceived`
- `WorkflowMessagePublished`
- `WorkflowStageAcked`
- `WorkflowStageFailed`

These events let operators debug stage traffic without mixing workflow transport
concerns into status history.

## Acceptance criteria

The topology counts as first-class when all of the following are true:

1. A user can declare the full multi-stage pipeline in one topology object.
2. Producers can publish to a stage or a named entry route without raw AMQP
   code.
3. Workers can consume a named stage with `WorkflowConsumer`.
4. Handlers can publish downstream work with `WorkflowContext`.
5. Status events from workflow handlers still reach `StatusHub`, history, and
   SSE unchanged.
6. Planner, re-planner, and writer flows are documented with concrete diagrams
   and code examples in the getting-started guide.

For end-user examples and Mermaid diagrams, see
[Getting Started](getting-started.md).
