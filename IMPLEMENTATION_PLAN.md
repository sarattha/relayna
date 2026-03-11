# Step-by-Step Implementation Plan: Shared Relayna Library for TARA2 Services

## Summary
Create a shared internal Python package `tara2-relayna` to centralize RabbitMQ task publish/consume patterns, status streaming, Redis-backed SSE fanout, and stream history replay used by:
1. `tara2_doc_ingestion`
2. `tara2_summary_service`
3. `tara2_translation_service`

Keep each service’s business logic and request/response schemas unchanged, and replace only shared infrastructure code first.

## Step-by-Step Plan
1. Define shared architecture contract and freeze current behavior.
- Capture current queue/exchange names, routing keys, stream args, Redis key prefixes, terminal statuses, and endpoint behavior from the 3 repos.
- Write a short “behavior parity checklist” that must remain true after migration.
- Freeze canonical status event shape and required fields.

2. Create package scaffold `tara2-relayna`.
- Add package structure:
  - `tara2_relayna/contracts.py`
  - `tara2_relayna/config.py`
  - `tara2_relayna/rabbitmq.py`
  - `tara2_relayna/status_store.py`
  - `tara2_relayna/status_hub.py`
  - `tara2_relayna/sse.py`
  - `tara2_relayna/history.py`
  - `tara2_relayna/fastapi.py`
- Add `pyproject.toml`, versioning, and internal publishing configuration.
- Add strict typing and lint settings aligned with current repos (Python 3.13).

3. Implement canonical message contracts and adapters.
- Add canonical models:
  - `TaskEnvelope`
  - `StatusEventEnvelope`
  - `TerminalStatusSet`
- Include fields: `task_id`, `status`, `timestamp`, `message`, `meta`, `result`, `correlation_id`, `event_id`, `spec_version`.
- Add alias mapper for doc-ingestion (`documentId` <-> `task_id`) to preserve compatibility.

4. Implement shared Redis status store and SSE stream layer.
- Move duplicated `RedisStatusStore` logic into `status_store.py`.
- Keep behavior: `LPUSH`, `LTRIM`, TTL, publish on Redis channel.
- Implement SSE stream helper that:
  - emits `ready`
  - replays history oldest->newest
  - subscribes to Redis pubsub
  - closes on terminal statuses (`completed`, `failed`, `ready`, `error` configurable per service).

5. Implement shared RabbitMQ topology + publisher + shared status consumer.
- Add topology declarer for tasks exchange/queue, status exchange, status queue/stream.
- Add configurable routing strategy interface:
  - default task routing key strategy
  - default status routing by task_id
  - shard routing (translation aggregator compatibility).
- Implement shared status consumer loop:
  - consume from status queue/stream
  - ack early
  - sanitize sensitive metadata (remove `auth_token`)
  - write normalized events to Redis store.

6. Implement shared history replay reader.
- Add stream replay utility with protections:
  - `start_offset`
  - `max_seconds`
  - `max_scan`
  - optional `task_id` filter
- Return normalized events and deterministic stop conditions.
- Keep behavior compatible with existing `/history` endpoints.

7. Add FastAPI integration helpers.
- Provide reusable router factory for:
  - `GET /events/{task_id}`
  - `GET /history/{task_id}` or `GET /history?task_id=...` based on service style.
- Provide lifespan helpers for Rabbit/Redis init + status hub background task.
- Keep each service free to choose route paths while reusing internals.

8. Migrate `tara2_summary_service` first (pilot).
- Replace:
  - [rabbitmq_client.py](/home/sarattha/tara2_summary_service/app/common/rabbitmq_client.py)
  - [redis_store.py](/home/sarattha/tara2_summary_service/app/common/redis_store.py)
  - SSE/history internal helpers in [main.py](/home/sarattha/tara2_summary_service/app/api/main.py)
- Keep existing API signatures unchanged.
- Validate parity checklist after migration.

9. Migrate `tara2_translation_service` second.
- Replace duplicated Rabbit/Redis/SSE/history logic with shared package.
- Keep shard routing behavior from aggregator path via routing strategy config.
- Preserve current `meta` sanitization and parent/child task event behavior.

10. Migrate `tara2_doc_ingestion` third.
- Replace:
  - [status_stream.py](/home/sarattha/tara2_doc_ingestion/src/tara2_doc_ingestion/infrastructure/adapters/status_stream.py)
  - [redis_store.py](/home/sarattha/tara2_doc_ingestion/src/tara2_document_handler/redis_store.py)
  - stream/SSE logic in [api.py](/home/sarattha/tara2_doc_ingestion/src/tara2_document_handler/api.py)
- Standardize internals on `aio-pika` via shared package.
- Keep external payload compatibility (`documentId` still returned to existing clients).
- Add `/history/{document_id}` endpoint for parity with the other two services.

11. Remove duplicated infrastructure code from all 3 repos.
- Delete legacy local modules only after test and staging validation.
- Keep temporary compatibility shims for one release if needed.

12. Roll out progressively and observe.
- Deploy Summary first, then Translation, then Doc Ingestion.
- Monitor:
  - task enqueue success rate
  - status publish success rate
  - SSE connection duration/error rate
  - Redis key growth and TTL eviction
  - RabbitMQ stream lag and replay latency.

## Important Public APIs / Interfaces / Types
- New shared package API:
  - `RelaynaRabbitClient`
  - `StatusHub`
  - `RedisStatusStore`
  - `SSEStatusStream`
  - `StreamHistoryReader`
  - `RoutingStrategy` interface
- Canonical event/task types:
  - `TaskEnvelope`
  - `StatusEventEnvelope`
- Service API compatibility:
  - Keep existing submit endpoints unchanged.
  - Keep existing `/events/{task_id}` behavior unchanged.
  - Keep existing `/history` behavior, with doc-ingestion adding `/history/{document_id}`.

## Test Cases and Scenarios
1. Contract tests for envelope serialization/deserialization and alias mapping (`documentId`).
2. Rabbit topology tests for stream/classic queue declarations and bindings.
3. Publisher tests for routing keys, headers, correlation_id behavior.
4. Shared consumer tests for early ack, malformed payload handling, Redis write best-effort behavior.
5. SSE tests for replay order, terminal close, malformed pubsub payload handling.
6. History replay tests for offset handling and max_seconds/max_scan guards.
7. Integration tests per service for submit -> queued -> progress -> terminal event flow.
8. Backward compatibility tests for existing endpoint payloads and status values.
9. Security tests ensuring sensitive fields like `auth_token` are never emitted in SSE/history output.
10. Load test scenario with high status event volume to confirm Redis TTL/maxlen behavior.

## Rollout and Acceptance Criteria
1. No endpoint contract break in existing clients.
2. Event ordering and terminal-state behavior match baseline.
3. No increase in task loss/requeue anomalies in RabbitMQ.
4. All three services pass CI with shared package dependency.
5. Duplicate local infra modules removed or reduced to thin wrappers.

## Assumptions and Defaults
- Package name: `tara2-relayna` (internal/private).
- Python version: `>=3.13`.
- RabbitMQ streams remain enabled for status queues where currently used.
- Redis remains the SSE hot path store with default TTL 1 day.
- Summary and Translation endpoint contracts stay unchanged.
- Doc-ingestion keeps external `documentId` compatibility via adapter mapping.
- Rollout order default: Summary -> Translation -> Doc Ingestion.
