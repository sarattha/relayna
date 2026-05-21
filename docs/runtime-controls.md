# Runtime Controls

Relayna runtime controls cover the operational decisions that happen while work
is in flight: lease ownership, worker health, backpressure snapshots, retry
policy decisions, and DLQ diagnosis. They are optional surfaces. A service can
adopt them incrementally without changing task payload schemas.

## Task Leases And Heartbeat Expiry

Task leases prevent two workers from processing the same task attempt at the
same time and give operators a way to detect stuck in-flight work.

Enable leases by wiring a `RedisTaskLeaseStore` and `LeasePolicy` into a
consumer:

```python
from redis.asyncio import Redis

from relayna.consumer import RetryPolicy, TaskConsumer
from relayna.storage import LeasePolicy, LeaseRecoveryAction, RedisTaskLeaseStore

lease_store = RedisTaskLeaseStore(Redis.from_url("redis://localhost:6379/0"), prefix="orders")

consumer = TaskConsumer(
    rabbitmq=rabbit,
    handler=handle_task,
    retry_policy=RetryPolicy(max_retries=3, delay_ms=30000),
    lease_store=lease_store,
    lease_policy=LeasePolicy(
        enabled=True,
        ttl_seconds=90,
        heartbeat_interval_seconds=20,
        recovery_action=LeaseRecoveryAction.PUBLISH_STALE_STATUS,
    ),
    lease_owner_id="orders-worker-1",
)
```

`WorkflowConsumer` accepts the same `lease_store`, `lease_policy`, and
`lease_owner_id` arguments. Workflow leases use the workflow `message_id` when
available so each stage message has a stable lease id.

Lease recovery is intentionally explicit. `LeaseRecoveryAction.OBSERVE_ONLY` is
the default and records ownership without publishing recovery status. Use
`PUBLISH_STALE_STATUS` when you want expired leases to become visible in task
status history. `REQUEUE`, `RETRY`, and `DEAD_LETTER` identify stronger
operator intent for recovery publishers you provide around
`TaskLeaseExpiryScanner`.

Run an expiry scanner when your deployment wants background recovery:

```python
from relayna.storage import TaskLease, TaskLeaseExpiryScanner


async def publish_stale_status(lease: TaskLease) -> None:
    await status_store.set_history(
        lease.task_id,
        {
            "task_id": lease.task_id,
            "status": "lease_expired",
            "message": f"Lease {lease.lease_id} expired for {lease.consumer_name}.",
            "meta": {"lease_id": lease.lease_id, "recovery_action": lease.recovery_action.value},
        },
    )


scanner = TaskLeaseExpiryScanner(
    store=lease_store,
    status_publisher=publish_stale_status,
    interval_seconds=5,
    batch_size=100,
)

await scanner.run_forever()
```

If the recovery publisher fails once, the Redis claim marker is released and
the lease expiry index is restored so the next scan can retry. Missing lease
payloads are cleaned from claim markers to avoid unbounded Redis set growth.

## Worker Health With Active Leases

Studio can read worker liveness from `GET /relayna/health/workers`. The route is
created by `create_worker_health_router(...)` and can include active leases.

```python
from relayna.api import WorkerHeartbeatSummary, create_worker_health_router
from relayna.storage import task_leases_for_health


async def heartbeat_provider() -> list[WorkerHeartbeatSummary]:
    leases = await lease_store.list_by_owner("orders-worker-1")
    return [
        WorkerHeartbeatSummary(
            worker_name="orders-worker-1",
            running=True,
            last_heartbeat_at=max((lease.heartbeat_at for lease in leases), default=None),
            active_leases=task_leases_for_health(leases),
        )
    ]


app.include_router(create_worker_health_router(heartbeat_provider=heartbeat_provider))
```

Advertise the route in capabilities so Studio knows it can federate worker
health:

```python
from relayna.api import HEALTH_CAPABILITY_ROUTE_IDS, STATUS_CAPABILITY_ROUTE_IDS, merge_capability_route_ids

supported_routes = merge_capability_route_ids(
    STATUS_CAPABILITY_ROUTE_IDS,
    HEALTH_CAPABILITY_ROUTE_IDS,
)
```

Studio treats `running=False` as unhealthy even when active leases are also
expired. Expired leases on otherwise running workers produce a stale worker
state.

## Runtime Backpressure Snapshots

Runtime backpressure is an operator-facing snapshot of pressure signals. It
does not throttle traffic by itself. Use it to expose queue depth, worker, and
DLQ health to humans or higher-level policy code.

