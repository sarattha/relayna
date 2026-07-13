# Indexed Service Event Feed v2

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This document is maintained in accordance with `PLANS.md` at the repository
root.

## Purpose / Big Picture

Relayna services retain normalized task status and observation events so the
Studio control plane can poll `GET /events/feed`. The released implementation
stores complete JSON events in one Redis list and finds an `after` cursor by
repeatedly transferring and parsing list chunks from the head. Deep or missing
cursors can therefore transfer all 5,000 retained payloads for one page, and
deep pagination repeats that work. Studio also downloads a full 200-event page
every five seconds even when the feed is unchanged.

After this change, the SDK stores the feed in a bounded Redis sorted-set index
and payload hash. Cursor lookup is indexed, each page transfers at most
`limit + 1` payloads, and a missing cursor immediately falls back to the newest
page. Studio first performs a one-item head probe and downloads a full page
only when the feed has changed. Operators can observe the same HTTP response
models, ordering, pagination cursors, deduplication, TTL, and retention behavior
as before, with bounded Redis and HTTP traffic.

## Progress

- [x] (2026-07-13 16:04Z) Confirmed a clean `main` checkout at release tag `v1.4.27`, GitHub authentication, and Docker availability; created and finalized branch `codex/indexed-service-event-feed-v2`.
- [x] (2026-07-13 16:04Z) Applied `$implementation-strategy` and `$production-freeze-guard`; the user explicitly approved breaking the v1.4.27 persisted Redis perimeter and requested direct v2 replacement without a v1 compatibility path.
- [x] (2026-07-13 16:24Z) Implemented the SDK sorted-set/payload-hash feed, cached atomic Redis write script, and exact bounded pagination.
- [x] (2026-07-13 16:24Z) Implemented Studio idle head probing while preserving first sync and changed-feed catch-up behavior.
- [x] (2026-07-13 16:24Z) Added SDK and Studio regression, complexity-bound, corruption-invariant, and opt-in real-Redis integration tests.
- [x] (2026-07-13 16:24Z) Updated the approved freeze perimeter, release version `1.4.28`, changelog, lock metadata, and operator/developer documentation.
- [x] (2026-07-13 16:30Z) Passed focused and complete verification, Redis 7 concurrency/pagination acceptance, package and container builds, and production Studio browser validation using `$computer-use`.
- [ ] Review, commit, push, open a draft PR, then monitor and address the first review cycle.

## Surprises & Discoveries

- Observation: The current feed implementation is byte-for-byte present in the
  released `v1.4.27` tag, so this is a released persisted Redis behavior rather
  than unreleased branch churn.
  Evidence: `git show v1.4.27:src/relayna/observability/feed.py` contains the
  list scan in `RedisServiceEventFeedStore.get_feed`.

- Observation: Studio defaults to `pull_page_limit=200`,
  `pull_max_pages=25`, and a five-second pull interval. A first full catch-up
  can make the SDK transfer about 65,300 serialized Redis list entries to
  deliver 5,000 unique events.
  Evidence: `studio/backend/src/relayna_studio/events.py` and
  `studio/backend/src/relayna_studio/app.py`.

- Observation: Docker is available but host `redis-server` and `redis-cli`
  binaries are not installed, so real Redis acceptance will use an isolated
  Docker container.
  Evidence: local tool discovery on 2026-07-13.

- Observation: Sending the Lua source through `EVAL` for every event would add
  avoidable write bandwidth even while fixing read bandwidth. Registering the
  script once lets redis-py use `EVALSHA` with automatic `NOSCRIPT` recovery.
  Evidence: focused SDK and real-Redis tests pass through the registered script
  path.

- Observation: The final opt-in real Redis test inserted 5,000 events with bounded
  concurrency, paginated all 5,000 exactly once, verified missing-cursor
  fallback and TTLs, exercised reads during concurrent writes and trims, and
  cleaned its namespaced keys in 1.89 seconds.
  Evidence: `RELAYNA_TEST_REDIS_URL=redis://127.0.0.1:16379/15 uv run pytest
  tests/test_service_event_feed_redis.py -q` reported `1 passed`.

- Observation: The first GitHub Actions security job exposed a newly published
  `PYSEC-2026-2132` advisory for transitive development dependency Click 8.3.1
  in the SDK and 8.3.2 in Studio backend; Click 8.3.3 contains the fix. This was
  unrelated to the feed implementation but blocked the release security gate.
  Evidence: PR #108 Actions runs 29266595774 and 29266789226; local
  `make security-sdk` and `make -C studio/backend security` both passed after
  the dependency-floor updates.

## Decision Log

