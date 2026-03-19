# Plan: first-class multi-stage workflow topology

> Status: proposed design only. Nothing in this document is implemented or
> advertised as a supported topology yet; it is a contributor-facing plan for
> future work.

## Context

`relayna` currently treats two RabbitMQ shapes as first-class:

- `SharedTasksSharedStatusTopology`
- `SharedTasksSharedStatusShardedAggregationTopology`

Those cover a single shared task ingress plane, a shared status plane, and an
optional shard-based aggregation side-channel. They do **not** yet model a
workflow exchange with multiple agent-specific inboxes and outboxes such as
planner, search, writer, and replanner stages.

The topology discussed in the design conversation has a different shape:

- one shared status plane that all services can publish into
- one workflow exchange used as a task bus between internal agents
- multiple first-class agent queues bound by topic patterns
- explicit stage-to-stage routing such as `#.topicPlanner.in` and
  `#.writer.out`
- status/history/SSE remaining available across the entire workflow

This document describes a concrete plan to add that support properly instead of
forcing users to stitch it together with `DirectQueuePublisher` or custom AMQP
code.

## Goals

1. Add a first-class topology for multi-stage topic-routed workflows.
2. Keep the shared status stream/queue model intact so `StatusHub`,
   `StreamHistoryReader`, Redis history, and SSE keep working unchanged.
3. Model workflow stages declaratively so users can describe the topology once
   and reuse it across publishers, consumers, FastAPI apps, and tests.
4. Preserve the v1 ergonomics of named topology classes instead of exposing a
   low-level, AMQP-only API.
5. Avoid regressing the existing shared-task and sharded-aggregation topologies.

## Non-goals

- Replacing orchestration logic in user applications.
- Building a DAG scheduler or retry engine inside `relayna`.
- Automatically inferring workflow graphs from handler code.
- Removing the existing topologies.

## Current gaps in the codebase

The current abstractions assume a single task queue and routing key:

- `RelaynaTopology` exposes singular task methods such as
  `task_queue_name()`, `task_binding_keys()`, and `ensure_tasks_queue(...)`.
- `TaskConsumer` is hard-wired to `ensure_tasks_queue()` and therefore can only
  consume from the one shared task queue.
- `RelaynaRabbitClient.publish_task(...)` always publishes to the topology's
  single task exchange with the topology's task routing strategy.
- The only additional queue family is shard-based aggregation, and those queues
  are still attached to the shared status exchange.

That means the code can represent:

- producer -> shared task queue -> worker -> shared status exchange
- producer -> shared task queue -> worker -> shard queues on status exchange

But it cannot represent:

- workflow exchange -> planner queue
- planner queue -> search queue
- search queue -> writer queue
- writer queue -> replanner queue
- multiple agent inboxes/outboxes as reusable topology metadata

## Proposed product shape

Add a new first-class topology family for multi-stage workflows, tentatively:

- `SharedStatusWorkflowTopology`
- or `SharedTasksSharedStatusWorkflowTopology`

Recommended direction: use `SharedStatusWorkflowTopology` because the workflow
exchange is not just "shared tasks" anymore; it is a named task bus for staged
agent traffic.

### New concepts

Introduce a small declarative model for workflow stages and routes:

```python
from dataclasses import dataclass

@dataclass(slots=True)
class WorkflowStage:
    name: str
    queue: str
    binding_keys: tuple[str, ...]
    publish_routing_key: str | None = None
    queue_arguments: dict[str, Any] | None = None

@dataclass(slots=True)
class SharedStatusWorkflowTopology:
    rabbitmq_url: str
    workflow_exchange: str
    status_exchange: str
    status_queue: str
    stages: tuple[WorkflowStage, ...]
    dead_letter_exchange: str | None = None
    prefetch_count: int = 1
    status_use_streams: bool = True
    ...
```

That keeps the topology declarative while letting each stage own one inbox and
one or more binding patterns.

### Routing model

Use a topic exchange for the workflow plane. That directly matches the diagram's
`*.planner.in` / `*.writer.out` style routing. The shared status exchange stays
as a topic exchange as it is today.