```python
from relayna.api import RUNTIME_CAPABILITY_ROUTE_IDS, create_backpressure_router
from relayna.observability import (
    DLQPressureCollector,
    QueuePressureCollector,
    RuntimePressureService,
    RuntimePressureSignal,
    WorkerHealthPressureCollector,
)


def record_pressure(signal: RuntimePressureSignal) -> None:
    runtime.metrics.record_pressure_signal(
        scope=signal.scope,
        kind=signal.kind,
        severity=signal.severity.value,
        value=signal.value,
    )


pressure_service = RuntimePressureService(
    collectors=[
        QueuePressureCollector(
            queue_names=["orders.tasks.queue"],
            inspect_queue=runtime.rabbitmq.inspect_queue,
            warning_depth=100,
            critical_depth=1000,
        ),
        WorkerHealthPressureCollector(workers_provider=lambda: current_workers()),
        DLQPressureCollector(summaries_provider=dlq_service.get_queue_summaries),
    ],
    metrics_recorder=record_pressure,
)

app.include_router(create_backpressure_router(pressure_service=pressure_service))
```

The default route is:

- `GET /relayna/runtime/backpressure`

Response shape:

```json
{
  "reported_at": "2026-05-21T10:00:00+00:00",
  "signals": [
    {
      "scope": "queue",
      "scope_id": "orders.tasks.queue",
      "kind": "queue_depth_high",
      "severity": "warning",
      "value": 250,
      "threshold": 100,
      "reason": "Queue has 250 ready messages.",
      "recommended_action": "Add consumers or slow publishers."
    }
  ]
}
```

Advertise `RUNTIME_CAPABILITY_ROUTE_IDS` when exposing this route through
`create_capabilities_router(...)`.

## Runtime Policy Engine

`relayna.policies` is the public policy-decision surface. The first engine
centralizes retry decisions while preserving existing defaults.

```python
from relayna.policies import RetryDecisionContext, RuntimePolicyEngine

engine = RuntimePolicyEngine()
decision = engine.decide_retry(
    RetryDecisionContext(
        worker_type="task",
        queue_name="orders.tasks.queue",
        retry_attempt=1,
        reason="handler_error",
        exception_type="TimeoutError",
        task_id="task-123",
        task_type="order.capture",
        max_retries=3,
    )
)

assert decision.action == "retry"
assert decision.retry_attempt == 2
```

The built-in static policy behaves like the existing consumer retry behavior:

- no max retries means reject by default
- no max retries plus `failure_action="requeue"` means requeue
- attempts below `max_retries` retry with the next attempt number
- attempts at or above `max_retries` dead-letter

Custom retry policy objects implement `decide_retry(...)`:

```python
from relayna.policies import (
    RetryDecision,
    RetryDecisionAction,
    RetryDecisionContext,
    RuntimePolicyEngine,
    StaticRetryDecisionPolicy,
)


class RejectValidationErrors:
    fallback = StaticRetryDecisionPolicy()

    def decide_retry(self, context: RetryDecisionContext) -> RetryDecision:
        if context.exception_type == "ValidationError":
            return RetryDecision(
                action=RetryDecisionAction.REJECT,
                retry_attempt=context.retry_attempt,
                max_retries=context.max_retries,
                reason="validation_error",
                policy_name="reject_validation_errors",
            )
        return self.fallback.decide_retry(context)


engine = RuntimePolicyEngine(retry_policy=RejectValidationErrors())
```

Current `TaskConsumer` and `WorkflowConsumer` use the default engine internally.
They do not yet accept a public `policy_engine` constructor argument; keep using
`RetryPolicy` for broker retry infrastructure.

## DLQ Diagnosis Bundles

Indexed DLQ records now carry an optional diagnosis bundle. It is stored with
the DLQ record and returned by detail endpoints.

```json
{
  "dlq_id": "sha256:...",
  "reason": "handler_error",
  "retry_attempt": 3,
  "max_retries": 3,
  "diagnosis": {
    "failure": {
      "reason": "handler_error",
      "exception_type": "RuntimeError",
      "terminal_retry_attempt": 3
    },
    "retry": {
      "attempt": 3,
      "max_retries": 3,
      "source_queue_name": "orders.tasks.queue",
      "retry_queue_name": "orders.tasks.queue.retry",
      "policy_name": null,
      "policy_reason": "handler_error"
    },
    "ownership": {
      "consumer_name": "orders-worker-1",
      "task_id": "task-123",
      "correlation_id": "corr-123"
    },
    "envelope": {
      "task_type": "order.capture",
      "workflow_stage": null,
      "action": null,
      "content_type": "application/json",
      "body_encoding": "json"
    },
    "replay": {
      "recommended_action": "review_before_replay",
      "warnings": ["Message reached the configured retry limit."]
    }
  }
}
```

Use the diagnosis before replaying a DLQ message. It gives operators the retry
limit, source queue, payload classification, and replay warnings without
requiring them to decode headers manually.

## Execution Graph Live State

Execution graph nodes and edges include live state fields so Studio and API
clients can render the current state of an execution path.

Important fields:

- `state`
- `state_reason`
- `updated_at`

Typical states include `pending`, `running`, `retrying`, `succeeded`, `failed`,
and `dead_lettered`. Retry and DLQ observations update the graph so operators
can see whether a path is still retrying or has reached a terminal DLQ state.

See [Execution Graphs](execution-graphs.md) for the graph API, data model, and
Mermaid export.