- Decision: Compatibility boundary is release tag `v1.4.27`; directly replace
  the documented `{prefix}:feed` list schema rather than add dual-write,
  migration, or backward reads.
  Rationale: The user explicitly stated production can move immediately,
  requested v2 without v1 preservation, and approved breaking the production
  freeze. The incompatibility will be documented prominently for `v1.4.28`.
  Date/Author: 2026-07-13 / Codex.

- Decision: Keep the public constructor, route, response model, event cursor,
  newest-first ordering, missing-cursor fallback, deduplication, TTL, and
  `feed_maxlen` behavior stable.
  Rationale: The performance problem can be fixed in Redis storage and Studio
  polling without creating unnecessary HTTP or Python API breaks.
  Date/Author: 2026-07-13 / Codex.

- Decision: Store cursor order in a ZSET scored by an atomic monotonically
  increasing sequence and complete JSON events in a HASH keyed by cursor.
  Rationale: `ZSCORE` resolves an existing cursor directly, score range reads
  provide stable older-page pagination, and the atomic reader fetches only the
  selected hash payloads. Redis Streams would still require a
  cursor-to-stream-ID mapping.
  Date/Author: 2026-07-13 / Codex.

- Decision: Add a one-item Studio head probe before its normal pull page when a
  stored cursor exists.
  Rationale: This preserves the current feed API and cross-version behavior
  while avoiding full idle responses. The SDK no-cursor path will read exactly
  `limit + 1`, so a one-item HTTP probe reads only two Redis payloads.
  Date/Author: 2026-07-13 / Codex.

- Decision: Bump all release artifacts to `1.4.28`.
  Rationale: The change is a performance fix with an intentional persisted
  Redis storage break after `v1.4.27`.
  Date/Author: 2026-07-13 / Codex.

- Decision: Cache the atomic writer through `Redis.register_script` instead of
  issuing the complete script with each event.
  Rationale: This retains one-command atomicity while keeping steady-state
  write traffic bounded to the SHA and event arguments.
  Date/Author: 2026-07-13 / Codex.

- Decision: Read cursor position and payloads in one cached Lua script.
  Rationale: A separate ZSET lookup followed by `HMGET` could race with
  retention trimming. The atomic reader guarantees each selected cursor and
  payload come from one Redis snapshot while preserving the `limit + 1` bound.
  Date/Author: 2026-07-13 / Codex.

## Outcomes & Retrospective

Implementation and local acceptance are complete. The indexed feed bounds each
100-event request to 101 cursors and payloads even for deep or missing cursors,
while Studio's unchanged-feed loop uses a one-item HTTP probe. The complete
verification stack passed with 419 SDK tests, 241 Studio backend tests, and 92
Studio frontend tests. Redis 7 acceptance covered 5,000 concurrent inserts,
full pagination, trimming under concurrent reads, duplicate rejection, and
TTLs. Production container builds and a Computer Use walkthrough verified the
overview, service health and capabilities, recent activity, task history, and
task search against real Redis. The remaining work is publication and the
first PR review cycle. The intentional operational risk is the documented lack
of a v1 list migration: all SDK instances sharing a feed prefix must move to
v1.4.28 together.

## Context and Orientation

The SDK implementation is
`src/relayna/observability/feed.py::RedisServiceEventFeedStore`. It normalizes
task status and runtime observation events, deduplicates them by cursor, stores
them in Redis, and serves the FastAPI route created by
`src/relayna/api/events_routes.py::create_events_router`.

The Studio control plane implementation is
`studio/backend/src/relayna_studio/events.py::StudioEventIngestService`. Its
background worker enumerates healthy registered services, fetches their
`/events/feed` routes, ingests unseen events, and persists the newest event
cursor. `studio/backend/src/relayna_studio/app.py` wires the default five-second
worker interval.

SDK tests live in `tests/test_service_event_feed.py`; Studio pull tests live in
`studio/backend/tests/test_studio_events.py`. Redis key documentation is in
`docs/redis-keys.md`; release and deployment documentation is spread across
`CHANGELOG.md`, `docs/getting-started.md`, `docs/releases.md`,
`docs/studio-backend.md`, and packaging manifests.

## Compatibility Boundary

Compatibility boundary: latest and frozen release tag `v1.4.27`. The new
release intentionally changes the persisted service-event Redis keys and does
not read or migrate the v1 `{prefix}:feed` list. Deployments must upgrade SDK
service instances together and may delete the old list after rollout. The
public SDK exports, constructor signature, FastAPI route path/query parameters,
response fields, cursor values, and Studio retained event schema remain stable.

The user explicitly approved this production-perimeter break. Freeze manifests
will be updated only if their asserted version or enumerated surface truly
changes, and the PR/final handoff will carry this compatibility note.