Suggested workflow routing convention:

- `<namespace>.<stage>.in` for work sent to a stage
- `<namespace>.<stage>.out` for stage-emitted workflow messages
- `<namespace>.status` reserved for status-like workflow notifications if ever
  needed later

`namespace` can be derived from the task type, tenant, or workflow family, but
that policy should live in a dedicated routing strategy rather than being baked
into consumers.

## Required library changes

### 1. Expand the topology protocol

Refactor `RelaynaTopology` so it can describe zero, one, or many workflow/task
queues without breaking the existing classes.

Recommended approach:

- keep existing singular methods for backward compatibility
- add multi-queue workflow-oriented methods alongside them
- provide default implementations on existing topologies that map the single
  task queue into the new API

Proposed additions:

- `workflow_exchange_name()`
- `workflow_queue_names()`
- `workflow_binding_keys(queue_name: str)`
- `workflow_queue_arguments(queue_name: str)`
- `ensure_workflow_queue(queue_name: str, ...)`
- `publish_workflow_routing_key(message, *, stage: str | None = None)`
- `default_workflow_queue_name()` for compatibility with `TaskConsumer`

This lets the current topologies continue behaving exactly like they do today,
while the new topology can expose many stage queues.

### 2. Add stage-aware publishing APIs

Extend `RelaynaRabbitClient` with an explicit workflow publishing surface.

Recommended new methods:

- `publish_workflow_message(payload, *, routing_key: str)`
- `publish_to_stage(payload, *, stage: str)`
- `ensure_workflow_queue(queue_name: str)`
- `ensure_stage_queue(stage: str)`

Keep `publish_task(...)` as a convenience alias for topologies that still model a
single ingress queue. For the new topology, `publish_task(...)` should publish to
an explicitly configured entry stage or raise a clear error unless a default
entry stage is configured.

### 3. Generalize consumers

`TaskConsumer` currently always consumes the singular task queue. To make the new
topology first-class, consumption needs to be stage-aware.

Recommended implementation path:

- keep `TaskConsumer` for backward compatibility
- add a new `WorkflowConsumer` that accepts either `stage="planner"` or an
  explicit `queue_name`
- make `TaskConsumer` a thin compatibility wrapper around `WorkflowConsumer`
  bound to `default_workflow_queue_name()`

`WorkflowConsumer` should:

- declare/bind the selected queue through the topology
- preserve the current envelope validation flow
- preserve lifecycle-status publishing support
- expose stage name in observation events and consumer naming

### 4. Add workflow context publishing helpers

Handlers in a multi-stage workflow will usually need to do both:

- publish user-visible status updates
- publish the next workflow message to another stage

Add helper methods on the execution context, for example:

- `context.publish_to_stage(stage="writer", payload=...)`
- `context.publish_workflow_message(routing_key="team.writer.in", payload=...)`

This is important because otherwise the new topology is only partially
first-class: users would still need to grab raw RabbitMQ client objects to move
messages between stages.

### 5. Preserve shared-status semantics

No architectural change is needed for `StatusHub`, `StreamHistoryReader`,
`RedisStatusStore`, or SSE if the workflow topology still publishes status onto
`status_exchange` using the existing status envelope. The plan should preserve
that contract exactly.

The only likely addition is documentation clarifying that workflow messages and
status messages are different lanes:

- workflow exchange for agent-to-agent work
- status exchange for user-visible progress and history

### 6. Decide on envelope strategy

The existing `TaskEnvelope` is a good fit for ingress work, but the workflow
plane likely needs slightly richer metadata. Introduce a dedicated workflow
message envelope rather than overloading aggregation status events.

Recommended new contract:

- `WorkflowEnvelope`
  - `task_id`
  - `message_id`
  - `stage`
  - `action` or `topic`
  - `payload`
  - `meta`
  - `correlation_id`
  - `parent_message_id` (optional)
  - `origin_stage` (optional)

This gives internal stage-to-stage traffic a canonical shape while keeping
`StatusEventEnvelope` unchanged.

