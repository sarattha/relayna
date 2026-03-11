# Behavior Parity Checklist

## Queue + Exchange Topology
- Summary tasks exchange/queue/routing key remain unchanged.
- Translation tasks exchange/queue/routing key remain unchanged.
- Translation aggregation shard routing (`agg.<shard>`) remains unchanged.
- Doc ingestion backend queue names remain unchanged.
- Status queues remain durable and use stream mode where enabled.

## Status Event Flow
- API submit endpoints still publish initial `queued` events.
- Workers still publish stage-by-stage status updates.
- Shared status consumer still ACKs early and stores best-effort in Redis.
- Redis history remains LPUSH newest-first with max-length trimming + TTL.

## SSE Behavior
- `/events/{id}` starts with `ready` event.
- History is replayed oldest-to-newest before live updates.
- Streams stop on terminal status:
  - Summary/Translation: `completed`, `failed`
  - Doc ingestion: `ready`, `error`

## History Behavior
- Summary/Translation history endpoints still replay from RabbitMQ stream.
- Doc ingestion now adds `/history/{document_id}` using stream replay.
- History replay keeps max time/scan/match guardrails.

## Compatibility
- Existing request/response models in each service are unchanged.
- Doc-ingestion payloads keep `documentId` for clients while also storing canonical `task_id`.
- Sensitive metadata keys like `auth_token` are sanitized before SSE/history fanout in translation path.
