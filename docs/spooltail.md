# Spooltail backend

Relayna uses RabbitMQ and Redis by default. The optional Spooltail backend provides single-task submission, workers, and per-task status and results through Spooltail's native Python gRPC client.

## RabbitMQ and Redis

Use `RelaynaRabbitClient` to publish and `TaskConsumer(rabbitmq=..., handler=...)` to consume. Configure `SharedTasksSharedStatusTopology` with the RabbitMQ URL and task and status exchanges and queues. Set the Redis URL when constructing the FastAPI runtime with `create_relayna_lifespan(topology=..., redis_url=...)`. See the [complete RabbitMQ and Redis example](getting-started.md).

## Install and start Spooltail

Install Relayna normally and install the native SDK separately in the same environment. The verified SDK is `relayna-spooltail==0.0.1` from commit `4e59f51202de1a4f360378119a7f9f660bfa2379`. This preview is distributed through a private Git repository and has no PyPI release. With Git access, install it using the pinned revision below. You can also build a wheel with `uv build --project python` from that revision and install the wheel in your application's environment.

```bash
uv pip install 'relayna-spooltail @ git+ssh://git@github.com/sarattha/relayna-spooltail.git@4e59f51202de1a4f360378119a7f9f660bfa2379#subdirectory=python'
```

Enable the broker's gRPC listener. The snippet below assumes the broker has been built with `cargo build --release --locked` from the same revision and runs on the same host as your application.

```bash
SPOOLTAIL_DATA=./data \
  SPOOLTAIL_LISTEN=127.0.0.1:7474 \
  SPOOLTAIL_GRPC_LISTEN=127.0.0.1:7443 \
  SPOOLTAIL_GRPC_TLS=off \
  ./target/release/spooltail
```

The examples use plaintext on loopback. For TLS, set `SPOOLTAIL_GRPC_TLS=on` on the broker and configure `SPOOLTAIL_GRPC_TLS_CERT` and `SPOOLTAIL_GRPC_TLS_KEY`. Leave the client's `tls` at its default of `True` and pass the trusted CA as PEM bytes using `root_certificates` if needed. This broker revision supports server TLS and has no client authentication. Use a trusted network.

## Run a worker and submit tasks

Use the same broker target, task topic, and status topic in publishers, workers, and status readers. Workers in the same group share task claims. Separate groups receive independent deliveries for the same task topic, so use one group for workers that are meant to compete. Use distinct topic pairs to isolate applications.

```python
import asyncio

from relayna.consumer import RetryPolicy
from relayna.contracts import TaskEnvelope
from relayna.spooltail import SpooltailClient, SpooltailTaskConsumer, SpooltailTaskContext


async def handle(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
    await context.publish_status(status="processing")
    result = {"doubled": task.payload["number"] * 2}
    await context.publish_status(status="completed", result=result)


async def main() -> None:
    async with SpooltailClient("127.0.0.1:7443", tls=False) as client:
        worker = SpooltailTaskConsumer(
            client=client,
            handler=handle,
            retry_policy=RetryPolicy(max_retries=2, delay_ms=1_000),
        )
        await client.publish_task(TaskEnvelope(task_id="example-1", payload={"number": 21}))
        await worker.run_once()  # Poll and process one available task.
        print(await client.get_latest("example-1"))


asyncio.run(main())
```

In a worker service, await `worker.run_forever()` instead of `run_once()`. Call `worker.stop()` and await the loop before closing its client. Each loop handles one lease at a time. Run more workers in the same group for concurrency. In production, the publisher and worker can use independent connections.

Use a new task ID for each independent execution. Spooltail deduplicates by topic, task ID, and event ID. Relayna defaults the work event ID to `"task"`. If a publish response is lost, retry with the same event ID and the same serialized payload. Reusing the same `TaskEnvelope` preserves its creation timestamp. A caller using a mapping should supply and preserve that timestamp on retries. Do not use the same task ID for a changed payload.

## Read status and results

Use `client.get_latest(task_id)` for the latest retained event, including a terminal result. Use `client.get_history(task_id, after=event_id)` for a history page. The broker returns at most 64 records in chronological order. Pass the last event ID as the next page's exclusive cursor until a page is empty. An expired or unknown cursor raises the native SDK's `CursorGapError`.

A FastAPI service can own the same client and install Relayna's latest-status and SSE routes.

```python
from fastapi import FastAPI

from relayna.spooltail import SpooltailClient

client = SpooltailClient("127.0.0.1:7443", tls=False)
app = FastAPI(lifespan=client.lifespan)
app.include_router(client.status_router())
```

`GET /status/{task_id}` returns the latest event. `GET /events/{task_id}` streams events and accepts `Last-Event-ID` for exclusive replay. The stream ends when it delivers `completed` or `failed`. Clients should close their SSE connection after a terminal event or a `gap` event. For a gap, use the latest status or restart replay without the stale cursor. History pagination is available through the Python client with event IDs. The RabbitMQ stream-based `/history` route uses different cursor semantics.

## Capability differences

| Capability | Spooltail behavior |
| --- | --- |
| Work and status storage | One Spooltail broker owns leased work and status history. RabbitMQ and Redis services are unnecessary. Broker records, including results, fit within a 1 MiB journal frame. Retention is broker-managed, with no Relayna per-task Redis TTL or history length setting. |
| Acknowledgement and results | A successful handler return acknowledges its lease and writes `completed` atomically. A handler can supply one terminal `completed` or `failed` event and a result through its context. Relayna commits that event after the handler returns. Progress events publish immediately. Have workers publish terminal status through their context so it is coupled to acknowledgement. |
| Retry and lease recovery | Handler exceptions use native delayed retry when a retry budget is configured. Exhaustion writes `failed` atomically, and invalid work is marked dead in the group. The worker renews its lease while the handler runs. Lease expiry can redeliver work even without an explicit retry policy. The broker exposes a generation counter, so Relayna counts one retry per explicit requeue or per two expired claims. External handler side effects need their own idempotency. |
| Existing SDK features | The Spooltail adapter covers individual tasks, canonical status envelopes, latest, per-task history, and SSE. Batch publishing, priority, routing and sharding, workflows, aggregation, manual retry, Relayna DLQ inspection and replay, Redis repositories, and Studio integrations are outside this adapter's scope. Native dead-letter settlement marks a record dead for its group and has no re-drive API. |
| Broker maturity | The verified broker is a single-node preview. It lacks replication, failover, and physical storage reclamation. |

For local integration tests, run the broker on loopback with plaintext gRPC and install the pinned SDK into the test environment. From the Relayna root, run `SPOOLTAIL_TEST_TARGET=127.0.0.1:7443 uv run pytest tests/test_spooltail.py`. Use `uv run --with /path/to/relayna_spooltail-0.0.1-py3-none-any.whl pytest tests/test_spooltail.py` with the same target to install the optional SDK from a wheel for the test run.