### 7. Add observability for workflow hops

The current observation system covers task message lifecycle and task-handler
failures. Add workflow-specific events so multi-stage systems can be debugged
without custom logging.

Recommended additions:

- `WorkflowMessagePublished`
- `WorkflowMessageReceived`
- `WorkflowStageStarted`
- `WorkflowStageFailed`
- `WorkflowStageAcked`

These should include at least `task_id`, `stage`, `routing_key`, and
`correlation_id` when available.

## Backward compatibility strategy

Keep the current public API working while introducing the new topology:

1. Leave `SharedTasksSharedStatusTopology` and
   `SharedTasksSharedStatusShardedAggregationTopology` unchanged from the user
   perspective.
2. Introduce the workflow topology as an additive API.
3. Keep `publish_task(...)` and `TaskConsumer` as supported compatibility paths.
4. Internally refactor those older paths onto the generalized workflow queue
   APIs where possible.

This avoids a v2-only redesign while still letting the new topology become a
first-class feature in v1.x.

## Suggested implementation phases

### Phase 1: topology and contracts

- Add `WorkflowStage` and the new topology class.
- Add `WorkflowEnvelope` contract.
- Extend the topology protocol with workflow-aware methods.
- Add unit tests for stage queue declaration and topic bindings.

### Phase 2: RabbitMQ client and consumers

- Add workflow publishing helpers to `RelaynaRabbitClient`.
- Add `WorkflowConsumer` and stage-aware queue selection.
- Keep `TaskConsumer` as a compatibility wrapper.
- Add tests for publish/consume flows across multiple stages.

### Phase 3: runtime ergonomics

- Add `TaskContext` or a renamed execution context helper for publishing to the
  next stage.
- Add observability events for workflow hops.
- Add examples for planner/search/writer/replanner chains.

### Phase 4: documentation and migration

- Update README and docs to list the workflow topology as a third first-class
  topology.
- Add a full getting-started section for multi-stage workflows.
- Add migration guidance showing when to use:
  - shared task + shared status
  - shared task + shared status + sharded aggregation
  - shared status + workflow topology

## Test plan

Add or extend tests in these areas:

- `tests/test_routing.py`
  - workflow topic bindings per stage
  - entry-stage publish behavior
  - explicit stage publish behavior
- `tests/test_consumer.py`
  - workflow consumer queue selection
  - context publishing to downstream stages
  - handler failure behavior per stage
- `tests/test_contracts.py`
  - `WorkflowEnvelope` validation and alias normalization
- integration-style smoke tests
  - planner -> search -> writer hop sequence
  - status events remaining visible to `StatusHub` and history readers

## Recommended acceptance criteria

The feature should only be considered "first-class" when all of the following
are true:

1. A user can declare the entire multi-stage workflow in one topology object.
2. A producer can publish into the entry stage without custom AMQP code.
3. A worker can consume a named stage with a library consumer, not a raw queue.
4. A handler can publish to another stage through library context helpers.
5. Status events from all stages still flow through `StatusHub`, Redis history,
   history replay, and SSE without extra integration code.
6. README and getting-started docs explicitly present this as a supported
   topology, not an escape hatch.

## Open questions to settle before implementation

1. Should workflow stages use one queue per stage only, or should the topology
   also support multiple competing queues per stage role?
2. Should `WorkflowEnvelope` be distinct from `TaskEnvelope`, or should
   `TaskEnvelope` be generalized and versioned carefully?
3. Should the workflow exchange be mandatory for the new topology, or should the
   topology also allow hybrid mode with the existing direct task exchange?
4. How much routing policy should live in `RoutingStrategy` versus on each stage
   definition?
5. Should aggregation be modeled as a special workflow stage in the future, or
   remain status-exchange-specific as it is today?

## Recommended next step

Implement Phase 1 first, but do it with the Phase 2 consumer API already in
mind. The biggest risk is adding a topology object that still cannot be used by
publishers and consumers ergonomically. If the queue declaration, publish API,
and consumer API are designed together, the workflow topology can land as a real
first-class addition rather than a documentation-only concept.