## Plan of Work

In `src/relayna/observability/feed.py`, replace list key helpers and list scans
with helpers for the sequence, ZSET index, and payload HASH keys. Store and trim
the three structures atomically so concurrent service writers receive unique
scores and readers never observe an index member without a payload. Preserve
dedupe markers and TTL behavior. Implement newest-page and cursor-page reads
with one extra item to calculate `next_cursor`; validate or skip corrupt/missing
payload values without allowing an unbounded Redis transfer.

In `studio/backend/src/relayna_studio/events.py`, probe a service feed with
`limit=1` when a pull cursor exists. Return immediately if the newest cursor is
unchanged. When it changed, continue through the existing full-page catch-up
logic so no new event is lost and the newest cursor advances only after
successful ingestion.

Update fake Redis implementations and focused tests to assert concrete Redis
command bounds, deep/missing cursor semantics, concurrent ordering, trimming,
TTL, duplicate rejection, corrupt payload handling, Studio idle behavior,
first sync, and backlog catch-up. Add a real Redis integration test or an
equivalent repeatable acceptance command using Docker without making the main
test suite depend unconditionally on Docker.

Bump SDK, Studio backend, and Studio frontend package versions and lockfile
workspace metadata to `1.4.28`. Add a changelog compatibility warning and update
Redis key, Studio polling, installation, and release docs. Do not update
unrelated examples or freeze entries.

## Concrete Steps

Run from `/Users/jobz/Works/relayna`:

    uv run pytest tests/test_service_event_feed.py
    uv run --project studio/backend pytest studio/backend/tests/test_studio_events.py
    docker run --rm -d --name relayna-feed-v2-test -p 16379:6379 redis:7-alpine
    <run the focused real-Redis acceptance test against redis://127.0.0.1:16379/15>
    docker stop relayna-feed-v2-test
    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build

Run the Studio stack locally, seed representative service events through real
Redis, and use Computer Use in Chrome to verify the Studio UI remains usable
and reflects the same event/task functionality as v1.

After all checks pass, inspect the complete diff, stage only plan-scoped files,
commit, push `codex/indexed-service-event-feed-v2`, and open a draft PR against
`main`. Monitor the first review/check cycle, implement actionable feedback,
reply to each addressed thread, resolve it, and rerun affected verification.

## Validation and Acceptance

The change is accepted only when all of the following are demonstrated:

- A feed containing 5,000 events returns `limit=100` pages with at most 101
  indexed payload values transferred per request, including deep and missing
  cursors.
- Newest-first order, exact `count`, `next_cursor`, duplicate handling,
  retention trimming, TTL behavior, and missing-cursor fallback match v1.
- Multiple writers cannot assign duplicate ordering scores or expose partial
  index/payload entries.
- Studio with an unchanged stored cursor makes only the one-item probe and
  ingests nothing; first sync and changed-feed catch-up ingest every unseen
  event once and advance the cursor correctly.
- SDK and Studio backend format, lint, typecheck, and full test targets pass in
  the mandatory order; Studio frontend test and build targets pass if affected
  release artifacts require them.
- The behavior succeeds against a real Redis 7 container and the local Studio
  experience is inspected through Computer Use.
- Versions and docs consistently identify `1.4.28` and explain that the v1 feed
  list is not migrated.
- A draft PR exists, its checks are green or explained, and the first review
  cycle has no unresolved actionable threads.

## Idempotence and Recovery

All test and formatting commands are rerunnable. The Docker acceptance
container uses a dedicated name and database; remove a stale container before
retrying. The direct v2 schema uses new keys, so a partially upgraded service
must be rolled forward rather than run mixed SDK versions against one prefix.
Rollback to `v1.4.27` requires restoring its list data from a pre-upgrade Redis
backup or accepting a newly empty v1 feed.

If PR checks or review reveal a defect, make focused follow-up commits on the
same branch and rerun the relevant focused tests plus the complete mandatory
verification stack before resolving the review thread.

## Artifacts and Notes

Current branch:

    codex/indexed-service-event-feed-v2

Expected release:

    1.4.28

Expected external route contract:

    GET /events/feed?after=<event-cursor>&limit=<1..500>
    {"count": <n>, "items": [...], "next_cursor": <cursor-or-null>}

## Interfaces and Dependencies

No new Python or JavaScript dependency is expected. Redis 7 is already the
development dependency family and supports the required ZSET, HASH, Lua, and
transaction operations. The public `RedisServiceEventFeedStore` constructor and
`RelaynaServiceEventFeedResponse` model must remain unchanged. Internal Redis
keys will be documented and namespaced beneath the existing configurable
prefix.
